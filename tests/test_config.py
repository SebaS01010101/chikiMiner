from pathlib import Path

import pytest

from chikiminer.config import MinerConfig
from chikiminer.github.graphql import GraphQLQueryMode


def test_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(ValueError, match="GITHUB_TOKEN"):
        MinerConfig.from_env(dotenv_path=Path("does-not-exist.env"))


def test_config_loads_token_and_safe_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("GITHUB_TOKEN=secret-value\n", encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    config = MinerConfig.from_env(dotenv_path=env)

    assert config.github_token.get_secret_value() == "secret-value"
    assert config.batch_size == 50
    assert config.concurrency == 1
    assert config.graphql_mode is GraphQLQueryMode.HEAD
    assert "secret-value" not in repr(config)
