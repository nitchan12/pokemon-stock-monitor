"""Unit tests for src/notifier.py.

httpx.Client is mocked throughout — these tests never call the real
Telegram API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx

from src.detector import Event, EventType
from src.models import Availability, Product
from src.notifier import TelegramNotifier, format_event_message

CHECKED_AT = datetime(2026, 8, 6, 5, 0, 0, tzinfo=timezone.utc)


def make_product(name: str = "Pokemon TCG MA6", price: str | None = "1980") -> Product:
    return Product(
        id="10161784",
        name=name,
        price=Decimal(price) if price is not None else None,
        availability=Availability.PRE_ORDER,
        product_url="https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-10161784.html",
        checked_at=CHECKED_AT,
    )


def _make_response(status_code: int = 200, text: str = '{"ok": true}') -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        text=text,
        request=httpx.Request("POST", "https://api.telegram.org/botTEST/sendMessage"),
    )


def _patch_client(post_side_effect):
    mock_client_instance = MagicMock()
    mock_client_instance.post.side_effect = post_side_effect
    mock_client_cm = MagicMock()
    mock_client_cm.__enter__.return_value = mock_client_instance
    mock_client_cm.__exit__.return_value = False
    return patch("src.notifier.httpx.Client", return_value=mock_client_cm), mock_client_instance


class TestFormatEventMessage:
    def test_new_product_message_contains_all_fields(self):
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())
        text = format_event_message(event)

        assert "พบสินค้าใหม่" in text
        assert "Pokemon TCG MA6" in text
        assert "฿1,980" in text
        assert "เปิดพรีออเดอร์" in text
        assert "toysrus.co.th" in text
        assert "06/08/2026" in text

    def test_price_changed_message_shows_old_and_new_price(self):
        event = Event(
            event_type=EventType.PRICE_CHANGED,
            product=make_product(),
            old_price=Decimal("1980"),
            new_price=Decimal("2200"),
        )
        text = format_event_message(event)

        assert "ราคาเดิม: ฿1,980" in text
        assert "ราคาใหม่: ฿2,200" in text

    def test_missing_price_uses_unspecified_label(self):
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product(price=None))
        text = format_event_message(event)

        assert "ไม่ระบุราคา" in text

    def test_product_name_is_html_escaped(self):
        product = make_product(name="<script>alert(1)</script>")
        event = Event(event_type=EventType.NEW_PRODUCT, product=product)
        text = format_event_message(event)

        assert "<script>" not in text
        assert "&lt;script&gt;" in text


class TestTelegramNotifierSendEvent:
    def test_successful_send_returns_true(self):
        patcher, mock_client = _patch_client([_make_response(200)])
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())

        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(event)

        assert result is True
        assert mock_client.post.call_count == 1

    def test_retries_transient_failure_then_succeeds(self):
        patcher, mock_client = _patch_client([httpx.ConnectError("boom"), _make_response(200)])
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())

        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(event)

        assert result is True
        assert mock_client.post.call_count == 2

    def test_server_error_is_retried_then_fails_gracefully(self):
        patcher, mock_client = _patch_client([_make_response(500)] * 3)
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())

        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(event)

        assert result is False
        assert mock_client.post.call_count == 3

    def test_bad_token_400_is_not_retried(self):
        patcher, mock_client = _patch_client([_make_response(401, text="Unauthorized")])
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())

        with patcher:
            result = TelegramNotifier("bad-token", "chat1").send_event(event)

        assert result is False
        assert mock_client.post.call_count == 1

    def test_never_raises_on_unexpected_error(self):
        patcher, _mock_client = _patch_client(RuntimeError("boom"))
        event = Event(event_type=EventType.NEW_PRODUCT, product=make_product())

        with patcher:
            # Should not raise.
            TelegramNotifier("TEST", "chat1").send_event(event)


class TestTelegramNotifierSendEvents:
    def test_sends_all_events_and_counts_successes(self):
        patcher, mock_client = _patch_client([_make_response(200), _make_response(200)])
        events = [
            Event(event_type=EventType.NEW_PRODUCT, product=make_product()),
            Event(event_type=EventType.OUT_OF_STOCK, product=make_product()),
        ]

        with patcher:
            sent_count = TelegramNotifier("TEST", "chat1").send_events(events)

        assert sent_count == 2
        assert mock_client.post.call_count == 2

    def test_one_failure_does_not_stop_remaining_sends(self):
        patcher, mock_client = _patch_client([_make_response(401)] + [_make_response(200)] * 1)
        events = [
            Event(event_type=EventType.NEW_PRODUCT, product=make_product()),
            Event(event_type=EventType.OUT_OF_STOCK, product=make_product()),
        ]

        with patcher:
            sent_count = TelegramNotifier("TEST", "chat1").send_events(events)

        assert sent_count == 1
        assert mock_client.post.call_count == 2
