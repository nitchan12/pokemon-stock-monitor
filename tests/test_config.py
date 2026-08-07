"""Unit tests for src/config.py.

Each test clears the relevant environment variables first so tests never
depend on (or pollute) the real shell environment or a real .env file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import (
    DEFAULT_MAX_NOTIFY_COUNT,
    DEFAULT_PRODUCT_URLS,
    DEFAULT_REPEAT_INTERVAL_MINUTES,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ConfigError,
    load_config,
)

ENV_VARS = (
    "BOT_TOKEN",
    "CHAT_ID",
    "REQUEST_TIMEOUT",
    "PRODUCT_URLS",
    "MAX_NOTIFY_COUNT",
    "REPEAT_INTERVAL_MINUTES",
    "REQUEST_DELAY_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def empty_env_file(tmp_path: Path) -> Path:
    path = tmp_path / ".env"
    path.write_text("", encoding="utf-8")
    return path


@pytest.fixture
def valid_credentials(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("CHAT_ID", "999999")


class TestDefaults:
    def test_loads_valid_config_with_defaults(self, valid_credentials, empty_env_file: Path):
        settings = load_config(env_file=empty_env_file)

        assert settings.bot_token == "123:abc"
        assert settings.chat_id == "999999"
        assert settings.request_timeout == DEFAULT_REQUEST_TIMEOUT_SECONDS
        assert settings.product_urls == DEFAULT_PRODUCT_URLS
        assert settings.max_notify_count == DEFAULT_MAX_NOTIFY_COUNT
        assert settings.repeat_interval_minutes == DEFAULT_REPEAT_INTERVAL_MINUTES

    def test_default_pages_are_all_ma6_with_distinct_ids(self):
        assert DEFAULT_PRODUCT_URLS  # never empty
        assert all("ma6" in url for url in DEFAULT_PRODUCT_URLS)
        ids = {url.rsplit("-", 1)[-1] for url in DEFAULT_PRODUCT_URLS}
        assert len(ids) == len(DEFAULT_PRODUCT_URLS)

    def test_all_three_known_ma6_products_are_monitored(self):
        # 10161784 (the ฿1,980 booster box) briefly returned HTTP 410 and
        # vanished from search, then came back with pre-ordering open. Pages
        # here can disappear and return, so it stays on the list and a failed
        # fetch is treated as transient rather than a reason to drop it.
        for pid in ("10161784", "10161785", "10161786"):
            assert any(pid in url for url in DEFAULT_PRODUCT_URLS), f"{pid} not monitored"


class TestOverrides:
    def test_custom_request_timeout(self, valid_credentials, empty_env_file, monkeypatch):
        monkeypatch.setenv("REQUEST_TIMEOUT", "30")
        assert load_config(env_file=empty_env_file).request_timeout == 30.0

    def test_custom_alert_policy(self, valid_credentials, empty_env_file, monkeypatch):
        monkeypatch.setenv("MAX_NOTIFY_COUNT", "5")
        monkeypatch.setenv("REPEAT_INTERVAL_MINUTES", "2")

        settings = load_config(env_file=empty_env_file)
        assert settings.max_notify_count == 5
        assert settings.repeat_interval_minutes == 2

    def test_custom_product_urls_are_split_on_commas(
        self, valid_credentials, empty_env_file, monkeypatch
    ):
        monkeypatch.setenv("PRODUCT_URLS", "https://a.test/1.html, https://a.test/2.html")

        settings = load_config(env_file=empty_env_file)
        assert settings.product_urls == ("https://a.test/1.html", "https://a.test/2.html")


class TestFailures:
    def test_missing_bot_token_raises(self, empty_env_file, monkeypatch):
        monkeypatch.setenv("CHAT_ID", "999999")
        with pytest.raises(ConfigError, match="BOT_TOKEN"):
            load_config(env_file=empty_env_file)

    def test_missing_chat_id_raises(self, empty_env_file, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "123:abc")
        with pytest.raises(ConfigError, match="CHAT_ID"):
            load_config(env_file=empty_env_file)

    def test_missing_both_are_listed_together(self, empty_env_file):
        with pytest.raises(ConfigError) as exc_info:
            load_config(env_file=empty_env_file)

        assert "BOT_TOKEN" in str(exc_info.value)
        assert "CHAT_ID" in str(exc_info.value)

    def test_blank_bot_token_counts_as_missing(self, empty_env_file, monkeypatch):
        monkeypatch.setenv("BOT_TOKEN", "   ")
        monkeypatch.setenv("CHAT_ID", "999999")
        with pytest.raises(ConfigError, match="BOT_TOKEN"):
            load_config(env_file=empty_env_file)

    def test_non_numeric_timeout_raises(self, valid_credentials, empty_env_file, monkeypatch):
        monkeypatch.setenv("REQUEST_TIMEOUT", "not-a-number")
        with pytest.raises(ConfigError, match="REQUEST_TIMEOUT"):
            load_config(env_file=empty_env_file)

    def test_non_numeric_max_notify_count_raises(
        self, valid_credentials, empty_env_file, monkeypatch
    ):
        monkeypatch.setenv("MAX_NOTIFY_COUNT", "many")
        with pytest.raises(ConfigError, match="MAX_NOTIFY_COUNT"):
            load_config(env_file=empty_env_file)

    def test_zero_max_notify_count_is_rejected(
        self, valid_credentials, empty_env_file, monkeypatch
    ):
        # ge=1 on the model: alerting zero times would make the whole
        # program pointless, so it must fail loudly rather than run silently.
        monkeypatch.setenv("MAX_NOTIFY_COUNT", "0")
        with pytest.raises(ConfigError):
            load_config(env_file=empty_env_file)

    def test_product_urls_set_but_empty_raises(
        self, valid_credentials, empty_env_file, monkeypatch
    ):
        monkeypatch.setenv("PRODUCT_URLS", " , , ")
        with pytest.raises(ConfigError, match="PRODUCT_URLS"):
            load_config(env_file=empty_env_file)
