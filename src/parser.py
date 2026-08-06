"""parser.py — extracts Product objects from search-results HTML.

This module performs **no network I/O**; it only parses HTML strings handed
to it by ``scraper.py``. Selectors below were captured directly from the
live toysrus.co.th search-results page (Salesforce B2C Commerce / SFCC
storefront) via live DOM inspection, not guessed:

    .search-results .product-grid [data-pid]        -> one product tile
    a.card-link span                                  -> product name
    a.card-link[href]                                  -> product URL
    .price .value                                      -> price text ("฿1,980")
    .product-overlay .label                             -> promo badge text
    .no-results                                        -> "zero results" marker

KNOWN LIMITATION — availability detection:
    The search-results grid does not expose a dedicated "out of stock"
    badge on any product observed at verification time (all live listings
    were tagged as pre-order, new, best-seller, or clearance — see
    README.md "Known Limitations"). The OUT_OF_STOCK branch below is
    therefore best-effort: a CSS-class check plus a Thai/English phrase
    list, not a verified live example. If a genuinely out-of-stock product
    is later observed with a different signal, update
    ``OUT_OF_STOCK_PHRASES`` / ``_detect_availability`` accordingly rather
    than assuming this list is complete. When no signal matches, the
    product is classified UNKNOWN rather than guessed as IN_STOCK.
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

BASE_URL = "https://www.toysrus.co.th"

# Container selectors, in priority order. Scoping to `.search-results` is
# required: the page also renders unrelated `[data-pid]` tiles elsewhere
# (e.g. a "trending now" header carousel) that must not be treated as
# search results.
SEARCH_RESULTS_CONTAINER_SELECTORS = (".search-results .product-grid", ".search-results")
NO_RESULTS_SELECTOR = ".no-results"
PRODUCT_TILE_SELECTOR = "[data-pid]"
PRODUCT_NAME_SELECTOR = "a.card-link span"
PRODUCT_PRICE_SELECTOR = ".price .value"
PRODUCT_LINK_SELECTOR = "a.card-link"
PRODUCT_BADGE_SELECTOR = ".product-overlay .label"

# Negative (out-of-stock) and neutral (pre-order) phrase lists used as one
# of several signals in `_detect_availability` — never the sole signal.
OUT_OF_STOCK_PHRASES = (
    "สินค้าหมด",
    "หมดสต็อก",
    "หมดสต๊อก",
    "สินค้าหมดชั่วคราว",
    "out of stock",
    "sold out",
)
PRE_ORDER_PHRASES = ("พรีออเดอร์", "pre-order", "pre order")
OUT_OF_STOCK_CLASS_TOKENS = ("sold-out", "soldout", "out-of-stock", "unavailable")

PRICE_CLEAN_PATTERN = re.compile(r"[^\d.]")


class ParserError(Exception):
    """Raised when the page structure doesn't match any known shape (not
    even the "no results" marker). Callers should treat this as an
    HTML_CHANGED condition and stop rather than silently reporting zero
    products — see project requirements in README.md."""


def parse_search_results(html: str, source_url: str = BASE_URL) -> list[Product]:
    """Parse a toysrus.co.th search-results page into a list of Products.

    Returns an empty list only when the page legitimately shows a
    "no results" state. Raises :class:`ParserError` if neither a product
    grid nor a no-results marker can be found, since that means the page
    structure likely changed and guessing would be unsafe.
    """
    soup = BeautifulSoup(html, "lxml")

    container = _find_results_container(soup)
    has_no_results_marker = soup.select_one(NO_RESULTS_SELECTOR) is not None

    if container is None:
        if has_no_results_marker:
            logger.info("Search returned zero results (no-results marker present).")
            return []
        raise ParserError(
            "Could not locate the search-results container or a no-results "
            "marker; the page structure may have changed."
        )

    tiles = container.select(PRODUCT_TILE_SELECTOR)
    if not tiles:
        if has_no_results_marker:
            return []
        raise ParserError(
            "search-results container found but contains no product tiles "
            "and no no-results marker; the page structure may have changed."
        )

    checked_at = datetime.now(timezone.utc)
    products: list[Product] = []
    for tile in tiles:
        product = _parse_tile(tile, source_url, checked_at)
        if product is not None:
            products.append(product)
    return products


def _find_results_container(soup: BeautifulSoup) -> Tag | None:
    for selector in SEARCH_RESULTS_CONTAINER_SELECTORS:
        container = soup.select_one(selector)
        if container is not None:
            return container
    return None


def _parse_tile(tile: Tag, source_url: str, checked_at: datetime) -> Product | None:
    pid = tile.get("data-pid")
    if not pid:
        logger.warning("Skipping a product tile with no data-pid attribute.")
        return None

    name = _extract_text(tile, PRODUCT_NAME_SELECTOR)
    if not name:
        logger.warning("Skipping product %s: could not extract a name.", pid)
        return None

    url = _extract_url(tile)
    if url is None:
        logger.warning("Skipping product %s: could not extract a product URL.", pid)
        return None

    price = _extract_price(tile)
    badge_text = _extract_text(tile, PRODUCT_BADGE_SELECTOR)
    availability = _detect_availability(tile, price, badge_text)

    try:
        return Product(
            id=str(pid),
            name=name,
            price=price,
            availability=availability,
            product_url=HttpUrl(url),
            checked_at=checked_at,
        )
    except Exception:
        logger.exception("Failed to build Product model for pid=%s (source=%s)", pid, source_url)
        return None


def _extract_text(tile: Tag, selector: str) -> str | None:
    el = tile.select_one(selector)
    if el is None:
        return None
    text = el.get_text(strip=True)
    return text or None


def _extract_url(tile: Tag) -> str | None:
    link = tile.select_one(PRODUCT_LINK_SELECTOR)
    if link is None or not link.get("href"):
        return None
    href = str(link["href"]).split("?")[0]
    if href.startswith("http"):
        return href
    return f"{BASE_URL.rstrip('/')}/{href.lstrip('/')}"


def _extract_price(tile: Tag) -> Decimal | None:
    el = tile.select_one(PRODUCT_PRICE_SELECTOR)
    if el is None:
        return None
    raw = el.get_text(strip=True)
    cleaned = PRICE_CLEAN_PATTERN.sub("", raw)
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        logger.warning("Could not parse price text %r", raw)
        return None


def _detect_availability(
    tile: Tag, price: Decimal | None, badge_text: str | None
) -> Availability:
    """Combine multiple independent signals — never a single string
    comparison — to classify availability, per project requirements:

      1. CSS class on the tile itself (sold-out / unavailable / disabled).
      2. Promotional badge text, matched against known phrase lists.
      3. Presence of a price, as a final and weaker positive signal.

    Falls back to UNKNOWN when nothing matches, rather than assuming
    IN_STOCK — an unverified guess is worse than an explicit "don't know".
    """
    raw_classes = tile.get("class")
    if isinstance(raw_classes, list):
        class_list: list[str] = [str(c) for c in raw_classes]
    elif isinstance(raw_classes, str):
        class_list = [raw_classes]
    else:
        class_list = []
    tile_classes = " ".join(class_list).lower()
    if any(token in tile_classes for token in OUT_OF_STOCK_CLASS_TOKENS):
        return Availability.OUT_OF_STOCK

    if badge_text:
        lowered = badge_text.lower()
        if any(phrase.lower() in lowered for phrase in OUT_OF_STOCK_PHRASES):
            return Availability.OUT_OF_STOCK
        if any(phrase.lower() in lowered for phrase in PRE_ORDER_PHRASES):
            return Availability.PRE_ORDER

    if price is not None:
        return Availability.IN_STOCK

    return Availability.UNKNOWN
