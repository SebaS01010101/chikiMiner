from pathlib import Path

import pandas as pd

from chikiminer.csv_io import inspect_csv, iter_unique_references, write_classifications_atomic
from chikiminer.models import RepositoryStatus


def _write_csv(path: Path) -> None:
    pd.DataFrame(
        [
            {"id": "1", "name": "Foo/Bar", "note": "first"},
            {"id": "2", "name": "foo/bar", "note": "second"},
            {"id": "3", "name": "other/project", "note": "third"},
            {"id": "4", "name": "invalid", "note": "fourth"},
        ]
    ).to_csv(path, index=False)


def test_inspection_normalizes_and_deduplicates_without_changing_input(tmp_path: Path) -> None:
    source = tmp_path / "repositories.csv"
    _write_csv(source)

    result = inspect_csv(source, chunksize=2)

    assert result.rows == 4
    assert result.columns == ("id", "name", "note")
    assert result.repository_column == "name"
    assert result.valid_repository_references == 3
    assert result.invalid_rows == 1
    assert result.unique_repositories == 2
    assert result.duplicate_rows == 1
    assert result.invalid_examples == ("invalid",)


def test_unique_references_are_emitted_once_in_first_seen_order(tmp_path: Path) -> None:
    source = tmp_path / "repositories.csv"
    _write_csv(source)

    refs = list(iter_unique_references(source, chunksize=2))

    assert [str(ref) for ref in refs] == ["Foo/Bar", "other/project"]


def test_output_keeps_all_rows_adds_classification_and_preserves_order(tmp_path: Path) -> None:
    source = tmp_path / "repositories.csv"
    output = tmp_path / "matches.csv"
    _write_csv(source)

    written = write_classifications_atomic(
        source,
        output,
        {
            "foo/bar": RepositoryStatus.MATCH,
            "other/project": RepositoryStatus.NO_MATCH,
        },
        chunksize=2,
    )

    assert written == 4
    result = pd.read_csv(output, dtype="string", keep_default_na=False)
    assert result.columns.tolist() == ["id", "name", "note", "uses_ghaw"]
    assert result["id"].tolist() == ["1", "2", "3", "4"]
    assert result["name"].tolist() == ["Foo/Bar", "foo/bar", "other/project", "invalid"]
    assert result["uses_ghaw"].tolist() == ["True", "True", "False", ""]
    assert not output.with_name("matches.csv.tmp").exists()
