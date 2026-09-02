# chikiMiner

chikiMiner detects repositories that use GitHub Agentic Workflows (GH-AW).
For each candidate it lists only `.github/workflows/` and checks whether the
directory contains both files with the same base name:

```text
daily-report.md
daily-report.lock.yml
```

The file contents are never downloaded, repositories are never cloned, and
GitHub Code Search is not used.

## Requisitos

- Python 3.11 or newer
- [uv](https://docs.astral.sh/uv/)
- Git
- A GitHub token in a local `.env` file

## Instalación

```powershell
uv sync
```

Configure the token without committing it:

```powershell
Copy-Item .env.example .env
# Edit .env and set GITHUB_TOKEN=...
```

The real token is read with `python-dotenv`, is never printed, and `.env` is
ignored by Git.

## Uso

```powershell
uv run miner repositories.csv --output repositories_ghaw.csv
```

The compatibility alias is also available:

```powershell
uv run chikiminer repositories.csv --output repositories_ghaw.csv
```

Useful options:

```text
--batch-size 50             GraphQL aliases per batch (default 50)
--resume / --no-resume      Reuse definitive SQLite results (default on)
--cache-path PATH           Checkpoint path
--concurrency 1             Explicit bounded concurrency; safe default is 1
--backend graphql|rest      Backend to use (default graphql)
--graphql-mode head|default-branch|two-phase (default head)
```

For the inspected large dataset, the measured faster profile was:

```powershell
uv run miner results.csv --output results_ghaw.csv `
  --batch-size 100 --concurrency 2 --resume
```

The conservative CLI defaults remain batch 50 and concurrency 1. The larger
profile is an explicit, measured choice for this run, not an unbounded worker
pool.

The command writes `OUTPUT.tmp` first and replaces the final output with
`os.replace` only after the CSV is complete. The output keeps every input row,
the input columns, original row order, original values and duplicate rows. It
appends a `uses_ghaw` column containing `True` for `MATCH`, `False` for
`NO_MATCH`, and an empty value for unresolved or invalid rows.

## Entrada inspeccionada

The real dataset currently placed in the project root is:

```text
results.csv
```

Its observed schema has 473,619 rows and these 35 columns:

```text
id, name, isFork, commits, branches, releases, forks, mainLanguage,
defaultBranch, license, homepage, watchers, stargazers, contributors, size,
createdAt, pushedAt, updatedAt, totalIssues, openIssues, totalPullRequests,
openPullRequests, blankLines, codeLines, commentLines, metrics, lastCommit,
lastCommitSHA, hasWiki, isArchived, isDisabled, isLocked, languages, labels,
topics
```

`name` is the repository identity column and has the form `owner/repository`.
The parser lowercases only the internal join key; it does not change the CSV.
The CLI accepts another CSV with the same identity convention.

## Arquitectura

The responsibilities are separated as follows:

- `csv_io.py`: pandas chunked reading, validation, normalization, deduplication
  and atomic output; it does not know HTTP.
- `detector.py`: pure O(f) set-based GH-AW detection, where f is the number of
  workflow entries.
- `models.py`: Pydantic domain models and explicit classification states.
- `github/`: one long-lived HTTPX client, REST/GraphQL adapters, rate-limit
  parsing, retries, aliases, partial errors, adaptive splitting and fallback.
- `checkpoint.py`: SQLite persistence after every logical batch.
- `service.py`: orchestration and resume policy.
- `cli.py`: Typer options, messages and exit codes.

The GraphQL fast path uses aliases and requests only `name` and `type`. The
selected production mode is the compact `HEAD` tree query:

```graphql
repository(owner: "...", name: "...") {
  object(expression: "HEAD:.github/workflows") {
    ... on Tree { entries { name type } }
  }
}
```

The two-phase explicit-default-branch mode remains available for
experimentation:

```graphql
repository(owner: "...", name: "...") {
  defaultBranchRef { name }
}
```

followed by:

```graphql
object(expression: "<default-branch>:.github/workflows") {
  ... on Tree { entries { name type } }
}
```

The `head` and one-query `defaultBranchRef { target { ... on Commit { file(...) }}}`
variants were compared against REST. On the fixed 257-repository sample,
`HEAD` matched REST including `main`, `master`, another default branch, a
missing workflow directory and a nonexistent repository. `Commit.file` had
many partial errors and fallbacks, so it was not selected.

The CLI default is now `graphql` + `head`, batch 50 and concurrency 1. REST
remains the conservative fallback and can be selected with `--backend rest`.
The 473k-repository run was resumed with batch 100 and concurrency 2 after a
controlled benchmark; its checkpoint remains compatible with changing these
values between runs. A later 500-repository comparison selected batch 100 and
concurrency 4 for the fastest measured profile without splits or errors.

The authenticated benchmark used the same 257 repositories for every run:

| Strategy | HTTP requests | Elapsed | GraphQL cost | Equivalence |
| --- | ---: | ---: | ---: | --- |
| REST (repeat baseline) | 294 | 96.00 s | — | baseline |
| GraphQL HEAD, batch 10 | 29 | 29.03 s | 27 | yes |
| GraphQL HEAD, batch 25 | 14 | 26.98 s | 12 | yes |
| GraphQL HEAD, batch 50 | 9 | 21.43 s | 7 | yes |

The first REST run had one transient error; the repeated baseline had no
errors. HEAD had two recovered partial errors and one REST fallback in each
batch-size run. The one-query `defaultBranchRef` variant had 74 partial errors
and 37 fallbacks, so it was rejected.

REST uses `GET /repos/{owner}/{repo}/contents/.github/workflows` without `ref`.
A 404 from Contents is ambiguous, so the REST adapter makes one metadata
request only in that case: an existing repository becomes an empty successful
listing (`NO_MATCH` after detection), while a missing repository becomes
`NOT_FOUND`. It stores ETags and sends `If-None-Match` when revalidating.

## Estados y resume

The checkpoint table is `repository_cache` and stores:

```text
repo, status, uses_ghaw, checked_at, backend, workflow_pair,
etag, error, attempts
```

`MATCH`, `NO_MATCH` and `NOT_FOUND` are reusable with `--resume`. `FORBIDDEN`,
`RATE_LIMITED`, `UNAVAILABLE` and `ERROR` are retried on a later run. An error,
timeout, 403 or rate limit is never converted into `NO_MATCH`. If unresolved
repositories remain, the command still writes all input rows with empty
 classification values for unresolved repositories but exits with a non-zero
 status.

## Experimento de API y benchmark

Before processing a large dataset, run the fixed-sample experiment after
configuring `.env`:

```powershell
uv run python -m chikiminer.experiment results.csv --sample-size 250 --output experiment.json
```

It uses the same sample for REST serial and GraphQL batch sizes 10, 25 and 50,
and compares `HEAD`, one-query `defaultBranchRef`, and two-phase results. It
reports HTTP requests, elapsed time, response bytes, GraphQL cost, errors,
partial errors, fallbacks and p50/p95 logical-batch latency. It also emits a
recommendation only when results are equivalent to REST and have no unresolved
errors.

The experiment was completed with the local token before selecting the
production backend. The full 473k-repository run is resumable and is currently
being processed in the local workspace; its final counts are intentionally not
hardcoded here.

## Complejidad y límites

For n CSV rows, u unique repositories, f workflow entries and batch size b:

- normalization and deduplication: O(n) expected;
- detector: O(f) time and O(f) set memory;
- ideal GraphQL HTTP calls: `ceil(u / b)` per GraphQL phase;
- REST serial calls: one Contents request per repository, plus occasional 404
  disambiguation requests.

GraphQL HTTP requests and GraphQL point cost are different metrics. The client
records `rateLimit.cost`, `remaining`, `resetAt` and REST `x-ratelimit-*` headers,
honors `Retry-After`, waits for reset when remaining is zero, uses bounded
exponential backoff with jitter, and splits batches after timeout/502/504 or
resource-limit failures. Concurrency is explicitly bounded and defaults to 1.

## Tests

```powershell
uv run pytest
uv run miner --help
```

The test suite uses `httpx.MockTransport` for GraphQL and REST. It covers the
pure detector, Pydantic models, CSV deduplication/output, SQLite persistence,
partial GraphQL data/errors, query splitting, retries, rate limits, ETags,
REST status codes and service resume behavior.

## Referencias oficiales

- [REST repository contents](https://docs.github.com/en/rest/repos/contents)
- [GraphQL rate and query limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api)
- [GraphQL Commit reference](https://docs.github.com/en/graphql/reference/objects)
