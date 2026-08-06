"""Unit tests for src/config.py.

Each test clears the relevant environment variables first so tests never
depend on (or pollute) the real shell environment or a real .env file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import DEFAULT_REQUEST_TIMEOUT_SECONDS, DEFAULT_TARGET_URL, ConfigError, load_config

ENV_VARS = ("BOT_TOKEN", "CHAT_ID", "REQUEST_TIMEOUT", "TARGET_URL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    return path


class TestLoadConfigSuccess:
    def test_loads_valid_config_from_env_vars(self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")
        monkeypatch.setenv("CHAT_ID", "999999")

        settings = load_config(env_file=empty_env_file)

        assert settings.bot_token == "123:abc"
        assert settings.chat_id == "999999"
        assert settings.request_timeout == DEFAULT_REQUEST_TIMEOUT_SECONDS
        assert settings.target_url == DEFAULT_TARGET_URL

    def test_custom_request_timeout_is_used(self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")
        monkeypatch.setenv("CHAT_ID", "999999")
        monkeypatch.setenv("REQUEST_TIMEOUT", "30")

        settings = load_config(env_file=empty_env_file)

        assert settings.request_timeout == 30.0

    def test_custom_target_url_is_used(self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")
        monkeypatch.setenv("CHAT_ID", "999999")
        monkeypatch.setenv("TARGET_URL", "https://example.test/search")

        settings = load_config(env_file=empty_env_file)

        assert settings.target_url == "https://example.test/search"


class TestLoadConfigFailures:
    def test_missing_bot_token_raises_config_error(
        self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
    ):
        monkeypatch.setenv("CHAT_ID", "999999")

        with pytest.raises(ConfigError, match="BOT_TOKEN"):
            load_config(env_file=empty_env_file)

    def test_missing_chat_id_raises_config_error(self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")

        with pytest.raises(ConfigError, match="CHAT_ID"):
            load_config(env_file=empty_env_file)

    def test_missing_both_lists_both_in_error(self, empty_env_file: Path):
        with pytest.raises(ConfigError) as exc_info:
            load_config(env_file=empty_env_file)

        assert "BOT_TOKEN" in str(exc_info.value)
        assert "CHAT_ID" in str(exc_info.value)

    def test_non_numeric_request_timeout_raises_config_error(
        self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
    ):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")
        monkeypatch.setenv("CHAT_ID", "999999")
        monkeypatch.setenv("REQUEST_TIMEOUT", "not-a-number")

        with pytest.raises(ConfigError, match="REQUEST_TIMEOUT"):
            load_config(env_file=empty_env_file)

    def test_blank_bot_token_is_treated_as_missing(
        self, monkeypatch: pytest.MonkeyPatch, empty_env_file: Path
    ):
        monkeypatch.setenv("BOT_TOKEN", "   ")
        monkeypatch.setenv("CHAT_ID", "999999")

        with pytest.raises(ConfigError, match="BOT_TOKEN"):
            load_config(env_file=empty_env_file)
