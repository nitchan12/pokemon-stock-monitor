"""notifier.py — sends Telegram notifications for detected change Events.

Uses the Telegram Bot API's ``sendMessage`` endpoint directly over HTTP
(no telegram SDK dependency needed for a single call). Mirrors scraper.py's
defensive retry pattern: transient failures (timeout, network error, 5xx)
are retried with exponential backoff; permanent failures (bad token, bad
chat id, 4xx) fail once and are logged, never raised past the public API,
so a single failed notification never aborts the rest of a run.
"""

from __future__ import annotations

import html
import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.detector import Event, EventType
from src.models import Availability
from src.utils import format_price_thb, format_thai_datetime

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10.0

MAX_RETRY_ATTEMPTS = 3
RETRY_WAIT_MULTIPLIER_SECONDS = 1
RETRY_WAIT_MAX_SECONDS = 8
SERVER_ERROR_THRESHOLD = 500
CLIENT_ERROR_THRESHOLD = 400

EVENT_HEADERS: dict[EventType, str] = {
    EventType.NEW_PRODUCT: "\U0001f195 พบสินค้าใหม่ในหน้าค้นหา MA6",
    EventType.BACK_IN_STOCK: "\U0001f6a8 MA6 มีสินค้าแล้ว!",
    EventType.OUT_OF_STOCK: "❌ สินค้าหมด",
    EventType.PRICE_CHANGED: "\U0001f4b0 ราคาสินค้าเปลี่ยนแปลง",
    EventType.PRODUCT_REMOVED: "⚠️ สินค้าถูกนำออกจากหน้าค้นหา",
}

AVAILABILITY_LABELS: dict[Availability, str] = {
    Availability.IN_STOCK: "มีสินค้า",
    Availability.OUT_OF_STOCK: "หมดสินค้า",
    Availability.PRE_ORDER: "เปิดพรีออเดอร์",
    Availability.UNKNOWN: "ไม่ทราบสถานะ",
}


class NotifierError(Exception):
    """Base class for all notifier failures. Never escapes the public API."""


class RetryableNotifierError(NotifierError):
    """A transient failure (timeout, connection error, 5xx) worth retrying."""


class NonRetryableNotifierError(NotifierError):
    """A permanent failure (4xx — bad token/chat id/message) — retrying
    would not help."""


def format_event_message(event: Event) -> str:
    """Render a single Event as a human-readable Telegram HTML message."""
    product = event.product
    header = EVENT_HEADERS[event.event_type]

    lines = [f"<b>{header}</b>", "", f"ชื่อสินค้า: {html.escape(product.name)}"]

    if event.event_type == EventType.PRICE_CHANGED:
        lines.append(f"ราคาเดิม: {format_price_thb(event.old_price)}")
        lines.append(f"ราคาใหม่: {format_price_thb(event.new_price)}")
    else:
        lines.append(f"ราคา: {format_price_thb(product.price)}")

    availability_label = AVAILABILITY_LABELS.get(product.availability, product.availability.value)
    lines.append(f"สถานะ: {availability_label}")
    lines.append(f"ลิงก์: {html.escape(str(product.product_url))}")
    lines.append(f"เวลา: {format_thai_datetime(product.checked_at)}")

    return "\n".join(lines)


class TelegramNotifier:
    """Thin, defensive wrapper around the Telegram Bot API sendMessage call."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds

    def send_event(self, event: Event) -> bool:
        """Format and send a single event. Returns True on success, False
        on failure. Never raises."""
        text = format_event_message(event)
        try:
            self._send_with_retry(text)
            return True
        except NotifierError as exc:
            logger.error(
                "Failed to send Telegram notification for %s (product %s): %s",
                event.event_type,
                event.product.id,
                exc,
            )
            return False
        except Exception:  # pragma: no cover - last-resort safety net
            logger.exception(
                "Unexpected error sending Telegram notification for %s (product %s)",
                event.event_type,
                event.product.id,
            )
            return False

    def send_events(self, events: list[Event]) -> int:
        """Send every event in order. Returns the number sent successfully;
        a failure on one event does not stop the rest from being sent."""
        return sum(1 for event in events if self.send_event(event))

    @retry(
        reraise=True,
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_WAIT_MULTIPLIER_SECONDS, max=RETRY_WAIT_MAX_SECONDS),
        retry=retry_if_exception_type(RetryableNotifierError),
    )
    def _send_with_retry(self, text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        try:
            with httpx.Client(timeout=self._timeout_seconds) as client:
                response = client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise RetryableNotifierError(f"timeout sending Telegram message: {exc}") from exc
        except httpx.TransportError as exc:
            raise RetryableNotifierError(f"network error sending Telegram message: {exc}") from exc

        if response.status_code >= SERVER_ERROR_THRESHOLD:
            raise RetryableNotifierError(
                f"Telegram server error {response.status_code}: {response.text}"
            )
        if response.status_code >= CLIENT_ERROR_THRESHOLD:
            raise NonRetryableNotifierError(
                f"Telegram API error {response.status_code}: {response.text}"
            )
