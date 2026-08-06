"""config.py — application configuration, loaded from environment variables.

Uses python-dotenv to populate ``os.environ`` from a ``.env`` file (see
``.env.example`` for the full list of variables), then validates the
result into a :class:`Settings` object. Fails fast with a clear,
actionable :class:`ConfigError` if required variables are missing, rather
than letting the program crash later with a confusing error deep inside
the notifier.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# The Pokémon TCG MA6 product detail pages being monitored. Each is a direct
# PDP URL rather than a search-results page, because the PDP exposes the
# authoritative add-to-cart / out-of-stock markup (see parser.py).
#
# Product 10161784 ("MA6 (58) ชุด 30th Celebration", ฿1,980) was previously
# monitored here but the storefront now returns HTTP 410 Gone for it and it
# no longer appears in search results — it has been delisted. It is left out
# rather than kept as a permanently-failing fetch. To monitor it again (or
# any other page), set PRODUCT_URLS in .env.
DEFAULT_PRODUCT_URLS: tuple[str, ...] = (
    "https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-futuristic-rare-set-58-30th-celebration-expected-september-2026-10161786.html",
    "https://www.toysrus.co.th/th-th/pre-order-pokemon-tcg-ma6-first-partner-58-30th-celebration-expected-september-2026-10161785.html",
)

DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
DEFAULT_STATE_FILE = Path("data/state.json")

# "Limited repeat" alert policy: alert the moment a product becomes
# available, then re-alert at most MAX_NOTIFY_COUNT times total while it
# stays available, spaced at least REPEAT_INTERVAL_MINUTES apart. Prevents
# both "alert once and I missed it" and "alert every 5 minutes forever".
DEFAULT_MAX_NOTIFY_COUNT = 3
DEFAULT_REPEAT_INTERVAL_MINUTES = 10

# Delay between consecutive product-page requests within a single run, so a
# run does not fire three requests at the target site simultaneously.
DEFAULT_REQUEST_DELAY_SECONDS = 2.0


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Validated application configuration for a single monitor run."""

    bot_token: str = Field(..., min_length=1)
    chat_id: str = Field(..., min_length=1)
    request_timeout: float = Field(default=DEFAULT_REQUEST_TIMEOUT_SECONDS, gt=0)
    product_urls: tuple[str, ...] = DEFAULT_PRODUCT_URLS
    state_file: Path = DEFAULT_STATE_FILE
    max_notify_count: int = Field(default=DEFAULT_MAX_NOTIFY_COUNT, ge=1)
    repeat_interval_minutes: int = Field(default=DEFAULT_REPEAT_INTERVAL_MINUTES, ge=0)
    request_delay_seconds: float = Field(default=DEFAULT_REQUEST_DELAY_SECONDS, ge=0)


def load_config(env_file: Path | None = None) -> Settings:
    """Load and validate settings from the environment.

    Args:
        env_file: optional explicit path to a ``.env`` file. If omitted,
            ``python-dotenv`` looks for ``.env`` in the current working
            directory (its normal default behavior).

    Raises:
        ConfigError: if BOT_TOKEN/CHAT_ID are missing, or if any optional
            numeric setting is present but not a valid number.
    """
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(override=False)

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()

    missing = [
        name for name, value in (("BOT_TOKEN", bot_token), ("CHAT_ID", chat_id)) if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in "
            "(see README.md 'Telegram Setup')."
        )

    request_timeout = _read_number("REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT_SECONDS)
    max_notify_count = int(_read_number("MAX_NOTIFY_COUNT", DEFAULT_MAX_NOTIFY_COUNT))
    repeat_interval = int(_read_number("REPEAT_INTERVAL_MINUTES", DEFAULT_REPEAT_INTERVAL_MINUTES))
    request_delay = _read_number("REQUEST_DELAY_SECONDS", DEFAULT_REQUEST_DELAY_SECONDS)
    product_urls = _read_product_urls()

    try:
        return Settings(
            bot_token=bot_token,
            chat_id=chat_id,
            request_timeout=request_timeout,
            product_urls=product_urls,
            max_notify_count=max_notify_count,
            repeat_interval_minutes=repeat_interval,
            request_delay_seconds=request_delay,
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc


def _read_number(name: str, default: float) -> float:
    """Read an optional numeric env var, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _read_product_urls() -> tuple[str, ...]:
    """Read PRODUCT_URLS (comma-separated) if set, else use the defaults."""
    raw = os.environ.get("PRODUCT_URLS", "").strip()
    if not raw:
        return DEFAULT_PRODUCT_URLS

    urls = tuple(part.strip() for part in raw.split(",") if part.strip())
    if not urls:
        raise ConfigError("PRODUCT_URLS was set but contained no usable URLs")
    return urls
