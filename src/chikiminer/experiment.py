"""Small, reproducible REST/GraphQL correctness and benchmark experiment.

This module intentionally operates on a fixed sample only.  It is not used by
the production service and never reads or sends workflow contents.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .config import MinerConfig
from .csv_io import iter_unique_references
from .github.client import ClientMetrics, GitHubClient
from .github.graphql import GraphQLQueryMode
from .github.models import ApiOutcome, WorkflowListing
from .models import RepositoryRef

DEFAULT_PROBES = (
    "microsoft/vscode",
    "octocat/Hello-World",
    "github/docs",
    "torvalds/linux",
    "ansible/ansible",
    "joshjohanning-org/agents-and-agentic-workflows",
    "chikiminer-experiment-owner/repository-that-does-not-exist",
)


@dataclass(frozen=True)
class BenchmarkResult:
    backend: str
    mode: str | None
    batch_size: int | None
    repositories: int
    http_requests: int
    elapsed_seconds: float
    response_bytes: int
    graphql_cost: int
    errors: int
    partial_errors: int
    rest_fallbacks: int
    p50_seconds: float
    p95_seconds: float
    outcomes: dict[str, int]
    native_graphql_successes: int = 0
    equivalent_to_rest: bool | None = None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(percentile) - 1]


def select_fixed_sample(path: str | Path, size: int, *, probes: Iterable[str] = DEFAULT_PROBES) -> tuple[RepositoryRef, ...]:
    """Select first-seen CSV repositories plus deterministic edge-case probes."""

    if size < 1 or size > 500:
        raise ValueError("sample size must be between 1 and 500")
    refs: list[RepositoryRef] = []
    seen: set[str] = set()
    for ref in iter_unique_references(path):
        if ref.canonical_name not in seen:
            refs.append(ref)
            seen.add(ref.canonical_name)
        if len(refs) >= size:
            break
    for raw in probes:
        ref = RepositoryRef.from_reference(raw)
        if ref.canonical_name not in seen:
            refs.append(ref)
            seen.add(ref.canonical_name)
    return tuple(refs)


def _signature(listing: WorkflowListing) -> tuple[str, tuple[tuple[str, bool], ...]]:
    entries = tuple(sorted((entry.name, entry.is_file) for entry in listing.entries))
    return listing.outcome.value, entries


def equivalent_results(
    baseline: Mapping[str, WorkflowListing],
    candidate: Mapping[str, WorkflowListing],
) -> bool:
    """Compare only complete API outcomes, never two sets of failures."""

    comparable = {ApiOutcome.SUCCESS, ApiOutcome.NOT_FOUND, ApiOutcome.NOT_MODIFIED}
    if len(candidate) != len(baseline):
        return False
    return all(
        key in candidate
        and baseline[key].outcome in comparable
        and candidate[key].outcome in comparable
        and _signature(candidate[key]) == _signature(baseline[key])
        for key in baseline
    )


def _metrics_copy(metrics: ClientMetrics) -> ClientMetrics:
    return ClientMetrics(**asdict(metrics))


def _result(
    *,
    backend: str,
    mode: str | None,
    batch_size: int | None,
    sample_size: int,
    metrics: ClientMetrics,
    elapsed: float,
    durations: list[float],
    listings: dict[str, WorkflowListing],
    native_successes: int = 0,
) -> BenchmarkResult:
    outcomes = Counter(listing.outcome.value for listing in listings.values())
    errors = sum(count for outcome, count in outcomes.items() if outcome not in {ApiOutcome.SUCCESS.value, ApiOutcome.NOT_FOUND.value, ApiOutcome.NOT_MODIFIED.value})
    return BenchmarkResult(
        backend=backend,
        mode=mode,
        batch_size=batch_size,
        repositories=sample_size,
        http_requests=metrics.http_requests,
        elapsed_seconds=elapsed,
        response_bytes=metrics.response_bytes,
        graphql_cost=metrics.graphql_cost,
        errors=errors,
        partial_errors=metrics.partial_errors,
        rest_fallbacks=metrics.rest_fallbacks,
        p50_seconds=_percentile(durations, 50),
        p95_seconds=_percentile(durations, 95),
        outcomes=dict(outcomes),
        native_graphql_successes=native_successes,
    )


def benchmark_rest(client: GitHubClient, repos: tuple[RepositoryRef, ...]) -> tuple[BenchmarkResult, dict[str, WorkflowListing]]:
    client.metrics.reset()
    started = time.perf_counter()
    durations: list[float] = []
    listings: dict[str, WorkflowListing] = {}
    for repo in repos:
        request_started = time.perf_counter()
        listing = client.inspect_rest(repo)
        durations.append(time.perf_counter() - request_started)
        listings[repo.canonical_name] = listing
    result = _result(backend="rest", mode=None, batch_size=None, sample_size=len(repos), metrics=_metrics_copy(client.metrics), elapsed=time.perf_counter() - started, durations=durations, listings=listings)
    return result, listings


def benchmark_graphql(
    client: GitHubClient,
    repos: tuple[RepositoryRef, ...],
    *,
    mode: GraphQLQueryMode,
    batch_size: int,
) -> tuple[BenchmarkResult, dict[str, WorkflowListing]]:
    client.metrics.reset()
    started = time.perf_counter()
    durations: list[float] = []
    listings: dict[str, WorkflowListing] = {}
    native_successes = 0
    for start in range(0, len(repos), batch_size):
        batch = repos[start : start + batch_size]
        request_started = time.perf_counter()
        batch_results = client.inspect_graphql_batch(batch, mode=mode)
        durations.append(time.perf_counter() - request_started)
        listings.update(batch_results)
        native_successes += sum(1 for listing in batch_results.values() if listing.backend == "graphql")
    result = _result(backend="graphql", mode=mode.value, batch_size=batch_size, sample_size=len(repos), metrics=_metrics_copy(client.metrics), elapsed=time.perf_counter() - started, durations=durations, listings=listings, native_successes=native_successes)
    return result, listings


def choose_recommendation(results: list[BenchmarkResult]) -> str:
    """Choose a stable zero-error run, preferring native GraphQL efficiency."""

    eligible = [item for item in results if item.equivalent_to_rest is True and item.errors == 0]
    if not eligible:
        return "rest"
    graphql = [item for item in eligible if item.backend == "graphql" and item.rest_fallbacks == 0]
    candidates = graphql or [item for item in eligible if item.backend == "graphql"] or eligible
    selected = min(candidates, key=lambda item: (item.http_requests, item.graphql_cost, item.elapsed_seconds))
    if selected.backend == "rest":
        return "rest"
    return f"graphql:{selected.mode}:batch={selected.batch_size}"


def run_experiment(client: GitHubClient, repos: tuple[RepositoryRef, ...]) -> dict[str, object]:
    """Run every strategy on the exact same repositories."""

    rest_result, baseline = benchmark_rest(client, repos)
    results = [rest_result]
    modes = (GraphQLQueryMode.HEAD, GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY, GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE)
    for mode in modes:
        for batch_size in (10, 25, 50):
            current, listings = benchmark_graphql(client, repos, mode=mode, batch_size=batch_size)
            current = BenchmarkResult(**{**asdict(current), "equivalent_to_rest": equivalent_results(baseline, listings)})
            results.append(current)
    return {
        "repositories": [str(repo) for repo in repos],
        "results": [asdict(item) for item in results],
        "recommendation": choose_recommendation(results),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark chikiMiner REST and GraphQL strategies on a small fixed sample.")
    parser.add_argument("input_csv", type=Path)
    parser.add_argument("--sample-size", type=int, default=250)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        config = MinerConfig.from_env()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
        return 2
    repos = select_fixed_sample(args.input_csv, args.sample_size)
    with GitHubClient(config.github_token.get_secret_value(), max_attempts=config.max_attempts) as client:
        report = run_experiment(client, repos)
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
