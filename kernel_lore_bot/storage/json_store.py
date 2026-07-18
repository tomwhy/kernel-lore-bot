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
from datetime import datetime, timezone
from pathlib import Path

from kernel_lore_bot.storage.base import BaseStore, Subscriber

log = logging.getLogger(__name__)

STATE_VERSION = 1


def _subscriber_from_json(chat: str, rec: dict) -> Subscriber:
    """Build a Subscriber from one entry of the "subscribers" object."""
    return Subscriber(chat_id=int(chat), follows=set(rec.get("follows", [])))


def _subscriber_to_json(sub: Subscriber) -> dict:
    """The on-disk shape of one subscriber. Sorted so writes are stable."""
    return {"follows": sorted(sub.follows)}


def _load_state(path: Path) -> dict[int, Subscriber]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        subs = [
            _subscriber_from_json(chat, rec)
            for chat, rec in raw.get("subscribers", {}).items()
        ]
        return {sub.chat_id: sub for sub in subs}
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        # The file exists but is unreadable (e.g. a crash landed the rename
        # before an fsync). Do NOT silently discard it — every subscriber and
        # follow would be gone with only a warning to show for it. Preserve
        # the bytes under a timestamped backup name so the data stays
        # recoverable on disk, log loudly, and only then continue empty.
        backup = path.with_name(
            path.name + "." + datetime.now(timezone.utc).strftime("corrupt-%Y%m%d%H%M%S")
        )
        try:
            os.replace(path, backup)
            log.error(
                "Could not parse %s: %s — original preserved at %s, starting fresh",
                path, exc, backup,
            )
        except OSError as replace_exc:
            log.error(
                "Could not parse %s: %s — AND could not back it up (%s), starting fresh",
                path, exc, replace_exc,
            )
        return {}


class JsonStore(BaseStore):
    """Store backed by a single JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        super().__init__(_load_state(self._path))

    def _flush(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "subscribers": {
                str(chat): _subscriber_to_json(self._subs[chat])
                for chat in sorted(self._subs)
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        # Flush and fsync the temp file's contents to disk *before* the
        # rename. os.replace() is atomic w.r.t. the directory entry, but
        # without an fsync the data itself can still be sitting in the OS
        # page cache when the rename lands — a host/container crash right
        # after can leave state.json truncated or zero-length even though
        # the rename "completed".
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)
