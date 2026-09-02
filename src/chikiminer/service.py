"""Application orchestration: CSV, checkpoint, GitHub and pure detection."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import islice
from pathlib import Path
from typing import Protocol

from .checkpoint import Checkpoint
from .csv_io import CsvInspection, inspect_csv, iter_unique_references, write_classifications_atomic
from .detector import find_ghaw_pair
from .github.client import ClientMetrics
from .github.graphql import GraphQLQueryMode
from .github.models import ApiOutcome, WorkflowListing
from .models import InspectionResult, RepositoryRef, RepositoryStatus


class RepositoryInspector(Protocol):
    """Minimal client interface needed by the service and easy to mock."""

    metrics: ClientMetrics

    def inspect_batch(
        self,
        repos: Sequence[RepositoryRef],
        *,
        backend: str,
        graphql_mode: GraphQLQueryMode,
        etags: Mapping[str, str] | None = None,
    ) -> Mapping[str, WorkflowListing]: ...


@dataclass(frozen=True)
class ProgressSnapshot:
    processed: int
    total: int
    rows_in_csv: int
    unique_repositories: int
    cache_hits: int
    matches: int
    unresolved: int
    graphql_batches: int
    rest_fallbacks: int
    current_batch_size: int
    graphql_last_cost: int | None
    graphql_remaining: int | None


@dataclass(frozen=True)
class RunSummary:
    inspection: CsvInspection
    status_counts: dict[str, int]
    cache_hits: int
    remote_repositories: int
    output_rows: int
    elapsed_seconds: float
    metrics: ClientMetrics

    @property
    def unresolved(self) -> int:
        return sum(count for status, count in self.status_counts.items() if status not in {RepositoryStatus.MATCH.value, RepositoryStatus.NO_MATCH.value, RepositoryStatus.NOT_FOUND.value})

    @property
    def matches(self) -> int:
        return self.status_counts.get(RepositoryStatus.MATCH.value, 0)


@dataclass(frozen=True)
class _BatchContext:
    batch: list[RepositoryRef]
    cached: dict[str, InspectionResult]
    reusable: dict[str, InspectionResult]
    pending: list[RepositoryRef]


def _batched(values: Iterator[RepositoryRef], size: int) -> Iterator[list[RepositoryRef]]:
    while True:
        batch = list(islice(values, size))
        if not batch:
            return
        yield batch


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _status_from_outcome(outcome: ApiOutcome) -> RepositoryStatus:
    mapping = {
        ApiOutcome.NOT_FOUND: RepositoryStatus.NOT_FOUND,
        ApiOutcome.FORBIDDEN: RepositoryStatus.FORBIDDEN,
        ApiOutcome.RATE_LIMITED: RepositoryStatus.RATE_LIMITED,
        ApiOutcome.UNAVAILABLE: RepositoryStatus.UNAVAILABLE,
        ApiOutcome.ERROR: RepositoryStatus.ERROR,
    }
    return mapping.get(outcome, RepositoryStatus.ERROR)


class MinerService:
    """Run a resumable classification without duplicating remote work."""

    def __init__(
        self,
        inspector: RepositoryInspector,
        checkpoint: Checkpoint,
        *,
        backend: str = "graphql",
        graphql_mode: GraphQLQueryMode = GraphQLQueryMode.HEAD,
        batch_size: int = 50,
        resume: bool = True,
        csv_chunksize: int = 10_000,
        concurrency: int = 1,
        progress_callback: Callable[[ProgressSnapshot], None] | None = None,
    ) -> None:
        if backend not in {"graphql", "rest"}:
            raise ValueError("backend must be 'graphql' or 'rest'")
        if batch_size < 1 or batch_size > 100:
            raise ValueError("batch_size must be between 1 and 100")
        if concurrency < 1 or concurrency > 4:
            raise ValueError("concurrency must be between 1 and 4")
        # The default is deliberately serial.  Values above one use a small
        # explicit window and still commit results in the coordinator thread.
        self.inspector = inspector
        self.checkpoint = checkpoint
        self.backend = backend
        self.graphql_mode = graphql_mode
        self.batch_size = batch_size
        self.resume = resume
        self.csv_chunksize = csv_chunksize
        self.concurrency = concurrency
        self.progress_callback = progress_callback

    def _result_from_listing(
        self,
        repo: RepositoryRef,
        listing: WorkflowListing | None,
        *,
        cached: InspectionResult | None,
    ) -> InspectionResult:
        now = _utc_now()
        if listing is None:
            return InspectionResult(repo=repo.canonical_name, status=RepositoryStatus.ERROR, checked_at=now, backend=self.backend, error="GitHub client returned no result for repository", attempts=0)

        if listing.outcome is ApiOutcome.NOT_MODIFIED:
            if cached and cached.status.is_definitive:
                return cached.model_copy(
                    update={
                        "checked_at": now,
                        "backend": listing.backend or cached.backend,
                        "etag": listing.etag or cached.etag,
                        "error": None,
                        "attempts": cached.attempts + listing.attempts,
                    }
                )
            return InspectionResult(repo=repo.canonical_name, status=RepositoryStatus.ERROR, checked_at=now, backend=listing.backend, etag=listing.etag, error="received 304 without a reusable definitive checkpoint", attempts=listing.attempts)

        if listing.outcome is ApiOutcome.SUCCESS:
            filenames = (entry.name for entry in listing.entries if entry.is_file)
            pair = find_ghaw_pair(filenames)
            status = RepositoryStatus.MATCH if pair is not None else RepositoryStatus.NO_MATCH
            return InspectionResult(
                repo=repo.canonical_name,
                status=status,
                uses_ghaw=pair is not None,
                checked_at=now,
                backend=listing.backend,
                workflow_pair=pair,
                etag=listing.etag,
                error=None,
                attempts=listing.attempts,
            )

        return InspectionResult(
            repo=repo.canonical_name,
            status=_status_from_outcome(listing.outcome),
            checked_at=now,
            backend=listing.backend,
            etag=listing.etag,
            error=listing.error,
            attempts=listing.attempts,
        )

    def _emit_progress(self, processed: int, inspection: CsvInspection, cache_hits: int, status_counts: Counter[str]) -> None:
        if not self.progress_callback:
            return
        metrics = self.inspector.metrics
        self.progress_callback(
            ProgressSnapshot(
                processed=processed,
                total=inspection.unique_repositories,
                rows_in_csv=inspection.rows,
                unique_repositories=inspection.unique_repositories,
                cache_hits=cache_hits,
                matches=status_counts[RepositoryStatus.MATCH.value],
                unresolved=sum(count for status, count in status_counts.items() if status not in {RepositoryStatus.MATCH.value, RepositoryStatus.NO_MATCH.value, RepositoryStatus.NOT_FOUND.value}),
                graphql_batches=metrics.graphql_batches,
                rest_fallbacks=metrics.rest_fallbacks,
                current_batch_size=self.batch_size,
                graphql_last_cost=getattr(metrics, "last_graphql_cost", None),
                graphql_remaining=metrics.last_graphql_remaining,
            )
        )

    def _prepare_batch(self, batch: list[RepositoryRef]) -> _BatchContext:
        cached = self.checkpoint.get_many(batch)
        reusable = {
            repo.canonical_name: result
            for repo in batch
            if (result := cached.get(repo.canonical_name)) is not None and Checkpoint.is_reusable(result) and self.resume
        }
        pending = [repo for repo in batch if repo.canonical_name not in reusable]
        return _BatchContext(batch=batch, cached=cached, reusable=reusable, pending=pending)

    def _execute_batch(self, context: _BatchContext) -> tuple[list[InspectionResult], list[InspectionResult]]:
        batch_results = list(context.reusable.values())
        if context.pending:
            listings = self.inspector.inspect_batch(
                context.pending,
                backend=self.backend,
                graphql_mode=self.graphql_mode,
                etags={repo.canonical_name: context.cached[repo.canonical_name].etag for repo in context.pending if context.cached.get(repo.canonical_name) and context.cached[repo.canonical_name].etag},
            )
            for repo in context.pending:
                batch_results.append(self._result_from_listing(repo, listings.get(repo.canonical_name), cached=context.cached.get(repo.canonical_name)))
        remote_results = [result for result in batch_results if result.repo not in context.reusable]
        return batch_results, remote_results

    def run(self, input_path: str | Path, output_path: str | Path) -> RunSummary:
        started = time.perf_counter()
        inspection = inspect_csv(input_path, chunksize=self.csv_chunksize)
        classifications: dict[str, RepositoryStatus] = {}
        status_counts: Counter[str] = Counter()
        cache_hits = 0
        remote_repositories = 0
        batch_iter = _batched(
            iter_unique_references(
                input_path,
                repository_column=inspection.repository_column,
                chunksize=self.csv_chunksize,
            ),
            self.batch_size,
        )
        executor: ThreadPoolExecutor | None = None

        try:
            if self.concurrency > 1:
                executor = ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="chikiminer")
            while True:
                contexts: list[_BatchContext] = []
                for _ in range(self.concurrency):
                    try:
                        context = self._prepare_batch(next(batch_iter))
                    except StopIteration:
                        break
                    contexts.append(context)
                    cache_hits += len(context.reusable)
                    remote_repositories += len(context.pending)
                if not contexts:
                    break

                futures: dict[Future[tuple[list[InspectionResult], list[InspectionResult]]], _BatchContext] = {}
                if executor is None:
                    batch_results, remote_results = self._execute_batch(contexts[0])
                    futures = {}
                    completed_batches = [(contexts[0], batch_results, remote_results)]
                else:
                    for context in contexts:
                        futures[executor.submit(self._execute_batch, context)] = context
                    completed_batches = []
                    for future in as_completed(futures):
                        batch_results, remote_results = future.result()
                        completed_batches.append((futures[future], batch_results, remote_results))

                for context, batch_results, remote_results in completed_batches:
                    # This is the durable checkpoint boundary. It is reached
                    # once for every logical batch before progress is emitted.
                    if remote_results:
                        self.checkpoint.save_many(remote_results)
                    for result in batch_results:
                        classifications[result.repo] = result.status
                        status_counts[result.status.value] += 1
                    self._emit_progress(len(classifications), inspection, cache_hits, status_counts)
        except KeyboardInterrupt:
            if executor is not None:
                # Let requests already in flight finish before the CLI closes
                # the shared HTTPX client. Results from completed logical
                # batches are already durable; only in-flight batches may be
                # retried on the next resume.
                executor.shutdown(wait=True, cancel_futures=True)
                executor = None
            raise
        finally:
            if executor is not None:
                executor.shutdown(wait=True)

        output_rows = write_classifications_atomic(
            input_path,
            output_path,
            classifications,
            repository_column=inspection.repository_column,
            chunksize=self.csv_chunksize,
        )
        elapsed = time.perf_counter() - started
        return RunSummary(
            inspection=inspection,
            status_counts=dict(status_counts),
            cache_hits=cache_hits,
            remote_repositories=remote_repositories,
            output_rows=output_rows,
            elapsed_seconds=elapsed,
            metrics=self.inspector.metrics,
        )
