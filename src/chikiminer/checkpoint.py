"""SQLite checkpoint/cache for resumable repository classifications."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .models import InspectionResult, RepositoryRef, RepositoryStatus


SCHEMA = """
CREATE TABLE IF NOT EXISTS repository_cache (
    repo TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    uses_ghaw INTEGER NULL,
    checked_at TEXT NOT NULL,
    backend TEXT NULL,
    workflow_pair TEXT NULL,
    etag TEXT NULL,
    error TEXT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
)
"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_datetime(value: datetime | None) -> str:
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


class Checkpoint:
    """A small durable SQLite store committed after each remote batch."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.execute(SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None  # type: ignore[assignment]

    def __enter__(self) -> Checkpoint:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    @staticmethod
    def is_reusable(result: InspectionResult | None) -> bool:
        return bool(result and result.status.is_definitive)

    @staticmethod
    def _from_row(row: sqlite3.Row | tuple[object, ...]) -> InspectionResult | None:
        try:
            repo, status, uses_ghaw, checked_at, backend, pair, etag, error, attempts = row
            parsed_status = RepositoryStatus(str(status))
            parsed_uses = None if uses_ghaw is None else bool(uses_ghaw)
            checked = datetime.fromisoformat(str(checked_at)) if checked_at else None
            return InspectionResult(
                repo=str(repo),
                status=parsed_status,
                uses_ghaw=parsed_uses,
                checked_at=checked,
                backend=str(backend) if backend is not None else None,
                workflow_pair=str(pair) if pair is not None else None,
                etag=str(etag) if etag is not None else None,
                error=str(error) if error is not None else None,
                attempts=int(attempts or 0),
            )
        except (TypeError, ValueError, ValidationError):
            # A malformed/stale cache record must be rechecked, not treated as
            # a negative result.
            return None

    def get(self, repo: str | RepositoryRef) -> InspectionResult | None:
        key = repo.canonical_name if isinstance(repo, RepositoryRef) else str(repo).lower()
        cursor = self._connection.execute(
            "SELECT repo, status, uses_ghaw, checked_at, backend, workflow_pair, etag, error, attempts FROM repository_cache WHERE repo = ?",
            (key,),
        )
        row = cursor.fetchone()
        return self._from_row(row) if row else None

    def get_many(self, repos: Iterable[str | RepositoryRef]) -> dict[str, InspectionResult]:
        keys = [repo.canonical_name if isinstance(repo, RepositoryRef) else str(repo).lower() for repo in repos]
        found: dict[str, InspectionResult] = {}
        # SQLite has a finite bound-variable limit.  Chunking also keeps this
        # method usable if a caller chooses a very large batch size later.
        for start in range(0, len(keys), 900):
            part = keys[start : start + 900]
            if not part:
                continue
            placeholders = ",".join("?" for _ in part)
            cursor = self._connection.execute(
                f"SELECT repo, status, uses_ghaw, checked_at, backend, workflow_pair, etag, error, attempts FROM repository_cache WHERE repo IN ({placeholders})",
                part,
            )
            for row in cursor.fetchall():
                result = self._from_row(row)
                if result is not None:
                    found[result.repo] = result
        return found

    def save(self, result: InspectionResult) -> InspectionResult:
        checked = result.checked_at or utc_now()
        saved = result.model_copy(update={"checked_at": checked})
        self.save_many([saved])
        return saved

    def save_many(self, results: Iterable[InspectionResult]) -> None:
        rows = []
        for result in results:
            checked = result.checked_at or utc_now()
            rows.append(
                (
                    result.repo.lower(),
                    result.status.value,
                    None if result.uses_ghaw is None else int(result.uses_ghaw),
                    _iso_datetime(checked),
                    result.backend,
                    result.workflow_pair,
                    result.etag,
                    result.error,
                    result.attempts,
                )
            )
        if not rows:
            return
        self._connection.executemany(
            """
            INSERT INTO repository_cache
                (repo, status, uses_ghaw, checked_at, backend, workflow_pair, etag, error, attempts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(repo) DO UPDATE SET
                status = excluded.status,
                uses_ghaw = excluded.uses_ghaw,
                checked_at = excluded.checked_at,
                backend = excluded.backend,
                workflow_pair = excluded.workflow_pair,
                etag = excluded.etag,
                error = excluded.error,
                attempts = excluded.attempts
            """,
            rows,
        )
        self._connection.commit()

