"""Unit tests for src/utils.py."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.utils import format_price_thb, format_thai_datetime


class TestFormatPriceThb:
    def test_none_returns_unspecified_label(self):
        assert format_price_thb(None) == "ไม่ระบุราคา"

    def test_whole_number_has_no_decimals(self):
        assert format_price_thb(Decimal("1980")) == "฿1,980"

    def test_large_number_gets_thousands_separators(self):
        assert format_price_thb(Decimal("6500")) == "฿6,500"

    def test_small_whole_number(self):
        assert format_price_thb(Decimal("555")) == "฿555"

    def test_fractional_price_keeps_two_decimals(self):
        assert format_price_thb(Decimal("199.50")) == "฿199.50"


class TestFormatThaiDatetime:
    def test_converts_utc_to_bangkok_local_time(self):
        dt = datetime(2026, 8, 6, 5, 0, 0, tzinfo=timezone.utc)  # 05:00 UTC == 12:00 ICT
        assert format_thai_datetime(dt) == "06/08/2026 12:00 น."

    def test_output_contains_thai_suffix(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert format_thai_datetime(dt).endswith("น.")
