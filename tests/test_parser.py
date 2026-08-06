"""Unit tests for src/parser.py (product detail page parsing).

All fixtures live under tests/fixtures/ and are pure HTML — no network I/O
happens in this test module. The in-stock and out-of-stock fixtures contain
markup copied verbatim from the live site's raw server response.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from src.models import Availability
from src.parser import ParserError, detect_availability, parse_product_page

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MA6_URL = (
    "https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-58-"
    "30th-celebration-expected-september-2026-10161784.html"
)


def _load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup(_load_fixture(name), "lxml")


class TestDetectAvailability:
    def test_real_out_of_stock_page_is_out_of_stock(self):
        assert detect_availability(_soup("pdp_out_of_stock.html")) == Availability.OUT_OF_STOCK

    def test_real_in_stock_page_is_in_stock(self):
        assert detect_availability(_soup("pdp_in_stock.html")) == Availability.IN_STOCK

    def test_conflicting_signals_yield_unknown_not_a_guess(self):
        # data-available="true" but the out-of-stock button is rendered.
        # Must NOT report IN_STOCK — a false positive sends the user to a
        # page where they cannot actually buy anything.
        assert detect_availability(_soup("pdp_conflicting_signals.html")) == Availability.UNKNOWN

    def test_no_signals_at_all_yields_unknown(self):
        assert detect_availability(_soup("pdp_no_signals.html")) == Availability.UNKNOWN

    def test_data_ready_to_order_is_ignored(self):
        # Both real fixtures carry data-ready-to-order="true", yet they must
        # resolve to opposite verdicts. This proves the parser is not keying
        # off that attribute.
        oos = _soup("pdp_out_of_stock.html")
        in_stock = _soup("pdp_in_stock.html")
        assert 'data-ready-to-order="true"' in str(oos)
        assert 'data-ready-to-order="true"' in str(in_stock)
        assert detect_availability(oos) == Availability.OUT_OF_STOCK
        assert detect_availability(in_stock) == Availability.IN_STOCK

    def test_populated_cart_url_does_not_imply_in_stock(self):
        # Regression for a bug that silently disabled the whole monitor:
        # input.add-to-cart-url carries a value in BOTH states in the raw
        # server HTML (it only looked empty in the post-JavaScript DOM).
        # Treating it as an in-stock signal deadlocked every vote at 2-vs-1
        # and reported UNKNOWN forever, so no alert could ever fire.
        html = _load_fixture("pdp_out_of_stock.html")
        assert 'class="add-to-cart-url" value="' in html  # value really is set
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.OUT_OF_STOCK

    def test_json_ld_out_of_stock_is_counted(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {"@type":"Product","offers":{"availability":"http://schema.org/OutOfStock"}}
          </script>
          <div class="availability product-availability" data-available="false"></div>
        </body></html>
        """
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.OUT_OF_STOCK

    def test_json_ld_in_stock_is_counted(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {"@type":"Product","offers":{"availability":"http://schema.org/InStock"}}
          </script>
          <div class="availability product-availability" data-available="true"></div>
        </body></html>
        """
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.IN_STOCK

    def test_malformed_json_ld_abstains_instead_of_breaking(self):
        html = """
        <html><body>
          <script type="application/ld+json">{ this is not valid json </script>
          <div class="availability product-availability" data-available="false"></div>
          <button class="back-in-store"></button>
        </body></html>
        """
        # The two good signals still carry the verdict.
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.OUT_OF_STOCK

    def test_unrecognized_json_ld_availability_abstains(self):
        html = """
        <html><body>
          <script type="application/ld+json">
          {"@type":"Product","offers":{"availability":"http://schema.org/PreOrder"}}
          </script>
          <div class="availability product-availability" data-available="false"></div>
        </body></html>
        """
        # Only one usable signal remains -> not enough to decide.
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.UNKNOWN

    def test_single_signal_alone_is_not_enough(self):
        # Only data-available present, no buttons and no cart-url input.
        html = """
        <html><body>
          <div class="availability product-availability" data-available="true"></div>
        </body></html>
        """
        assert detect_availability(BeautifulSoup(html, "lxml")) == Availability.UNKNOWN


class TestParseProductPage:
    def test_extracts_name_price_id_and_url(self):
        product = parse_product_page(_load_fixture("pdp_out_of_stock.html"), MA6_URL)

        assert product.id == "10161784"
        assert "MA6" in product.name
        assert product.price == Decimal("1980")
        assert str(product.product_url) == MA6_URL
        assert product.availability == Availability.OUT_OF_STOCK

    def test_in_stock_page_parses_as_in_stock(self):
        product = parse_product_page(_load_fixture("pdp_in_stock.html"), MA6_URL)
        assert product.availability == Availability.IN_STOCK

    def test_checked_at_is_timezone_aware(self):
        product = parse_product_page(_load_fixture("pdp_out_of_stock.html"), MA6_URL)
        assert product.checked_at.tzinfo is not None

    def test_non_product_page_raises_parser_error(self):
        with pytest.raises(ParserError):
            parse_product_page(_load_fixture("pdp_malformed.html"), MA6_URL)

    def test_product_id_falls_back_to_url_when_data_pid_missing(self):
        html = """
        <html><body>
          <h1 class="product-name">Some Product</h1>
        </body></html>
        """
        product = parse_product_page(html, MA6_URL)
        assert product.id == "10161784"  # recovered from the URL slug

    def test_missing_price_is_none_not_an_error(self):
        html = """
        <html><body>
          <div data-pid="10161784"><h1 class="product-name">Some Product</h1></div>
        </body></html>
        """
        product = parse_product_page(html, MA6_URL)
        assert product.price is None
        assert product.availability == Availability.UNKNOWN

    def test_carousel_product_id_does_not_leak_into_the_result(self):
        # Regression: a PDP renders related-product carousels whose tiles
        # carry their own data-pid. A page-wide lookup returned the
        # carousel's id (10132862) instead of the real product (10161784),
        # which would have collapsed all three monitored pages onto one
        # state key and corrupted repeat-alert throttling.
        product = parse_product_page(_load_fixture("pdp_with_carousel.html"), MA6_URL)

        assert product.id == "10161784"
        assert product.id != "10132862"

    def test_carousel_price_does_not_leak_into_the_result(self):
        product = parse_product_page(_load_fixture("pdp_with_carousel.html"), MA6_URL)

        assert product.price == Decimal("1980")  # not the carousel's ฿499

    def test_carousel_does_not_affect_availability(self):
        product = parse_product_page(_load_fixture("pdp_with_carousel.html"), MA6_URL)
        assert product.availability == Availability.OUT_OF_STOCK

    def test_product_id_comes_from_url_even_if_page_pid_differs(self):
        # The URL is what we actually requested, so it wins over any id the
        # page happens to render.
        html = """
        <html><body>
          <div class="product-detail" data-pid="99999999">
            <h1 class="product-name">Some Product</h1>
          </div>
        </body></html>
        """
        assert parse_product_page(html, MA6_URL).id == "10161784"

    def test_falls_back_to_scoped_pid_when_url_has_no_id(self):
        html = """
        <html><body>
          <div class="product-detail" data-pid="10161784">
            <h1 class="product-name">Some Product</h1>
          </div>
        </body></html>
        """
        assert parse_product_page(html, "https://example.test/no-id-here").id == "10161784"

    def test_unparseable_price_falls_back_to_none(self):
        html = """
        <html><body>
          <div data-pid="10161784">
            <h1 class="product-name">Some Product</h1>
            <div class="prices"><span class="sales"><span class="value">12.34.56</span></span></div>
          </div>
        </body></html>
        """
        product = parse_product_page(html, MA6_URL)
        assert product.price is None
