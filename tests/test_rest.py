from __future__ import annotations

import httpx
import pytest

from chikiminer.github.client import GitHubClient
from chikiminer.github.models import ApiOutcome
from chikiminer.models import RepositoryRef


def _client(handler, *, max_attempts: int = 1, sleeps: list[float] | None = None) -> GitHubClient:
    http_client = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.github.com")
    return GitHubClient(
        "test-token",
        http_client=http_client,
        max_attempts=max_attempts,
        sleep_fn=(sleeps.append if sleeps is not None else lambda _seconds: None),
        random_fn=lambda: 0.0,
    )


def _response(request: httpx.Request, payload, *, status: int = 200, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=payload, headers=headers or {}, request=request)


def test_rest_200_returns_names_types_and_etag() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/foo/repo/contents/.github/workflows"
        return _response(
            request,
            [{"name": "foo.md", "type": "file", "content": "must not be used"}, {"name": "subdir", "type": "dir"}],
            headers={"ETag": '"abc"', "X-RateLimit-Limit": "5000", "X-RateLimit-Remaining": "4999", "X-RateLimit-Used": "1", "X-RateLimit-Reset": "4102444800", "X-RateLimit-Resource": "core"},
        )

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert result.outcome is ApiOutcome.SUCCESS
    assert [(entry.name, entry.is_file) for entry in result.entries] == [("foo.md", True), ("subdir", False)]
    assert result.etag == '"abc"'
    assert result.rate_limit and result.rate_limit.remaining == 4999


def test_rest_304_sends_if_none_match() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["If-None-Match"] == '"old"'
        return _response(request, None, status=304, headers={"ETag": '"old"', "X-RateLimit-Remaining": "4998"})

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"), etag='"old"')
    finally:
        client.close()

    assert result.outcome is ApiOutcome.NOT_MODIFIED
    assert result.etag == '"old"'


def test_rest_404_missing_directory_is_disambiguated_as_success_empty() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path.endswith("/contents/.github/workflows"):
            return _response(request, {"message": "Not Found"}, status=404)
        return _response(request, {"name": "repo", "private": False})

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert result.outcome is ApiOutcome.SUCCESS
    assert result.entries == ()
    assert result.not_found_path is True
    assert len(paths) == 2
    assert client.metrics.rest_404_probes == 1


def test_rest_404_missing_repository_is_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"message": "Not Found"}, status=404)

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="missing", name="repo"))
    finally:
        client.close()

    assert result.outcome is ApiOutcome.NOT_FOUND


@pytest.mark.parametrize("status,expected", [(403, ApiOutcome.FORBIDDEN), (451, ApiOutcome.FORBIDDEN), (429, ApiOutcome.RATE_LIMITED), (500, ApiOutcome.ERROR), (502, ApiOutcome.UNAVAILABLE), (503, ApiOutcome.UNAVAILABLE), (504, ApiOutcome.UNAVAILABLE)])
def test_rest_status_mapping(status: int, expected: ApiOutcome) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"message": "failure"}, status=status, headers={"X-RateLimit-Remaining": "100"})

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert result.outcome is expected


def test_rest_403_with_zero_remaining_is_rate_limited() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response(request, {"message": "API rate limit exceeded"}, status=403, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "4102444800"})

    client = _client(handler)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert result.outcome is ApiOutcome.RATE_LIMITED


def test_rest_secondary_rate_limit_message_is_retried() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response(request, {"message": "You have exceeded a secondary rate limit."}, status=403, headers={"X-RateLimit-Remaining": "100"})
        return _response(request, [])

    client = _client(handler, max_attempts=2, sleeps=sleeps)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert calls == 2
    assert sleeps
    assert result.outcome is ApiOutcome.SUCCESS


def test_retry_after_is_respected_and_retry_is_bounded() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return _response(request, {"message": "secondary rate limit"}, status=429, headers={"Retry-After": "2", "X-RateLimit-Remaining": "100"})
        return _response(request, [])

    client = _client(handler, max_attempts=2, sleeps=sleeps)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert calls == 2
    assert sleeps == [2.0]
    assert result.outcome is ApiOutcome.SUCCESS


def test_timeout_after_max_attempts_is_unavailable() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("read timeout", request=request)

    client = _client(handler, max_attempts=2)
    try:
        result = client.inspect_rest(RepositoryRef(owner="foo", name="repo"))
    finally:
        client.close()

    assert calls == 2
    assert result.outcome is ApiOutcome.UNAVAILABLE
