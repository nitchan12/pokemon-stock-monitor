"""Unit tests for src/storage.py.

Uses pytest's `tmp_path` fixture for a real (but disposable) filesystem —
atomic-write and backup behavior are filesystem-level guarantees that are
not meaningful to test against a mock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.models import Availability, Product, StoredState
from src.storage import StorageError, backup_state, load_state, save_state

CHECKED_AT = datetime(2026, 8, 6, 12, 0, 0, tzinfo=timezone.utc)


def make_product(id: str = "1") -> Product:
    return Product(
        id=id,
        name="Test Product",
        price=Decimal("1000"),
        availability=Availability.IN_STOCK,
        product_url=f"https://www.toysrus.co.th/th-th/product-{id}.html",
        checked_at=CHECKED_AT,
    )


class TestLoadState:
    def test_missing_file_returns_empty_state(self, tmp_path: Path):
        state = load_state(tmp_path / "state.json")
        assert state.products == {}
        assert state.last_checked_at is None

    def test_round_trips_saved_state(self, tmp_path: Path):
        path = tmp_path / "state.json"
        original = StoredState(products={"1": make_product("1")}, last_checked_at=CHECKED_AT)
        save_state(path, original)

        loaded = load_state(path)
        assert loaded.products["1"].id == "1"
        assert loaded.products["1"].price == Decimal("1000")
        assert loaded.last_checked_at == CHECKED_AT

    def test_corrupt_file_falls_back_to_backup(self, tmp_path: Path):
        path = tmp_path / "state.json"
        good_state = StoredState(products={"1": make_product("1")})
        save_state(path, good_state)  # writes state.json, no .bak yet (first write)
        save_state(path, good_state)  # second write creates state.json.bak from the good file

        path.write_text("{not valid json!!", encoding="utf-8")

        loaded = load_state(path)
        assert loaded.products["1"].id == "1"

    def test_corrupt_file_with_no_backup_raises_storage_error(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json!!", encoding="utf-8")

        with pytest.raises(StorageError):
            load_state(path)

    def test_invalid_schema_falls_back_to_backup(self, tmp_path: Path):
        path = tmp_path / "state.json"
        good_state = StoredState(products={"1": make_product("1")})
        save_state(path, good_state)
        save_state(path, good_state)  # ensure a valid .bak exists

        # Valid JSON, but does not match the StoredState schema at all.
        path.write_text('{"products": "this should be a dict, not a string"}', encoding="utf-8")

        loaded = load_state(path)
        assert loaded.products["1"].id == "1"


class TestSaveState:
    def test_creates_parent_directory(self, tmp_path: Path):
        path = tmp_path / "nested" / "dir" / "state.json"
        save_state(path, StoredState())
        assert path.exists()

    def test_no_leftover_temp_files(self, tmp_path: Path):
        path = tmp_path / "state.json"
        save_state(path, StoredState(products={"1": make_product("1")}))

        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_overwrites_existing_file_atomically(self, tmp_path: Path):
        path = tmp_path / "state.json"
        save_state(path, StoredState(products={"1": make_product("1")}))
        save_state(path, StoredState(products={"2": make_product("2")}))

        loaded = load_state(path)
        assert set(loaded.products) == {"2"}


class TestBackupState:
    def test_no_op_when_file_does_not_exist(self, tmp_path: Path):
        result = backup_state(tmp_path / "state.json")
        assert result is None

    def test_creates_bak_file_with_previous_content(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text('{"products": {}, "last_checked_at": null}', encoding="utf-8")

        backup_path = backup_state(path)

        assert backup_path is not None
        assert backup_path.name == "state.json.bak"
        assert backup_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
