"""parser.py — extracts a Product from a product detail page (PDP).

This module performs **no network I/O**; it only parses HTML strings handed
to it by ``scraper.py``.

All selectors below were captured from the live toysrus.co.th PDP markup
(Salesforce B2C Commerce / SFCC storefront) by inspecting the **raw server
response** — i.e. the HTML before any JavaScript runs — for a product in
each availability state. They are verified, not guessed.

Availability markup, verified in both states
--------------------------------------------
OUT OF STOCK (all three MA6 pages at time of writing)::

    <input type="hidden" class="add-to-cart-url">          <-- no value
    <button class="btn ... btn-secondary back-in-store" data-back-in-stock>
      ไม่มี <span class="d-block">แจ้งเตือนฉันเมื่อมีของกลับมาพร้อมจำหน่าย</span>
    </button>
    <div class="availability product-availability"
         data-ready-to-order="true" data-available="false" ...>

IN STOCK (verified against a different, in-stock product on the same site)::

    <input type="hidden" class="add-to-cart-url" value=".../Cart-AddProduct">
    <button class="btn ... btn-info add-to-cart" data-pid="..." data-add-to-cart>
      เพิ่มสินค้าไปยังรถเข็น
    </button>
    <div class="availability product-availability"
         data-ready-to-order="true" data-available="true" ...>

IMPORTANT: ``data-ready-to-order`` is ``"true"`` in BOTH states and is
therefore useless for deciding availability — it must not be used as a
signal. Only ``data-available`` differs, and it is corroborated by which
button is rendered and whether the add-to-cart URL input carries a value.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from bs4 import BeautifulSoup, Tag
from pydantic import HttpUrl

from src.models import Availability, Product

logger = logging.getLogger(__name__)

# Everything is looked up inside this container rather than page-wide. A PDP
# also renders "trending now" / "related products" carousels whose tiles
# carry their OWN data-pid — a page-wide lookup picks up a carousel item
# belonging to a completely different product. Verified on the live page:
# the first page-wide [data-pid] is a carousel item, while
# .product-detail[data-pid] is the real product.
PRODUCT_ROOT_SELECTOR = ".product-detail"

AVAILABILITY_SELECTOR = ".availability.product-availability"
ADD_TO_CART_BUTTON_SELECTOR = "button.add-to-cart"
BACK_IN_STORE_BUTTON_SELECTOR = "button.back-in-store"
ADD_TO_CART_URL_INPUT_SELECTOR = "input.add-to-cart-url"
PRODUCT_NAME_SELECTOR = "h1.product-name"
PRODUCT_PRICE_SELECTOR = ".prices .sales .value"
PRODUCT_ID_ATTRIBUTE = "data-pid"

# Product id is recoverable from the URL slug, which always ends in
# "-<numeric id>.html" on this storefront. This is the most trustworthy
# source because it is the URL *we* requested and cannot be contaminated by
# anything rendered on the page.
PRODUCT_ID_URL_PATTERN = re.compile(r"-(\d+)\.html")
PRICE_CLEAN_PATTERN = re.compile(r"[^\d.]")

# At least this many of the three availability signals must agree before we
# commit to IN_STOCK or OUT_OF_STOCK. Below this, we report UNKNOWN rather
# than guess — a wrong "in stock" alert sends the user to a dead page, and a
# wrong "out of stock" silently hides a real restock.
MIN_AGREEING_SIGNALS = 2


class ParserError(Exception):
    """Raised when the PDP does not match the expected structure at all.

    Callers should treat this as an HTML_CHANGED condition and stop, rather
    than silently reporting a product as unavailable.
    """


def parse_product_page(html: str, source_url: str) -> Product:
    """Parse a single product detail page into a :class:`Product`.

    Raises:
        ParserError: if the page has no recognizable product name, which
            means the markup changed enough that nothing can be trusted.
    """
    soup = BeautifulSoup(html, "lxml")
    root = _find_product_root(soup)

    name = _extract_text(root, PRODUCT_NAME_SELECTOR)
    if not name:
        raise ParserError(
            f"Could not find a product name ({PRODUCT_NAME_SELECTOR}) on {source_url}; "
            "the page structure may have changed."
        )

    product_id = _extract_product_id(root, source_url)
    if product_id is None:
        raise ParserError(f"Could not determine a product id for {source_url}")

    availability = detect_availability(soup)
    price = _extract_price(root)

    return Product(
        id=product_id,
        name=name,
        price=price,
        availability=availability,
        product_url=HttpUrl(source_url),
        checked_at=datetime.now(timezone.utc),
    )


def detect_availability(soup: BeautifulSoup) -> Availability:
    """Decide availability by combining three independent page signals.

    Signals (each votes IN_STOCK / OUT_OF_STOCK / abstains):
      1. ``data-available`` on the product-availability block.
      2. Which action button is rendered: ``add-to-cart`` vs ``back-in-store``.
      3. Whether the hidden ``add-to-cart-url`` input carries a value.

    A verdict requires at least :data:`MIN_AGREEING_SIGNALS` votes on one
    side and none on the other. Any disagreement, or too few signals,
    yields UNKNOWN — deliberately, so an ambiguous page never fires an alert.
    """
    in_stock_votes = 0
    out_of_stock_votes = 0

    availability_block = soup.select_one(AVAILABILITY_SELECTOR)
    if availability_block is not None:
        raw = availability_block.get("data-available")
        value = str(raw).strip().lower() if raw is not None else ""
        if value == "true":
            in_stock_votes += 1
        elif value == "false":
            out_of_stock_votes += 1

    has_add_to_cart = soup.select_one(ADD_TO_CART_BUTTON_SELECTOR) is not None
    has_back_in_store = soup.select_one(BACK_IN_STORE_BUTTON_SELECTOR) is not None
    if has_add_to_cart and not has_back_in_store:
        in_stock_votes += 1
    elif has_back_in_store and not has_add_to_cart:
        out_of_stock_votes += 1

    cart_url_input = soup.select_one(ADD_TO_CART_URL_INPUT_SELECTOR)
    if cart_url_input is not None:
        cart_url = cart_url_input.get("value")
        if cart_url is not None and str(cart_url).strip():
            in_stock_votes += 1
        else:
            out_of_stock_votes += 1

    if in_stock_votes >= MIN_AGREEING_SIGNALS and out_of_stock_votes == 0:
        return Availability.IN_STOCK
    if out_of_stock_votes >= MIN_AGREEING_SIGNALS and in_stock_votes == 0:
        return Availability.OUT_OF_STOCK

    logger.warning(
        "Availability signals were inconclusive (in_stock=%d, out_of_stock=%d); "
        "reporting UNKNOWN rather than guessing.",
        in_stock_votes,
        out_of_stock_votes,
    )
    return Availability.UNKNOWN


def _find_product_root(soup: BeautifulSoup) -> BeautifulSoup | Tag:
    """Return the container holding *this* product, not a carousel item.

    Falls back to the whole document if the container is missing, so a
    layout change degrades to the old page-wide behavior rather than
    failing outright.
    """
    root = soup.select_one(PRODUCT_ROOT_SELECTOR)
    if root is None:
        logger.warning(
            "Product root %s not found; falling back to a page-wide search, "
            "which may pick up related-product carousel data.",
            PRODUCT_ROOT_SELECTOR,
        )
        return soup
    return root


def _extract_product_id(root: BeautifulSoup | Tag, source_url: str) -> str | None:
    """Derive the product id, preferring the URL we requested.

    The URL slug is authoritative: it identifies the exact page fetched and
    cannot be affected by anything the page renders. The scoped
    ``data-pid`` is only a fallback for URLs that don't match the expected
    slug shape.
    """
    match = PRODUCT_ID_URL_PATTERN.search(source_url)
    if match:
        return match.group(1)

    pid = root.get(PRODUCT_ID_ATTRIBUTE) if isinstance(root, Tag) else None
    if pid and str(pid).strip():
        return str(pid).strip()

    element = root.select_one(f"[{PRODUCT_ID_ATTRIBUTE}]")
    if element is not None:
        nested_pid = element.get(PRODUCT_ID_ATTRIBUTE)
        if nested_pid and str(nested_pid).strip():
            return str(nested_pid).strip()
    return None


def _extract_text(soup: BeautifulSoup | Tag, selector: str) -> str | None:
    element = soup.select_one(selector)
    if element is None:
        return None
    text = element.get_text(strip=True)
    return text or None


def _extract_price(root: BeautifulSoup | Tag) -> Decimal | None:
    raw = _extract_text(root, PRODUCT_PRICE_SELECTOR)
    if raw is None:
        return None

    cleaned = PRICE_CLEAN_PATTERN.sub("", raw)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Could not parse price text %r", raw)
        return None
