"""
JsonStore: the whole state in one file, loaded once, written atomically.

    {
      "version": 1,
      "subscribers": {"12345": {"follows": ["msgid-a@example.com"]}}
    }

One file means one atomic write per mutation, so /stop cannot half-apply. Reads
are served from memory; this is safe because python-telegram-bot runs the job
queue and handlers on a single event loop, so there is exactly one owner.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from kernel_lore_bot.storage.base import BaseStore

log = logging.getLogger(__name__)

STATE_VERSION = 1


def _load_state(path: Path) -> dict[int, set[str]]:
    if not path.exists():
        return _migrate_legacy(path.parent)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            int(chat): set(rec.get("follows", []))
            for chat, rec in raw.get("subscribers", {}).items()
        }
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        log.warning("Could not load %s: %s — starting fresh", path, exc)
        return {}


def _migrate_legacy(state_dir: Path) -> dict[int, set[str]]:
    """
    Import the old two-file format, if present.

    The old files are left on disk so the first deploy stays rollback-able.
    """
    subs_file = state_dir / "subscribers.json"
    follows_file = state_dir / "follows.json"
    if not subs_file.exists() and not follows_file.exists():
        return {}

    state: dict[int, set[str]] = {}

    try:
        for chat in json.loads(subs_file.read_text(encoding="utf-8")):
            state[int(chat)] = set()
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("Could not migrate subscribers.json: %s", exc)

    try:
        legacy = json.loads(follows_file.read_text(encoding="utf-8"))
        for thread_id, chats in legacy.items():
            for chat in chats:
                state.setdefault(int(chat), set()).add(thread_id)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
        log.warning("Could not migrate follows.json: %s", exc)

    log.info("Migrated %d subscriber(s) from the legacy two-file state", len(state))
    return state


class JsonStore(BaseStore):
    """Store backed by a single JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        super().__init__(_load_state(self._path))
        if not self._path.exists() and self._subs:
            self._flush()  # persist a completed migration immediately

    def _flush(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "subscribers": {
                str(chat): {"follows": sorted(threads)}
                for chat, threads in sorted(self._subs.items())
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
