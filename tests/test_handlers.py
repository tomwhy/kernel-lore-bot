import pytest

from kernel_lore_bot.delivery.handlers import Handlers
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import InMemoryStore

from .conftest import FakeContext, FakeHttpClient, FakeUpdate


def _registry(names=("netdev", "lkml", "linux-media", "linux-input")):
    """A ListRegistry whose fallback is the whole index — no fetch needed."""
    from kernel_lore_bot.sources.lore.index import ListRegistry

    client = FakeHttpClient({})  # every refresh fails; the fallback stands
    return ListRegistry(client, "https://lore.example.org", fallback=names)


def _handlers(store, settings=None, on_scrape=None):
    return Handlers(
        settings=settings or Settings(),
        store=store,
        list_registry=_registry(),
        on_scrape=on_scrape,
    )


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def handlers(store):
    return _handlers(store, settings=Settings(admin_chat_id=99))


# -- /start ---------------------------------------------------------

async def test_start_subscribes_a_new_chat(handlers, store):
    update = FakeUpdate(chat_id=1)
    await handlers.start(update, FakeContext())
    assert store.subscribers() == {1}
    assert update.message.replies[0]["html"] is True
    assert "Welcome" in update.message.replies[0]["text"]


async def test_start_twice_is_idempotent(handlers, store):
    await handlers.start(FakeUpdate(chat_id=1), FakeContext())
    second = FakeUpdate(chat_id=1)
    await handlers.start(second, FakeContext())
    assert store.subscribers() == {1}
    assert "already subscribed" in second.message.replies[0]["text"]


# -- /stop ----------------------------------------------------------

async def test_stop_unsubscribes_and_clears_follows(handlers, store):
    store.follow("t1", 1)
    update = FakeUpdate(chat_id=1)
    await handlers.stop(update, FakeContext())
    assert store.subscribers() == set()
    assert store.followers("t1") == []
    assert "unsubscribed" in update.message.replies[0]["text"]


async def test_stop_warns_that_curation_is_discarded(handlers, store):
    """
    Finding 5: /stop discards lists and blocked authors too (the whole
    Subscriber record is removed), and /start re-seeds all defaults -- but
    the old reply never said so. This does not change what /stop deletes,
    only what it says.
    """
    store.add_subscriber(1)
    store.add_lists(1, ["netdev"])
    store.block(1, "kernel test robot")
    update = FakeUpdate(chat_id=1)
    await handlers.stop(update, FakeContext())
    text = update.message.replies[0]["text"]
    assert "lists" in text.lower()
    assert "blocked addresses" in text.lower()
    # And the actual deletion behavior is unchanged.
    assert store.mailing_lists(1) == set()
    assert store.blocked_authors(1) == set()


async def test_stop_when_not_subscribed_says_so(handlers):
    update = FakeUpdate(chat_id=1)
    await handlers.stop(update, FakeContext())
    assert "weren't subscribed" in update.message.replies[0]["text"]


# -- /status --------------------------------------------------------

async def test_status_reports_subscription_and_follow_count(handlers, store):
    store.add_subscriber(1)
    store.follow("t1", 1)
    store.follow("t2", 1)
    update = FakeUpdate(chat_id=1)
    await handlers.status(update, FakeContext())
    reply = update.message.replies[0]
    assert reply["html"] is True
    assert "subscribed" in reply["text"]
    assert "<b>2</b>" in reply["text"]


async def test_status_for_a_stranger(handlers):
    update = FakeUpdate(chat_id=1)
    await handlers.status(update, FakeContext())
    assert "not subscribed" in update.message.replies[0]["text"]


async def test_status_warns_a_zero_lists_subscriber_that_no_digest_will_arrive(handlers, store):
    """
    Finding 2: /status used to say "✅ You are subscribed..." unconditionally
    -- the exact reassurance someone gets when they run the exact command
    used to check why digests stopped arriving, even though a zero-lists
    subscriber (a state the v2 format deliberately allows) will never
    receive one.
    """
    store.add_subscriber(1)  # InMemoryStore() fixture has no default_lists
    update = FakeUpdate(chat_id=1)
    await handlers.status(update, FakeContext())
    text = update.message.replies[0]["text"]
    assert "No lists" in text
    assert "will not receive a digest" in text


async def test_status_for_a_subscriber_with_lists_reports_counts(handlers, store):
    store.add_subscriber(1)
    store.add_lists(1, ["netdev", "lkml"])
    store.block(1, "kernel test robot")
    update = FakeUpdate(chat_id=1)
    await handlers.status(update, FakeContext())
    text = update.message.replies[0]["text"]
    assert "will not receive a digest" not in text
    assert "<b>2</b>" in text  # 2 lists
    assert "<b>1</b>" in text  # 1 blocked author


# -- /scrape --------------------------------------------------------

async def test_scrape_is_rejected_for_non_admins(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    handlers = _handlers(store, settings=Settings(admin_chat_id=99), on_scrape=on_scrape)
    update = FakeUpdate(chat_id=1)
    await handlers.scrape(update, FakeContext())
    assert called == []
    assert update.message.replies == []


async def test_scrape_runs_for_the_admin(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    handlers = _handlers(store, settings=Settings(admin_chat_id=99), on_scrape=on_scrape)
    update = FakeUpdate(chat_id=99)
    await handlers.scrape(update, FakeContext())
    assert len(called) == 1
    assert "Scraping" in update.message.replies[0]["text"]
    assert "complete" in update.message.replies[1]["text"]


async def test_scrape_reports_failure_without_raising(store):
    async def on_scrape(bot):
        raise RuntimeError("lore is down")

    handlers = _handlers(store, settings=Settings(admin_chat_id=99), on_scrape=on_scrape)
    update = FakeUpdate(chat_id=99)
    await handlers.scrape(update, FakeContext())
    assert "failed" in update.message.replies[-1]["text"]
    assert "lore is down" in update.message.replies[-1]["text"]


async def test_scrape_is_rejected_when_no_admin_is_configured(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    # admin_chat_id defaults to 0, which disables privileged commands.
    handlers = _handlers(store, on_scrape=on_scrape)
    await handlers.scrape(FakeUpdate(chat_id=0), FakeContext())
    assert called == []


# -- buttons --------------------------------------------------------

async def test_follow_button_records_the_follow_and_flips_the_button(handlers, store):
    update = FakeUpdate(chat_id=1, callback_data="follow:t1@x.com")
    context = FakeContext()
    await handlers.on_button(update, context)

    assert store.followers("t1@x.com") == [1]
    assert update.callback_query.answered is True
    flipped = update.callback_query.markups[0].inline_keyboard[0][0]
    assert flipped.callback_data == "unfollow:t1@x.com"
    assert "notified" in context.bot.texts_to(1)[0]


async def test_following_twice_says_already_following(handlers, store):
    store.follow("t1@x.com", 1)
    context = FakeContext()
    await handlers.on_button(FakeUpdate(chat_id=1, callback_data="follow:t1@x.com"), context)
    assert "Already following" in context.bot.texts_to(1)[0]


async def test_unfollow_button_removes_the_follow_and_flips_back(handlers, store):
    store.follow("t1@x.com", 1)
    update = FakeUpdate(chat_id=1, callback_data="unfollow:t1@x.com")
    context = FakeContext()
    await handlers.on_button(update, context)

    assert store.followers("t1@x.com") == []
    flipped = update.callback_query.markups[0].inline_keyboard[0][0]
    assert flipped.callback_data == "follow:t1@x.com"
    assert "Unfollowed" in context.bot.texts_to(1)[0]


async def test_unfollowing_something_you_do_not_follow(handlers):
    context = FakeContext()
    await handlers.on_button(FakeUpdate(chat_id=1, callback_data="unfollow:t1@x.com"), context)
    assert "weren't following" in context.bot.texts_to(1)[0]


async def test_button_still_works_when_the_message_is_too_old_to_edit(handlers, store):
    from telegram.error import TelegramError

    update = FakeUpdate(chat_id=1, callback_data="follow:t1@x.com")
    update.callback_query.edit_error = TelegramError("message can't be edited")
    context = FakeContext()
    await handlers.on_button(update, context)
    # The follow is still recorded and the user still hears back.
    assert store.followers("t1@x.com") == [1]
    assert context.bot.texts_to(1) != []


async def test_expired_callback_data_is_answered_not_crashed(handlers):
    # DEFECT 13: after a restart PTB hands back InvalidCallbackData, not a str.
    class InvalidCallbackData:
        pass

    update = FakeUpdate(chat_id=1, callback_data=InvalidCallbackData())
    await handlers.on_button(update, FakeContext())
    assert update.callback_query.answered is True
    assert "expired" in (update.callback_query.answer_text or "").lower()


async def test_on_button_works_when_message_exposes_only_chat_dot_id(handlers, store):
    # Finding 5: python-telegram-bot 22 hands back an InaccessibleMessage for
    # a deleted/too-old message, which has no .chat_id attribute -- only
    # .chat. FakeQuery.message models that same reduced surface (see
    # tests/conftest.py); this must not raise AttributeError.
    update = FakeUpdate(chat_id=1, callback_data="follow:t1@x.com")
    assert not hasattr(update.callback_query.message, "chat_id")

    await handlers.on_button(update, FakeContext())

    assert store.followers("t1@x.com") == [1]


async def test_unknown_callback_data_is_ignored(handlers, store):
    update = FakeUpdate(chat_id=1, callback_data="garbage")
    await handlers.on_button(update, FakeContext())
    assert store.subscribers() == set()


# -- /lists -----------------------------------------------------------

async def test_lists_requires_a_subscription():
    store = InMemoryStore()
    update, context = FakeUpdate(chat_id=1), FakeContext()

    await _handlers(store).lists(update, context)

    assert "/start" in update.message.replies[0]["text"]


async def test_bare_lists_shows_current_lists():
    store = InMemoryStore(default_lists=("netdev", "lkml"))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = []

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "lkml" in text and "netdev" in text


async def test_lists_add_accepts_a_valid_name():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}
    assert "✅" in update.message.replies[0]["text"]


async def test_lists_add_rejects_an_unknown_name_and_keeps_the_good_ones():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev", "netdevv"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}
    text = update.message.replies[0]["text"]
    assert "netdevv" in text and "❌" in text


async def test_lists_add_did_you_mean_escapes_html_in_index_names():
    """
    Finding 3: list names come from lore's manifest.js.gz, fetched over the
    network -- not a trusted constant. A manifest key like
    "/<b>netdev</b>/git/0.git" (lowercased by fetch_list_names, but never
    HTML-escaped) yields an index name that carries raw markup. Before the
    fix, ', '.join(hints) interpolated that raw name straight into an HTML
    reply, which Telegram would then reject as unparsable markup.
    """
    store = InMemoryStore()
    store.add_subscriber(1)
    handlers = _handlers(
        store, settings=Settings(admin_chat_id=99)
    )
    # Swap in an index that contains a markup-bearing name, as fetch_list_names
    # would produce from a malicious/broken manifest key.
    handlers.list_registry._index = handlers.list_registry._index.__class__(
        frozenset({"<b>netdev</b>", "netdev-real"})
    )
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev"]

    await handlers.lists(update, context)

    text = update.message.replies[0]["text"]
    assert "<b>netdev</b>" not in text  # raw markup must never reach the reply
    assert "&lt;b&gt;netdev&lt;/b&gt;" in text


async def test_lists_add_suggests_near_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "linux"]

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "linux-media" in text and "linux-input" in text


async def test_lists_add_is_case_insensitive():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "NetDev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}


async def test_lists_add_deduplicates_repeated_names_in_the_report():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}
    text = update.message.replies[0]["text"]
    assert text.count("netdev") == 1


async def test_lists_del_removes_and_warns_when_empty():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["del", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == set()
    assert "no lists" in update.message.replies[0]["text"].lower()


async def test_lists_del_does_not_validate_against_the_index():
    """Removing a name you somehow hold must work even if lore dropped it."""
    store = InMemoryStore(default_lists=("retired-list",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["del", "retired-list"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == set()


async def test_lists_del_deduplicates_repeated_names_in_the_report():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["del", "netdev", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == set()
    text = update.message.replies[0]["text"]
    assert text.count("netdev") == 1


async def test_lists_search_shows_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["search", "linux"]

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "linux-media" in text and "linux-input" in text


async def test_lists_search_reports_no_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["search", "zzzz"]

    await _handlers(store).lists(update, context)

    assert "no lists match" in update.message.replies[0]["text"].lower()


async def test_lists_rejects_an_unknown_subcommand():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["frobnicate", "netdev"]

    await _handlers(store).lists(update, context)

    assert "/lists add" in update.message.replies[0]["text"]


async def test_lists_add_without_names_shows_usage():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add"]

    await _handlers(store).lists(update, context)

    assert "/lists add" in update.message.replies[0]["text"]


# -- /filters -----------------------------------------------------------


async def test_filters_requires_a_subscription():
    store = InMemoryStore()
    update, context = FakeUpdate(chat_id=1), FakeContext()

    await _handlers(store).filters(update, context)

    assert "/start" in update.message.replies[0]["text"]


async def test_bare_filters_lists_current_blocks():
    store = InMemoryStore(default_blocks=("lkp@intel.com",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = []

    await _handlers(store).filters(update, context)

    assert "lkp@intel.com" in update.message.replies[0]["text"]


async def test_filters_block_stores_the_address():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "lkp@intel.com"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == {"lkp@intel.com"}


async def test_filters_block_normalises_the_typed_address():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "LKP@Intel.COM"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == {"lkp@intel.com"}


async def test_filters_block_rejects_a_display_name():
    """A name can never match now, so accepting one would store a rule the
    subscriber believes works and which silently never fires."""
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "Kernel", "Test", "Robot"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == set()
    text = update.message.replies[0]["text"]
    assert "address" in text.lower()


async def test_filters_block_rejects_a_single_word_that_is_not_an_address():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "robot"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == set()
    assert "address" in update.message.replies[0]["text"].lower()


async def test_filters_block_reports_a_duplicate():
    store = InMemoryStore(default_blocks=("lkp@intel.com",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "LKP@INTEL.COM"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == {"lkp@intel.com"}
    assert "already" in update.message.replies[0]["text"].lower()


async def test_filters_block_duplicate_echoes_the_stored_spelling():
    """The reply must show what is actually recorded, not what was typed."""
    store = InMemoryStore(default_blocks=("lkp@intel.com",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "LKP@Intel.COM"]

    await _handlers(store).filters(update, context)

    text = update.message.replies[0]["text"]
    assert "lkp@intel.com" in text
    assert "LKP@Intel.COM" not in text


async def test_filters_unblock_removes_case_insensitively():
    store = InMemoryStore(default_blocks=("lkp@intel.com",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["unblock", "LKP@INTEL.COM"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == set()
    assert "✅" in update.message.replies[0]["text"]


async def test_filters_unblock_reports_a_miss():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["unblock", "nobody@example.com"]

    await _handlers(store).filters(update, context)

    assert "ℹ️" in update.message.replies[0]["text"]


async def test_filters_unblock_of_a_stale_name_block_still_works():
    """Migration drops name blocks, but a subscriber may still type one;
    unblocking it must report a clean miss rather than a format error."""
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["unblock", "Kernel", "Test", "Robot"]

    await _handlers(store).filters(update, context)

    assert "ℹ️" in update.message.replies[0]["text"]


async def test_filters_block_without_a_name_shows_usage():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block"]

    await _handlers(store).filters(update, context)

    assert "/filters block" in update.message.replies[0]["text"]


async def test_filters_rejects_an_unknown_subcommand():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["frobnicate", "someone@example.com"]

    await _handlers(store).filters(update, context)

    assert "/filters block" in update.message.replies[0]["text"]


async def test_filters_escapes_html_when_rejecting_a_bad_address():
    """The rejection echoes what the user typed, so it must be escaped."""
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "<b>evil</b>"]

    await _handlers(store).filters(update, context)

    assert "&lt;b&gt;evil&lt;/b&gt;" in update.message.replies[0]["text"]


async def test_filters_escapes_html_in_a_stored_address():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "<b>evil</b>@example.com"]

    await _handlers(store).filters(update, context)

    assert "&lt;b&gt;evil&lt;/b&gt;@example.com" in update.message.replies[0]["text"]
