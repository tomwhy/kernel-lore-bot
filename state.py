"""
state.py – persist seen thread IDs so we never send duplicates
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _state_dir() -> Path:
    dir = Path(os.environ.get("KERNEL_BOT_STATE_DIR", "data"))
    dir.mkdir(parents=True, exist_ok=True)
    return dir


def _path() -> Path:
    return _state_dir() / config.STATE_FILE


def load_seen() -> set[str]:
    p = _path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
        return set(data.get("seen", []))
    except (json.JSONDecodeError, KeyError) as exc:
        log.warning("Could not load state file: %s — starting fresh", exc)
        return set()


def save_seen(seen: set[str]) -> None:
    _path().write_text(json.dumps({"seen": list(seen)}, indent=2))


def is_first_run() -> bool:
    return not _path().exists()


def prune_old(seen: set[str], threads_by_id: dict) -> set[str]:
    """Remove IDs older than 30 days to keep the state file trim."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    fresh = {
        tid for tid in seen
        if tid in threads_by_id and threads_by_id[tid].updated >= cutoff
    }
    removed = len(seen) - len(fresh)
    if removed:
        log.debug("Pruned %d old thread IDs from state", removed)
    return fresh
