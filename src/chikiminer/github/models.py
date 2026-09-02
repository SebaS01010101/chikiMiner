"""Transport-facing GitHub models.

The service converts these listings into the durable repository classification
states.  A listing never claims ``NO_MATCH``: an empty successful listing must
still be passed through the pure detector first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from chikiminer.models import RateLimitInfo, RepositoryRef, WorkflowEntry


class ApiOutcome(str, Enum):
    SUCCESS = "SUCCESS"
    NOT_MODIFIED = "NOT_MODIFIED"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class WorkflowListing:
    """Names/types returned for one repository, without file contents."""

    repo: RepositoryRef
    outcome: ApiOutcome
    entries: tuple[WorkflowEntry, ...] = ()
    backend: str = ""
    etag: str | None = None
    error: str | None = None
    attempts: int = 0
    rate_limit: RateLimitInfo | None = None
    response_bytes: int = 0
    not_found_path: bool = False
    default_branch: str | None = None


@dataclass(frozen=True)
class GraphQLErrorInfo:
    """A GraphQL error associated with an alias or with the whole operation."""

    message: str
    error_type: str | None = None
    alias: str | None = None
    retryable: bool = False
    resource_limit: bool = False
    rate_limited: bool = False


@dataclass
class GraphQLBatchResult:
    """Parsed data and errors from one GraphQL HTTP response."""

    results: dict[str, WorkflowListing] = field(default_factory=dict)
    errors: dict[str, GraphQLErrorInfo] = field(default_factory=dict)
    global_errors: list[GraphQLErrorInfo] = field(default_factory=list)
    aliases: dict[str, RepositoryRef] = field(default_factory=dict)
    rate_limit: RateLimitInfo | None = None
    response_bytes: int = 0
