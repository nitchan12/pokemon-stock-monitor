"""Unit tests for src/detector.py.

detect_changes() is a pure function, so these tests build Product objects
directly rather than going through the parser/scraper.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.detector import EventType, detect_changes
from src.models import Availability, Product

CHECKED_AT = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def make_product(
    id: str = "1",
    name: str = "Test Product",
    price: str | None = "1000",
    availability: Availability = Availability.IN_STOCK,
) -> Product:
    return Product(
        id=id,
        name=name,
        price=Decimal(price) if price is not None else None,
        availability=availability,
        product_url=f"https://www.toysrus.co.th/th-th/product-{id}.html",
        checked_at=CHECKED_AT,
    )


class TestNewProduct:
    def test_product_not_in_previous_state_is_new_product(self):
        current = make_product(id="1")
        events = detect_changes([current], previous_state={})

        assert len(events) == 1
        assert events[0].event_type == EventType.NEW_PRODUCT
        assert events[0].product.id == "1"

    def test_no_events_when_nothing_changed(self):
        product = make_product(id="1", price="1000", availability=Availability.IN_STOCK)
        events = detect_changes([product], previous_state={"1": product})

        assert events == []


class TestPriceChanged:
    def test_price_increase_is_detected(self):
        previous = make_product(id="1", price="1000")
        current = make_product(id="1", price="1200")
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.PRICE_CHANGED
        assert events[0].old_price == Decimal("1000")
        assert events[0].new_price == Decimal("1200")

    def test_price_decrease_is_detected(self):
        previous = make_product(id="1", price="1200")
        current = make_product(id="1", price="1000")
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.PRICE_CHANGED

    def test_price_becoming_unknown_is_detected(self):
        previous = make_product(id="1", price="1000")
        current = make_product(id="1", price=None)
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.PRICE_CHANGED
        assert events[0].new_price is None


class TestStockTransitions:
    def test_out_of_stock_to_in_stock_is_back_in_stock(self):
        previous = make_product(id="1", availability=Availability.OUT_OF_STOCK)
        current = make_product(id="1", availability=Availability.IN_STOCK)
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.BACK_IN_STOCK

    def test_out_of_stock_to_pre_order_is_back_in_stock(self):
        previous = make_product(id="1", availability=Availability.OUT_OF_STOCK)
        current = make_product(id="1", availability=Availability.PRE_ORDER)
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.BACK_IN_STOCK

    def test_in_stock_to_out_of_stock_is_out_of_stock_event(self):
        previous = make_product(id="1", availability=Availability.IN_STOCK)
        current = make_product(id="1", availability=Availability.OUT_OF_STOCK)
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.OUT_OF_STOCK

    def test_pre_order_to_out_of_stock_is_out_of_stock_event(self):
        previous = make_product(id="1", availability=Availability.PRE_ORDER)
        current = make_product(id="1", availability=Availability.OUT_OF_STOCK)
        events = detect_changes([current], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.OUT_OF_STOCK

    def test_unknown_to_in_stock_does_not_fire_back_in_stock(self):
        previous = make_product(id="1", availability=Availability.UNKNOWN)
        current = make_product(id="1", availability=Availability.IN_STOCK)
        events = detect_changes([current], previous_state={"1": previous})

        assert events == []

    def test_in_stock_to_unknown_does_not_fire_out_of_stock(self):
        previous = make_product(id="1", availability=Availability.IN_STOCK)
        current = make_product(id="1", availability=Availability.UNKNOWN)
        events = detect_changes([current], previous_state={"1": previous})

        assert events == []


class TestProductRemoved:
    def test_product_missing_from_new_scrape_is_removed(self):
        previous = make_product(id="1")
        events = detect_changes([], previous_state={"1": previous})

        assert len(events) == 1
        assert events[0].event_type == EventType.PRODUCT_REMOVED
        assert events[0].product.id == "1"


class TestCombinedAndOrdering:
    def test_price_and_stock_change_together_produce_two_events(self):
        previous = make_product(id="1", price="1000", availability=Availability.OUT_OF_STOCK)
        current = make_product(id="1", price="1200", availability=Availability.IN_STOCK)
        events = detect_changes([current], previous_state={"1": previous})

        event_types = {e.event_type for e in events}
        assert event_types == {EventType.PRICE_CHANGED, EventType.BACK_IN_STOCK}

    def test_events_are_sorted_by_product_id_with_removals_last(self):
        previous = {
            "2": make_product(id="2", price="500"),
            "3": make_product(id="3", price="500"),
        }
        new_products = [
            make_product(id="1", price="500"),  # new
            make_product(id="2", price="600"),  # price changed
            # "3" is missing -> removed
        ]
        events = detect_changes(new_products, previous_state=previous)

        assert [e.event_type for e in events] == [
            EventType.NEW_PRODUCT,
            EventType.PRICE_CHANGED,
            EventType.PRODUCT_REMOVED,
        ]
        assert [e.product.id for e in events] == ["1", "2", "3"]

    def test_full_scan_produces_no_events_when_state_is_empty_and_scrape_is_empty(self):
        assert detect_changes([], previous_state={}) == []
