"""Validated runtime configuration loaded from environment and CLI values."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr, field_validator

from .github.graphql import GraphQLQueryMode


class MinerConfig(BaseModel):
    """Settings with safe defaults for a low-concurrency run."""

    github_token: SecretStr
    batch_size: int = Field(default=50, ge=1, le=100)
    resume: bool = True
    cache_path: Path = Path(".chikiminer-cache.sqlite3")
    concurrency: int = Field(default=1, ge=1, le=4)
    backend: Literal["graphql", "rest"] = "graphql"
    graphql_mode: GraphQLQueryMode = GraphQLQueryMode.HEAD
    csv_chunksize: int = Field(default=10_000, ge=100, le=250_000)
    max_attempts: int = Field(default=5, ge=1, le=10)

    @field_validator("github_token")
    @classmethod
    def reject_empty_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("GITHUB_TOKEN is required")
        return value

    @classmethod
    def from_env(cls, *, dotenv_path: str | Path | None = None, **overrides: object) -> MinerConfig:
        """Load ``.env`` without ever exposing the token in a representation."""

        load_dotenv(dotenv_path=dotenv_path, override=False)
        token = os.getenv("GITHUB_TOKEN", "").strip()
        if not token:
            raise ValueError("GITHUB_TOKEN is required; create .env from .env.example before running")
        return cls(github_token=SecretStr(token), **overrides)
