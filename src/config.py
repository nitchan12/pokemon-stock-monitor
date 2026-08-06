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

DEFAULT_TARGET_URL = "https://www.toysrus.co.th/th-th/search/?q=pokemon+tcg+ma6&lang=th_TH&cgid="
DEFAULT_REQUEST_TIMEOUT_SECONDS = 15.0
DEFAULT_STATE_FILE = Path("data/state.json")

REQUIRED_ENV_VARS = ("BOT_TOKEN", "CHAT_ID")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Validated application configuration for a single monitor run."""

    bot_token: str = Field(..., min_length=1)
    chat_id: str = Field(..., min_length=1)
    request_timeout: float = Field(default=DEFAULT_REQUEST_TIMEOUT_SECONDS, gt=0)
    target_url: str = DEFAULT_TARGET_URL
    state_file: Path = DEFAULT_STATE_FILE


def load_config(env_file: Path | None = None) -> Settings:
    """Load and validate settings from the environment.

    Args:
        env_file: optional explicit path to a ``.env`` file. If omitted,
            ``python-dotenv`` looks for ``.env`` in the current working
            directory (its normal default behavior).

    Raises:
        ConfigError: if BOT_TOKEN/CHAT_ID are missing, or REQUEST_TIMEOUT
            is present but not a valid positive number.
    """
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(override=False)

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    chat_id = os.environ.get("CHAT_ID", "").strip()
    request_timeout_raw = os.environ.get("REQUEST_TIMEOUT", "").strip()
    target_url = os.environ.get("TARGET_URL", "").strip() or DEFAULT_TARGET_URL

    values = {"BOT_TOKEN": bot_token, "CHAT_ID": chat_id}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill them in "
            "(see README.md 'Telegram Setup')."
        )

    request_timeout = DEFAULT_REQUEST_TIMEOUT_SECONDS
    if request_timeout_raw:
        try:
            request_timeout = float(request_timeout_raw)
        except ValueError as exc:
            raise ConfigError(
                f"REQUEST_TIMEOUT must be a positive number, got {request_timeout_raw!r}"
            ) from exc

    try:
        return Settings(
            bot_token=bot_token,
            chat_id=chat_id,
            request_timeout=request_timeout,
            target_url=target_url,
        )
    except ValidationError as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc
