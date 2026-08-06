"""models.py — domain models for the stock monitor.

Pydantic models validate data at the boundary between untrusted, third-party
HTML and the rest of the program. Nothing downstream (detector, storage,
notifier) should have to re-check basic invariants like "is this a valid
URL" or "is the name non-empty" — that happens once, here.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, HttpUrl, field_validator


class Availability(str, Enum):
    """Normalized stock availability for a product, as read from its product
    detail page (PDP).

    IN_STOCK and OUT_OF_STOCK are only assigned when several independent
    signals on the page agree (see ``parser.detect_availability``).
    UNKNOWN means the page did not provide a confident answer — for example
    the markup changed, or the availability block was missing. UNKNOWN is a
    deliberate, honest "don't know" and never triggers a notification.
    """

    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    UNKNOWN = "UNKNOWN"


class Product(BaseModel):
    """A single product as observed on its product detail page."""

    id: str = Field(..., min_length=1, description="Site-native product id (SFCC data-pid)")
    name: str
    price: Decimal | None = Field(default=None, description="Price in THB; None if not shown")
    availability: Availability
    product_url: HttpUrl
    checked_at: datetime

    @field_validator("name")
    @classmethod
    def _name_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("product name must not be blank")
        return stripped

    @field_validator("price")
    @classmethod
    def _price_must_not_be_negative(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value < 0:
            raise ValueError("price must not be negative")
        return value


class ProductState(BaseModel):
    """Per-product persisted state, including notification bookkeeping.

    ``notify_count`` and ``last_notified_at`` implement the "limited repeat"
    alerting policy: when a product becomes available we alert immediately,
    then re-alert a bounded number of times while it stays available, then
    go quiet. Both counters reset as soon as the product stops being
    available, so a future restock alerts again from scratch.

    This state must be persisted because each run is a separate process
    (GitHub Actions runners are ephemeral) — without it, a long-running
    restock would either alert on every single check or never re-alert.
    """

    product: Product
    notify_count: int = Field(default=0, ge=0)
    last_notified_at: datetime | None = None


class StoredState(BaseModel):
    """The full on-disk state persisted between runs (data/state.json).

    Keyed by product id so lookups during change detection are O(1) and the
    JSON stays stable/diffable across runs (no incidental key reordering).
    """

    products: dict[str, ProductState] = Field(default_factory=dict)
    last_checked_at: datetime | None = None
