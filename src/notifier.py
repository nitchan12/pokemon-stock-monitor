"""notifier.py — sends Telegram alerts for products that are in stock.

Uses the Telegram Bot API's ``sendMessage`` endpoint directly over HTTP
(no telegram SDK dependency needed for a single call). Mirrors scraper.py's
defensive retry pattern: transient failures (timeout, network error, 5xx)
are retried with exponential backoff; permanent failures (bad token, bad
chat id, 4xx) fail once and are logged, never raised past the public API,
so one failed message never aborts the rest of a run.
"""

from __future__ import annotations

import html
import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.detector import Event
from src.utils import format_price_thb, format_thai_datetime

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10.0

MAX_RETRY_ATTEMPTS = 3
RETRY_WAIT_MULTIPLIER_SECONDS = 1
RETRY_WAIT_MAX_SECONDS = 8
SERVER_ERROR_THRESHOLD = 500
CLIENT_ERROR_THRESHOLD = 400

FIRST_ALERT_HEADER = "\U0001f6a8 MA6 มีสินค้าแล้ว! รีบสั่งเลย"
REPEAT_ALERT_HEADER = "\U0001f514 เตือนซ้ำ: MA6 ยังมีสินค้าอยู่"


class NotifierError(Exception):
    """Base class for all notifier failures. Never escapes the public API."""


class RetryableNotifierError(NotifierError):
    """A transient failure (timeout, connection error, 5xx) worth retrying."""


class NonRetryableNotifierError(NotifierError):
    """A permanent failure (4xx — bad token/chat id/message); retrying
    would not help."""


def format_event_message(event: Event, max_notify_count: int) -> str:
    """Render an in-stock alert as a Telegram HTML message."""
    product = event.product
    header = REPEAT_ALERT_HEADER if event.is_repeat else FIRST_ALERT_HEADER

    lines = [
        f"<b>{header}</b>",
        "",
        f"ชื่อสินค้า: {html.escape(product.name)}",
        f"ราคา: {format_price_thb(product.price)}",
        "สถานะ: มีสินค้า พร้อมกดใส่ตะกร้า",
        f"ลิงก์: {html.escape(str(product.product_url))}",
        f"เวลาที่ตรวจพบ: {format_thai_datetime(product.checked_at)}",
    ]

    if event.is_repeat:
        lines.append("")
        lines.append(
            f"(แจ้งเตือนครั้งที่ {event.notify_number} จากสูงสุด {max_notify_count} ครั้ง "
            "ต่อการกลับมามีของหนึ่งรอบ)"
        )

    return "\n".join(lines)


class TelegramNotifier:
    """Thin, defensive wrapper around the Telegram Bot API sendMessage call."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_notify_count: int = 3,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout_seconds = timeout_seconds
        self._max_notify_count = max_notify_count

    def send_event(self, event: Event) -> bool:
        """Format and send a single alert. Returns True on success, False on
        failure. Never raises."""
        text = format_event_message(event, self._max_notify_count)
        try:
            self._send_with_retry(text)
            return True
        except NotifierError as exc:
            logger.error(
                "Failed to send Telegram alert for product %s: %s", event.product.id, exc
            )
            return False
        except Exception:  # pragma: no cover - last-resort safety net
            logger.exception(
                "Unexpected error sending Telegram alert for product %s", event.product.id
            )
            return False

    def send_events(self, events: list[Event]) -> int:
        """Send every alert in order. Returns the number sent successfully;
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
