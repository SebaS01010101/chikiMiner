"""Pandas-based CSV ingestion and atomic output helpers."""

from __future__ import annotations

import os
import re
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .models import RepositoryRef, RepositoryStatus

DEFAULT_REPOSITORY_COLUMN = "name"
DEFAULT_CHUNK_SIZE = 10_000
_REFERENCE_PATTERN = re.compile(r"^([^/\s]+)/([^/\s]+)$")


@dataclass(frozen=True)
class ReferenceExtraction:
    """Vectorized extraction of canonical repository keys from a DataFrame."""

    keys: pd.Series
    valid: pd.Series


@dataclass(frozen=True)
class CsvInspection:
    """Summary of the input schema and repository identity column."""

    path: Path
    rows: int
    columns: tuple[str, ...]
    repository_column: str
    valid_repository_references: int
    invalid_rows: int
    unique_repositories: int
    duplicate_rows: int
    duplicate_groups: int
    empty_counts: dict[str, int]
    invalid_examples: tuple[str, ...]


def _reader_options() -> dict[str, object]:
    """Keep values textual so output values do not get type-coerced."""

    return {
        "dtype": "string",
        "keep_default_na": False,
        "na_filter": False,
        "encoding": "utf-8",
        "on_bad_lines": "error",
    }


def iter_csv_chunks(
    path: str | Path,
    *,
    chunksize: int = DEFAULT_CHUNK_SIZE,
    usecols: list[str] | None = None,
) -> Iterator[pd.DataFrame]:
    """Yield CSV chunks in input order using pandas."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise FileNotFoundError(csv_path)
    if chunksize <= 0:
        raise ValueError("chunksize must be positive")

    options = _reader_options()
    if usecols is not None:
        options["usecols"] = usecols
    reader = pd.read_csv(csv_path, chunksize=chunksize, **options)
    try:
        yield from reader
    finally:
        reader.close()


def _resolve_repository_column(columns: pd.Index, requested: str | None) -> str:
    if requested:
        if requested not in columns:
            raise ValueError(f"repository column {requested!r} is not present in the CSV")
        return requested
    if DEFAULT_REPOSITORY_COLUMN in columns:
        return DEFAULT_REPOSITORY_COLUMN
    raise ValueError(
        "could not identify repository references: expected a 'name' column "
        "or an explicit repository_column"
    )


def extract_canonical_keys(values: pd.Series) -> ReferenceExtraction:
    """Convert owner/repository values to lowercase canonical keys.

    This function is vectorized and returns ``pd.NA`` for invalid references;
    the original Series is never modified.
    """

    cleaned = values.astype("string").str.strip()
    extracted = cleaned.str.extract(_REFERENCE_PATTERN, expand=True)
    extracted.columns = ["owner", "repository"]
    valid = extracted.notna().all(axis=1)
    keys = pd.Series(pd.NA, index=values.index, dtype="string")
    if valid.any():
        keys.loc[valid] = (
            extracted.loc[valid, "owner"].str.lower()
            + "/"
            + extracted.loc[valid, "repository"].str.lower()
        )
    return ReferenceExtraction(keys=keys, valid=valid)


def inspect_csv(
    path: str | Path,
    *,
    repository_column: str | None = None,
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> CsvInspection:
    """Inspect schema, null/empty cells, and repository deduplication stats."""

    csv_path = Path(path)
    rows = 0
    columns: tuple[str, ...] | None = None
    resolved_column: str | None = None
    valid_count = 0
    invalid_count = 0
    unique_keys: set[str] = set()
    duplicate_groups: set[str] = set()
    empty_counts: Counter[str] = Counter()
    invalid_examples: list[str] = []

    for chunk in iter_csv_chunks(csv_path, chunksize=chunksize):
        if columns is None:
            columns = tuple(str(column) for column in chunk.columns)
            resolved_column = _resolve_repository_column(chunk.columns, repository_column)
        rows += len(chunk)
        for column in chunk.columns:
            empty_counts[str(column)] += int(chunk[column].eq("").sum())

        extraction = extract_canonical_keys(chunk[resolved_column])  # type: ignore[index]
        valid_count += int(extraction.valid.sum())
        invalid_count += int((~extraction.valid).sum())

        valid_keys = extraction.keys.loc[extraction.valid].dropna().astype(str)
        for key in valid_keys.unique():
            if key in unique_keys:
                duplicate_groups.add(key)
            unique_keys.add(key)

        if len(invalid_examples) < 10:
            raw_invalid = chunk.loc[~extraction.valid, resolved_column].astype(str).tolist()  # type: ignore[index]
            invalid_examples.extend(raw_invalid[: 10 - len(invalid_examples)])

    if columns is None or resolved_column is None:
        # pandas still exposes headers for a header-only CSV, but the chunk
        # iterator has no frame to inspect.  Read only the header in that case.
        header = pd.read_csv(csv_path, nrows=0, **_reader_options())
        columns = tuple(str(column) for column in header.columns)
        resolved_column = _resolve_repository_column(header.columns, repository_column)

    return CsvInspection(
        path=csv_path,
        rows=rows,
        columns=columns,
        repository_column=resolved_column,
        valid_repository_references=valid_count,
        invalid_rows=invalid_count,
        unique_repositories=len(unique_keys),
        duplicate_rows=valid_count - len(unique_keys),
        duplicate_groups=len(duplicate_groups),
        empty_counts=dict(empty_counts),
        invalid_examples=tuple(invalid_examples),
    )


def iter_unique_references(
    path: str | Path,
    *,
    repository_column: str = DEFAULT_REPOSITORY_COLUMN,
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> Iterator[RepositoryRef]:
    """Yield each valid canonical repository once, in first-seen order."""

    seen: set[str] = set()
    for chunk in iter_csv_chunks(path, chunksize=chunksize, usecols=[repository_column]):
        extraction = extract_canonical_keys(chunk[repository_column])
        valid_values = chunk.loc[extraction.valid, repository_column].astype(str)  # type: ignore[index]
        valid_keys = extraction.keys.loc[extraction.valid].astype(str)
        for raw, key in zip(valid_values.tolist(), valid_keys.tolist(), strict=True):
            if key not in seen:
                seen.add(key)
                # Use the first-seen raw spelling for API construction while
                # the canonical key remains the durable join key.
                yield RepositoryRef.from_reference(raw)


def write_classifications_atomic(
    input_path: str | Path,
    output_path: str | Path,
    classifications: Mapping[str, RepositoryStatus | str],
    *,
    repository_column: str = DEFAULT_REPOSITORY_COLUMN,
    chunksize: int = DEFAULT_CHUNK_SIZE,
    result_column: str = "uses_ghaw",
) -> int:
    """Write every input row plus a nullable GH-AW classification atomically.

    Definitive matches are written as True and definitive non-matches as
    False. Unknown or invalid rows remain in the output with an empty result
    column; they must not be converted into a false negative.
    """

    source = Path(input_path)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    if temporary.exists():
        temporary.unlink()

    normalized = {
        key.lower(): (value.value if isinstance(value, RepositoryStatus) else str(value))
        for key, value in classifications.items()
    }
    written_rows = 0
    wrote_header = False

    try:
        for chunk in iter_csv_chunks(source, chunksize=chunksize):
            if not wrote_header:
                if result_column in chunk.columns:
                    raise ValueError(f"result column {result_column!r} already exists in the input CSV")

            extraction = extract_canonical_keys(chunk[repository_column])
            statuses = extraction.keys.map(normalized).fillna("")
            result_values = pd.Series(pd.NA, index=chunk.index, dtype="string")
            result_values.loc[statuses.eq(RepositoryStatus.MATCH.value)] = "True"
            result_values.loc[statuses.eq(RepositoryStatus.NO_MATCH.value)] = "False"
            chunk[result_column] = result_values
            chunk.to_csv(
                temporary,
                mode="a" if wrote_header else "w",
                header=not wrote_header,
                index=False,
                encoding="utf-8",
                lineterminator="\n",
            )
            wrote_header = True
            written_rows += len(chunk)

        if not wrote_header:
            header = pd.read_csv(source, nrows=0, **_reader_options())
            if result_column in header.columns:
                raise ValueError(f"result column {result_column!r} already exists in the input CSV")
            header[result_column] = pd.Series(dtype="string")
            header.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")

        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return written_rows


def write_matches_atomic(
    input_path: str | Path,
    output_path: str | Path,
    classifications: Mapping[str, RepositoryStatus | str],
    *,
    repository_column: str = DEFAULT_REPOSITORY_COLUMN,
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> int:
    """Backward-compatible alias for the all-rows classified output writer."""

    return write_classifications_atomic(
        input_path,
        output_path,
        classifications,
        repository_column=repository_column,
        chunksize=chunksize,
    )
