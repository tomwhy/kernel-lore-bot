import asyncio
import contextlib
import time
from datetime import datetime, timedelta, timezone

from telegram.error import BadRequest, Forbidden

from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.models import Entry, Node, Reply, Thread
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import InMemoryStore

from .conftest import FakeBot

NOW = datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc)


def _thread(
    msg_id,
    updated,
    author="Alice Adams",
    mailing_lists=frozenset({"netdev"}),
    root_age_hours=None,
):
    """
    Build a one-node thread.

    `updated` is the root's timestamp. When `root_age_hours` is given, it
    instead names how many hours *before* `updated` the root actually landed
    — the one mechanism the whole file uses to build a thread that classifies
    as UPDATED (root older than the cutoff). Existing callers that pass an
    already-old `updated` directly keep working unchanged.
    """
    if root_age_hours is not None:
        updated = updated - timedelta(hours=root_age_hours)
    entry = Entry(
        id=msg_id,
        title=f"[PATCH] {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author=author,
        updated=updated,
        reply=None,
    )
    return Thread(roots=(Node(entry=entry),), mailing_lists=frozenset(mailing_lists))


def _thread_with_reply(
    root_id,
    root_updated,
    reply_id,
    reply_updated,
    author="Alice Adams",
    mailing_lists=frozenset(),
):
    """
    A two-node thread: a root plus one reply.

    Used for the by-id-fetch tests, where a thread needs an old root (so
    classify() calls it UPDATED, not NEW) but a reply at/after the cutoff (so
    it also has genuine activity, per count_entries_since).
    """
    root_entry = Entry(
        id=root_id,
        title=f"[PATCH] {root_id}",
        url=f"https://lore.kernel.org/all/{root_id}",
        author=author,
        updated=root_updated,
        reply=None,
    )
    reply_entry = Entry(
        id=reply_id,
        title=f"Re: [PATCH] {root_id}",
        url=f"https://lore.kernel.org/all/{reply_id}",
        author=author,
        updated=reply_updated,
        reply=Reply(ref=root_id),
    )
    root = Node(entry=root_entry, children=(Node(entry=reply_entry),))
    return Thread(roots=(root,), mailing_lists=frozenset(mailing_lists))


class FakeSource:
    def __init__(self, threads, by_id=None):
        self.threads = threads
        # entry_id -> Thread. An id with no entry here simulates a fetch
        # failure (LoreSource._fetch_thread returns None and logs, rather
        # than raising) -- fetch_threads_by_id must skip it, not crash.
        self.by_id = dict(by_id or {})
        self.calls = []
        self.by_id_calls = []

    def fetch_threads(self, since, mailing_lists):
        self.calls.append(since)
        return list(self.threads)

    def fetch_threads_by_id(self, ids):
        ids = list(ids)
        self.by_id_calls.append(ids)
        return [self.by_id[i] for i in ids if i in self.by_id]


def _broadcaster(threads, store):
    return Broadcaster(
        settings=Settings(loopback_hours=4),
        store=store,
        source=FakeSource(threads),
    )


# -- guard rails ----------------------------------------------------

async def test_no_subscribers_means_nothing_is_fetched():
    source = FakeSource([_thread("a@x.com", NOW)])
    b = Broadcaster(Settings(), InMemoryStore(), source)
    await b.run(FakeBot(), now=NOW)
    assert source.calls == []


async def test_nothing_is_fetched_when_no_subscriber_wants_a_list():
    store = InMemoryStore()
    store.add_subscriber(1)  # subscribed, but zero lists

    source = FakeSource([])
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(FakeBot(), now=NOW)

    assert source.calls == []


async def test_no_threads_means_nothing_is_sent():
    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    bot = FakeBot()

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    markup = bot.sent[1]["reply_markup"]
    assert markup.inline_keyboard[0][0].callback_data == "follow:a@x.com"


async def test_thread_messages_are_sent_as_html_without_link_previews():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    bot = FakeBot()

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    assert bot.sent[1]["parse_mode"] == "HTML"
    assert bot.sent[1]["disable_web_page_preview"] is True


# -- per-subscriber routing ------------------------------------------


async def test_each_subscriber_gets_only_their_own_lists():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["netdev"])
    store.add_subscriber(2)
    store.add_lists(2, ["rcu"])

    threads = [
        _thread("net@example.com", NOW, mailing_lists={"netdev"}),
        _thread("rcu@example.com", NOW, mailing_lists={"rcu"}),
    ]
    bot = FakeBot()
    await _broadcaster(threads, store).run(bot, now=NOW)

    assert any("net@example.com" in t for t in bot.texts_to(1))
    assert not any("rcu@example.com" in t for t in bot.texts_to(1))
    assert any("rcu@example.com" in t for t in bot.texts_to(2))
    assert not any("net@example.com" in t for t in bot.texts_to(2))


async def test_a_cross_posted_thread_reaches_both_subscribers():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["netdev"])
    store.add_subscriber(2)
    store.add_lists(2, ["lkml"])

    threads = [_thread("x@example.com", NOW, mailing_lists={"netdev", "lkml"})]
    bot = FakeBot()
    await _broadcaster(threads, store).run(bot, now=NOW)

    assert any("x@example.com" in t for t in bot.texts_to(1))
    assert any("x@example.com" in t for t in bot.texts_to(2))


async def test_a_personal_block_hides_a_thread_from_only_that_subscriber():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.block(1, "kernel test robot")

    threads = [_thread("bot@example.com", NOW, author="Kernel Test Robot")]
    bot = FakeBot()
    await _broadcaster(threads, store).run(bot, now=NOW)

    assert bot.texts_to(1) == []
    assert any("bot@example.com" in t for t in bot.texts_to(2))


async def test_a_subscriber_with_nothing_visible_gets_no_header():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["rcu"])

    threads = [_thread("net@example.com", NOW, mailing_lists={"netdev"})]
    bot = FakeBot()
    await _broadcaster(threads, store).run(bot, now=NOW)

    assert bot.texts_to(1) == []


async def test_the_header_counts_only_what_that_subscriber_sees():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["netdev"])
    store.add_subscriber(2)
    store.add_lists(2, ["netdev", "rcu"])

    threads = [
        _thread("net@example.com", NOW, mailing_lists={"netdev"}),
        _thread("rcu@example.com", NOW, mailing_lists={"rcu"}),
    ]
    bot = FakeBot()
    await _broadcaster(threads, store).run(bot, now=NOW)

    assert "<b>1</b> new thread(s)" in bot.texts_to(1)[0]
    assert "<b>2</b> new thread(s)" in bot.texts_to(2)[0]


# -- updated threads ------------------------------------------------

async def test_updated_thread_notifies_only_its_followers():
    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    bot = FakeBot()

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)
    assert bot.sent == []


async def test_no_digest_header_when_only_updated_threads_exist():
    store = InMemoryStore(default_lists=("netdev",))
    store.follow("old@x.com", 1)
    bot = FakeBot()

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)
    assert not any("Kernel Lore Digest" in t for t in bot.texts_to(1))


async def test_followers_are_notified_about_threads_outside_their_lists():
    """An explicit follow outranks the follower's list and block settings."""
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["rcu"])
    store.follow("old@example.com", 1)
    store.block(1, "Alice Adams")

    updated = _thread("old@example.com", NOW, mailing_lists={"netdev"}, root_age_hours=48)
    bot = FakeBot()
    await _broadcaster([updated], store).run(bot, now=NOW)

    assert any("Thread update" in t for t in bot.texts_to(1))


# -- blocked authors --------------------------------------------------

async def test_blocked_authors_never_reach_subscribers():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.block(1, "kernel test robot")
    bot = FakeBot()

    threads = [
        _thread("bot@x.com", NOW, author="kernel test robot"),
        _thread("human@x.com", NOW, author="Linus Torvalds"),
    ]
    await _broadcaster(threads, store).run(bot, now=NOW)

    body = "\n".join(bot.texts_to(1))
    assert "human@x.com" in body
    assert "bot@x.com" not in body
    assert "1</b> new thread(s)" in bot.texts_to(1)[0]


# -- blocked subscribers --------------------------------------------

async def test_a_chat_that_blocked_the_bot_is_removed():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.follow("t1", 1)
    bot = FakeBot(fail_for={1})

    await _broadcaster([_thread("a@x.com", NOW)], store).run(bot, now=NOW)

    assert store.subscribers() == {2}
    assert store.followers("t1") == []


async def test_a_blocked_chat_is_not_retried_for_later_threads():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    bot = FakeBot(fail_for={1})

    threads = [_thread("a@x.com", NOW), _thread("b@x.com", NOW)]
    await _broadcaster(threads, store).run(bot, now=NOW)

    # Only the header attempt hits chat 1; after Forbidden it is skipped entirely.
    assert bot.attempts_to(1) == 1
    assert store.subscribers() == set()


async def test_a_follower_who_blocked_the_bot_is_unfollowed():
    store = InMemoryStore(default_lists=("netdev",))
    store.follow("old@x.com", 5)
    bot = FakeBot(fail_for={5})

    old = _thread("old@x.com", NOW - timedelta(hours=10))
    await _broadcaster([old], store).run(bot, now=NOW)

    assert store.followers("old@x.com") == []


# -- fetching followed threads by id (task 6b) -----------------------
#
# Task 6 scoped the scrape to store.all_mailing_lists() -- the union of
# subscribers' *lists*. A followed thread outside every list any subscriber
# wants was never fetched, never classified, and _notify_followers never saw
# it: a subscriber with zero lists who follows a thread got permanent
# silence. Broadcaster.collect now also fetches followed threads directly by
# Message-ID and merges them into the same result set.


async def test_zero_lists_follower_is_notified_when_followed_thread_updates():
    """The regression this task exists to fix."""
    store = InMemoryStore()
    store.follow("f@x.com", 1)  # zero lists -- follow() auto-subscribes

    followed = _thread_with_reply(
        "f@x.com", NOW - timedelta(hours=48), "reply@x.com", NOW
    )
    source = FakeSource([], by_id={"f@x.com": followed})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert any("Thread update" in t for t in bot.texts_to(1))


async def test_followed_thread_with_no_activity_since_cutoff_sends_nothing():
    """
    Every followed thread is now fetched on every run, whether or not
    anything happened. classify() labels a thread UPDATED purely because its
    root predates the cutoff -- it does not check whether any node is
    actually new. Without collect() filtering fetched-by-id threads on
    count_entries_since(), this follower would get a notification on every
    single scrape forever, even though the thread never moved.
    """
    store = InMemoryStore()
    store.follow("stale@x.com", 1)

    stale = _thread("stale@x.com", NOW - timedelta(days=2))  # no new activity
    source = FakeSource([], by_id={"stale@x.com": stale})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert bot.sent == []


async def test_thread_in_both_list_scrape_and_follows_is_fetched_once():
    """
    "both@x.com" is reachable two ways: the list scrape (subscriber 1's
    "netdev" list) and the follow set (subscriber 1 also follows it). It
    must be fetched only via the list scrape -- the by-id path is skipped
    entirely -- and the single resulting thread still carries its list name.
    """
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.follow("both@x.com", 1)

    # Old root (so it classifies UPDATED and _notify_followers sends it),
    # reached only through the list scrape.
    scraped = _thread("both@x.com", NOW, mailing_lists={"netdev"}, root_age_hours=48)
    source = FakeSource([scraped], by_id={"both@x.com": scraped})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert source.by_id_calls == []  # never refetched by id -- already covered
    texts = bot.texts_to(1)
    assert any("both@x.com" in t and "netdev" in t for t in texts)


async def test_a_followed_id_that_is_a_reply_in_an_already_fetched_thread_is_not_refetched():
    """
    A followed id may be a reply inside a thread the list scrape already
    pulled in whole, not just a followed root. Coverage must be checked
    against every node's Message-ID, not just thread ids.
    """
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.follow("reply-in-thread@x.com", 1)

    scraped = _thread_with_reply(
        "root@x.com",
        NOW - timedelta(hours=48),
        "reply-in-thread@x.com",
        NOW,
        mailing_lists={"netdev"},
    )
    source = FakeSource([scraped], by_id={"reply-in-thread@x.com": scraped})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert source.by_id_calls == []


async def test_a_failed_followed_fetch_does_not_affect_a_second_healthy_one():
    store = InMemoryStore()
    store.follow("dead@x.com", 1)
    store.follow("alive@x.com", 1)

    alive = _thread_with_reply(
        "alive@x.com", NOW - timedelta(hours=48), "reply@x.com", NOW
    )
    # "dead@x.com" has no route in by_id: FakeSource skips it, simulating
    # LoreSource._fetch_thread returning None on a FetchError.
    source = FakeSource([], by_id={"alive@x.com": alive})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert any("alive@x.com" in t for t in bot.texts_to(1))


async def test_two_followed_ids_in_the_same_off_list_thread_notify_only_once():
    """
    Two different followed ids can resolve to the same off-list thread --
    e.g. subscriber A follows the root, subscriber B follows one of its
    replies, and the thread is on no list any subscriber wants. Each id is
    looked up separately by Message-ID (lore's mbox endpoint returns the
    whole thread for either), but only one copy may reach classify(), or a
    shared follower gets the update notification twice.
    """
    store = InMemoryStore()
    store.follow("root@x.com", 1)
    store.follow("reply@x.com", 1)

    thread = _thread_with_reply(
        "root@x.com", NOW - timedelta(hours=48), "reply@x.com", NOW
    )
    source = FakeSource([], by_id={"root@x.com": thread, "reply@x.com": thread})
    bot = FakeBot()
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(bot, now=NOW)

    assert len(bot.texts_to(1)) == 1


async def test_nothing_is_fetched_when_there_are_no_lists_and_no_follows():
    store = InMemoryStore()
    store.add_subscriber(1)  # zero lists, zero follows

    source = FakeSource([])
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(FakeBot(), now=NOW)

    assert source.calls == []
    assert source.by_id_calls == []


async def test_the_scrape_runs_for_follows_even_with_no_lists():
    """The list scrape (source.fetch_threads) still runs -- with an empty
    mailing_lists sequence -- when there are follows but no lists, since the
    followed-id fetch alone must not skip the guard that also protects the
    list-scrape call."""
    store = InMemoryStore()
    store.follow("f@x.com", 1)

    source = FakeSource([])
    b = Broadcaster(Settings(loopback_hours=4), store, source)
    await b.run(FakeBot(), now=NOW)

    assert source.calls == [b.cutoff(NOW)]


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
    result = b.collect(b.cutoff(NOW), ("netdev",))
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
        def fetch_threads(self, since, mailing_lists):
            time.sleep(0.05)  # a real, blocking sleep — simulates requests.get()
            return []

    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
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
        def fetch_threads(self, since, mailing_lists):
            events.append("enter")
            time.sleep(0.05)  # real blocking work, like requests.get()
            events.append("exit")
            return []

    store = InMemoryStore(default_lists=("netdev",))
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
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)

    class UnsubscribingSource:
        def fetch_threads(self, since, mailing_lists):
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
    store = InMemoryStore(default_lists=("netdev",))
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
