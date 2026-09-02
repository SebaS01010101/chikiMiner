from datetime import datetime, timezone
from pathlib import Path

from chikiminer.checkpoint import Checkpoint
from chikiminer.models import InspectionResult, RepositoryStatus


def test_checkpoint_persists_and_reopens(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    result = InspectionResult(repo="Foo/Bar", status=RepositoryStatus.MATCH, uses_ghaw=True, backend="graphql", workflow_pair="daily", attempts=1, checked_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    with Checkpoint(path) as checkpoint:
        checkpoint.save(result)
        loaded = checkpoint.get("foo/bar")
    assert loaded is not None
    assert loaded.repo == "foo/bar"
    assert loaded.status is RepositoryStatus.MATCH
    assert loaded.uses_ghaw is True
    assert loaded.workflow_pair == "daily"
    assert loaded.checked_at == result.checked_at


def test_checkpoint_reuses_only_definitive_states(tmp_path: Path) -> None:
    path = tmp_path / "cache.sqlite3"
    results = [
        InspectionResult(repo="a/repo", status=RepositoryStatus.MATCH, uses_ghaw=True),
        InspectionResult(repo="b/repo", status=RepositoryStatus.NO_MATCH, uses_ghaw=False),
        InspectionResult(repo="c/repo", status=RepositoryStatus.NOT_FOUND),
        InspectionResult(repo="d/repo", status=RepositoryStatus.ERROR, error="timeout"),
    ]
    with Checkpoint(path) as checkpoint:
        checkpoint.save_many(results)
        loaded = checkpoint.get_many(["a/repo", "b/repo", "c/repo", "d/repo"])
        assert Checkpoint.is_reusable(loaded["a/repo"])
        assert Checkpoint.is_reusable(loaded["b/repo"])
        assert Checkpoint.is_reusable(loaded["c/repo"])
        assert not Checkpoint.is_reusable(loaded["d/repo"])


def test_checkpoint_keeps_null_classification_for_errors(tmp_path: Path) -> None:
    with Checkpoint(tmp_path / "cache.sqlite3") as checkpoint:
        checkpoint.save(InspectionResult(repo="a/repo", status=RepositoryStatus.RATE_LIMITED, error="wait"))
        loaded = checkpoint.get("a/repo")
    assert loaded is not None
    assert loaded.uses_ghaw is None

