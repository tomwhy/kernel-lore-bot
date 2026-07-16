"""
follows.py – persist per-thread follow subscriptions.

Data structure (follows.json):
{
  "<thread_id>": [<chat_id>, ...],
  ...
}

thread_id is the root Message-ID of the thread (without angle brackets).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import config

log = logging.getLogger(__name__)


def _path() -> Path:
    return config.FOLLOWS_FILE


def _load_raw() -> dict[str, list[int]]:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Could not load follows: %s — starting fresh", exc)
        return {}


def _save_raw(data: dict[str, list[int]]) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(data, indent=2))


# ------------------------------------------------------------------
#  Public API
# ------------------------------------------------------------------

def follow(thread_id: str, chat_id: int) -> bool:
    """
    Subscribe chat_id to updates for thread_id.
    Returns True if this is a new follow (wasn't already following).
    """
    data = _load_raw()
    followers = data.get(thread_id, [])
    if chat_id in followers:
        return False
    followers.append(chat_id)
    data[thread_id] = followers
    _save_raw(data)
    log.info("chat_id=%d now following thread %s", chat_id, thread_id)
    return True


def unfollow(thread_id: str, chat_id: int) -> bool:
    """
    Unsubscribe chat_id from thread_id.
    Returns True if they were following.
    """
    data = _load_raw()
    followers = data.get(thread_id, [])
    if chat_id not in followers:
        return False
    followers.remove(chat_id)
    if followers:
        data[thread_id] = followers
    else:
        data.pop(thread_id, None)
    _save_raw(data)
    log.info("chat_id=%d unfollowed thread %s", chat_id, thread_id)
    return True


def get_followers(thread_id: str) -> list[int]:
    """Return all chat_ids following this thread."""
    return _load_raw().get(thread_id, [])


def is_following(thread_id: str, chat_id: int) -> bool:
    return chat_id in get_followers(thread_id)


def all_followed_thread_ids() -> set[str]:
    """Return the set of all thread IDs that have at least one follower."""
    return set(_load_raw().keys())


def remove_subscriber(chat_id: int) -> None:
    """Remove chat_id from every thread's follower list (called on /stop)."""
    data = _load_raw()
    changed = False
    for thread_id in list(data.keys()):
        if chat_id in data[thread_id]:
            data[thread_id].remove(chat_id)
            changed = True
        if not data[thread_id]:
            del data[thread_id]
    if changed:
        _save_raw(data)
        log.info("Removed chat_id=%d from all thread follows", chat_id)


def prune_threads(keep_ids: Iterable[str]) -> int:
    """
    Remove follow entries for thread IDs not in keep_ids.
    Returns number of entries pruned.
    """
    keep = set(keep_ids)
    data = _load_raw()
    to_delete = [tid for tid in data if tid not in keep]
    for tid in to_delete:
        del data[tid]
    if to_delete:
        _save_raw(data)
    return len(to_delete)
