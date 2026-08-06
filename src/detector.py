"""detector.py — compares freshly-scraped products against the last known
state and returns a list of change Events.

This module performs no I/O and no parsing: it is a pure function of
"new products" + "previous state" -> "events". Keeping it pure makes it
trivial to unit test exhaustively (see tests/test_detector.py) and keeps
notification logic (notifier.py) completely decoupled from diffing logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from src.models import Availability, Product

# Availability values that represent "a customer could plausibly buy this
# right now" — used to decide BACK_IN_STOCK / OUT_OF_STOCK transitions.
# UNKNOWN is deliberately excluded from both sides: a transition into/out of
# UNKNOWN is not a confident enough signal to notify on (see parser.py's
# documented UNKNOWN fallback).
PURCHASABLE_STATES = frozenset({Availability.IN_STOCK, Availability.PRE_ORDER})


class EventType(str, Enum):
    """Kinds of change this detector can report."""

    NEW_PRODUCT = "NEW_PRODUCT"
    PRICE_CHANGED = "PRICE_CHANGED"
    BACK_IN_STOCK = "BACK_IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    PRODUCT_REMOVED = "PRODUCT_REMOVED"


@dataclass(frozen=True, slots=True)
class Event:
    """A single detected change for a single product.

    ``product`` always holds the most recent known Product data: for
    PRODUCT_REMOVED this is the last-seen product (it no longer appears in
    the new scrape), for every other event type it is the freshly-scraped
    product.
    """

    event_type: EventType
    product: Product
    old_price: Decimal | None = None
    new_price: Decimal | None = None
    old_availability: Availability | None = None
    new_availability: Availability | None = None


def detect_changes(
    new_products: list[Product],
    previous_state: dict[str, Product],
) -> list[Event]:
    """Compare `new_products` (this run's scrape) against `previous_state`
    (last saved state, keyed by product id) and return every detected
    change as an :class:`Event`. Order is deterministic (sorted by product
    id, NEW_PRODUCT/PRICE_CHANGED/availability-changes before
    PRODUCT_REMOVED) so callers and tests get stable output.
    """
    events: list[Event] = []
    new_by_id = {product.id: product for product in new_products}

    for product_id in sorted(new_by_id):
        current = new_by_id[product_id]
        previous = previous_state.get(product_id)

        if previous is None:
            events.append(Event(event_type=EventType.NEW_PRODUCT, product=current))
            continue

        events.extend(_diff_existing_product(previous, current))

    removed_ids = sorted(set(previous_state) - set(new_by_id))
    for product_id in removed_ids:
        events.append(
            Event(event_type=EventType.PRODUCT_REMOVED, product=previous_state[product_id])
        )

    return events


def _diff_existing_product(previous: Product, current: Product) -> list[Event]:
    events: list[Event] = []

    if previous.price != current.price:
        events.append(
            Event(
                event_type=EventType.PRICE_CHANGED,
                product=current,
                old_price=previous.price,
                new_price=current.price,
            )
        )

    if previous.availability != current.availability:
        # Only a genuine OUT_OF_STOCK <-> purchasable transition fires an
        # event. UNKNOWN is excluded from both sides on purpose: e.g.
        # UNKNOWN -> IN_STOCK (first confident parse of a previously
        # ambiguous tile) or IN_STOCK -> UNKNOWN (parser temporarily lost
        # its signal) are low-confidence and would be noisy to notify on.
        became_purchasable = (
            previous.availability == Availability.OUT_OF_STOCK
            and current.availability in PURCHASABLE_STATES
        )
        became_out_of_stock = (
            previous.availability in PURCHASABLE_STATES
            and current.availability == Availability.OUT_OF_STOCK
        )

        if became_purchasable:
            events.append(
                Event(
                    event_type=EventType.BACK_IN_STOCK,
                    product=current,
                    old_availability=previous.availability,
                    new_availability=current.availability,
                )
            )
        elif became_out_of_stock:
            events.append(
                Event(
                    event_type=EventType.OUT_OF_STOCK,
                    product=current,
                    old_availability=previous.availability,
                    new_availability=current.availability,
                )
            )

    return events
