"""Unit tests for src/parser.py.

All fixtures live under tests/fixtures/ and are pure HTML — no network I/O
happens in this test module.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.models import Availability
from src.parser import ParserError, parse_search_results

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


class TestParseSearchResults:
    def test_extracts_all_products_from_search_grid(self):
        html = _load_fixture("search_results.html")
        products = parse_search_results(html)

        assert len(products) == 3
        ids = {p.id for p in products}
        assert ids == {"10161784", "10161786", "10161785"}

    def test_ignores_unrelated_data_pid_tiles_outside_search_results(self):
        html = _load_fixture("search_results.html")
        products = parse_search_results(html)

        # The "trending now" tile (pid 10132862) lives outside .search-results
        # and must never appear in the parsed output.
        assert "10132862" not in {p.id for p in products}

    def test_extracts_name_price_and_url_correctly(self):
        html = _load_fixture("search_results.html")
        products = {p.id: p for p in parse_search_results(html)}

        product = products["10161784"]
        assert "MA6" in product.name
        assert product.price == Decimal("1980")
        assert str(product.product_url) == (
            "https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-58-"
            "30th-celebration-expected-september-2026-10161784.html"
        )
        assert product.availability == Availability.PRE_ORDER

    def test_prices_are_parsed_for_every_product(self):
        html = _load_fixture("search_results.html")
        products = {p.id: p for p in parse_search_results(html)}

        assert products["10161786"].price == Decimal("6500")
        assert products["10161785"].price == Decimal("555")

    def test_zero_results_returns_empty_list(self):
        html = _load_fixture("no_results.html")
        assert parse_search_results(html) == []

    def test_unrecognized_page_structure_raises_parser_error(self):
        html = _load_fixture("malformed.html")
        with pytest.raises(ParserError):
            parse_search_results(html)

    def test_missing_price_falls_back_to_unknown_availability(self):
        html = _load_fixture("missing_price.html")
        products = parse_search_results(html)

        assert len(products) == 1
        assert products[0].price is None
        assert products[0].availability == Availability.UNKNOWN

    def test_out_of_stock_signals_are_combined_not_single_signal(self):
        html = _load_fixture("out_of_stock.html")
        products = parse_search_results(html)

        assert len(products) == 1
        product = products[0]
        # Despite a price being present (a positive signal), the CSS class
        # AND badge text (negative signals) must win — proving multiple
        # signals are combined rather than any single one deciding alone.
        assert product.availability == Availability.OUT_OF_STOCK

    def test_checked_at_is_populated_and_timezone_aware(self):
        html = _load_fixture("search_results.html")
        products = parse_search_results(html)

        for product in products:
            assert product.checked_at.tzinfo is not None
