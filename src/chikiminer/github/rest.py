"""REST Contents API adapter and response normalization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from chikiminer.models import RepositoryRef

from .models import ApiOutcome, WorkflowListing
from .rate_limit import parse_header_rate_limit, response_mentions_rate_limit


def workflow_contents_path(repo: RepositoryRef) -> str:
    return f"/repos/{repo.owner}/{repo.name}/contents/.github/workflows"


def repository_metadata_path(repo: RepositoryRef) -> str:
    return f"/repos/{repo.owner}/{repo.name}"


def _error_message(response: httpx.Response) -> str:
    try:
        value = response.json()
    except ValueError:
        value = None
    if isinstance(value, Mapping) and value.get("message"):
        return str(value["message"])
    return f"GitHub REST response status {response.status_code}"


def parse_contents_payload(payload: Any):
    """Return only normalized name/type entries; never content fields."""

    from chikiminer.models import WorkflowEntry

    raw_entries = payload.get("entries") if isinstance(payload, Mapping) else payload
    if not isinstance(raw_entries, list):
        raise ValueError("REST contents response is not a directory listing")
    entries = []
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("REST directory entry is not an object")
        name = raw.get("name")
        entry_type = raw.get("type")
        if not isinstance(name, str) or not isinstance(entry_type, str):
            raise ValueError("REST directory entry lacks name/type")
        entries.append(WorkflowEntry.from_api_entry(name, entry_type))
    return tuple(entries)


def listing_from_rest_response(
    repo: RepositoryRef,
    response: httpx.Response,
    *,
    backend: str = "rest",
    attempts: int = 1,
) -> WorkflowListing:
    """Map a response to an API outcome without collapsing errors to false."""

    rate_limit = parse_header_rate_limit(response.headers)
    etag = response.headers.get("etag")
    body_bytes = len(response.content)
    if response.status_code == 200:
        try:
            entries = parse_contents_payload(response.json())
        except (ValueError, TypeError) as exc:
            return WorkflowListing(repo=repo, outcome=ApiOutcome.ERROR, backend=backend, etag=etag, error=str(exc), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
        return WorkflowListing(repo=repo, outcome=ApiOutcome.SUCCESS, entries=entries, backend=backend, etag=etag, attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    if response.status_code == 304:
        return WorkflowListing(repo=repo, outcome=ApiOutcome.NOT_MODIFIED, backend=backend, etag=etag, attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    if response.status_code == 404:
        return WorkflowListing(repo=repo, outcome=ApiOutcome.NOT_FOUND, backend=backend, error=_error_message(response), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    if response.status_code in {403, 451}:
        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        outcome = ApiOutcome.RATE_LIMITED if rate_limit.remaining == 0 or response_mentions_rate_limit(payload) else ApiOutcome.FORBIDDEN
        return WorkflowListing(repo=repo, outcome=outcome, backend=backend, error=_error_message(response), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    if response.status_code == 429:
        return WorkflowListing(repo=repo, outcome=ApiOutcome.RATE_LIMITED, backend=backend, error=_error_message(response), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    if response.status_code in {502, 503, 504}:
        return WorkflowListing(repo=repo, outcome=ApiOutcome.UNAVAILABLE, backend=backend, error=_error_message(response), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
    return WorkflowListing(repo=repo, outcome=ApiOutcome.ERROR, backend=backend, error=_error_message(response), attempts=attempts, rate_limit=rate_limit, response_bytes=body_bytes)
