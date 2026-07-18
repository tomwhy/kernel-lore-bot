from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel_lore_bot.http import FetchError

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lore"


@pytest.fixture
def fixture_text():
    """Read a checked-in lore fixture by filename."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _read


@pytest.fixture
def fixture_bytes():
    """Read a checked-in lore fixture by filename, as raw bytes."""

    def _read(name: str) -> bytes:
        return (FIXTURE_DIR / name).read_bytes()

    return _read


class FakeHttpClient:
    """
    HttpClient backed by canned responses.

    `routes` maps a URL to a list of successive response bodies, so a paginating
    caller hitting the same URL twice gets page 1 then page 2. A route whose
    value is a FetchError instance raises instead.
    """

    def __init__(self, routes: dict[str, object]):
        self.routes = {k: list(v) if isinstance(v, list) else v for k, v in routes.items()}
        self.calls: list[dict] = []

    def get(self, url: str, params: dict | None = None) -> bytes:
        self.calls.append({"url": url, "params": params})
        route = self.routes.get(url)
        if route is None:
            raise FetchError(f"no route for {url}")
        if isinstance(route, FetchError):
            raise route
        if not route:
            raise FetchError(f"route exhausted for {url}")
        return route.pop(0)


@pytest.fixture
def conftest_fake_client():
    return FakeHttpClient


class FakeBot:
    """Records outgoing messages instead of calling Telegram."""

    def __init__(
        self,
        fail_for: set[int] | None = None,
        error_for: dict[int, Exception] | None = None,
    ):
        self.sent: list[dict] = []
        self.attempts: list[int] = []
        self.fail_for = fail_for or set()
        # chat_id -> exception instance to raise instead of sending. For
        # simulating non-Forbidden TelegramErrors (BadRequest, NetworkError,
        # ...) that must NOT be treated as "this chat blocked the bot".
        self.error_for = error_for or {}

    async def send_message(self, chat_id, text, **kwargs):
        self.attempts.append(chat_id)
        if chat_id in self.fail_for:
            from telegram.error import Forbidden

            raise Forbidden("bot was blocked by the user")
        if chat_id in self.error_for:
            raise self.error_for[chat_id]
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    def texts_to(self, chat_id: int) -> list[str]:
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]

    def attempts_to(self, chat_id: int) -> int:
        return sum(1 for c in self.attempts if c == chat_id)


class FakeMessage:
    def __init__(self):
        self.replies: list[dict] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append({"text": text, "html": False, **kwargs})

    async def reply_html(self, text, **kwargs):
        self.replies.append({"text": text, "html": True, **kwargs})


class FakeQuery:
    def __init__(self, data, chat_id):
        self.data = data
        # Only .chat.id is exposed — not .chat_id — matching the surface
        # common to both telegram.Message and telegram.InaccessibleMessage
        # (the latter has no .chat_id). See kernel_lore_bot/delivery/handlers.py.
        self.message = SimpleNamespace(chat=SimpleNamespace(id=chat_id))
        self.answered = False
        self.answer_text: str | None = None
        self.markups: list = []
        self.edit_error: Exception | None = None

    async def answer(self, text=None, **kwargs):
        self.answered = True
        self.answer_text = text

    async def edit_message_reply_markup(self, reply_markup=None):
        if self.edit_error:
            raise self.edit_error
        self.markups.append(reply_markup)


class FakeUpdate:
    def __init__(self, chat_id=1, first_name="Ada", callback_data=None):
        self.effective_chat = SimpleNamespace(id=chat_id)
        self.effective_user = SimpleNamespace(first_name=first_name)
        self.message = FakeMessage()
        self.callback_query = (
            FakeQuery(callback_data, chat_id) if callback_data is not None else None
        )


class FakeContext:
    def __init__(self, bot=None, args=None):
        self.bot = bot or FakeBot()
        self.args = list(args or [])
