"""
subscribers.py – persist the set of chat IDs that have subscribed via /start
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import config

log = logging.getLogger(__name__)


def _path() -> Path:
    return config.SUBSCRIBERS_FILE


def load() -> set[int]:
    p = _path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text())
        return set(map(int, data))
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        log.warning("Could not load subscribers: %s — starting fresh", exc)
        return set()


def save(chat_ids: set[int]) -> None:
    _path().write_text(json.dumps(list(chat_ids), indent=2))


def add(chat_id: int) -> bool:
    """Add a subscriber. Returns True if it was a new subscription."""
    subs = load()
    if chat_id in subs:
        return False
    subs.add(chat_id)
    save(subs)
    log.info("New subscriber: chat_id=%d  (total: %d)", chat_id, len(subs))
    return True


def remove(chat_id: int) -> bool:
    """Remove a subscriber. Returns True if they were subscribed."""
    subs = load()
    if chat_id not in subs:
        return False
    subs.discard(chat_id)
    save(subs)
    log.info("Unsubscribed: chat_id=%d  (total: %d)", chat_id, len(subs))
    return True

def remove_many(ids: Iterable[int]) -> None:
    subs = load() 
    for id in ids:
        subs.discard(id)
    save(subs)

def count() -> int:
    return len(load())
