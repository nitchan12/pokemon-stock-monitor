"""diagnose.py — one-off diagnostic: what does httpx actually receive?

Compares the availability signals the parser looks for against the HTML the
scraper really gets, for each monitored product page. Run this when the
monitor reports UNKNOWN so you can see exactly which signals are missing.

Needs no .env and sends no Telegram messages.

    python3 diagnose.py
"""

from __future__ import annotations

from bs4 import BeautifulSoup

from src.config import DEFAULT_PRODUCT_URLS
from src.parser import (
    ADD_TO_CART_BUTTON_SELECTOR,
    AVAILABILITY_SELECTOR,
    BACK_IN_STORE_BUTTON_SELECTOR,
    PRODUCT_NAME_SELECTOR,
    PRODUCT_ROOT_SELECTOR,
    _read_json_ld_availability,
    detect_availability,
)
from src.scraper import Scraper

SEPARATOR = "=" * 72


def main() -> int:
    scraper = Scraper()

    for url in DEFAULT_PRODUCT_URLS:
        print(SEPARATOR)
        print(url.rsplit("/", 1)[-1][:66])
        print(SEPARATOR)

        result = scraper.fetch_html(url)
        if not result.success or result.html is None:
            print(f"  FETCH FAILED: {result.error}")
            continue

        html = result.html
        soup = BeautifulSoup(html, "lxml")

        print(f"  HTTP status          : {result.status_code}")
        print(f"  HTML length          : {len(html):,} bytes")
        print(f"  product root found   : {soup.select_one(PRODUCT_ROOT_SELECTOR) is not None}")
        print(f"  product name found   : {soup.select_one(PRODUCT_NAME_SELECTOR) is not None}")

        json_ld = _read_json_ld_availability(soup)
        print(
            "  1. JSON-LD availability : "
            + (json_ld.value if json_ld is not None else "NOT FOUND / abstained")
        )

        block = soup.select_one(AVAILABILITY_SELECTOR)
        if block is None:
            print("  2. data-available       : NOT FOUND")
        else:
            print(f"  2. data-available       : {block.get('data-available')!r}")

        add_btn = soup.select_one(ADD_TO_CART_BUTTON_SELECTOR)
        back_btn = soup.select_one(BACK_IN_STORE_BUTTON_SELECTOR)
        if add_btn is not None and back_btn is None:
            print("  3. action button        : add-to-cart (in stock)")
        elif back_btn is not None and add_btn is None:
            print("  3. action button        : back-in-store (out of stock)")
        else:
            print("  3. action button        : inconclusive (both or neither present)")

        print(f"  --> VERDICT             : {detect_availability(soup).value}")

        # Cheap tells for bot-detection / region-gated responses.
        lowered = html.lower()
        for marker in ("captcha", "access denied", "cloudflare", "akamai", "bot detection"):
            if marker in lowered:
                print(f"  !! response mentions {marker!r} — possible bot/region block")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
