"""Typer command-line entry points for chikiMiner."""

from __future__ import annotations

from pathlib import Path

import typer

from .checkpoint import Checkpoint
from .config import MinerConfig
from .github.client import GitHubClient
from .github.graphql import GraphQLQueryMode
from .service import MinerService, ProgressSnapshot

app = typer.Typer(
    name="miner",
    help="Detect GitHub repositories that use GitHub Agentic Workflows.",
    add_completion=False,
    no_args_is_help=True,
)


def _mode(value: str) -> GraphQLQueryMode:
    normalized = value.strip().lower()
    choices = {
        "head": GraphQLQueryMode.HEAD,
        "default-branch": GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY,
        "two-phase": GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE,
    }
    if normalized not in choices:
        raise typer.BadParameter("must be one of: head, default-branch, two-phase")
    return choices[normalized]


def _progress(snapshot: ProgressSnapshot) -> None:
    typer.echo(
        "Processed: "
        f"{snapshot.processed:,} / {snapshot.total:,} | "
        f"Rows in CSV: {snapshot.rows_in_csv:,} | "
        f"Unique repos: {snapshot.unique_repositories:,} | "
        f"Cache hits: {snapshot.cache_hits:,} | "
        f"GH-AW found: {snapshot.matches:,} | "
        f"Errors pending: {snapshot.unresolved:,} | "
        f"GraphQL batches: {snapshot.graphql_batches:,} | "
        f"REST fallbacks: {snapshot.rest_fallbacks:,} | "
        f"Current batch size: {snapshot.current_batch_size} | "
        f"GraphQL last cost: {snapshot.graphql_last_cost if snapshot.graphql_last_cost is not None else '-'} | "
        f"GraphQL remaining: {snapshot.graphql_remaining if snapshot.graphql_remaining is not None else '-'}"
    )


@app.command()
def run(
    input_csv: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="CSV with repository candidates."),
    output: Path = typer.Option(..., "--output", "-o", help="Output CSV with all input rows and a uses_ghaw column."),
    batch_size: int = typer.Option(50, "--batch-size", min=1, max=100, help="GraphQL alias batch size."),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="Reuse definitive checkpoint results."),
    cache_path: Path = typer.Option(Path(".chikiminer-cache.sqlite3"), "--cache-path", help="SQLite checkpoint path."),
    concurrency: int = typer.Option(1, "--concurrency", min=1, max=4, help="Bounded request concurrency; 1 is the safe default."),
    backend: str = typer.Option("graphql", "--backend", help="Backend: graphql or rest."),
    graphql_mode: str = typer.Option("head", "--graphql-mode", help="GraphQL mode: head, default-branch or two-phase."),
) -> None:
    """Classify repositories and atomically write all rows with GH-AW results."""

    backend = backend.strip().lower()
    if backend not in {"graphql", "rest"}:
        raise typer.BadParameter("backend must be graphql or rest", param_hint="--backend")
    try:
        selected_mode = _mode(graphql_mode)
        config = MinerConfig.from_env(
            batch_size=batch_size,
            resume=resume,
            cache_path=cache_path,
            concurrency=concurrency,
            backend=backend,
            graphql_mode=selected_mode,
        )
    except ValueError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    try:
        with GitHubClient(config.github_token.get_secret_value(), max_attempts=config.max_attempts) as client, Checkpoint(config.cache_path) as checkpoint:
            service = MinerService(
                client,
                checkpoint,
                backend=config.backend,
                graphql_mode=config.graphql_mode,
                batch_size=config.batch_size,
                resume=config.resume,
                csv_chunksize=config.csv_chunksize,
                concurrency=config.concurrency,
                progress_callback=_progress,
            )
            summary = service.run(input_csv, output)
    except KeyboardInterrupt:
        typer.echo("Execution interrupted.\nProgress was saved.\nRun chikiMiner again to resume.", err=True)
        raise typer.Exit(code=130) from None
    except (OSError, RuntimeError, ValueError) as exc:
        typer.echo(f"Execution failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"Output written atomically: {output}")
    typer.echo(f"MATCH: {summary.status_counts.get('MATCH', 0):,}")
    typer.echo(f"NO_MATCH: {summary.status_counts.get('NO_MATCH', 0):,}")
    typer.echo(f"NOT_FOUND: {summary.status_counts.get('NOT_FOUND', 0):,}")
    unresolved = summary.unresolved
    for status in ("FORBIDDEN", "RATE_LIMITED", "UNAVAILABLE", "ERROR"):
        typer.echo(f"{status}: {summary.status_counts.get(status, 0):,}")
    if summary.inspection.invalid_rows:
        typer.echo(f"Invalid input rows skipped: {summary.inspection.invalid_rows:,}", err=True)
    typer.echo(f"Output rows: {summary.output_rows:,}")
    typer.echo("Classification column: uses_ghaw (True, False, or empty when unresolved/invalid)")
    typer.echo(f"Elapsed: {summary.elapsed_seconds:.2f}s")
    typer.echo(f"HTTP requests: {summary.metrics.http_requests:,}; response bytes: {summary.metrics.response_bytes:,}")
    typer.echo(f"GraphQL cost observed: {summary.metrics.graphql_cost:,}")
    if unresolved:
        typer.echo(f"{unresolved:,} repositories remain without a definitive classification; resume later.", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
