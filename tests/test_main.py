"""Unit tests for src/main.py's orchestration flow.

Collaborators (config, scraper, parser, detector, notifier, storage) are
mocked at the `src.main` import site, so these tests exercise only the
control flow / exit-code logic in `run_once`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from src.config import ConfigError, Settings
from src.detector import DetectionResult, Event, EventType
from src.main import (
    EXIT_CONFIG_ERROR,
    EXIT_FETCH_ERROR,
    EXIT_STORAGE_ERROR,
    EXIT_SUCCESS,
    main,
    run_once,
)
from src.models import Availability, Product, ProductState, StoredState
from src.parser import ParserError
from src.scraper import FetchResult
from src.storage import StorageError

SETTINGS = Settings(
    bot_token="TEST",
    chat_id="chat1",
    request_timeout=5.0,
    product_urls=(
        "https://example.test/product-1.html",
        "https://example.test/product-2.html",
        "https://example.test/product-3.html",
    ),
    state_file=Path("/tmp/does-not-matter/state.json"),
    request_delay_seconds=0,  # keep tests fast
)


def make_product(id: str = "1", availability: Availability = Availability.OUT_OF_STOCK) -> Product:
    return Product(
        id=id,
        name="Pokemon TCG MA6",
        price=Decimal("1980"),
        availability=availability,
        product_url=f"https://example.test/product-{id}.html",
        checked_at=datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc),
    )


def _ok_fetch(url: str = "https://example.test/product-1.html") -> FetchResult:
    return FetchResult(success=True, url=url, status_code=200, html="<html></html>")


class TestConfigFailure:
    def test_missing_config_returns_config_error_exit_code(self):
        with patch("src.main.load_config", side_effect=ConfigError("missing BOT_TOKEN")):
            assert run_once(settings=None) == EXIT_CONFIG_ERROR

    def test_injected_settings_skip_load_config(self):
        with (
            patch("src.main.load_config") as mock_load_config,
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=make_product()),
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            run_once(settings=SETTINGS)

        mock_load_config.assert_not_called()


class TestAllPagesFail:
    def test_every_fetch_failing_returns_fetch_error(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = FetchResult(
                success=False, url="https://example.test/product-1.html", error="timeout"
            )
            assert run_once(settings=SETTINGS) == EXIT_FETCH_ERROR

    def test_every_parse_failing_returns_fetch_error(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", side_effect=ParserError("structure changed")),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            assert run_once(settings=SETTINGS) == EXIT_FETCH_ERROR


class TestPartialFailureIsTolerated:
    def test_one_bad_page_does_not_stop_the_others(self):
        # Page 2 fails to download; pages 1 and 3 succeed.
        fetches = [
            _ok_fetch(),
            FetchResult(success=False, url="https://example.test/product-2.html", error="timeout"),
            _ok_fetch(),
        ]
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", side_effect=[make_product("1"), make_product("3")]),
            patch("src.main.detect_in_stock") as mock_detect,
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.side_effect = fetches
            mock_detect.return_value = DetectionResult(events=[], new_state={})
            assert run_once(settings=SETTINGS) == EXIT_SUCCESS

        parsed = mock_detect.call_args.kwargs["products"]
        assert [p.id for p in parsed] == ["1", "3"]

    def test_one_unparseable_page_does_not_stop_the_others(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch(
                "src.main.parse_product_page",
                side_effect=[make_product("1"), ParserError("bad"), make_product("3")],
            ),
            patch("src.main.detect_in_stock") as mock_detect,
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            mock_detect.return_value = DetectionResult(events=[], new_state={})
            assert run_once(settings=SETTINGS) == EXIT_SUCCESS

        assert [p.id for p in mock_detect.call_args.kwargs["products"]] == ["1", "3"]


class TestNotification:
    def test_no_events_skips_the_notifier_entirely(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=make_product()),
            patch("src.main.detect_in_stock", return_value=DetectionResult([], {})),
            patch("src.main.TelegramNotifier") as mock_notifier_cls,
            patch("src.main.save_state") as mock_save,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            assert run_once(settings=SETTINGS) == EXIT_SUCCESS

        mock_notifier_cls.assert_not_called()
        mock_save.assert_called_once()

    def test_in_stock_event_is_sent(self):
        product = make_product("1", Availability.IN_STOCK)
        event = Event(
            event_type=EventType.IN_STOCK, product=product, notify_number=1, is_repeat=False
        )
        detection = DetectionResult(
            events=[event],
            new_state={"1": ProductState(product=product, notify_count=1)},
        )
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=product),
            patch("src.main.detect_in_stock", return_value=detection),
            patch("src.main.TelegramNotifier") as mock_notifier_cls,
            patch("src.main.save_state") as mock_save,
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            mock_notifier_cls.return_value.send_events.return_value = 1
            assert run_once(settings=SETTINGS) == EXIT_SUCCESS

        mock_notifier_cls.return_value.send_events.assert_called_once_with([event])
        # The persisted state must be the one the detector produced, so the
        # repeat-throttle counters survive to the next run.
        saved_state = mock_save.call_args[0][1]
        assert saved_state.products["1"].notify_count == 1


class TestUnreadableExistingState:
    """A state file written by an older, incompatible version must not take
    the monitor down — it should degrade to empty state and keep running."""

    def test_unreadable_state_does_not_crash_the_run(self):
        with (
            patch("src.main.load_state", side_effect=StorageError("incompatible schema")),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=make_product()),
            patch("src.main.detect_in_stock", return_value=DetectionResult([], {})),
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            assert run_once(settings=SETTINGS) == EXIT_SUCCESS

    def test_unreadable_state_falls_back_to_empty_state(self):
        with (
            patch("src.main.load_state", side_effect=StorageError("incompatible schema")),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=make_product()),
            patch("src.main.detect_in_stock") as mock_detect,
            patch("src.main.save_state"),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            mock_detect.return_value = DetectionResult([], {})
            run_once(settings=SETTINGS)

        assert mock_detect.call_args.kwargs["previous_state"] == {}


class TestStorageFailure:
    def test_save_failure_returns_storage_error(self):
        with (
            patch("src.main.load_state", return_value=StoredState()),
            patch("src.main.Scraper") as mock_scraper_cls,
            patch("src.main.parse_product_page", return_value=make_product()),
            patch("src.main.detect_in_stock", return_value=DetectionResult([], {})),
            patch("src.main.save_state", side_effect=StorageError("disk full")),
        ):
            mock_scraper_cls.return_value.fetch_html.return_value = _ok_fetch()
            assert run_once(settings=SETTINGS) == EXIT_STORAGE_ERROR


class TestEntrypoint:
    def test_main_without_schedule_runs_once(self):
        with (
            patch("src.main.configure_logging"),
            patch("src.main.run_once", return_value=EXIT_SUCCESS) as mock_run_once,
        ):
            assert main([]) == EXIT_SUCCESS
        mock_run_once.assert_called_once()

    def test_main_with_schedule_delegates_to_scheduler(self):
        with (
            patch("src.main.configure_logging"),
            patch("src.main._run_scheduled", return_value=EXIT_SUCCESS) as mock_sched,
        ):
            assert main(["--schedule", "*/2 * * * *"]) == EXIT_SUCCESS
        mock_sched.assert_called_once_with("*/2 * * * *")
