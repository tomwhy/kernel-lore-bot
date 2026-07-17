import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone

from telegram.error import BadRequest, Forbidden

from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.filters import BlockedAuthors
from kernel_lore_bot.models import Entry, Node, Thread
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import InMemoryStore

from .conftest import FakeBot

NOW = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)


def _thread(msg_id, updated, author="Alice Adams", mailing_list="netdev"):
    entry = Entry(
        id=msg_id,
        title=f"[PATCH] {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author=author,
        updated=updated,
        reply=None,
    )
    return Thread(roots=(Node(entry=entry),), mailing_list=mailing_list)


class FakeSource:
    def __init__(self, threads):
        self.threads = threads
        self.calls = []

    def fetch_threads(self, since):
        self.calls.append(since)
        return list(self.threads)


def _broadcaster(threads, store, filters=()):
    return Broadcaster(
        settings=Settings(loopback_hours=4),
        store=store,
        source=FakeSource(threads),
        filters=filters,
    )


# -- guard rails ----------------------------------------------------

async def test_no_subscribers_means_nothing_is_fetched():
    source = FakeSource([_thread("a@x.com", NOW)])
    b = Broadcaster(Settings(), InMemoryStore(), source)
    await b.run(FakeBot(), now=NOW)
    assert source.calls == []


async def test_no_threads_means_nothing_is_sent():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()
    await _broadcaster([], store).run(bot, now=NOW)
    assert bot.sent == []


# -- cutoff ---------------------------------------------------------

def test_cutoff_is_loopback_hours_before_now():
    b = _broadcaster([], InMemoryStore())
    assert b.cutoff(NOW) == NOW - timedelta(hours=4)


# -- new threads ----------------------------------------------------

async def test_new_thread_is_broadcast_to_every_subscriber():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_subscriber(2)
    bot = FakeBot()

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    # Each subscriber gets the header plus one thread message.
    assert len(bot.texts_to(1)) == 2
    assert len(bot.texts_to(2)) == 2
    assert "Kernel Lore Digest" in bot.texts_to(1)[0]
    assert "1</b> new thread(s)" in bot.texts_to(1)[0]
    assert "[PATCH] a@x.com" in bot.texts_to(1)[1]


async def test_new_thread_message_carries_a_follow_button():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    markup = bot.sent[1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "follow:a@x.com"


async def test_thread_messages_are_sent_as_html_without_link_previews():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    assert bot.sent[1]["parse_mode"] == "HTML"
    assert bot.sent[1]["disable_web_page_preview"] is True


# -- updated threads ------------------------------------------------

async def test_updated_thread_notifies_only_its_followers():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.follow("old@x.com", 2)
    bot = FakeBot()

    old = _thread("old@x.com", NOW - timedelta(hours=10))  # before the cutoff
    await _broadcaster([old], store).run(bot, now=NOW)

    assert bot.texts_to(1) == []
    assert "Thread update" in bot.texts_to(2)[0]
    assert bot.sent[0]["reply_markup"].inline_keyboard[0][0].callback_data == (
        "unfollow:old@x.com"
    )


async def test_updated_thread_with_no_followers_sends_nothing():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)
    assert bot.sent == []


async def test_no_digest_header_when_only_updated_threads_exist():
    store = InMemoryStore()
    store.follow("old@x.com", 1)
    bot = FakeBot()

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)
    assert not any("Kernel Lore Digest" in t for t in bot.texts_to(1))


# -- filters --------------------------------------------------------

async def test_blocked_authors_never_reach_subscribers():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()

    threads = [
        _thread("bot@x.com", NOW, author="kernel test robot"),
        _thread("human@x.com", NOW, author="Linus Torvalds"),
    ]
    await _broadcaster(threads, store, filters=[BlockedAuthors(("kernel test robot",))]).run(
        bot, now=NOW
    )

    body = "\n".join(bot.texts_to(1))
    assert "human@x.com" in body
    assert "bot@x.com" not in body
    assert "1</b> new thread(s)" in bot.texts_to(1)[0]


# -- blocked subscribers --------------------------------------------

async def test_a_chat_that_blocked_the_bot_is_removed():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.follow("t1", 1)
    bot = FakeBot(fail_for={1})

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    assert store.subscribers() == {2}
    assert store.followers("t1") == []


async def test_a_blocked_chat_is_not_retried_for_later_threads():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot(fail_for={1})

    threads = [_thread("a@x.com", NOW), _thread("b@x.com", NOW)]
    await _broadcaster(threads, store).run(bot, now=NOW)

    # Only the header attempt hits chat 1; after Forbidden it is skipped entirely.
    assert bot.attempts_to(1) == 1
    assert store.subscribers() == set()


async def test_a_follower_who_blocked_the_bot_is_unfollowed():
    store = InMemoryStore()
    store.follow("old@x.com", 5)
    bot = FakeBot(fail_for={5})

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)

    assert store.followers("old@x.com") == []


# -- collect --------------------------------------------------------

def test_collect_returns_new_threads_before_updated_ones():
    store = InMemoryStore()
    b = _broadcaster(
        [
            _thread("old@x.com", NOW - timedelta(hours=10)),
            _thread("new@x.com", NOW),
        ],
        store,
    )
    result = b.collect(b.cutoff(NOW))
    assert [c.thread.id for c in result] == ["new@x.com", "old@x.com"]


# -- defect 18: the event loop must not freeze during the scrape ---


async def test_run_does_not_block_the_event_loop_during_collect():
    """
    collect() runs synchronous, blocking HTTP under the hood. If run() calls
    it directly, nothing else — like a concurrently scheduled heartbeat task
    — can make progress for the whole duration. Offloading it via
    asyncio.to_thread lets the loop keep servicing other work.
    """

    class SlowSource:
        def fetch_threads(self, since):
            time.sleep(0.05)  # a real, blocking sleep — simulates requests.get()
            return []

    store = InMemoryStore()
    store.add_subscriber(1)
    b = Broadcaster(Settings(), store, SlowSource())

    heartbeats = 0

    async def heartbeat():
        nonlocal heartbeats
        while True:
            heartbeats += 1
            await asyncio.sleep(0.005)

    task = asyncio.create_task(heartbeat())
    try:
        await b.run(FakeBot(), now=NOW)
        # Checked immediately after run() returns, before the heartbeat task
        # gets any further chances to run: if the event loop had been frozen
        # for the 50ms of SlowSource.fetch_threads, this would still be 0.
        assert heartbeats >= 1, "event loop appears to have been blocked during run()"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# -- defect 19: only Forbidden may prune a subscriber ---------------


async def test_a_transient_telegram_error_does_not_unsubscribe():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_subscriber(2)
    bot = FakeBot(error_for={1: BadRequest("oops")})

    threads = [_thread("a@x.com", NOW), _thread("b@x.com", NOW)]
    await _broadcaster(threads, store).run(bot, now=NOW)

    # chat 1 is retried for every message — it is not "blocked" — and never
    # actually receives anything, since it always errors.
    assert bot.attempts_to(1) == 3  # header + 2 thread messages
    assert bot.texts_to(1) == []
    assert store.subscribers() == {1, 2}

    # chat 2 is unaffected and gets the full digest.
    assert len(bot.texts_to(2)) == 3


async def test_a_follower_transient_telegram_error_does_not_unfollow():
    store = InMemoryStore()
    store.follow("old@x.com", 5)
    bot = FakeBot(error_for={5: BadRequest("oops")})

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)

    assert store.followers("old@x.com") == [5]


# -- concurrency guard: collect() runs on a worker thread (defect 18), so two
# broadcasts can genuinely overlap and would share one Source/requests.Session,
# which is not thread-safe. run() must serialize.


async def test_concurrent_run_calls_do_not_interleave_their_scrapes():
    """
    Simulates the scheduled job and an admin's /scrape firing at the same
    time. Both share one Broadcaster (and thus one Source). Without a lock,
    asyncio.to_thread would let both scrapes run on separate worker threads
    at once; with the lock, the second run() waits for the first to finish
    before its scrape starts.
    """
    events: list[str] = []

    class TrackingSource:
        def fetch_threads(self, since):
            events.append("enter")
            time.sleep(0.05)  # real blocking work, like requests.get()
            events.append("exit")
            return []

    store = InMemoryStore()
    store.add_subscriber(1)
    b = Broadcaster(Settings(), store, TrackingSource())

    await asyncio.gather(
        b.run(FakeBot(), now=NOW),
        b.run(FakeBot(), now=NOW),
    )

    # Each "enter" must be immediately followed by its own "exit" — never
    # enter, enter, exit, exit, which would mean the scrapes overlapped.
    assert events == ["enter", "exit", "enter", "exit"]


# -- finding 1: /stop mid-scrape must not still receive the digest ---


async def test_a_chat_that_stops_mid_scrape_receives_nothing():
    """
    collect() runs on a worker thread (defect 18), which deliberately frees
    the event loop for the whole scrape. A user can /stop during that window;
    the store correctly removes them, but a stale subscriber_ids snapshot
    taken *before* collect() would still mail them the digest. The fix
    re-reads store.subscribers() after collect() returns.
    """
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_subscriber(2)

    class UnsubscribingSource:
        def fetch_threads(self, since):
            # Simulates chat 1 sending /stop while the scrape is in flight.
            store.remove_subscriber(1)
            return [_thread("a@x.com", NOW)]

    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, UnsubscribingSource())
    await b.run(bot, now=NOW)

    assert bot.texts_to(1) == []
    assert len(bot.texts_to(2)) == 2


# -- finding 3: the digest header must use the injected `now` --------


async def test_digest_header_uses_the_injected_now_not_wall_clock():
    store = InMemoryStore()
    store.add_subscriber(1)
    bot = FakeBot()
    injected_now = datetime(2020, 1, 1, tzinfo=timezone.utc)

    await _broadcaster([_thread("a@x.com", injected_now)], store).run(bot, now=injected_now)

    assert "2020-01-01 00:00 UTC" in bot.texts_to(1)[0]


async def test_broadcaster_constructed_outside_a_running_loop_can_still_run():
    """
    cli.py constructs Broadcaster from plain synchronous code, before any
    event loop is running (build_components/Broadcaster() happen outside
    asyncio.run). The lock must not be created eagerly at construction time —
    doing so risks binding to a loop that isn't the one run() is later
    awaited on.
    """
    store = InMemoryStore()
    b = _broadcaster([], store)  # constructed with no loop running
    await b.run(FakeBot(), now=NOW)  # now a loop exists; must not raise
