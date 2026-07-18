"""
The scrape-and-send job.

New threads go to every subscriber; updated threads go only to the people who
followed them. Chats that have blocked the bot are pruned as they are found.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from telegram.error import Forbidden, TelegramError

from kernel_lore_bot.delivery.formatting import (
    format_header,
    format_thread,
    format_update_notification,
)
from kernel_lore_bot.delivery.keyboards import follow_keyboard, unfollow_keyboard
from kernel_lore_bot.digest import classify
from kernel_lore_bot.filters import Filter, apply_filters
from kernel_lore_bot.models import Classified, ThreadStatus
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.base import Source
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

# A short yield between threads so the event loop can service button presses.
_YIELD_SECONDS = 0.01


class SendResult(enum.Enum):
    """Outcome of one send_to() call, distinguishing "blocked" from "failed"."""

    OK = "ok"
    # The chat has blocked the bot (Forbidden). Safe to unsubscribe/unfollow.
    BLOCKED = "blocked"
    # Any other TelegramError: transient, a 5xx, a network blip, a bad
    # request. The subscription must survive this — only Forbidden may prune.
    FAILED = "failed"


async def send_to(bot, chat_id: int, text: str, reply_markup=None) -> SendResult:
    """
    Send one HTML message.

    Returns SendResult.BLOCKED only for a genuine Forbidden (the user blocked
    the bot) — that's the only outcome that should ever unsubscribe/unfollow
    someone. Any other TelegramError is SendResult.FAILED: logged, but the
    chat is still a real subscriber and must be retried on the next message.
    """
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return SendResult.OK
    except Forbidden:
        log.warning("chat_id=%d blocked the bot — will unsubscribe", chat_id)
        return SendResult.BLOCKED
    except TelegramError as exc:
        log.error("Telegram error sending to chat_id=%d: %s", chat_id, exc)
        return SendResult.FAILED


class Broadcaster:
    """Runs one scrape and delivers the results."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        source: Source,
        filters: Sequence[Filter] = (),
    ) -> None:
        self.settings = settings
        self.store = store
        self.source = source
        self.filters = list(filters)
        # Created lazily in run(), not here: Broadcaster is constructed in
        # cli.py before any event loop is running, and binding an
        # asyncio.Lock to "whatever loop happens to be current" at
        # construction time risks tying it to the wrong loop.
        self._lock: Optional[asyncio.Lock] = None

    def cutoff(self, now: Optional[datetime] = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now - timedelta(hours=self.settings.loopback_hours)

    def collect(self, cutoff: datetime, mailing_lists: Sequence[str]) -> list[Classified]:
        """Fetch, filter, and classify. No Telegram, no async."""
        threads = list(self.source.fetch_threads(cutoff, mailing_lists))
        kept = apply_filters(threads, self.filters)
        if len(kept) < len(threads):
            log.info(
                "Filtered out %d thread(s) by blocklist (%d remaining)",
                len(threads) - len(kept),
                len(kept),
            )
        return classify(kept, cutoff)

    async def run(self, bot, now: Optional[datetime] = None) -> None:
        # collect() now runs on a worker thread (see below), so the
        # scheduled job and an admin's /scrape can genuinely run at the same
        # time. Both would share this Broadcaster's self.source and its
        # requests.Session, which is not thread-safe — serialize here so the
        # second caller waits instead of racing the first.
        if self._lock is None:
            self._lock = asyncio.Lock()

        async with self._lock:
            await self._run_locked(bot, now)

    async def _run_locked(self, bot, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)

        subscriber_ids = self.store.subscribers()
        if not subscriber_ids:
            log.info("No subscribers yet — nothing to send.")
            return

        cutoff = self.cutoff(now)
        # collect() is a synchronous scrape (blocking HTTP across ~18 mailing
        # lists). Run it off the event loop so /start and button presses keep
        # being serviced while it's in flight. This is safe from corruption:
        # collect() only touches self.source/self.filters, never self.store,
        # so the Store's single-event-loop-owner assumption is untouched.
        # But the offload deliberately opens a multi-minute window during
        # which a subscriber can /stop, so the `subscriber_ids` snapshot
        # taken above is stale by the time collect() returns — it is only
        # used for the early "nothing to fetch" guard. The actual send below
        # re-reads self.store.subscribers() so a chat that unsubscribed
        # mid-scrape does not still receive the digest.
        classified = await asyncio.to_thread(
            self.collect, cutoff, self.settings.mailing_lists
        )
        if not classified:
            log.info("No new threads to send.")
            return

        new = [c for c in classified if c.status is ThreadStatus.NEW]
        updated = [c for c in classified if c.status is ThreadStatus.UPDATED]

        subscriber_ids = self.store.subscribers()
        log.info(
            "Broadcast: %d new thread(s) to %d subscriber(s); "
            "%d updated thread(s) → follower notifications",
            len(new), len(subscriber_ids), len(updated),
        )

        blocked: set[int] = set()
        await self._send_digest(bot, new, subscriber_ids, cutoff, blocked, now)
        await self._notify_followers(bot, updated)

        if blocked:
            self.store.remove_subscribers(blocked)
            log.info("Auto-removed %d blocked subscriber(s)", len(blocked))

        log.info("Broadcast complete.")

    async def _send_digest(self, bot, new, subscriber_ids, cutoff, blocked, now) -> None:
        if not new:
            return

        header = format_header(len(new), now)
        for chat_id in subscriber_ids:
            if await send_to(bot, chat_id, header) is SendResult.BLOCKED:
                blocked.add(chat_id)

        for i, item in enumerate(new, start=1):
            text = format_thread(item, cutoff)
            markup = follow_keyboard(item.thread.id)

            for chat_id in subscriber_ids:
                if chat_id in blocked:
                    continue
                result = await send_to(bot, chat_id, text, reply_markup=markup)
                if result is SendResult.BLOCKED:
                    blocked.add(chat_id)

            log.debug("New thread #%d/%d done", i, len(new))
            await asyncio.sleep(_YIELD_SECONDS)

    async def _notify_followers(self, bot, updated) -> None:
        for item in updated:
            thread_id = item.thread.id
            follower_ids = self.store.followers(thread_id)
            if not follower_ids:
                continue

            text = format_update_notification(item.thread)
            markup = unfollow_keyboard(thread_id)

            log.info(
                "Notifying %d follower(s) of updated thread: %s",
                len(follower_ids), item.thread.title,
            )

            for chat_id in follower_ids:
                result = await send_to(bot, chat_id, text, reply_markup=markup)
                if result is SendResult.BLOCKED:
                    self.store.unfollow(thread_id, chat_id)

            await asyncio.sleep(_YIELD_SECONDS)
