"""parser.py — extracts a Product from a product detail page (PDP).

This module performs **no network I/O**; it only parses HTML strings handed
to it by ``scraper.py``.

All selectors below were captured from the live toysrus.co.th PDP markup
(Salesforce B2C Commerce / SFCC storefront) by inspecting the **raw server
response** — i.e. the HTML before any JavaScript runs — for a product in
each availability state. They are verified, not guessed.

Availability signals, measured against the RAW server HTML of a real
out-of-stock page and a real in-stock page:

===============================  ==============  ==============  =========
signal                           out of stock    in stock        usable?
===============================  ==============  ==============  =========
JSON-LD ``offers.availability``  ``OutOfStock``  ``InStock``     yes
``data-available`` attribute     ``"false"``     ``"true"``      yes
action button rendered           back-in-store   add-to-cart     yes
``data-ready-to-order``          ``"true"``      ``"true"``      NO
``add-to-cart-url`` has value    set             set             NO
===============================  ==============  ==============  =========

The last two rows are recorded deliberately: both look like plausible
availability signals and neither actually discriminates.

``add-to-cart-url`` in particular caused a real bug. Inspecting the
*live DOM* in a browser showed it empty on an out-of-stock page, so it was
adopted as a signal — but JavaScript had cleared it after load. The raw
HTML that httpx receives has it populated in both states, so it voted
"in stock" on every page, permanently deadlocking the vote at 2-vs-1 and
reporting UNKNOWN forever. Every signal here is now verified against raw
HTML rather than the rendered DOM.
"""

from __future__ import annotations

import json
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
JSON_LD_SELECTOR = 'script[type="application/ld+json"]'
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
    action_label = _extract_action_label(soup)

    return Product(
        id=product_id,
        name=name,
        price=price,
        availability=availability,
        product_url=HttpUrl(source_url),
        checked_at=datetime.now(timezone.utc),
        action_label=action_label,
    )


def _extract_action_label(soup: BeautifulSoup) -> str | None:
    """Return the text on the orderable action button, if one is rendered.

    The storefront uses the SAME ``add-to-cart`` markup for a normal
    purchase ("เพิ่มสินค้าไปยังรถเข็น") and for an open pre-order
    ("สั่งของล่วงหน้า"); only the label differs. Availability detection
    treats them identically — both mean "you can order this now" — but the
    label is worth surfacing so the buyer knows which one they are getting.
    """
    button = soup.select_one(ADD_TO_CART_BUTTON_SELECTOR)
    if button is None:
        return None
    label = button.get_text(strip=True)
    return label or None


def detect_availability(soup: BeautifulSoup) -> Availability:
    """Decide availability by combining three independent page signals.

    Signals (each votes IN_STOCK / OUT_OF_STOCK / abstains):
      1. schema.org ``offers.availability`` in the page's JSON-LD block.
      2. ``data-available`` on the product-availability block.
      3. Which action button is rendered: ``add-to-cart`` vs ``back-in-store``.

    A verdict requires at least :data:`MIN_AGREEING_SIGNALS` votes on one
    side and none on the other. Any disagreement, or too few signals,
    yields UNKNOWN — deliberately, so an ambiguous page never fires an alert.
    """
    in_stock_votes = 0
    out_of_stock_votes = 0

    json_ld_verdict = _read_json_ld_availability(soup)
    if json_ld_verdict is Availability.IN_STOCK:
        in_stock_votes += 1
    elif json_ld_verdict is Availability.OUT_OF_STOCK:
        out_of_stock_votes += 1

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


def _read_json_ld_availability(soup: BeautifulSoup) -> Availability | None:
    """Read schema.org ``offers.availability`` from the page's JSON-LD.

    Returns None (abstain from voting) if the block is absent, unparseable,
    or carries an availability value we don't recognize — a malformed
    third-party blob must never be able to swing the verdict.
    """
    for script in soup.select(JSON_LD_SELECTOR):
        raw = script.string or script.get_text()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Skipping unparseable JSON-LD block")
            continue

        for candidate in data if isinstance(data, list) else [data]:
            verdict = _availability_from_json_ld_node(candidate)
            if verdict is not None:
                return verdict
    return None


def _availability_from_json_ld_node(node: object) -> Availability | None:
    if not isinstance(node, dict):
        return None

    offers = node.get("offers")
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        raw = offer.get("availability")
        if not isinstance(raw, str):
            continue
        value = raw.rsplit("/", 1)[-1].strip().lower()
        if value == "instock":
            return Availability.IN_STOCK
        if value in {"outofstock", "soldout", "discontinued"}:
            return Availability.OUT_OF_STOCK
        logger.debug("Unrecognized JSON-LD availability value %r; abstaining", raw)
    return None


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
