from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pandas as pd

from chikiminer.checkpoint import Checkpoint
from chikiminer.github.client import ClientMetrics
from chikiminer.github.client import GitHubClient
from chikiminer.github.graphql import GraphQLQueryMode
from chikiminer.github.models import ApiOutcome, WorkflowListing
from chikiminer.models import RepositoryRef, WorkflowEntry
from chikiminer.service import MinerService, ProgressSnapshot


class FakeInspector:
    def __init__(self) -> None:
        self.metrics = ClientMetrics()
        self.batches: list[list[str]] = []

    def inspect_batch(self, repos, *, backend: str, graphql_mode: GraphQLQueryMode, etags=None):
        self.batches.append([repo.canonical_name for repo in repos])
        results = {}
        for repo in repos:
            if repo.canonical_name == "foo/bar":
                normalized = (WorkflowEntry(name="report.md", is_file=True), WorkflowEntry(name="report.lock.yml", is_file=True))
            else:
                normalized = ()
            results[repo.canonical_name] = WorkflowListing(repo=repo, outcome=ApiOutcome.SUCCESS, entries=normalized, backend=backend, attempts=1)
        return results


def _write_input(path: Path) -> None:
    pd.DataFrame(
        [
            {"id": "1", "name": "Foo/Bar", "value": "first"},
            {"id": "2", "name": "foo/bar", "value": "second"},
            {"id": "3", "name": "other/project", "value": "third"},
            {"id": "4", "name": "invalid", "value": "kept-in-output"},
        ]
    ).to_csv(path, index=False)


def test_service_deduplicates_remote_calls_and_preserves_duplicate_rows(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    output = tmp_path / "output.csv"
    _write_input(source)
    inspector = FakeInspector()
    progress: list[ProgressSnapshot] = []

    with Checkpoint(tmp_path / "cache.sqlite3") as checkpoint:
        summary = MinerService(inspector, checkpoint, batch_size=25, csv_chunksize=2, progress_callback=progress.append).run(source, output)

    assert inspector.batches == [["foo/bar", "other/project"]]
    assert summary.inspection.invalid_rows == 1
    assert summary.matches == 1
    assert summary.output_rows == 4
    result = pd.read_csv(output, dtype="string", keep_default_na=False)
    assert result.columns.tolist() == ["id", "name", "value", "uses_ghaw"]
    assert result["id"].tolist() == ["1", "2", "3", "4"]
    assert result["name"].tolist() == ["Foo/Bar", "foo/bar", "other/project", "invalid"]
    assert result["value"].tolist() == ["first", "second", "third", "kept-in-output"]
    assert result["uses_ghaw"].tolist() == ["True", "True", "False", ""]
    assert progress[-1].processed == 2


def test_service_resume_uses_definitive_checkpoint_without_remote_call(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    _write_input(source)
    cache_path = tmp_path / "cache.sqlite3"

    first = FakeInspector()
    with Checkpoint(cache_path) as checkpoint:
        MinerService(first, checkpoint, csv_chunksize=2).run(source, first_output)
    second = FakeInspector()
    with Checkpoint(cache_path) as checkpoint:
        summary = MinerService(second, checkpoint, csv_chunksize=2).run(source, second_output)

    assert first.batches == [["foo/bar", "other/project"]]
    assert second.batches == []
    assert summary.cache_hits == 2
    assert second_output.read_text(encoding="utf-8") == first_output.read_text(encoding="utf-8")


def test_service_limits_concurrent_batches_to_configured_window(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    output = tmp_path / "output.csv"
    pd.DataFrame([{"id": str(index), "name": f"owner/repo-{index}", "value": "x"} for index in range(5)]).to_csv(source, index=False)

    class SlowInspector(FakeInspector):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum = 0
            self.lock = threading.Lock()

        def inspect_batch(self, repos, *, backend: str, graphql_mode: GraphQLQueryMode, etags=None):
            with self.lock:
                self.active += 1
                self.maximum = max(self.maximum, self.active)
            try:
                time.sleep(0.02)
                return super().inspect_batch(repos, backend=backend, graphql_mode=graphql_mode, etags=etags)
            finally:
                with self.lock:
                    self.active -= 1

    inspector = SlowInspector()
    with Checkpoint(tmp_path / "cache.sqlite3") as checkpoint:
        summary = MinerService(inspector, checkpoint, batch_size=1, concurrency=2, csv_chunksize=2).run(source, output)

    assert len(inspector.batches) == 5
    assert inspector.maximum <= 2
    assert summary.inspection.unique_repositories == 5


def test_service_revalidates_rest_etag_when_resume_is_disabled(tmp_path: Path) -> None:
    source = tmp_path / "input.csv"
    first_output = tmp_path / "first.csv"
    second_output = tmp_path / "second.csv"
    pd.DataFrame([{"id": "1", "name": "owner/repo", "value": "x"}]).to_csv(source, index=False)
    cache_path = tmp_path / "cache.sqlite3"

    def first_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"name": "report.md", "type": "file"}, {"name": "report.lock.yml", "type": "file"}], headers={"ETag": '"v1"'}, request=request)

    first_client = GitHubClient("token", http_client=httpx.Client(transport=httpx.MockTransport(first_handler), base_url="https://api.github.com"), max_attempts=1)
    try:
        with Checkpoint(cache_path) as checkpoint:
            MinerService(first_client, checkpoint, backend="rest", csv_chunksize=1).run(source, first_output)
    finally:
        first_client.close()

    def second_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"v1"'
        return httpx.Response(304, headers={"ETag": '"v1"'}, request=request)

    second_client = GitHubClient("token", http_client=httpx.Client(transport=httpx.MockTransport(second_handler), base_url="https://api.github.com"), max_attempts=1)
    try:
        with Checkpoint(cache_path) as checkpoint:
            summary = MinerService(second_client, checkpoint, backend="rest", resume=False, csv_chunksize=1).run(source, second_output)
    finally:
        second_client.close()

    assert summary.matches == 1
    assert second_output.read_text(encoding="utf-8") == first_output.read_text(encoding="utf-8")
