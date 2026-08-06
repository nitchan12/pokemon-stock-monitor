"""utils.py — small, reusable, side-effect-free formatting helpers, plus
application-wide logging setup.

Formatting helpers are kept separate from notifier.py so they are
trivially unit-testable and reusable anywhere else in the project that
needs to display a price or a timestamp to a human.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from rich.logging import RichHandler

BANGKOK_TZ = ZoneInfo("Asia/Bangkok")
UNSPECIFIED_PRICE_LABEL = "ไม่ระบุราคา"
THAI_DATETIME_FORMAT = "%d/%m/%Y %H:%M น."


def format_price_thb(price: Decimal | None) -> str:
    """Format a THB price for display, e.g. Decimal('1980') -> '฿1,980'.

    Returns a Thai "unspecified" label for None rather than an empty
    string, so notification messages never render a blank field.
    """
    if price is None:
        return UNSPECIFIED_PRICE_LABEL

    normalized = price.normalize()
    if normalized == normalized.to_integral_value():
        return f"฿{int(normalized):,}"
    return f"฿{normalized:,.2f}"


def format_thai_datetime(dt: datetime) -> str:
    """Format a timezone-aware datetime in Asia/Bangkok local time."""
    local_dt = dt.astimezone(BANGKOK_TZ)
    return local_dt.strftime(THAI_DATETIME_FORMAT)


def configure_logging(level: str = "INFO") -> None:
    """Configure application-wide logging through Rich.

    Called once from main.py's entrypoint. Every module in this project
    uses ``logging.getLogger(__name__)`` and never calls ``print`` directly,
    so this is the single place log formatting/output is controlled.
    """
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False, markup=True)],
        force=True,
    )
