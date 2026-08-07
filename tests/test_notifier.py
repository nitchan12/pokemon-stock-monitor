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

CHECKED_AT = datetime(2026, 8, 6, 5, 0, 0, tzinfo=timezone.utc)  # 12:00 ICT
MAX_NOTIFY = 3


def make_product(
    name: str = "Pokemon TCG MA6",
    price: str | None = "1980",
    action_label: str | None = "เพิ่มสินค้าไปยังรถเข็น",
) -> Product:
    return Product(
        id="10161784",
        name=name,
        price=Decimal(price) if price is not None else None,
        availability=Availability.IN_STOCK,
        product_url="https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-10161784.html",
        checked_at=CHECKED_AT,
        action_label=action_label,
    )


def make_event(notify_number: int = 1, is_repeat: bool = False, **kwargs) -> Event:
    return Event(
        event_type=EventType.IN_STOCK,
        product=make_product(**kwargs),
        notify_number=notify_number,
        is_repeat=is_repeat,
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
    def test_first_alert_contains_all_fields(self):
        text = format_event_message(make_event(), MAX_NOTIFY)

        assert "มีสินค้าแล้ว" in text
        assert "Pokemon TCG MA6" in text
        assert "฿1,980" in text
        assert "toysrus.co.th" in text
        assert "06/08/2026 12:00" in text

    def test_first_alert_is_not_labelled_as_a_repeat(self):
        text = format_event_message(make_event(notify_number=1, is_repeat=False), MAX_NOTIFY)
        assert "เตือนซ้ำ" not in text
        assert "แจ้งเตือนครั้งที่" not in text

    def test_repeat_alert_is_labelled_and_shows_the_count(self):
        text = format_event_message(make_event(notify_number=2, is_repeat=True), MAX_NOTIFY)

        assert "เตือนซ้ำ" in text
        assert "แจ้งเตือนครั้งที่ 2 จากสูงสุด 3 ครั้ง" in text

    def test_missing_price_uses_unspecified_label(self):
        text = format_event_message(make_event(price=None), MAX_NOTIFY)
        assert "ไม่ระบุราคา" in text

    def test_product_name_is_html_escaped(self):
        text = format_event_message(make_event(name="<script>alert(1)</script>"), MAX_NOTIFY)

        assert "<script>" not in text
        assert "&lt;script&gt;" in text


class TestPreorderVsNormalPurchase:
    """An open pre-order and a normal purchase are indistinguishable in the
    markup apart from the button label, so the alert must use that label to
    tell the buyer which kind of order they are about to place."""

    def test_preorder_uses_preorder_wording(self):
        text = format_event_message(make_event(action_label="สั่งของล่วงหน้า"), MAX_NOTIFY)

        assert "เปิดให้สั่งจองแล้ว" in text
        assert "สถานะ: เปิดให้สั่งจองล่วงหน้า" in text
        assert "มีสินค้าแล้ว" not in text

    def test_normal_purchase_uses_in_stock_wording(self):
        text = format_event_message(make_event(action_label="เพิ่มสินค้าไปยังรถเข็น"), MAX_NOTIFY)

        assert "มีสินค้าแล้ว" in text
        assert "สถานะ: มีสินค้า พร้อมกดใส่ตะกร้า" in text
        assert "สั่งจอง" not in text

    def test_repeat_preorder_alert_is_labelled_as_a_repeat(self):
        text = format_event_message(
            make_event(notify_number=2, is_repeat=True, action_label="สั่งของล่วงหน้า"),
            MAX_NOTIFY,
        )

        assert "เตือนซ้ำ" in text
        assert "สั่งจอง" in text

    def test_button_label_is_shown_to_the_user(self):
        text = format_event_message(make_event(action_label="สั่งของล่วงหน้า"), MAX_NOTIFY)
        assert "ปุ่มบนเว็บ: สั่งของล่วงหน้า" in text

    def test_missing_label_falls_back_to_in_stock_wording(self):
        text = format_event_message(make_event(action_label=None), MAX_NOTIFY)

        assert "มีสินค้าแล้ว" in text
        assert "ปุ่มบนเว็บ" not in text

    def test_english_preorder_label_is_recognized(self):
        text = format_event_message(make_event(action_label="Pre-Order Now"), MAX_NOTIFY)
        assert "เปิดให้สั่งจองแล้ว" in text


class TestTelegramNotifierSendEvent:
    def test_successful_send_returns_true(self):
        patcher, mock_client = _patch_client([_make_response(200)])
        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(make_event())

        assert result is True
        assert mock_client.post.call_count == 1

    def test_retries_transient_failure_then_succeeds(self):
        patcher, mock_client = _patch_client([httpx.ConnectError("boom"), _make_response(200)])
        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(make_event())

        assert result is True
        assert mock_client.post.call_count == 2

    def test_server_error_is_retried_then_fails_gracefully(self):
        patcher, mock_client = _patch_client([_make_response(500)] * 3)
        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(make_event())

        assert result is False
        assert mock_client.post.call_count == 3

    def test_bad_token_is_not_retried(self):
        patcher, mock_client = _patch_client([_make_response(401, text="Unauthorized")])
        with patcher:
            result = TelegramNotifier("bad-token", "chat1").send_event(make_event())

        assert result is False
        assert mock_client.post.call_count == 1

    def test_never_raises_on_unexpected_error(self):
        patcher, _ = _patch_client(RuntimeError("boom"))
        with patcher:
            result = TelegramNotifier("TEST", "chat1").send_event(make_event())
        assert result is False


class TestTelegramNotifierSendEvents:
    def test_sends_all_events_and_counts_successes(self):
        patcher, mock_client = _patch_client([_make_response(200), _make_response(200)])
        with patcher:
            sent = TelegramNotifier("TEST", "chat1").send_events([make_event(), make_event()])

        assert sent == 2
        assert mock_client.post.call_count == 2

    def test_one_failure_does_not_stop_remaining_sends(self):
        patcher, mock_client = _patch_client([_make_response(401), _make_response(200)])
        with patcher:
            sent = TelegramNotifier("TEST", "chat1").send_events([make_event(), make_event()])

        assert sent == 1
        assert mock_client.post.call_count == 2
