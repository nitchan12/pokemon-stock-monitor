"""Unit tests for src/main.py's orchestration flow.

Every collaborator (config, scraper, parser, detector, notifier, storage)
is mocked at the `src.main` import site, so these tests exercise only the
control flow / exit-code logic in `run_once`, not the collaborators'
internal behavior (those are covered by their own test modules).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.config import ConfigError, Settings
from src.detector import Event, EventType
from src.main import (
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_ERROR,
    EXIT_PARSE_ERROR,
    EXIT_STORAGE_ERROR,
    EXIT_SUCCESS,
    main,
    run_once,
)
from src.models import Availability, Product, StoredState
from src.parser import ParserError
from src.scraper import FetchResult
from src.storage import StorageError

SETTINGS = Settings(
    bot_token="TEST",
    chat_id="chat1",
    request_timeout=5.0,
    target_url="https://example.test/search",
    state_file=Path("/tmp/does-not-matter/state.json"),
)


def make_product(id: str = "1") -> Product:
    from datetime import datetime, timezone
    from decimal import Decimal

    return Product(
        id=id,
        name="Test Product",
        price=Decimal("1000"),
        availability=Availability.IN_STOCK,
        product_url=f"https://example.test/product-{id}.html",
        checked_at=datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc),
    )


class TestRunOnceConfigError:
    def test_missing_config_returns_config_error_exit_code(self):
        with patch("src.main.load_config", side_effect=ConfigError("missing BOT_TOKEN")):
            code = run_once(settings=None)

        assert code == EXIT_CONFIG_ERROR


class TestRunOnceFetchError:
    def test_fetch_failure_returns_fetch_error_exit_code(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=False, url=SETTINGS.target_url, error="timeout"
            )
            code = run_once(settings=SETTINGS)

        assert code == EXIT_FETCH_ERROR


class TestRunOnceParseError:
    def test_parser_error_returns_parse_error_exit_code(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_search_results", side_effect=ParserError("structure changed")),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=True, url=SETTINGS.target_url, status_code=200, html="<html></html>"
            )
            code = run_once(settings=SETTINGS)

        assert code == EXIT_PARSE_ERROR


class TestRunOnceSuccessNoEvents:
    def test_no_changes_skips_notifier_and_saves_state(self):
        product = make_product("1")
        with (
            patch("src.main.load_state", return_value=StoredState(products={"1": product})),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_search_results", return_value=[product]),
            patch("src.main.detect_changes", return_value=[]),
            patch("src.main.TelegramNotifier") as mock_notifier_cls,
            patch("src.main.save_state") as mock_save_state,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=True, url=SETTINGS.target_url, status_code=200, html="<html></html>"
            )
            code = run_once(settings=SETTINGS)

        assert code == EXIT_SUCCESS
        mock_notifier_cls.assert_not_called()
        mock_save_state.assert_called_once()


class TestRunOnceSuccessWithEvents:
    def test_events_are_sent_via_notifier(self):
        product = make_product("1")
        event = Event(event_type=EventType.NEW_PRODUCT, product=product)
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_search_results", return_value=[product]),
            patch("src.main.detect_changes", return_value=[event]),
            patch("src.main.TelegramNotifier") as mock_notifier_cls,
            patch("src.main.save_state") as mock_save_state,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=True, url=SETTINGS.target_url, status_code=200, html="<html></html>"
            )
            mock_notifier_cls.return_value.send_events.return_value = 1
            code = run_once(settings=SETTINGS)

        assert code == EXIT_SUCCESS
        mock_notifier_cls.return_value.send_events.assert_called_once_with([event])
        mock_save_state.assert_called_once()


class TestRunOnceStorageError:
    def test_save_state_failure_returns_storage_error_exit_code(self):
        product = make_product("1")
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_search_results", return_value=[product]),
            patch("src.main.detect_changes", return_value=[]),
            patch("src.main.save_state", side_effect=StorageError("disk full")),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=True, url=SETTINGS.target_url, status_code=200, html="<html></html>"
            )
            code = run_once(settings=SETTINGS)

        assert code == EXIT_STORAGE_ERROR


class TestMainEntrypoint:
    def test_main_without_schedule_runs_once(self):
        with (
            patch("src.main.configure_logging"),
            patch("src.main.run_once", return_value=EXIT_SUCCESS) as mock_run_once,
        ):
            code = main([])

        assert code == EXIT_SUCCESS
        mock_run_once.assert_called_once()

    def test_main_with_schedule_flag_delegates_to_scheduler(self):
        with (
            patch("src.main.configure_logging"),
            patch("src.main._run_scheduled", return_value=EXIT_SUCCESS) as mock_run_scheduled,
        ):
            code = main(["--schedule", "*/30 * * * *"])

        assert code == EXIT_SUCCESS
        mock_run_scheduled.assert_called_once_with("*/30 * * * *")


class TestRunOnceReusesInjectedSettings:
    def test_settings_passed_in_skips_load_config(self):
        product = make_product("1")
        with (
            patch("src.main.load_config") as mock_load_config,
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_search_results", return_value=[product]),
            patch("src.main.detect_changes", return_value=[]),
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=True, url=SETTINGS.target_url, status_code=200, html="<html></html>"
            )
            run_once(settings=SETTINGS)

        mock_load_config.assert_not_called()
