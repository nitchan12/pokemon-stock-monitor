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
    """Normalized stock/availability status for a product.

    IN_STOCK / OUT_OF_STOCK / PRE_ORDER are signals inferred from the page.
    UNKNOWN means the page did not contain enough signal to classify the
    product confidently (e.g. missing price, no recognizable badge) — see
    ``parser._detect_availability`` for the exact combination of rules.
    UNKNOWN is a deliberate, honest "don't know", not a bug.
    """

    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRE_ORDER = "PRE_ORDER"
    UNKNOWN = "UNKNOWN"


class Product(BaseModel):
    """A single product as observed on the target search results page."""

    id: str = Field(..., min_length=1, description="Site-native product id (SFCC data-pid)")
    name: str
    price: Decimal | None = Field(default=None, description="Price in THB; None if not shown on the page")
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
