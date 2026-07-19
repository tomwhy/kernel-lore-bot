"""Smoke test for kernel_lore_bot.delivery.app.build_application.

app.py wires together Settings/Store/Source/ListRegistry into a live PTB
Application, but had zero test coverage: an earlier change made
`list_registry` a required Handlers argument and app.py silently kept
calling it without one, so the bot raised TypeError on startup for three
whole tasks with the suite green throughout. This test exists to make that
class of regression (missing constructor arg, dropped command registration,
misordered handlers) fail loudly instead of quietly.
"""

from __future__ import annotations

from telegram.ext import CallbackQueryHandler, CommandHandler

from kernel_lore_bot.delivery.app import ADMIN_COMMANDS, PUBLIC_COMMANDS, build_application
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.lore.index import ListRegistry
from kernel_lore_bot.storage import InMemoryStore

from .conftest import FakeHttpClient

# PTB validates the shape of a bot token at ApplicationBuilder.build() time
# (it must look like "<digits>:<35+ chars>"), but makes no network call
# until the application is actually started/polled. This never touches
# the network.
DUMMY_TOKEN = "123456:dummy-token-for-construction"


class FakeSource:
    """Minimal Source stand-in. build_application never calls its methods —
    it only stores the object and hands it to Broadcaster/Handlers — so an
    empty stub is enough to prove no accidental eager call happens."""

    def fetch_threads(self, since, mailing_lists):
        raise AssertionError("build_application must not fetch threads at construction time")

    def fetch_threads_by_id(self, ids):
        raise AssertionError("build_application must not fetch threads at construction time")


def _registry():
    # Every refresh route is missing, so if anything tried to refresh the
    # index it would raise FetchError -- this proves build_application
    # never calls .refresh() itself.
    return ListRegistry(FakeHttpClient({}), "https://lore.example.org", fallback=("netdev",))


def _build_app():
    settings = Settings(telegram_bot_token=DUMMY_TOKEN)
    store = InMemoryStore()
    source = FakeSource()
    list_registry = _registry()
    return build_application(settings, store, source, list_registry)


def test_build_application_does_not_raise():
    # The core regression: Handlers(...) missing a required arg raised
    # TypeError here, and nothing caught it because this test didn't exist.
    app = _build_app()
    assert app is not None


def test_all_expected_commands_are_registered():
    app = _build_app()

    registered_commands: set[str] = set()
    for handler in app.handlers[0]:
        if isinstance(handler, CommandHandler):
            registered_commands |= set(handler.commands)

    assert registered_commands == {
        "start",
        "stop",
        "status",
        "lists",
        "filters",
        "scrape",
    }


def test_public_and_admin_command_menus():
    public_names = {name for name, _ in PUBLIC_COMMANDS}
    admin_names = {name for name, _ in ADMIN_COMMANDS}

    assert "lists" in public_names
    assert "filters" in public_names
    assert "scrape" not in public_names
    assert "scrape" in admin_names


def test_callback_query_handler_registered_after_command_handlers():
    # app.py has a comment: the CallbackQueryHandler (Follow/Unfollow
    # buttons) must come after the command handlers, or PTB would try to
    # match callback-button updates against it before the /commands get a
    # chance. Pin the ordering, not just presence.
    app = _build_app()
    group = app.handlers[0]

    command_indexes = [i for i, h in enumerate(group) if isinstance(h, CommandHandler)]
    callback_indexes = [i for i, h in enumerate(group) if isinstance(h, CallbackQueryHandler)]

    assert command_indexes, "expected at least one CommandHandler"
    assert callback_indexes, "expected a CallbackQueryHandler for Follow/Unfollow buttons"
    assert max(command_indexes) < min(callback_indexes)
