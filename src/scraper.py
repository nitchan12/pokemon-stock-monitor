"""scraper.py — downloads raw HTML from the target URL.

Responsibilities are intentionally narrow:
    * Perform a single HTTP GET against a URL using ``httpx``, with a realistic
      User-Agent, a bounded timeout, and an exponential-backoff retry policy.
    * Never raise out of the public API. Every outcome — success, timeout,
      network failure, unexpected status code, or unexpected content — is
      reported back as a :class:`FetchResult` so callers can decide what to do.

This module does **not** parse HTML. That responsibility belongs to
``parser.py`` (see the project architecture in README.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Retry tuning. Kept as named constants rather than inline literals so the
# policy is easy to find and adjust in one place (no magic numbers).
MAX_RETRY_ATTEMPTS = 3
RETRY_WAIT_MULTIPLIER_SECONDS = 1
RETRY_WAIT_MAX_SECONDS = 8

# Status codes at/above this threshold are treated as transient server-side
# failures and are retried. Codes at/above CLIENT_ERROR_THRESHOLD but below
# this are treated as permanent client errors and are not retried.
SERVER_ERROR_THRESHOLD = 500
CLIENT_ERROR_THRESHOLD = 400


class ScraperError(Exception):
    """Base class for all scraper failures. Never escapes ``fetch_html``."""


class RetryableFetchError(ScraperError):
    """A transient failure (timeout, connection error, 5xx) worth retrying."""


class NonRetryableFetchError(ScraperError):
    """A permanent failure (4xx, wrong content-type, empty body) — retrying
    would not help, so it is raised once and reported immediately."""


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Outcome of a single fetch attempt against the target URL.

    Exactly one of ``html`` (on success) or ``error`` (on failure) is set.
    """

    success: bool
    url: str
    status_code: int | None = None
    html: str | None = None
    error: str | None = None


class Scraper:
    """Thin, defensive HTTP client wrapper for downloading a target page.

    A single :class:`httpx.Client` is created per call to keep the scraper
    free of long-lived connection state between scheduled runs (important
    once this runs under APScheduler or as a one-shot GitHub Actions job).
    """

    def __init__(
        self,
        timeout_seconds: float = 15.0,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._headers = {
            "User-Agent": user_agent,
            "Accept-Language": "th-TH,th;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }

    def fetch_html(self, url: str) -> FetchResult:
        """Download ``url`` and return a :class:`FetchResult`. Never raises."""
        try:
            html, status_code = self._fetch_with_retry(url)
            return FetchResult(success=True, url=url, status_code=status_code, html=html)
        except ScraperError as exc:
            logger.error("Failed to fetch %s: %s", url, exc)
            return FetchResult(success=False, url=url, error=str(exc))
        except Exception as exc:  # pragma: no cover - last-resort safety net
            logger.exception("Unexpected error while fetching %s", url)
            return FetchResult(success=False, url=url, error=f"unexpected error: {exc}")

    @retry(
        reraise=True,
        stop=stop_after_attempt(MAX_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=RETRY_WAIT_MULTIPLIER_SECONDS, max=RETRY_WAIT_MAX_SECONDS),
        retry=retry_if_exception_type(RetryableFetchError),
    )
    def _fetch_with_retry(self, url: str) -> tuple[str, int]:
        response = self._request_once(url)
        self._validate_response(response, url)
        return response.text, response.status_code

    def _request_once(self, url: str) -> httpx.Response:
        try:
            with httpx.Client(
                headers=self._headers,
                timeout=self._timeout_seconds,
                follow_redirects=True,
            ) as client:
                return client.get(url)
        except httpx.TimeoutException as exc:
            logger.warning("Timeout while requesting %s, will retry: %s", url, exc)
            raise RetryableFetchError(f"timeout while requesting {url}: {exc}") from exc
        except httpx.TransportError as exc:
            logger.warning("Network error while requesting %s, will retry: %s", url, exc)
            raise RetryableFetchError(f"network error while requesting {url}: {exc}") from exc

    @staticmethod
    def _validate_response(response: httpx.Response, url: str) -> None:
        status = response.status_code
        if status >= SERVER_ERROR_THRESHOLD:
            raise RetryableFetchError(f"server error {status} for {url}")
        if status >= CLIENT_ERROR_THRESHOLD:
            raise NonRetryableFetchError(f"client error {status} for {url}")

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            raise NonRetryableFetchError(
                f"unexpected content-type {content_type!r} for {url} (expected HTML)"
            )

        if not response.text or not response.text.strip():
            raise NonRetryableFetchError(f"empty response body for {url}")
