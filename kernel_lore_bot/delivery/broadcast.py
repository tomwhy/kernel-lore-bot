"""
The scrape-and-send job.

One scrape covers the union of every subscriber's mailing lists; each
subscriber then gets only the threads whose lists intersect theirs and whose
author they have not personally blocked. Followers of an updated thread are
notified regardless of their lists or blocks. Chats that have blocked the bot
are pruned as they are found.
"""

from __future__ import annotations

import asyncio
import enum
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterable, Optional, Sequence

from telegram.error import Forbidden, TelegramError

from kernel_lore_bot.delivery.formatting import (
    format_header,
    format_thread,
    format_update_notification,
)
from kernel_lore_bot.delivery.keyboards import follow_keyboard, unfollow_keyboard
from kernel_lore_bot.digest import classify, count_entries_since
from kernel_lore_bot.filters import BlockedAuthors
from kernel_lore_bot.models import Classified, ThreadStatus
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.base import Source
from kernel_lore_bot.storage import Store

if TYPE_CHECKING:
    from kernel_lore_bot.sources.lore.index import ListRegistry

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
        *,
        list_registry: Optional[ListRegistry] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.source = source
        # Refreshed inside collect(), which already runs off the event loop.
        # Optional so a dry run can skip it.
        self.list_registry = list_registry
        # Created lazily in run(), not here: Broadcaster is constructed in
        # cli.py before any event loop is running, and binding an
        # asyncio.Lock to "whatever loop happens to be current" at
        # construction time risks tying it to the wrong loop.
        self._lock: Optional[asyncio.Lock] = None

    def cutoff(self, now: Optional[datetime] = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now - timedelta(hours=self.settings.loopback_hours)

    def collect(
        self,
        cutoff: datetime,
        mailing_lists: Sequence[str],
        followed_ids: Iterable[str] = (),
    ) -> list[Classified]:
        """
        Fetch and classify. No Telegram, no async, no filtering.

        Filtering is per-subscriber now (see visible_for), so it cannot happen
        here — one scrape feeds differently-filtered digests.

        `followed_ids` is fetched separately, by Message-ID, and merged in —
        see the class docstring's explanation of why an explicit follow beats
        list membership. A followed id already reached by the list scrape (as
        a root or as a reply — checked against every node, not just roots) is
        not refetched: that thread is already in `threads` with its real
        mailing_lists, which is exactly what a merge would produce anyway.
        """
        if self.list_registry is not None:
            self.list_registry.refresh()
        threads = list(self.source.fetch_threads(cutoff, mailing_lists))

        covered = {node.entry.id for thread in threads for node in thread.walk()}
        ids_to_fetch = [tid for tid in dict.fromkeys(followed_ids) if tid not in covered]
        if ids_to_fetch:
            merged_ids: set[str] = set()
            for thread in self.source.fetch_threads_by_id(ids_to_fetch):
                if thread.id in merged_ids:
                    # Two different followed ids can resolve to the same
                    # off-list thread -- e.g. one subscriber follows the
                    # root and another follows one of its replies, and lore's
                    # mbox endpoint returns the whole thread for either id.
                    # Each was still fetched once (there is no way to know
                    # they collide before fetching), but only one copy may
                    # reach classify(). _notify_followers unions followers
                    # across every node of a thread, so if the same thread
                    # reached classify() twice, every one of its followers --
                    # whether following the root or a reply -- would get the
                    # same update notification twice.
                    continue
                # classify() assumes every thread it is handed is already
                # known to have activity at/after cutoff — a guarantee
                # fetch_threads gets for free from the atom feed's `since`
                # filter (it only ever surfaces entries >= since). A by-id
                # fetch has no such filter: it downloads the whole mbox
                # unconditionally, so most followed threads on most runs will
                # have nothing new. Without this check, classify() would
                # still label a followed thread UPDATED purely because its
                # root predates the cutoff, and _notify_followers would spam
                # every follower on every single scrape regardless of
                # whether anything happened. This is the one caller that can
                # break classify()'s "already fresh" assumption on its own,
                # so it is the one place that must restore it — classify()
                # itself keeps its existing, simpler contract unchanged.
                if count_entries_since(thread, cutoff) > 0:
                    threads.append(thread)
                    merged_ids.add(thread.id)

        return classify(threads, cutoff)

    def visible_for(
        self, chat_id: int, classified: Sequence[Classified]
    ) -> list[Classified]:
        """What this subscriber's lists and blocks leave them."""
        lists = self.store.mailing_lists(chat_id)
        if not lists:
            return []
        author_filter = BlockedAuthors(tuple(self.store.blocked_authors(chat_id)))
        return [
            item
            for item in classified
            if (item.thread.mailing_lists & lists) and author_filter.allows(item.thread)
        ]

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

        wanted = sorted(self.store.all_mailing_lists())
        followed_ids = sorted(self.store.all_followed_threads())
        if not wanted and not followed_ids:
            log.info(
                "No subscriber wants any mailing list or follows any thread "
                "— nothing to fetch."
            )
            return

        cutoff = self.cutoff(now)
        # collect() is a synchronous scrape. Run it off the event loop so
        # /start and button presses keep being serviced while it is in
        # flight. This is safe from corruption: collect() only touches
        # self.source and self.list_registry, never self.store, so the
        # Store's single-event-loop-owner assumption is untouched. But the
        # offload deliberately opens a multi-minute window during which a
        # subscriber can /stop, so the snapshot above is stale by the time
        # collect() returns — it is only used for the early guard. The send
        # below re-reads self.store.subscribers().
        classified = await asyncio.to_thread(self.collect, cutoff, wanted, followed_ids)
        if not classified:
            log.info("No new threads to send.")
            return

        new = [c for c in classified if c.status is ThreadStatus.NEW]
        updated = [c for c in classified if c.status is ThreadStatus.UPDATED]

        subscriber_ids = self.store.subscribers()
        log.info(
            "Broadcast: %d new thread(s) collected for %d subscriber(s) "
            "(filtered per-subscriber); %d updated thread(s) → follower notifications",
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

        for chat_id in subscriber_ids:
            visible = self.visible_for(chat_id, new)
            if not visible:
                # Say nothing rather than sending a header announcing zero
                # threads — that reads like a bug to the person receiving it.
                continue

            result = await send_to(bot, chat_id, format_header(len(visible), now))
            if result is SendResult.BLOCKED:
                blocked.add(chat_id)
                continue

            for item in visible:
                result = await send_to(
                    bot,
                    chat_id,
                    format_thread(item, cutoff),
                    reply_markup=follow_keyboard(item.thread.id),
                )
                if result is SendResult.BLOCKED:
                    blocked.add(chat_id)
                    break

            log.debug("Digest sent to chat_id=%d (%d thread(s))", chat_id, len(visible))
            await asyncio.sleep(_YIELD_SECONDS)

    async def _notify_followers(self, bot, updated) -> None:
        for item in updated:
            # A follow can be held on ANY node of the thread, not just the
            # root: mbox.py can surface a node as a root that later turns
            # out to be a reply once its true parent is archived, and a
            # split thread has more than one root while Thread.id only
            # names roots[0]. Walk every node and union their followers, so
            # a follow on a reply id is not silently invisible. Track which
            # node id(s) each chat actually follows, so a Forbidden prune
            # below removes the real follow rather than a root id the chat
            # may never have held.
            ids_by_chat: dict[int, list[str]] = {}
            for node in item.thread.walk():
                for chat_id in self.store.followers(node.entry.id):
                    ids_by_chat.setdefault(chat_id, []).append(node.entry.id)
            if not ids_by_chat:
                continue

            text = format_update_notification(item.thread)

            log.info(
                "Notifying %d follower(s) of updated thread: %s",
                len(ids_by_chat), item.thread.title,
            )

            for chat_id, followed_ids in ids_by_chat.items():
                # The button must carry an id this chat actually holds, or
                # pressing it is a no-op and the chat has no way to stop the
                # notifications (there is no /following command; /stop drops
                # the whole subscription). Prefer the root id when the chat
                # holds it, since that's the id the digest's own follow
                # button uses and keeps the common case unchanged; otherwise
                # fall back to one of the reply ids it does hold.
                button_id = (
                    item.thread.id if item.thread.id in followed_ids else followed_ids[0]
                )
                markup = unfollow_keyboard(button_id)
                result = await send_to(bot, chat_id, text, reply_markup=markup)
                if result is SendResult.BLOCKED:
                    for followed_id in followed_ids:
                        self.store.unfollow(followed_id, chat_id)

            await asyncio.sleep(_YIELD_SECONDS)
