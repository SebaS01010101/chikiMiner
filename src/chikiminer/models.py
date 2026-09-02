"""Domain models used by chikiMiner.

These models deliberately do not depend on pandas, HTTPX, or SQLite.  They
represent the small pieces of data that cross the boundaries between those
adapters.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RepositoryStatus(str, Enum):
    """Classification states persisted in the checkpoint."""

    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    RATE_LIMITED = "RATE_LIMITED"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"

    @property
    def is_definitive(self) -> bool:
        return self in {
            RepositoryStatus.MATCH,
            RepositoryStatus.NO_MATCH,
            RepositoryStatus.NOT_FOUND,
        }


class RepositoryRef(BaseModel):
    """A canonicalizable GitHub ``owner/repository`` reference."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    owner: str = Field(min_length=1)
    name: str = Field(min_length=1)

    _reference_separator: ClassVar[str] = "/"

    @field_validator("owner", "name")
    @classmethod
    def strip_and_validate_component(cls, value: str) -> str:
        value = value.strip()
        if not value or "/" in value or any(ch.isspace() for ch in value):
            raise ValueError("repository components must be non-empty and contain no whitespace or '/'")
        return value

    @model_validator(mode="after")
    def reject_dot_components(self) -> RepositoryRef:
        if self.owner in {".", ".."} or self.name in {".", ".."}:
            raise ValueError("repository components cannot be '.' or '..'")
        return self

    @classmethod
    def from_reference(cls, value: str) -> RepositoryRef:
        """Parse exactly one ``owner/repository`` reference."""

        if not isinstance(value, str):
            raise ValueError("repository reference must be a string")
        parts = value.strip().split(cls._reference_separator)
        if len(parts) != 2:
            raise ValueError("repository reference must have the form owner/repository")
        return cls(owner=parts[0], name=parts[1])

    @property
    def canonical_name(self) -> str:
        """Case-insensitive key used for deduplication and joins."""

        return f"{self.owner.lower()}/{self.name.lower()}"

    def __str__(self) -> str:
        return f"{self.owner}/{self.name}"


class WorkflowEntry(BaseModel):
    """The minimal representation of a workflow directory entry."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    is_file: bool

    @classmethod
    def from_api_entry(cls, name: str, entry_type: str) -> WorkflowEntry:
        """Normalize REST ``file`` and GraphQL ``blob`` entry types."""

        return cls(name=name, is_file=entry_type in {"file", "blob"})


class RateLimitInfo(BaseModel):
    """Rate-limit data observed on a GitHub response."""

    model_config = ConfigDict(extra="forbid")

    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    reset_at: datetime | None = None
    resource: str | None = None
    cost: int | None = None


class InspectionResult(BaseModel):
    """A classification result for one canonical repository."""

    model_config = ConfigDict(extra="forbid")

    repo: str
    status: RepositoryStatus
    uses_ghaw: bool | None = None
    checked_at: datetime | None = None
    backend: str | None = None
    workflow_pair: str | None = None
    etag: str | None = None
    error: str | None = None
    attempts: int = Field(default=0, ge=0)
    rate_limit: RateLimitInfo | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def keep_status_semantics(self) -> InspectionResult:
        if self.status is RepositoryStatus.MATCH and self.uses_ghaw is not True:
            raise ValueError("MATCH requires uses_ghaw=True")
        if self.status is RepositoryStatus.NO_MATCH and self.uses_ghaw is not False:
            raise ValueError("NO_MATCH requires uses_ghaw=False")
        if self.status in {
            RepositoryStatus.NOT_FOUND,
            RepositoryStatus.FORBIDDEN,
            RepositoryStatus.RATE_LIMITED,
            RepositoryStatus.UNAVAILABLE,
            RepositoryStatus.ERROR,
        } and self.uses_ghaw is not None:
            raise ValueError("non-success statuses must not carry a boolean classification")
        return self

