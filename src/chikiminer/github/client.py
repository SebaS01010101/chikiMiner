"""Long-lived HTTPX GitHub client with batching, retries and fallback."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable, Mapping, Sequence

import httpx

from chikiminer.models import RateLimitInfo, RepositoryRef

from .graphql import (
    GraphQLQueryMode,
    build_default_branch_name_query,
    build_default_branch_query,
    build_default_branch_tree_query,
    build_head_query,
    parse_batch_response,
)
from .models import ApiOutcome, GraphQLBatchResult, GraphQLErrorInfo, WorkflowListing
from .rate_limit import (
    conservative_retry_delay,
    parse_graphql_rate_limit,
    parse_header_rate_limit,
    response_mentions_rate_limit,
)
from .rest import (
    listing_from_rest_response,
    repository_metadata_path,
    workflow_contents_path,
)


@dataclass
class ClientMetrics:
    """Cumulative counters for one run or benchmark."""

    http_requests: int = 0
    graphql_requests: int = 0
    rest_requests: int = 0
    response_bytes: int = 0
    graphql_cost: int = 0
    graphql_batches: int = 0
    partial_errors: int = 0
    rest_fallbacks: int = 0
    rest_404_probes: int = 0
    graphql_splits: int = 0
    errors: int = 0
    last_graphql_cost: int | None = None
    last_graphql_remaining: int | None = None
    last_rest_remaining: int | None = None

    def reset(self) -> None:
        self.__dict__.update(ClientMetrics().__dict__)


@dataclass(frozen=True)
class _HttpCall:
    response: httpx.Response | None
    payload: Mapping[str, object] | None
    attempts: int
    rate_limit: RateLimitInfo | None
    error: str | None
    final_outcome: ApiOutcome | None
    response_bytes: int


@dataclass(frozen=True)
class _GraphQLFailure:
    outcome: ApiOutcome
    message: str
    attempts: int
    rate_limit: RateLimitInfo | None
    response_bytes: int
    split_eligible: bool = False


class GitHubClient:
    """A single reusable HTTPX client for REST and GraphQL calls.

    The public methods return only workflow names and types.  File contents,
    download URLs and repository archives are never requested.
    """

    def __init__(
        self,
        token: str,
        *,
        http_client: httpx.Client | None = None,
        max_attempts: int = 5,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        max_backoff_seconds: float = 60.0,
        disambiguate_rest_404: bool = True,
    ) -> None:
        if not token or not token.strip():
            raise ValueError("GITHUB_TOKEN is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(
            base_url="https://api.github.com",
            headers={
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "chikiMiner/0.1.0",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            follow_redirects=True,
        )
        self._token = token.strip() if http_client is not None else None
        self._max_attempts = max_attempts
        self._sleep = sleep_fn
        self._random = random_fn
        self._max_backoff_seconds = max_backoff_seconds
        self._disambiguate_rest_404 = disambiguate_rest_404
        self._metrics_lock = threading.Lock()
        self._rate_limit_lock = threading.Lock()
        self._last_rate_limits: dict[str, RateLimitInfo] = {}
        self.metrics = ClientMetrics()

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _headers(self, *, graphql: bool, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json" if graphql else "application/vnd.github+json",
            "User-Agent": "chikiMiner/0.1.0",
        }
        if not graphql:
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        else:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if extra:
            headers.update(extra)
        return headers

    def _wait_for_exhausted_rate_limit(self, kind: str) -> None:
        with self._rate_limit_lock:
            info = self._last_rate_limits.get(kind)
        if not info or info.remaining != 0 or not info.reset_at:
            return
        delay = (info.reset_at - datetime.now(timezone.utc)).total_seconds() + 1.0
        if delay > 0:
            self._sleep(delay)

    @staticmethod
    def _safe_json(response: httpx.Response) -> Mapping[str, object] | None:
        try:
            value = response.json()
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _response_message(response: httpx.Response) -> str:
        payload = GitHubClient._safe_json(response)
        if payload and payload.get("message"):
            return str(payload["message"])
        return f"GitHub response status {response.status_code}"

    def _record_call(self, kind: str, response_bytes: int, rate_limit: RateLimitInfo | None) -> None:
        with self._metrics_lock:
            self.metrics.http_requests += 1
            if kind == "graphql":
                self.metrics.graphql_requests += 1
                if rate_limit and rate_limit.cost is not None:
                    self.metrics.graphql_cost += rate_limit.cost
                if rate_limit:
                    self.metrics.last_graphql_cost = rate_limit.cost
                    self.metrics.last_graphql_remaining = rate_limit.remaining
            else:
                self.metrics.rest_requests += 1
                if rate_limit:
                    self.metrics.last_rest_remaining = rate_limit.remaining
            self.metrics.response_bytes += response_bytes

    def _increment(self, attribute: str, amount: int = 1) -> None:
        with self._metrics_lock:
            setattr(self.metrics, attribute, getattr(self.metrics, attribute) + amount)

    def _request(
        self,
        method: str,
        path: str,
        *,
        kind: str,
        json_body: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> _HttpCall:
        """Perform a bounded retry loop for one HTTP request."""

        total_bytes = 0
        last_rate: RateLimitInfo | None = None
        for attempt in range(1, self._max_attempts + 1):
            self._wait_for_exhausted_rate_limit(kind)
            try:
                response = self._http.request(
                    method,
                    path,
                    headers=self._headers(graphql=kind == "graphql", extra=headers),
                    json=json_body,
                )
                body_bytes = len(response.content)
                total_bytes += body_bytes
                payload = self._safe_json(response)
                if kind == "graphql":
                    rate = parse_graphql_rate_limit(payload or {}, response.headers)
                else:
                    rate = parse_header_rate_limit(response.headers)
                last_rate = rate
                with self._rate_limit_lock:
                    self._last_rate_limits[kind] = rate
                self._record_call(kind, body_bytes, rate)

                rate_limited = response.status_code in {429} or (
                    response.status_code == 403
                    and (rate.remaining == 0 or response_mentions_rate_limit(payload or {}))
                )
                graphql_body_rate_limited = kind == "graphql" and response.status_code == 200 and response_mentions_rate_limit(payload or {})
                transient = response.status_code in {502, 503, 504}
                if (rate_limited or graphql_body_rate_limited or transient) and attempt < self._max_attempts:
                    delay = conservative_retry_delay(
                        response.headers,
                        rate,
                        attempt=attempt,
                        max_seconds=self._max_backoff_seconds,
                        random_fn=self._random,
                    )
                    self._sleep(delay)
                    continue

                if rate_limited or graphql_body_rate_limited:
                    return _HttpCall(response, payload, attempt, rate, self._response_message(response), ApiOutcome.RATE_LIMITED, total_bytes)
                if transient:
                    return _HttpCall(response, payload, attempt, rate, self._response_message(response), ApiOutcome.UNAVAILABLE, total_bytes)
                return _HttpCall(response, payload, attempt, rate, None, None, total_bytes)
            except (
                httpx.ConnectError,
                httpx.ConnectTimeout,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.PoolTimeout,
                httpx.ReadError,
                httpx.RemoteProtocolError,
                httpx.CloseError,
                httpx.WriteError,
            ) as exc:
                self._record_call(kind, 0, last_rate)
                if attempt < self._max_attempts:
                    delay = conservative_retry_delay(
                        {},
                        last_rate,
                        attempt=attempt,
                        max_seconds=self._max_backoff_seconds,
                        random_fn=self._random,
                    )
                    self._sleep(delay)
                    continue
                return _HttpCall(None, None, attempt, last_rate, str(exc) or exc.__class__.__name__, ApiOutcome.UNAVAILABLE, total_bytes)
            except httpx.TransportError as exc:
                # Unknown transport errors remain terminal; known transient
                # stream/protocol failures are handled above and retried.
                self._record_call(kind, 0, last_rate)
                return _HttpCall(None, None, attempt, last_rate, str(exc) or exc.__class__.__name__, ApiOutcome.ERROR, total_bytes)

        raise AssertionError("unreachable retry loop")

    def inspect_rest(self, repo: RepositoryRef, *, etag: str | None = None, backend: str = "rest") -> WorkflowListing:
        """List ``.github/workflows`` and disambiguate a missing path."""

        extra = {"If-None-Match": etag} if etag else None
        call = self._request("GET", workflow_contents_path(repo), kind="rest", headers=extra)
        if call.response is None:
            self._increment("errors")
            return WorkflowListing(repo=repo, outcome=call.final_outcome or ApiOutcome.ERROR, backend=backend, error=call.error, attempts=call.attempts, rate_limit=call.rate_limit, response_bytes=call.response_bytes)

        listing = listing_from_rest_response(repo, call.response, backend=backend, attempts=call.attempts)
        listing = replace(listing, response_bytes=call.response_bytes, rate_limit=call.rate_limit)
        if listing.outcome is not ApiOutcome.NOT_FOUND or not self._disambiguate_rest_404:
            if listing.outcome in {ApiOutcome.ERROR, ApiOutcome.FORBIDDEN, ApiOutcome.RATE_LIMITED, ApiOutcome.UNAVAILABLE}:
                self._increment("errors")
            return listing

        # Contents 404 is ambiguous: it can mean that the directory is absent
        # or that the repository itself is absent.  One metadata request is
        # the correctness-preserving REST-only way to distinguish them.
        self._increment("rest_404_probes")
        metadata_call = self._request("GET", repository_metadata_path(repo), kind="rest")
        if metadata_call.response is None:
            self._increment("errors")
            return replace(listing, outcome=metadata_call.final_outcome or ApiOutcome.ERROR, error=metadata_call.error, attempts=listing.attempts + metadata_call.attempts, response_bytes=listing.response_bytes + metadata_call.response_bytes, rate_limit=metadata_call.rate_limit or listing.rate_limit)
        metadata_response = metadata_call.response
        total_attempts = listing.attempts + metadata_call.attempts
        total_bytes = listing.response_bytes + metadata_call.response_bytes
        metadata_rate = metadata_call.rate_limit
        if metadata_response.status_code == 200:
            # The repository exists; the requested directory simply does not.
            return replace(listing, outcome=ApiOutcome.SUCCESS, entries=(), error=None, attempts=total_attempts, response_bytes=total_bytes, rate_limit=metadata_rate, not_found_path=True)
        if metadata_response.status_code == 404:
            return replace(listing, outcome=ApiOutcome.NOT_FOUND, attempts=total_attempts, response_bytes=total_bytes, rate_limit=metadata_rate)
        if metadata_response.status_code in {403, 451}:
            outcome = ApiOutcome.RATE_LIMITED if metadata_rate and metadata_rate.remaining == 0 else ApiOutcome.FORBIDDEN
        elif metadata_response.status_code in {429}:
            outcome = ApiOutcome.RATE_LIMITED
        elif metadata_response.status_code in {502, 503, 504}:
            outcome = ApiOutcome.UNAVAILABLE
        else:
            outcome = ApiOutcome.ERROR
        self._increment("errors")
        return replace(listing, outcome=outcome, error=self._response_message(metadata_response), attempts=total_attempts, response_bytes=total_bytes, rate_limit=metadata_rate or listing.rate_limit)

    def _graphql_spec(self, spec: Mapping[str, object], *, parse_mode: GraphQLQueryMode) -> GraphQLBatchResult | _GraphQLFailure:
        aliases = spec["aliases"]
        assert isinstance(aliases, Mapping)
        repos = list(aliases.values())
        assert all(isinstance(repo, RepositoryRef) for repo in repos)
        call = self._request("POST", "/graphql", kind="graphql", json_body={"query": spec["query"]})
        if call.response is None:
            return _GraphQLFailure(call.final_outcome or ApiOutcome.ERROR, call.error or "GraphQL transport failure", call.attempts, call.rate_limit, call.response_bytes, split_eligible=call.final_outcome is ApiOutcome.UNAVAILABLE)
        if call.response.status_code != 200:
            outcome = call.final_outcome
            if outcome is None:
                if call.response.status_code == 403:
                    outcome = ApiOutcome.FORBIDDEN
                elif call.response.status_code in {502, 503, 504}:
                    outcome = ApiOutcome.UNAVAILABLE
                else:
                    outcome = ApiOutcome.ERROR
            return _GraphQLFailure(outcome, self._response_message(call.response), call.attempts, call.rate_limit, call.response_bytes, split_eligible=outcome is ApiOutcome.UNAVAILABLE)
        payload = call.payload or {}
        parsed = parse_batch_response(
            payload,
            repos,
            mode=parse_mode,
            backend="graphql",
            attempts=call.attempts,
            headers=call.response.headers,
            response_bytes=call.response_bytes,
            aliases=aliases,
        )
        self._increment("graphql_batches")
        self._increment("partial_errors", len(parsed.errors))
        if call.final_outcome is ApiOutcome.RATE_LIMITED and not parsed.results:
            return _GraphQLFailure(ApiOutcome.RATE_LIMITED, call.error or "GraphQL rate limit", call.attempts, parsed.rate_limit, call.response_bytes)
        if parsed.global_errors and not parsed.results:
            info = parsed.global_errors[0]
            outcome = ApiOutcome.RATE_LIMITED if info.rate_limited else ApiOutcome.UNAVAILABLE if info.retryable else ApiOutcome.ERROR
            return _GraphQLFailure(outcome, info.message, call.attempts, parsed.rate_limit, call.response_bytes, split_eligible=info.resource_limit or outcome is ApiOutcome.UNAVAILABLE)
        return parsed

    def _inspect_two_phase(self, repos: Sequence[RepositoryRef]) -> GraphQLBatchResult | _GraphQLFailure:
        phase_one_spec = build_default_branch_name_query(repos)
        phase_one = self._graphql_spec(phase_one_spec, parse_mode=GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE)
        if isinstance(phase_one, _GraphQLFailure):
            return phase_one

        branches: dict[str, tuple[RepositoryRef, str]] = {}
        combined = GraphQLBatchResult(
            aliases=dict(phase_one.aliases),
            errors=dict(phase_one.errors),
            global_errors=list(phase_one.global_errors),
            rate_limit=phase_one.rate_limit,
            response_bytes=phase_one.response_bytes,
        )
        for alias, listing in phase_one.results.items():
            if listing.default_branch:
                branches[alias] = (listing.repo, listing.default_branch)
            else:
                # A repository without a default branch (for example an empty
                # repository) is complete after phase one and represents an
                # empty workflow directory. Repositories with a branch remain
                # pending until phase two returns their tree.
                combined.results[alias] = listing

        if not branches:
            return combined

        phase_two_spec = build_default_branch_tree_query(branches)
        phase_two = self._graphql_spec(phase_two_spec, parse_mode=GraphQLQueryMode.HEAD)
        if isinstance(phase_two, _GraphQLFailure):
            info = GraphQLErrorInfo(
                message=phase_two.message,
                retryable=phase_two.outcome is ApiOutcome.UNAVAILABLE,
                resource_limit=phase_two.split_eligible,
                rate_limited=phase_two.outcome is ApiOutcome.RATE_LIMITED,
            )
            for alias in branches:
                combined.errors[alias] = replace(info, alias=alias)
            combined.global_errors.append(info)
            combined.rate_limit = phase_two.rate_limit or combined.rate_limit
            combined.response_bytes += phase_two.response_bytes
            return combined

        for alias, listing in phase_two.results.items():
            first = phase_one.results.get(alias)
            combined.results[alias] = replace(listing, default_branch=first.default_branch if first else None)
        combined.errors.update(phase_two.errors)
        combined.global_errors.extend(phase_two.global_errors)
        combined.rate_limit = phase_two.rate_limit or combined.rate_limit
        combined.response_bytes += phase_two.response_bytes
        return combined

    def _inspect_once(self, repos: Sequence[RepositoryRef], mode: GraphQLQueryMode) -> GraphQLBatchResult | _GraphQLFailure:
        if mode is GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE:
            return self._inspect_two_phase(repos)
        spec = build_head_query(repos) if mode is GraphQLQueryMode.HEAD else build_default_branch_query(repos)
        return self._graphql_spec(spec, parse_mode=mode)

    @staticmethod
    def _listing_for_error(repo: RepositoryRef, outcome: ApiOutcome, message: str, attempts: int, rate: RateLimitInfo | None, response_bytes: int, backend: str = "graphql") -> WorkflowListing:
        return WorkflowListing(repo=repo, outcome=outcome, backend=backend, error=message, attempts=attempts, rate_limit=rate, response_bytes=response_bytes)

    def _fallback_single(self, repo: RepositoryRef, failure: _GraphQLFailure | GraphQLErrorInfo, *, etag: str | None = None) -> WorkflowListing:
        if isinstance(failure, _GraphQLFailure):
            if failure.outcome is ApiOutcome.RATE_LIMITED:
                return self._listing_for_error(repo, ApiOutcome.RATE_LIMITED, failure.message, failure.attempts, failure.rate_limit, failure.response_bytes)
        elif failure.rate_limited:
            return self._listing_for_error(repo, ApiOutcome.RATE_LIMITED, failure.message, 0, None, 0)

        self._increment("rest_fallbacks")
        listing = self.inspect_rest(repo, etag=etag, backend="rest-fallback")
        return listing

    def _inspect_adaptive(
        self,
        repos: Sequence[RepositoryRef],
        mode: GraphQLQueryMode,
        *,
        logical_retry_budget: int = 1,
        etags: Mapping[str, str] | None = None,
    ) -> dict[str, WorkflowListing]:
        if not repos:
            return {}
        outcome = self._inspect_once(repos, mode)
        if isinstance(outcome, _GraphQLFailure):
            if outcome.outcome is ApiOutcome.RATE_LIMITED:
                return {repo.canonical_name: self._listing_for_error(repo, outcome.outcome, outcome.message, outcome.attempts, outcome.rate_limit, outcome.response_bytes) for repo in repos}
            if len(repos) > 1 and outcome.split_eligible:
                self._increment("graphql_splits")
                middle = len(repos) // 2
                left = self._inspect_adaptive(repos[:middle], mode, logical_retry_budget=logical_retry_budget, etags=etags)
                right = self._inspect_adaptive(repos[middle:], mode, logical_retry_budget=logical_retry_budget, etags=etags)
                left.update(right)
                return left
            if len(repos) == 1:
                return {repos[0].canonical_name: self._fallback_single(repos[0], outcome, etag=(etags or {}).get(repos[0].canonical_name))}
            # A batch-wide permission/authentication error should not trigger
            # one REST request per alias.  Persist it for a later resume.
            return {repo.canonical_name: self._listing_for_error(repo, outcome.outcome, outcome.message, outcome.attempts, outcome.rate_limit, outcome.response_bytes) for repo in repos}

        listings = {listing.repo.canonical_name: listing for listing in outcome.results.values()}
        if not outcome.errors:
            return listings

        failed_by_alias = outcome.errors
        # The alias-to-repository map is deterministic even when data/errors
        # arrive in an unexpected order; reconstruct it from the request list.
        alias_to_repo = dict(outcome.aliases) or {f"r{index}": repo for index, repo in enumerate(repos)}
        failed_repos = [alias_to_repo[alias] for alias in failed_by_alias if alias in alias_to_repo]
        for alias, info in failed_by_alias.items():
            if info.rate_limited or (outcome.rate_limit and outcome.rate_limit.remaining == 0):
                repo = alias_to_repo.get(alias)
                if repo:
                    listings[repo.canonical_name] = self._listing_for_error(repo, ApiOutcome.RATE_LIMITED, info.message, 0, outcome.rate_limit, outcome.response_bytes)

        retryable_repos = [repo for repo in failed_repos if repo.canonical_name not in listings]
        if retryable_repos and logical_retry_budget > 0:
            retry_results = self._inspect_adaptive(retryable_repos, mode, logical_retry_budget=logical_retry_budget - 1, etags=etags)
            listings.update(retry_results)
        else:
            for repo in retryable_repos:
                alias = next(alias for alias, candidate in alias_to_repo.items() if candidate.canonical_name == repo.canonical_name)
                listings[repo.canonical_name] = self._fallback_single(repo, failed_by_alias[alias], etag=(etags or {}).get(repo.canonical_name))
        return listings

    def inspect_graphql_batch(
        self,
        repos: Sequence[RepositoryRef],
        *,
        mode: GraphQLQueryMode = GraphQLQueryMode.HEAD,
        etags: Mapping[str, str] | None = None,
    ) -> dict[str, WorkflowListing]:
        """Inspect a batch and adaptively split only transport/resource failures."""

        return self._inspect_adaptive(list(repos), mode, etags=etags)

    def inspect_batch(
        self,
        repos: Sequence[RepositoryRef],
        *,
        backend: str = "graphql",
        graphql_mode: GraphQLQueryMode = GraphQLQueryMode.HEAD,
        etags: Mapping[str, str] | None = None,
    ) -> dict[str, WorkflowListing]:
        """Inspect a batch using the selected backend."""

        if backend == "rest":
            return {repo.canonical_name: self.inspect_rest(repo, etag=(etags or {}).get(repo.canonical_name)) for repo in repos}
        if backend != "graphql":
            raise ValueError("backend must be 'graphql' or 'rest'")
        return self.inspect_graphql_batch(repos, mode=graphql_mode, etags=etags)
