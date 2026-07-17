"""Store implementation with no persistence. Used by tests."""

from __future__ import annotations

from kernel_lore_bot.storage.base import BaseStore


class InMemoryStore(BaseStore):
    """BaseStore with _flush left as a no-op."""
