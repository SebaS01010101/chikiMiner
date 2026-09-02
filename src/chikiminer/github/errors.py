"""Small exception types for GitHub transport failures."""

from __future__ import annotations


class GitHubClientError(RuntimeError):
    """Base error for non-classification failures in the GitHub client."""


class GitHubConfigurationError(GitHubClientError):
    """Raised when the client cannot be configured safely."""

