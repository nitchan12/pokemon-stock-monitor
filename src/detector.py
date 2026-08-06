"""detector.py — decides which products should trigger a Telegram alert.

Scope is deliberately narrow: this monitor exists to answer one question —
*"can I buy it right now?"* — so the only event it emits is
:data:`EventType.IN_STOCK`. Price changes, new SKUs, and disappearing
products are intentionally not reported.

Alerting policy ("limited repeat")
-----------------------------------
* A product that has just become available alerts immediately.
* While it *stays* available, it re-alerts at most
  ``settings.max_notify_count`` times in total, spaced at least
  ``settings.repeat_interval_minutes`` apart.
* Once the cap is reached, it goes quiet even though the product is still
  in stock.
* The counters reset the moment the product is no longer IN_STOCK, so a
  later restock alerts again from scratch.

:data:`Availability.UNKNOWN` never alerts — an ambiguous page must not send
the user chasing a product they cannot actually buy.

This module is a pure function of (products, previous state, now) so it is
exhaustively unit-testable without mocking time or the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from src.models import Availability, Product, ProductState

# Sentinel meaning "this product has never been alerted in the current
# in-stock streak", used for readability instead of a bare 0.
NO_ALERTS_SENT_YET = 0


class EventType(str, Enum):
    """The single kind of event this monitor reports."""

    IN_STOCK = "IN_STOCK"


@dataclass(frozen=True, slots=True)
class Event:
    """A product that is available and is due for an alert.

    ``is_repeat`` lets the notifier word a follow-up reminder differently
    from the first "it just came in stock" alert. ``notify_number`` is
    1-based (1 = first alert of this streak).
    """

    event_type: EventType
    product: Product
    notify_number: int
    is_repeat: bool


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Everything ``main`` needs after a detection pass.

    ``events`` are the alerts to send; ``new_state`` is the updated
    per-product bookkeeping to persist, already reflecting the alerts that
    are about to be sent.
    """

    events: list[Event]
    new_state: dict[str, ProductState]


def detect_in_stock(
    products: list[Product],
    previous_state: dict[str, ProductState],
    now: datetime,
    max_notify_count: int,
    repeat_interval_minutes: int,
) -> DetectionResult:
    """Return the alerts to send plus the state to persist.

    Products are processed in sorted-id order so output is deterministic.
    """
    events: list[Event] = []
    new_state: dict[str, ProductState] = {}

    for product in sorted(products, key=lambda p: p.id):
        previous = previous_state.get(product.id)

        if product.availability is not Availability.IN_STOCK:
            # Not buyable (OUT_OF_STOCK or UNKNOWN): record the observation
            # and reset the alert counters for the next restock.
            new_state[product.id] = ProductState(
                product=product,
                notify_count=NO_ALERTS_SENT_YET,
                last_notified_at=None,
            )
            continue

        already_sent = previous.notify_count if previous is not None else NO_ALERTS_SENT_YET
        last_sent_at = previous.last_notified_at if previous is not None else None

        if _should_alert(
            already_sent=already_sent,
            last_sent_at=last_sent_at,
            now=now,
            max_notify_count=max_notify_count,
            repeat_interval_minutes=repeat_interval_minutes,
        ):
            notify_number = already_sent + 1
            events.append(
                Event(
                    event_type=EventType.IN_STOCK,
                    product=product,
                    notify_number=notify_number,
                    is_repeat=already_sent > NO_ALERTS_SENT_YET,
                )
            )
            new_state[product.id] = ProductState(
                product=product,
                notify_count=notify_number,
                last_notified_at=now,
            )
        else:
            # Still in stock but throttled (cap reached, or too soon since
            # the last alert): carry the existing counters forward unchanged.
            new_state[product.id] = ProductState(
                product=product,
                notify_count=already_sent,
                last_notified_at=last_sent_at,
            )

    return DetectionResult(events=events, new_state=new_state)


def _should_alert(
    already_sent: int,
    last_sent_at: datetime | None,
    now: datetime,
    max_notify_count: int,
    repeat_interval_minutes: int,
) -> bool:
    if already_sent >= max_notify_count:
        return False
    if already_sent == NO_ALERTS_SENT_YET or last_sent_at is None:
        return True
    return now - last_sent_at >= timedelta(minutes=repeat_interval_minutes)
