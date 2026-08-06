"""Unit tests for src/detector.py.

`detect_in_stock` is a pure function that takes `now` as a parameter, so
the limited-repeat timing policy is tested by passing explicit timestamps
rather than by mocking the clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.detector import EventType, detect_in_stock
from src.models import Availability, Product, ProductState

T0 = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)
MAX_NOTIFY = 3
REPEAT_MINUTES = 10


def make_product(
    id: str = "1",
    availability: Availability = Availability.IN_STOCK,
    price: str | None = "1980",
) -> Product:
    return Product(
        id=id,
        name="Pokemon TCG MA6",
        price=Decimal(price) if price is not None else None,
        availability=availability,
        product_url=f"https://www.toysrus.co.th/th-th/product-{id}.html",
        checked_at=T0,
    )


def run(products, previous_state=None, now=T0):
    return detect_in_stock(
        products=products,
        previous_state=previous_state or {},
        now=now,
        max_notify_count=MAX_NOTIFY,
        repeat_interval_minutes=REPEAT_MINUTES,
    )


class TestOnlyInStockAlerts:
    def test_out_of_stock_produces_no_event(self):
        result = run([make_product(availability=Availability.OUT_OF_STOCK)])
        assert result.events == []

    def test_unknown_produces_no_event(self):
        result = run([make_product(availability=Availability.UNKNOWN)])
        assert result.events == []

    def test_in_stock_produces_an_event(self):
        result = run([make_product(availability=Availability.IN_STOCK)])

        assert len(result.events) == 1
        assert result.events[0].event_type == EventType.IN_STOCK
        assert result.events[0].notify_number == 1
        assert result.events[0].is_repeat is False

    def test_price_change_alone_produces_no_event(self):
        previous = {
            "1": ProductState(
                product=make_product(price="1980", availability=Availability.OUT_OF_STOCK)
            )
        }
        result = run(
            [make_product(price="2500", availability=Availability.OUT_OF_STOCK)],
            previous_state=previous,
        )
        assert result.events == []


class TestLimitedRepeatPolicy:
    def test_second_check_too_soon_is_throttled(self):
        first = run([make_product()])
        second = run([make_product()], previous_state=first.new_state, now=T0 + timedelta(minutes=5))

        assert len(first.events) == 1
        assert second.events == []

    def test_repeat_fires_once_interval_elapsed(self):
        first = run([make_product()])
        second = run(
            [make_product()], previous_state=first.new_state, now=T0 + timedelta(minutes=10)
        )

        assert len(second.events) == 1
        assert second.events[0].notify_number == 2
        assert second.events[0].is_repeat is True

    def test_stops_after_max_notify_count(self):
        state: dict[str, ProductState] = {}
        sent = []
        # Simulate 60 minutes of 5-minute checks while the product stays in stock.
        for minute in range(0, 61, 5):
            result = run([make_product()], previous_state=state, now=T0 + timedelta(minutes=minute))
            state = result.new_state
            sent.extend(result.events)

        assert len(sent) == MAX_NOTIFY
        assert [e.notify_number for e in sent] == [1, 2, 3]

    def test_counters_reset_when_product_goes_out_of_stock(self):
        # Exhaust the alert budget.
        state: dict[str, ProductState] = {}
        for minute in range(0, 41, 10):
            state = run(
                [make_product()], previous_state=state, now=T0 + timedelta(minutes=minute)
            ).new_state
        assert state["1"].notify_count == MAX_NOTIFY

        # Product sells out -> counters must reset.
        state = run(
            [make_product(availability=Availability.OUT_OF_STOCK)],
            previous_state=state,
            now=T0 + timedelta(minutes=50),
        ).new_state
        assert state["1"].notify_count == 0
        assert state["1"].last_notified_at is None

        # It restocks later -> must alert again immediately.
        result = run([make_product()], previous_state=state, now=T0 + timedelta(minutes=60))
        assert len(result.events) == 1
        assert result.events[0].notify_number == 1
        assert result.events[0].is_repeat is False

    def test_unknown_also_resets_counters(self):
        first = run([make_product()])
        after_unknown = run(
            [make_product(availability=Availability.UNKNOWN)],
            previous_state=first.new_state,
            now=T0 + timedelta(minutes=5),
        )
        assert after_unknown.new_state["1"].notify_count == 0


class TestStateBookkeeping:
    def test_state_records_every_product_regardless_of_availability(self):
        result = run(
            [
                make_product(id="1", availability=Availability.IN_STOCK),
                make_product(id="2", availability=Availability.OUT_OF_STOCK),
                make_product(id="3", availability=Availability.UNKNOWN),
            ]
        )
        assert set(result.new_state) == {"1", "2", "3"}

    def test_last_notified_at_is_recorded_on_alert(self):
        result = run([make_product()])
        assert result.new_state["1"].last_notified_at == T0

    def test_throttled_check_preserves_original_last_notified_at(self):
        first = run([make_product()])
        second = run([make_product()], previous_state=first.new_state, now=T0 + timedelta(minutes=5))
        assert second.new_state["1"].last_notified_at == T0

    def test_events_are_sorted_by_product_id(self):
        result = run(
            [make_product(id="3"), make_product(id="1"), make_product(id="2")]
        )
        assert [e.product.id for e in result.events] == ["1", "2", "3"]

    def test_empty_input_produces_no_events_and_empty_state(self):
        result = run([])
        assert result.events == []
        assert result.new_state == {}


class TestMultipleProducts:
    def test_only_the_in_stock_product_alerts(self):
        result = run(
            [
                make_product(id="1", availability=Availability.OUT_OF_STOCK),
                make_product(id="2", availability=Availability.IN_STOCK),
                make_product(id="3", availability=Availability.OUT_OF_STOCK),
            ]
        )
        assert [e.product.id for e in result.events] == ["2"]

    def test_products_are_throttled_independently(self):
        # Product 1 already alerted once at T0; product 2 is newly in stock.
        previous = {
            "1": ProductState(product=make_product(id="1"), notify_count=1, last_notified_at=T0)
        }
        result = run(
            [make_product(id="1"), make_product(id="2")],
            previous_state=previous,
            now=T0 + timedelta(minutes=5),
        )
        # 1 is throttled (only 5 min elapsed), 2 alerts immediately.
        assert [e.product.id for e in result.events] == ["2"]
