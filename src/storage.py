"""storage.py — load/save the on-disk state file (data/state.json).

Guarantees:
    * Atomic writes: the new state is written to a temporary file in the
      same directory and then moved into place with ``Path.replace``, which
      is an atomic rename on POSIX filesystems. A crash or power loss
      mid-write can never leave a half-written state.json on disk.
    * Backups: before a write replaces the existing file, a ``.bak`` copy of
      the previous, known-good state is kept — see ``backup_state``.
    * Corruption resilience: if state.json is unreadable/invalid JSON,
      ``load_state`` automatically falls back to the ``.bak`` copy (logging
      a warning) instead of silently resetting to empty state, which would
      cause every product to look "new" and re-trigger every notification.
      If the backup is also unusable, a ``StorageError`` is raised so the
      caller can decide how to proceed rather than running on data that may
      be wrong.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import ValidationError

from src.models import StoredState

logger = logging.getLogger(__name__)

BACKUP_SUFFIX = ".bak"


class StorageError(Exception):
    """Raised when the state file cannot be loaded or saved reliably."""


def load_state(path: Path) -> StoredState:
    """Load the state file at ``path``.

    Returns an empty :class:`StoredState` if the file does not exist yet
    (first run). Falls back to ``path.bak`` if ``path`` exists but is
    corrupt. Raises :class:`StorageError` if neither can be read.
    """
    if not path.exists():
        logger.info("No existing state file at %s; starting with empty state.", path)
        return StoredState()

    try:
        return _read_state_file(path)
    except (OSError, ValueError, ValidationError) as exc:
        logger.error("State file %s is unreadable or invalid: %s", path, exc)
        return _load_backup_or_raise(path, original_error=exc)


def save_state(path: Path, state: StoredState) -> None:
    """Atomically persist ``state`` to ``path``, backing up the previous
    file first. Never leaves ``path`` in a partially-written state."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        backup_state(path)

    payload = state.model_dump_json(indent=2)

    tmp_file = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    tmp_path = Path(tmp_file.name)
    try:
        with tmp_file:
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        tmp_path.replace(path)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise StorageError(f"failed to write state file {path}: {exc}") from exc

    logger.debug("Saved state (%d products) to %s", len(state.products), path)


def backup_state(path: Path) -> Path | None:
    """Copy the current ``path`` to ``path.bak``. No-op if ``path`` does not
    exist. Returns the backup path, or None if there was nothing to back up."""
    if not path.exists():
        return None

    backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    shutil.copyfile(path, backup_path)
    return backup_path


def _read_state_file(path: Path) -> StoredState:
    raw = path.read_text(encoding="utf-8")
    return StoredState.model_validate_json(raw)


def _load_backup_or_raise(path: Path, original_error: Exception) -> StoredState:
    backup_path = path.with_suffix(path.suffix + BACKUP_SUFFIX)
    if not backup_path.exists():
        raise StorageError(
            f"state file {path} is corrupt and no backup ({backup_path}) exists: {original_error}"
        ) from original_error

    try:
        state = _read_state_file(backup_path)
    except (OSError, ValueError, ValidationError) as backup_error:
        raise StorageError(
            f"state file {path} is corrupt and backup {backup_path} is also "
            f"unreadable: {backup_error}"
        ) from backup_error

    logger.warning("Recovered state from backup file %s after %s was corrupt.", backup_path, path)
    return state
