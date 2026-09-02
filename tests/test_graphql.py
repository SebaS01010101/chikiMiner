from __future__ import annotations

import json

import httpx

from chikiminer.github.client import GitHubClient
from chikiminer.github.graphql import GraphQLQueryMode, build_default_branch_query, build_head_query
from chikiminer.github.models import ApiOutcome
from chikiminer.models import RepositoryRef, WorkflowEntry


def _client(handler, *, max_attempts: int = 1, sleeps: list[float] | None = None) -> GitHubClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.github.com")
    return GitHubClient(
        "test-token",
        http_client=http_client,
        max_attempts=max_attempts,
        sleep_fn=(sleeps.append if sleeps is not None else lambda _seconds: None),
        random_fn=lambda: 0.0,
    )


def _response(request: httpx.Request, payload: dict, *, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {}, request=request)


def _rate(cost: int = 1, remaining: int = 4999) -> dict[str, object]:
    return {"cost": cost, "limit": 5000, "remaining": remaining, "used": 1, "resetAt": "2099-01-01T00:00:00Z"}


def test_head_batch_parses_entries_and_cost() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.read().decode())["query"]
        assert 'owner: "foo"' in body
        assert 'owner: "bar"' in body
        return _response(
            request,
            {
                "data": {
                    "r0": {"object": {"entries": [{"name": "a.md", "type": "blob"}, {"name": "a.lock.yml", "type": "blob"}, {"name": "nested", "type": "tree"}]}},
                    "r1": {"object": None},
                    "rateLimit": _rate(cost=4, remaining=4996),
                }
            },
        )

    client = _client(handler)
    try:
        results = client.inspect_graphql_batch([RepositoryRef(owner="foo", name="one"), RepositoryRef(owner="bar", name="two")], mode=GraphQLQueryMode.HEAD)
    finally:
        client.close()

    assert results["foo/one"].outcome is ApiOutcome.SUCCESS
    assert [entry.name for entry in results["foo/one"].entries] == ["a.md", "a.lock.yml", "nested"]
    assert results["foo/one"].entries[-1].is_file is False
    assert results["bar/two"].outcome is ApiOutcome.SUCCESS
    assert results["bar/two"].entries == ()
    assert client.metrics.graphql_cost == 4
    assert client.metrics.last_graphql_remaining == 4996


def test_graphql_partial_data_retries_only_failed_alias() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.read().decode())["query"]
        requests.append(query)
        if len(requests) == 1:
            return _response(
                request,
                {
                    "data": {
                        "r0": {"object": {"entries": [{"name": "a.md", "type": "blob"}]}},
                        "r2": {"object": {"entries": [{"name": "c.lock.yml", "type": "blob"}]}},
                        "rateLimit": _rate(),
                    },
                    "errors": [{"type": "INTERNAL", "message": "temporary alias failure", "path": ["r1", "object"]}],
                },
            )
        assert 'name: "two"' in query
        assert 'name: "one"' not in query
        assert 'name: "three"' not in query
        return _response(request, {"data": {"r0": {"object": {"entries": [{"name": "b.md", "type": "blob"}, {"name": "b.lock.yml", "type": "blob"}]}}, "rateLimit": _rate()}})

    repos = [RepositoryRef(owner="foo", name=name) for name in ("one", "two", "three")]
    client = _client(handler)
    try:
        results = client.inspect_graphql_batch(repos, mode=GraphQLQueryMode.HEAD)
    finally:
        client.close()

    assert len(requests) == 2
    assert results["foo/one"].outcome is ApiOutcome.SUCCESS
    assert results["foo/two"].outcome is ApiOutcome.SUCCESS
    assert results["foo/three"].outcome is ApiOutcome.SUCCESS
    assert client.metrics.partial_errors == 1


def test_graphql_null_repository_is_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"data": {"r0": None, "rateLimit": _rate()}})

    client = _client(handler)
    try:
        result = client.inspect_graphql_batch([RepositoryRef(owner="missing", name="repo")], mode=GraphQLQueryMode.HEAD)["missing/repo"]
    finally:
        client.close()

    assert result.outcome is ApiOutcome.NOT_FOUND
    assert result.entries == ()


def test_default_branch_one_query_parses_commit_file_tree() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.read().decode())["query"]
        assert "defaultBranchRef" in query
        assert "file(path: \".github/workflows\")" in query
        return _response(
            request,
            {"data": {"r0": {"defaultBranchRef": {"name": "master", "target": {"file": {"type": "tree", "object": {"entries": [{"name": "report.md", "type": "blob"}, {"name": "report.lock.yml", "type": "blob"}]}}}}}, "rateLimit": _rate()}},
        )

    client = _client(handler)
    try:
        result = client.inspect_graphql_batch([RepositoryRef(owner="foo", name="repo")], mode=GraphQLQueryMode.DEFAULT_BRANCH_ONE_QUERY)["foo/repo"]
    finally:
        client.close()

    assert result.outcome is ApiOutcome.SUCCESS
    assert result.default_branch == "master"
    assert {entry.name for entry in result.entries} == {"report.md", "report.lock.yml"}


def test_default_branch_two_phase_uses_returned_branch() -> None:
    phases: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.read().decode())["query"]
        phases.append(query)
        if len(phases) == 1:
            assert "defaultBranchRef { name }" in query
            return _response(request, {"data": {"r0": {"defaultBranchRef": {"name": "main"}}, "r1": {"defaultBranchRef": None}, "rateLimit": _rate(cost=1)}})
        assert '"main:.github/workflows"' in query
        return _response(request, {"data": {"r0": {"object": {"entries": [{"name": "report.md", "type": "blob"}, {"name": "report.lock.yml", "type": "blob"}]}}, "rateLimit": _rate(cost=2)}})

    repos = [RepositoryRef(owner="foo", name="one"), RepositoryRef(owner="foo", name="empty")]
    client = _client(handler)
    try:
        results = client.inspect_graphql_batch(repos, mode=GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE)
    finally:
        client.close()

    assert len(phases) == 2
    assert results["foo/one"].default_branch == "main"
    assert results["foo/one"].outcome is ApiOutcome.SUCCESS
    assert results["foo/empty"].outcome is ApiOutcome.SUCCESS
    assert results["foo/empty"].entries == ()
    assert client.metrics.graphql_cost == 3


def test_two_phase_partial_tree_error_does_not_keep_phase_one_placeholder() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.read().decode())["query"]
        calls.append(query)
        if len(calls) == 1:
            return _response(
                request,
                {
                    "data": {
                        "r0": {"defaultBranchRef": {"name": "main"}},
                        "r1": {"defaultBranchRef": None},
                        "r2": {"defaultBranchRef": {"name": "main"}},
                        "rateLimit": _rate(),
                    }
                },
            )
        if len(calls) == 2:
            assert query.count('"main:.github/workflows"') == 2
            return _response(
                request,
                {
                    "data": {
                        "r0": {"object": {"entries": [{"name": "a.md", "type": "blob"}]}},
                        "rateLimit": _rate(),
                    },
                    "errors": [{"message": "temporary tree failure", "path": ["r2", "object"]}],
                },
            )
        if len(calls) == 3:
            assert "name: \"three\"" in query
            assert "name: \"one\"" not in query
            return _response(request, {"data": {"r0": {"defaultBranchRef": {"name": "main"}}, "rateLimit": _rate()}})
        assert len(calls) == 4
        return _response(
            request,
            {
                "data": {
                    "r0": {"object": {"entries": [{"name": "b.md", "type": "blob"}, {"name": "b.lock.yml", "type": "blob"}]}},
                    "rateLimit": _rate(),
                }
            },
        )

    repos = [
        RepositoryRef(owner="foo", name="one"),
        RepositoryRef(owner="foo", name="empty"),
        RepositoryRef(owner="foo", name="three"),
    ]
    client = _client(handler)
    try:
        results = client.inspect_graphql_batch(repos, mode=GraphQLQueryMode.DEFAULT_BRANCH_TWO_PHASE)
    finally:
        client.close()

    assert len(calls) == 4
    assert results["foo/one"].entries == (WorkflowEntry(name="a.md", is_file=True),)
    assert results["foo/empty"].entries == ()
    assert {entry.name for entry in results["foo/three"].entries} == {"b.md", "b.lock.yml"}


def test_batch_502_is_split_before_rest_fallback() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return _response(request, {"message": "bad gateway"}, status=502)
        query = json.loads(request.read().decode())["query"]
        aliases = {"r0": {"object": {"entries": []}}, "r1": {"object": {"entries": []}}}
        if "owner: \"three\"" in query:
            aliases = {"r0": {"object": {"entries": []}}, "r1": {"object": {"entries": []}}}
        return _response(request, {"data": {**aliases, "rateLimit": _rate()}})

    repos = [RepositoryRef(owner="foo", name=str(index)) for index in range(4)]
    client = _client(handler)
    try:
        results = client.inspect_graphql_batch(repos, mode=GraphQLQueryMode.HEAD)
    finally:
        client.close()

    assert len(calls) == 3
    assert all(item.outcome is ApiOutcome.SUCCESS for item in results.values())
    assert client.metrics.graphql_splits == 1


def test_graphql_single_persistent_failure_uses_rest_fallback() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/graphql":
            return _response(request, {"message": "gateway timeout"}, status=504)
        return _response(request, [{"name": "x.md", "type": "file"}], headers={"ETag": '"etag"', "X-RateLimit-Remaining": "4900"})

    repo = RepositoryRef(owner="foo", name="repo")
    client = _client(handler)
    try:
        result = client.inspect_graphql_batch([repo], mode=GraphQLQueryMode.HEAD)[repo.canonical_name]
    finally:
        client.close()

    assert paths == ["/graphql", "/contents/.github/workflows"] or paths == ["/graphql", "/repos/foo/repo/contents/.github/workflows"]
    assert result.outcome is ApiOutcome.SUCCESS
    assert result.backend == "rest-fallback"
    assert client.metrics.rest_fallbacks == 1


def test_graphql_http_200_rate_limit_is_not_treated_as_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(
            request,
            {"data": {"rateLimit": _rate(remaining=0)}, "errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
        )

    client = _client(handler)
    try:
        result = client.inspect_graphql_batch([RepositoryRef(owner="foo", name="repo")], mode=GraphQLQueryMode.HEAD)["foo/repo"]
    finally:
        client.close()

    assert result.outcome is ApiOutcome.RATE_LIMITED
    assert result.outcome is not ApiOutcome.SUCCESS


def test_remote_protocol_error_is_retried_and_stays_unavailable() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.RemoteProtocolError("incomplete chunked read")

    client = _client(handler, max_attempts=2, sleeps=sleeps)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert calls == 2
    assert sleeps
    assert result.outcome is ApiOutcome.UNAVAILABLE


def test_query_builders_emit_aliases_and_no_code_search() -> None:
    repos = [RepositoryRef(owner="foo", name="a"), RepositoryRef(owner="bar", name="b")]
    head = build_head_query(repos)["query"]
    default = build_default_branch_query(repos)["query"]
    assert "r0:" in head and "r1:" in head
    assert ".github/workflows" in head
    assert "search" not in head.lower()
    assert "defaultBranchRef" in default
