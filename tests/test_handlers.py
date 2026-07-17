import pytest

from kernel_lore_bot.delivery.handlers import Handlers
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import InMemoryStore

from .conftest import FakeContext, FakeUpdate


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def handlers(store):
    return Handlers(settings=Settings(admin_chat_id=99), store=store)


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


# -- /scrape --------------------------------------------------------

async def test_scrape_is_rejected_for_non_admins(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    handlers = Handlers(Settings(admin_chat_id=99), store, on_scrape=on_scrape)
    update = FakeUpdate(chat_id=1)
    await handlers.scrape(update, FakeContext())
    assert called == []
    assert update.message.replies == []


async def test_scrape_runs_for_the_admin(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    handlers = Handlers(Settings(admin_chat_id=99), store, on_scrape=on_scrape)
    update = FakeUpdate(chat_id=99)
    await handlers.scrape(update, FakeContext())
    assert len(called) == 1
    assert "Scraping" in update.message.replies[0]["text"]
    assert "complete" in update.message.replies[1]["text"]


async def test_scrape_reports_failure_without_raising(store):
    async def on_scrape(bot):
        raise RuntimeError("lore is down")

    handlers = Handlers(Settings(admin_chat_id=99), store, on_scrape=on_scrape)
    update = FakeUpdate(chat_id=99)
    await handlers.scrape(update, FakeContext())
    assert "failed" in update.message.replies[-1]["text"]
    assert "lore is down" in update.message.replies[-1]["text"]


async def test_scrape_is_rejected_when_no_admin_is_configured(store):
    called = []

    async def on_scrape(bot):
        called.append(bot)

    # admin_chat_id defaults to 0, which disables privileged commands.
    handlers = Handlers(Settings(), store, on_scrape=on_scrape)
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
