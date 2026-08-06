"""Unit tests for src/scraper.py.

httpx.Client is mocked throughout — these tests never touch the network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

from src.scraper import Scraper


def _make_response(
    status_code: int = 200,
    text: str = "<html>ok</html>",
    content_type: str = "text/html; charset=UTF-8",
) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        text=text,
        headers={"content-type": content_type},
        request=httpx.Request("GET", "https://example.test/"),
    )


def _patch_client(get_side_effect):
    """Patch src.scraper.httpx.Client so `with httpx.Client(...) as client: client.get(url)`
    returns/raises according to `get_side_effect` (a value, exception, or list for successive calls).
    """
    mock_client_instance = MagicMock()
    mock_client_instance.get.side_effect = get_side_effect
    mock_client_cm = MagicMock()
    mock_client_cm.__enter__.return_value = mock_client_instance
    mock_client_cm.__exit__.return_value = False
    return patch("src.scraper.httpx.Client", return_value=mock_client_cm), mock_client_instance


class TestFetchHtmlSuccess:
    def test_returns_html_on_200(self):
        patcher, mock_client = _patch_client([_make_response(200, "<html>hello</html>")])
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is True
        assert result.status_code == 200
        assert result.html == "<html>hello</html>"
        assert result.error is None
        assert mock_client.get.call_count == 1

    def test_succeeds_after_one_transient_failure(self):
        patcher, mock_client = _patch_client(
            [httpx.ConnectError("boom"), _make_response(200, "<html>ok</html>")]
        )
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is True
        assert result.html == "<html>ok</html>"
        assert mock_client.get.call_count == 2


class TestFetchHtmlRetryableFailures:
    def test_timeout_is_retried_and_eventually_fails(self):
        patcher, mock_client = _patch_client(httpx.TimeoutException("timed out"))
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "timeout" in result.error.lower()
        assert mock_client.get.call_count == 3  # MAX_RETRY_ATTEMPTS

    def test_connect_error_is_retried_and_eventually_fails(self):
        patcher, mock_client = _patch_client(httpx.ConnectError("refused"))
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "network error" in result.error.lower()
        assert mock_client.get.call_count == 3

    def test_server_error_500_is_retried_and_eventually_fails(self):
        patcher, mock_client = _patch_client([_make_response(500)] * 3)
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "server error 500" in result.error
        assert mock_client.get.call_count == 3


class TestFetchHtmlNonRetryableFailures:
    def test_404_is_not_retried(self):
        patcher, mock_client = _patch_client([_make_response(404)])
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "client error 404" in result.error
        assert mock_client.get.call_count == 1

    def test_empty_body_is_not_retried(self):
        patcher, mock_client = _patch_client([_make_response(200, text="")])
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "empty response body" in result.error
        assert mock_client.get.call_count == 1

    def test_non_html_content_type_is_not_retried(self):
        patcher, mock_client = _patch_client(
            [_make_response(200, text='{"a": 1}', content_type="application/json")]
        )
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "content-type" in result.error
        assert mock_client.get.call_count == 1


class TestFetchHtmlNeverRaises:
    def test_unexpected_exception_is_captured_not_raised(self):
        patcher, _mock_client = _patch_client(RuntimeError("something exploded"))
        with patcher:
            result = Scraper().fetch_html("https://example.test/search")

        assert result.success is False
        assert "unexpected error" in result.error
