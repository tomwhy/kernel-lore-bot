# Kernel Lore Bot Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the bot into a `kernel_lore_bot` package where all decision logic is pure functions and all I/O (HTTP, disk, Telegram) sits behind small injected interfaces, with a pytest suite covering the logic that is untested today.

**Architecture:** A frozen `Settings` dataclass is built once in `cli.py` and passed explicitly downward. Three protocols isolate the outside world — `HttpClient` (network), `Store` (disk), `Progress` (terminal) — each with a fake for tests. A `Source` protocol produces `Thread` objects; `LoreSource` is the only implementation. Pure modules (`models`, `mbox`, `atom`, `filters`, `digest`, `formatting`) import no I/O at all.

**Tech Stack:** Python 3.12, python-telegram-bot 22.7, requests, tqdm, pytest 9.1.1, pytest-asyncio 1.4.0.

**Design spec:** `docs/superpowers/specs/2026-07-16-refactor-for-extensibility-design.md`

## Global Constraints

- **Python interpreter:** use the project venv at `.venv\Scripts\python.exe` (Python 3.12.10). It already has runtime + test deps installed. All commands below assume CWD = repo root.
- **Run tests as:** `.venv\Scripts\python.exe -m pytest` (bare `pytest` is not on PATH).
- **Git identity is not configured.** Every commit must pass it explicitly:
  `git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "..."`
- **lore.kernel.org returns HTTP 403 without a User-Agent header.** Every request must send `User-Agent: kernel-lore-bot/1.0`. This is verified, not hypothetical.
- **New package name:** `kernel_lore_bot/`. Old top-level modules (`bot.py`, `scraper.py`, `config.py`, `follows.py`, `subscribers.py`, `main.py`) stay in place until Task 14 deletes them.
- **No new runtime dependencies.** Test-only deps go in `requirements-dev.txt`.
- **Preserve exactly:** message wording/layout, command semantics, scrape schedule.
- **Intentional changes only:** the 8 defects listed in the spec + Task 5 (empty-mbox crash).
- **Every task ends green:** `.venv\Scripts\python.exe -m pytest` passes before commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `kernel_lore_bot/settings.py` | `Settings` frozen dataclass; `load_settings(env, secrets_dir)` |
| `kernel_lore_bot/models.py` | `Reply`, `Entry`, `Node`, `Thread`, `ThreadStatus`, `Classified` — pure data |
| `kernel_lore_bot/http.py` | `HttpClient` protocol, `RequestsClient`, `FetchError` |
| `kernel_lore_bot/progress.py` | `Progress`/`ProgressBar` protocols, `TqdmProgress`, `NullProgress` |
| `kernel_lore_bot/sources/base.py` | `Source` protocol |
| `kernel_lore_bot/sources/lore/mbox.py` | pure: `iter_messages`, `parse_message`, `build_thread`, `parse_thread` |
| `kernel_lore_bot/sources/lore/atom.py` | pure: `FeedEntry`, `parse_feed_page`, `FeedParseError` |
| `kernel_lore_bot/sources/lore/source.py` | `LoreSource`: pagination, mbox fetch, cross-list dedupe |
| `kernel_lore_bot/filters.py` | `Filter` protocol, `BlockedAuthors`, `apply_filters` |
| `kernel_lore_bot/digest.py` | pure: `classify(threads, cutoff) -> list[Classified]` |
| `kernel_lore_bot/storage/base.py` | `Store` protocol, `BaseStore` (in-memory core + reverse index) |
| `kernel_lore_bot/storage/memory.py` | `InMemoryStore` |
| `kernel_lore_bot/storage/json_store.py` | `JsonStore`: single-file `state.json`, migration, atomic writes |
| `kernel_lore_bot/delivery/formatting.py` | pure: thread → HTML string |
| `kernel_lore_bot/delivery/keyboards.py` | follow/unfollow markup + callback-data parsing |
| `kernel_lore_bot/delivery/handlers.py` | `Handlers` class: start/stop/status/scrape/button |
| `kernel_lore_bot/delivery/broadcast.py` | `Broadcaster.run(bot)` — scrape-and-send job |
| `kernel_lore_bot/delivery/app.py` | `run_bot(settings, store, source)` wiring |
| `kernel_lore_bot/cli.py` | `main()`, `--dry` |
| `tests/conftest.py` | shared fixtures: `settings`, `store`, `FakeHttpClient`, `FakeBot` |
| `tests/fixtures/lore/*` | real + derived `.mbox` / `.atom` samples |

---

### Task 1: Test scaffolding and Settings

**Files:**
- Create: `requirements-dev.txt`, `pytest.ini`, `kernel_lore_bot/__init__.py`, `kernel_lore_bot/settings.py`
- Test: `tests/__init__.py`, `tests/test_settings.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings` frozen dataclass with fields `telegram_bot_token: str`, `admin_chat_id: int`, `mailing_lists: tuple[str, ...]`, `blocked_authors: tuple[str, ...]`, `loopback_hours: float`, `schedule_interval_hours: float`, `request_timeout: float`, `state_dir: Path`; property `state_file -> Path`. Function `load_settings(env: Mapping[str, str] | None = None, secrets_dir: Path = Path("/run/secrets")) -> Settings`. Constants `DEFAULT_MAILING_LISTS: tuple[str, ...]`, `DEFAULT_BLOCKED_AUTHORS: tuple[str, ...]`, `PLACEHOLDER_TOKEN: str`.

- [ ] **Step 1: Create the dev requirements and pytest config**

`requirements-dev.txt`:
```
-r requirements.txt
pytest==9.1.1
pytest-asyncio==1.4.0
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
asyncio_mode = auto
filterwarnings =
    error::RuntimeWarning
```

- [ ] **Step 2: Create empty package markers**

Create `kernel_lore_bot/__init__.py` containing only:
```python
"""Kernel Lore Telegram bot."""
```

Create `tests/__init__.py` as an empty file.

- [ ] **Step 3: Write the failing test**

`tests/test_settings.py`:
```python
from pathlib import Path

from kernel_lore_bot.settings import (
    DEFAULT_MAILING_LISTS,
    PLACEHOLDER_TOKEN,
    Settings,
    load_settings,
)


def test_defaults_do_not_require_env():
    s = load_settings(env={})
    assert s.telegram_bot_token == PLACEHOLDER_TOKEN
    assert s.admin_chat_id == 0
    assert s.mailing_lists == DEFAULT_MAILING_LISTS
    assert s.loopback_hours == 4.0


def test_env_overrides_defaults():
    s = load_settings(env={
        "TELEGRAM_BOT_TOKEN": "tok",
        "ADMIN_CHAT_ID": "42",
        "SCHEDULE_INTERVAL_HOURS": "0.5",
        "KERNEL_BOT_STATE_DIR": "/tmp/state",
    })
    assert s.telegram_bot_token == "tok"
    assert s.admin_chat_id == 42
    assert s.schedule_interval_hours == 0.5
    assert s.state_dir == Path("/tmp/state")


def test_docker_secret_beats_env(tmp_path):
    (tmp_path / "telegram_bot_token").write_text("  from-secret\n")
    s = load_settings(env={"TELEGRAM_BOT_TOKEN": "from-env"}, secrets_dir=tmp_path)
    assert s.telegram_bot_token == "from-secret"


def test_schedule_interval_defaults_to_loopback_hours():
    s = load_settings(env={"LOOPBACK_HOURS": "6"})
    assert s.loopback_hours == 6.0
    assert s.schedule_interval_hours == 6.0


def test_settings_is_frozen():
    import dataclasses
    import pytest

    s = Settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.admin_chat_id = 1


def test_state_file_lives_under_state_dir():
    s = Settings(state_dir=Path("data"))
    assert s.state_file == Path("data") / "state.json"
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_settings.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.settings'`

- [ ] **Step 5: Write the implementation**

`kernel_lore_bot/settings.py`:
```python
"""Configuration, loaded once at startup and passed explicitly downward."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

PLACEHOLDER_TOKEN = "YOUR_BOT_TOKEN_HERE"

DEFAULT_MAILING_LISTS: tuple[str, ...] = (
    "linux-media",
    "lkml",
    "netdev",
    "io-uring",
    "linux-input",
    "linux-fsdevel",
    "linux-sound",
    "linux-bluetooth",
    "linux-security-module",
    "linux-hardening",
    "linux-mm",
    "linux-modules",
    "netfilter-devel",
    "linux-sctp",
    "rcu",
    "fuse-devel",
    "linux-api",
    "kernel-hardening",
)

DEFAULT_BLOCKED_AUTHORS: tuple[str, ...] = ("kernel test robot",)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    telegram_bot_token: str = PLACEHOLDER_TOKEN
    admin_chat_id: int = 0
    mailing_lists: tuple[str, ...] = DEFAULT_MAILING_LISTS
    blocked_authors: tuple[str, ...] = DEFAULT_BLOCKED_AUTHORS
    loopback_hours: float = 4.0
    schedule_interval_hours: float = 4.0
    request_timeout: float = 15.0
    state_dir: Path = Path("data")

    @property
    def state_file(self) -> Path:
        return self.state_dir / "state.json"


def _read_secret(
    name: str,
    env: Mapping[str, str],
    secrets_dir: Path,
    fallback: str = "",
) -> str:
    """Docker secret file wins over env var, which wins over fallback."""
    try:
        return (secrets_dir / name).read_text().strip()
    except (FileNotFoundError, NotADirectoryError, OSError):
        pass
    return env.get(name.upper(), fallback)


def load_settings(
    env: Mapping[str, str] | None = None,
    secrets_dir: Path = Path("/run/secrets"),
) -> Settings:
    """Build Settings from the environment. The only code that reads the world."""
    if env is None:
        env = os.environ

    loopback = float(env.get("LOOPBACK_HOURS", "4"))

    return Settings(
        telegram_bot_token=_read_secret(
            "telegram_bot_token", env, secrets_dir, PLACEHOLDER_TOKEN
        ),
        admin_chat_id=int(env.get("ADMIN_CHAT_ID", "0")),
        loopback_hours=loopback,
        schedule_interval_hours=float(
            env.get("SCHEDULE_INTERVAL_HOURS", str(loopback))
        ),
        state_dir=Path(env.get("KERNEL_BOT_STATE_DIR", "data")),
    )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_settings.py -v`
Expected: PASS — 6 passed

- [ ] **Step 7: Commit**

```bash
git add requirements-dev.txt pytest.ini kernel_lore_bot/ tests/
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "feat: add Settings dataclass and test scaffolding"
```

---

### Task 2: Data models

**Files:**
- Create: `kernel_lore_bot/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Reply(ref: str)` — frozen.
  - `Entry(id: str, title: str, url: str, author: str, updated: datetime, reply: Reply | None)` — frozen, equality and hash by `id` only; property `is_reply -> bool`.
  - `Node(entry: Entry, children: tuple[Node, ...] = ())` — frozen; method `walk() -> Iterator[Node]` yielding self then descendants.
  - `Thread(roots: tuple[Node, ...], mailing_list: str = "")` — frozen; properties `title`, `author`, `updated`, `url`, `id` delegating to `roots[0].entry`; method `walk() -> Iterator[Node]` over all roots.
  - `ThreadStatus` — `enum.Enum` with members `NEW = "new"`, `UPDATED = "updated"`.
  - `Classified(thread: Thread, status: ThreadStatus)` — frozen.

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:
```python
from datetime import datetime, timezone

from kernel_lore_bot.models import (
    Classified,
    Entry,
    Node,
    Reply,
    Thread,
    ThreadStatus,
)


def _entry(msg_id: str, reply_to: str | None = None) -> Entry:
    return Entry(
        id=msg_id,
        title=f"subject {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author="Someone",
        updated=datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        reply=Reply(ref=reply_to) if reply_to else None,
    )


def test_entry_is_reply_reflects_reply_field():
    assert _entry("a").is_reply is False
    assert _entry("b", reply_to="a").is_reply is True


def test_entry_equality_and_hash_use_id_only():
    left = _entry("same")
    right = Entry(
        id="same",
        title="totally different",
        url="different",
        author="different",
        updated=datetime(1999, 1, 1, tzinfo=timezone.utc),
        reply=None,
    )
    assert left == right
    assert len({left, right}) == 1


def test_entry_not_equal_to_other_types():
    assert _entry("a") != "a"


def test_node_walk_yields_self_then_descendants():
    leaf = Node(entry=_entry("leaf", reply_to="mid"))
    mid = Node(entry=_entry("mid", reply_to="root"), children=(leaf,))
    root = Node(entry=_entry("root"), children=(mid,))
    assert [n.entry.id for n in root.walk()] == ["root", "mid", "leaf"]


def test_thread_properties_delegate_to_first_root():
    root = Node(entry=_entry("root"))
    thread = Thread(roots=(root,), mailing_list="netdev")
    assert thread.id == "root"
    assert thread.title == "subject root"
    assert thread.author == "Someone"
    assert thread.url == "https://lore.kernel.org/all/root"
    assert thread.updated == datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    assert thread.mailing_list == "netdev"


def test_thread_walk_covers_every_root():
    a = Node(entry=_entry("a"))
    b = Node(entry=_entry("b"), children=(Node(entry=_entry("b1", reply_to="b")),))
    thread = Thread(roots=(a, b))
    assert sorted(n.entry.id for n in thread.walk()) == ["a", "b", "b1"]


def test_thread_status_values_match_wire_strings():
    assert ThreadStatus.NEW.value == "new"
    assert ThreadStatus.UPDATED.value == "updated"


def test_classified_pairs_thread_with_status():
    thread = Thread(roots=(Node(entry=_entry("root")),))
    c = Classified(thread=thread, status=ThreadStatus.NEW)
    assert c.thread is thread
    assert c.status is ThreadStatus.NEW
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.models'`

- [ ] **Step 3: Write the implementation**

`kernel_lore_bot/models.py`:
```python
"""Pure data structures. This module imports no I/O."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Optional


@dataclass(frozen=True)
class Reply:
    """The In-Reply-To reference on a non-root entry."""

    ref: str  # Message-ID of the parent, without angle brackets


@dataclass(frozen=True, eq=False)
class Entry:
    """A single email message parsed from an mbox."""

    id: str  # Message-ID, without angle brackets
    title: str
    url: str
    author: str
    updated: datetime
    reply: Optional[Reply]  # None <-> this is a thread root

    @property
    def is_reply(self) -> bool:
        return self.reply is not None

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Entry) and self.id == other.id

    def __hash__(self) -> int:
        return hash(self.id)


@dataclass(frozen=True)
class Node:
    """One message in the thread tree, with its direct replies as children."""

    entry: Entry
    children: tuple[Node, ...] = ()

    def walk(self) -> Iterator[Node]:
        """Yield this node, then every descendant (depth-first)."""
        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(frozen=True)
class Thread:
    """
    An email thread reconstructed from its mbox archive.

    `roots` is normally exactly one node; more than one signals a split or
    malformed thread, which is kept rather than dropped.
    """

    roots: tuple[Node, ...]
    mailing_list: str = ""

    def walk(self) -> Iterator[Node]:
        for root in self.roots:
            yield from root.walk()

    @property
    def title(self) -> str:
        return self.roots[0].entry.title

    @property
    def author(self) -> str:
        return self.roots[0].entry.author

    @property
    def updated(self) -> datetime:
        return self.roots[0].entry.updated

    @property
    def url(self) -> str:
        return self.roots[0].entry.url

    @property
    def id(self) -> str:
        return self.roots[0].entry.id


class ThreadStatus(enum.Enum):
    NEW = "new"
    UPDATED = "updated"


@dataclass(frozen=True)
class Classified:
    """A thread paired with the status it was given for this run."""

    thread: Thread
    status: ThreadStatus
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_models.py -v`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add kernel_lore_bot/models.py tests/test_models.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "feat: add frozen data models with ThreadStatus enum"
```

---

### Task 3: HTTP and Progress boundaries

**Files:**
- Create: `kernel_lore_bot/http.py`, `kernel_lore_bot/progress.py`
- Test: `tests/test_http.py`, `tests/test_progress.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `http.FetchError(Exception)` — raised for any transport failure.
  - `http.USER_AGENT: str = "kernel-lore-bot/1.0"`.
  - `http.HttpClient` protocol: `get(url: str, params: dict | None = None) -> bytes`.
  - `http.RequestsClient(timeout: float = 15.0, user_agent: str = USER_AGENT, session: requests.Session | None = None)` implementing `HttpClient`.
  - `progress.ProgressBar` protocol: `update(n: int = 1) -> None`, `set_note(note: str) -> None`, `close() -> None`.
  - `progress.Progress` protocol: `bar(desc: str, total: int | None = None) -> ProgressBar`.
  - `progress.NullProgress`, `progress.NullBar`, `progress.TqdmProgress`, `progress.TqdmBar`.

**Note on scope:** `HttpClient.get` returns whole bytes, so the per-download byte-level tqdm bar in the old `_fetch_mbox` goes away. Progress is kept at the list/entry level only. This is an intentional cosmetic simplification — it keeps `requests` from leaking past `http.py` so a future NNTP source is not forced to pretend it speaks HTTP.

- [ ] **Step 1: Write the failing test for http**

`tests/test_http.py`:
```python
import pytest
import requests

from kernel_lore_bot.http import USER_AGENT, FetchError, RequestsClient


class _FakeResponse:
    def __init__(self, content=b"body", status=200):
        self.content = content
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(f"{self._status} error")


class _RecordingSession:
    def __init__(self, response=None, exc=None):
        self.response = response or _FakeResponse()
        self.exc = exc
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(
            {"url": url, "params": params, "timeout": timeout, "headers": headers}
        )
        if self.exc:
            raise self.exc
        return self.response


def test_get_returns_body_bytes():
    session = _RecordingSession(_FakeResponse(b"<feed/>"))
    client = RequestsClient(session=session)
    assert client.get("https://lore.kernel.org/x") == b"<feed/>"


def test_get_always_sends_user_agent():
    # lore.kernel.org returns 403 without a User-Agent. This is verified behavior.
    session = _RecordingSession()
    RequestsClient(session=session).get("https://lore.kernel.org/x")
    assert session.calls[0]["headers"]["User-Agent"] == USER_AGENT


def test_get_passes_params_and_timeout():
    session = _RecordingSession()
    RequestsClient(timeout=7.5, session=session).get("u", params={"t": "123"})
    assert session.calls[0]["params"] == {"t": "123"}
    assert session.calls[0]["timeout"] == 7.5


def test_transport_error_becomes_fetch_error():
    session = _RecordingSession(exc=requests.ConnectionError("boom"))
    with pytest.raises(FetchError) as excinfo:
        RequestsClient(session=session).get("https://lore.kernel.org/x")
    assert "https://lore.kernel.org/x" in str(excinfo.value)


def test_http_status_error_becomes_fetch_error():
    session = _RecordingSession(_FakeResponse(status=403))
    with pytest.raises(FetchError):
        RequestsClient(session=session).get("https://lore.kernel.org/x")
```

- [ ] **Step 2: Write the failing test for progress**

`tests/test_progress.py`:
```python
from kernel_lore_bot.progress import NullProgress


def test_null_progress_is_silent_and_chainable(capsys):
    bar = NullProgress().bar("anything", total=10)
    bar.update(3)
    bar.set_note("note")
    bar.close()
    assert capsys.readouterr().err == ""


def test_null_bar_works_as_context_manager():
    with NullProgress().bar("desc") as bar:
        bar.update()
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_http.py tests/test_progress.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.http'`

- [ ] **Step 4: Implement http.py**

`kernel_lore_bot/http.py`:
```python
"""The network boundary. Nothing outside this module imports `requests`."""

from __future__ import annotations

from typing import Protocol

import requests

USER_AGENT = "kernel-lore-bot/1.0"


class FetchError(Exception):
    """Any transport-level failure. Hides the underlying HTTP library."""


class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None) -> bytes: ...


class RequestsClient:
    """HttpClient backed by requests. lore 403s without a User-Agent."""

    def __init__(
        self,
        timeout: float = 15.0,
        user_agent: str = USER_AGENT,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.user_agent = user_agent
        self.session = session if session is not None else requests.Session()

    def get(self, url: str, params: dict | None = None) -> bytes:
        try:
            resp = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )
            resp.raise_for_status()
            return resp.content
        except requests.RequestException as exc:
            raise FetchError(f"GET {url} failed: {exc}") from exc
```

- [ ] **Step 5: Implement progress.py**

`kernel_lore_bot/progress.py`:
```python
"""The terminal boundary, so tests and dry runs are not polluted by tqdm."""

from __future__ import annotations

from typing import Protocol

import tqdm as tqdm_lib


class ProgressBar(Protocol):
    def update(self, n: int = 1) -> None: ...
    def set_note(self, note: str) -> None: ...
    def close(self) -> None: ...
    def __enter__(self) -> "ProgressBar": ...
    def __exit__(self, *exc_info: object) -> None: ...


class Progress(Protocol):
    def bar(self, desc: str, total: int | None = None) -> ProgressBar: ...


class NullBar:
    """Does nothing. Used in tests and dry runs."""

    def update(self, n: int = 1) -> None:
        pass

    def set_note(self, note: str) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> "NullBar":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class NullProgress:
    def bar(self, desc: str, total: int | None = None) -> NullBar:
        return NullBar()


class TqdmBar:
    def __init__(self, desc: str, total: int | None) -> None:
        self._bar = tqdm_lib.tqdm(
            total=total, desc=desc, unit=" entries", dynamic_ncols=True, leave=False
        )

    def update(self, n: int = 1) -> None:
        self._bar.update(n)

    def set_note(self, note: str) -> None:
        self._bar.set_postfix_str(note)

    def close(self) -> None:
        self._bar.close()

    def __enter__(self) -> "TqdmBar":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class TqdmProgress:
    def bar(self, desc: str, total: int | None = None) -> TqdmBar:
        return TqdmBar(desc, total)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_http.py tests/test_progress.py -v`
Expected: PASS — 7 passed

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/http.py kernel_lore_bot/progress.py tests/test_http.py tests/test_progress.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "feat: add HttpClient and Progress boundaries with fakes"
```

---

### Task 4: Real fixtures and characterization tests

**Purpose:** This task is the safety net for the whole refactor. It captures the **current** `scraper.py` behavior in tests *before* any code moves, so Task 5's extraction is proven to preserve behavior rather than merely documented after the fact. Do not skip or reorder this task.

**Files:**
- Create: `tests/fixtures/lore/thread_mt6392.mbox`, `tests/fixtures/lore/thread_single.mbox`, `tests/fixtures/lore/thread_multi_root.mbox`, `tests/fixtures/lore/thread_orphan_reply.mbox`, `tests/fixtures/lore/thread_malformed.mbox`
- Create: `tests/conftest.py`
- Test: `tests/test_characterization_mbox.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (deliberately — it tests the OLD `scraper` module).
- Produces: `conftest.py` fixture `fixture_text` — a callable `(name: str) -> str` reading `tests/fixtures/lore/<name>`. Reused by Tasks 5-7.

- [ ] **Step 1: Download one real thread mbox from lore**

The `-A` User-Agent flag is mandatory; lore returns 403 without it.

```bash
mkdir -p tests/fixtures/lore
curl -s --max-time 60 -A "kernel-lore-bot/1.0" \
  "https://lore.kernel.org/all/20260621081634.467858-1-l.scorcia@gmail.com/t.mbox.gz" \
  -o thread.mbox.gz
gzip -dc thread.mbox.gz > thread.mbox
grep -c "^From mboxrd@z" thread.mbox
```

Expected: `21`

If the thread has aged out of lore (404 or 0 messages), substitute any current thread: fetch `https://lore.kernel.org/linux-input/new.atom` with the same `-A` flag, take any `<link href=".../linux-input/<msgid>/"/>`, and use that `<msgid>`. The exact thread does not matter; the assertions in Step 6 are written against whatever you commit, so update the expected root id/title/author to match.

- [ ] **Step 2: Trim the real mbox to a committable fixture**

Keep the first 6 messages and truncate bodies to 5 lines each, preserving all headers. This keeps the fixture readable while retaining lore's real quirks — notably the mixed `Message-ID:` / `Message-Id:` header spellings.

Save this as `trim_fixture.py` in the repo root, run it, then delete it:

```python
import pathlib
import re

text = pathlib.Path("thread.mbox").read_text(encoding="utf-8", errors="replace")
seps = [m.start() for m in re.finditer(r"^From ", text, re.MULTILINE)]
seps.append(len(text))

out = []
for i in range(min(6, len(seps) - 1)):
    msg = text[seps[i]:seps[i + 1]]
    head, _, body = msg.partition("\n\n")
    body_lines = body.split("\n")[:5]
    out.append(head + "\n\n" + "\n".join(body_lines).rstrip() + "\n\n")

dest = pathlib.Path("tests/fixtures/lore/thread_mt6392.mbox")
dest.write_text("".join(out), encoding="utf-8")
print("messages:", len(out))
```

Run: `.venv\Scripts\python.exe trim_fixture.py`
Expected: `messages: 6`

Then: `rm trim_fixture.py thread.mbox thread.mbox.gz`

- [ ] **Step 3: Verify the fixture kept the mixed-case header quirk**

```bash
grep -c "^Message-ID:" tests/fixtures/lore/thread_mt6392.mbox
grep -c "^Message-Id:" tests/fixtures/lore/thread_mt6392.mbox
```

Expected: both counts >= 1, summing to 6. If the trim kept only one spelling, raise the message count in Step 2 until both appear — this quirk is the whole reason for using real data.

- [ ] **Step 4: Hand-write the four edge-case fixtures**

`tests/fixtures/lore/thread_single.mbox` — one root, no replies:

```
From mboxrd@z Thu Jan  1 00:00:00 1970
From: Alice Adams <alice@example.com>
Subject: [PATCH] solo patch
Date: Mon, 15 Jun 2026 10:00:00 +0000
Message-ID: <solo-1@example.com>

Body of the solo patch.
```

`tests/fixtures/lore/thread_multi_root.mbox` — two messages, neither a reply:

```
From mboxrd@z Thu Jan  1 00:00:00 1970
From: Alice Adams <alice@example.com>
Subject: [PATCH] first root
Date: Mon, 15 Jun 2026 10:00:00 +0000
Message-ID: <root-a@example.com>

First root body.

From mboxrd@z Thu Jan  1 00:00:00 1970
From: Bob Brown <bob@example.com>
Subject: [PATCH] second root
Date: Mon, 15 Jun 2026 11:00:00 +0000
Message-ID: <root-b@example.com>

Second root body.
```

`tests/fixtures/lore/thread_orphan_reply.mbox` — replies to a message not in the file:

```
From mboxrd@z Thu Jan  1 00:00:00 1970
From: Carol Clark <carol@example.com>
Subject: Re: [PATCH] not included here
Date: Mon, 15 Jun 2026 12:00:00 +0000
Message-ID: <orphan-1@example.com>
In-Reply-To: <missing-parent@example.com>

Reply to something outside this mbox.
```

`tests/fixtures/lore/thread_malformed.mbox` — RFC 2047 subject, unparseable date, and a message with no Message-ID:

```
From mboxrd@z Thu Jan  1 00:00:00 1970
From: =?utf-8?q?Bj=C3=B6rn_Andersson?= <bjorn@example.com>
Subject: =?utf-8?q?[PATCH]_caf=C3=A9_support?=
Date: not a real date
Message-Id: <malformed-1@example.com>

Body one.

From mboxrd@z Thu Jan  1 00:00:00 1970
From: Dave Davis <dave@example.com>
Subject: [PATCH] no message id at all
Date: Mon, 15 Jun 2026 13:00:00 +0000

Body two.
```

- [ ] **Step 5: Write conftest.py**

`tests/conftest.py`:

```python
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "lore"


@pytest.fixture
def fixture_text():
    """Read a checked-in lore fixture by filename."""

    def _read(name: str) -> str:
        return (FIXTURE_DIR / name).read_text(encoding="utf-8")

    return _read
```

- [ ] **Step 6: Write the characterization tests against the OLD scraper**

These import the existing top-level `scraper` module on purpose. Task 5 repoints them.

`tests/test_characterization_mbox.py`:

```python
"""
Characterization tests: they lock in the CURRENT behavior of scraper.py so the
extraction in Task 5 can be proven behavior-preserving. Task 5 repoints the
imports at kernel_lore_bot.sources.lore.mbox; the assertions must not change.
"""

from datetime import timezone

import pytest

import scraper


def _entries(text):
    return list(
        filter(None, map(scraper._parse_mbox_message, scraper.iter_mbox_emails(text)))
    )


def test_real_thread_parses_every_message(fixture_text):
    assert len(_entries(fixture_text("thread_mt6392.mbox"))) == 6


def test_real_thread_has_exactly_one_root(fixture_text):
    roots = [e for e in _entries(fixture_text("thread_mt6392.mbox")) if not e.is_reply]
    assert len(roots) == 1
    assert roots[0].id == "20260621081634.467858-1-l.scorcia@gmail.com"


def test_real_thread_root_fields(fixture_text):
    root = next(e for e in _entries(fixture_text("thread_mt6392.mbox")) if not e.is_reply)
    assert root.title == "[PATCH v9 0/9] Add support for MT6392 PMIC"
    assert root.author == "Luca Leonardo Scorcia"
    assert root.updated.tzinfo is not None
    assert root.url == (
        "https://lore.kernel.org/all/20260621081634.467858-1-l.scorcia@gmail.com"
    )


def test_mixed_case_message_id_headers_all_parse(fixture_text):
    # lore emits both "Message-ID:" and "Message-Id:" within one thread.
    assert all(e.id for e in _entries(fixture_text("thread_mt6392.mbox")))


def test_single_message_thread(fixture_text):
    entries = _entries(fixture_text("thread_single.mbox"))
    assert len(entries) == 1
    assert entries[0].is_reply is False


def test_multi_root_thread_yields_two_roots(fixture_text):
    roots = [
        e for e in _entries(fixture_text("thread_multi_root.mbox")) if not e.is_reply
    ]
    assert {r.id for r in roots} == {"root-a@example.com", "root-b@example.com"}


def test_orphan_reply_is_parsed_as_a_reply(fixture_text):
    entries = _entries(fixture_text("thread_orphan_reply.mbox"))
    assert len(entries) == 1
    assert entries[0].is_reply is True
    assert entries[0].reply.ref == "missing-parent@example.com"


def test_rfc2047_subject_is_decoded(fixture_text):
    first = next(
        e
        for e in _entries(fixture_text("thread_malformed.mbox"))
        if e.id == "malformed-1@example.com"
    )
    assert first.title == "[PATCH] café support"


def test_unparseable_date_falls_back_to_utc_now(fixture_text):
    first = next(
        e
        for e in _entries(fixture_text("thread_malformed.mbox"))
        if e.id == "malformed-1@example.com"
    )
    assert first.updated.tzinfo == timezone.utc


def test_message_without_message_id_is_dropped(fixture_text):
    entries = _entries(fixture_text("thread_malformed.mbox"))
    assert [e.id for e in entries] == ["malformed-1@example.com"]


def test_empty_mbox_currently_raises_runtime_error():
    # DEFECT: PEP 479 turns the StopIteration from next(seps) into RuntimeError,
    # which aborts the entire scrape. Task 5 fixes this and inverts this test.
    with pytest.raises(RuntimeError):
        list(scraper.iter_mbox_emails(""))
```

- [ ] **Step 7: Run the characterization tests to verify they PASS against the old code**

Run: `.venv\Scripts\python.exe -m pytest tests/test_characterization_mbox.py -v`
Expected: PASS — 11 passed.

Unlike normal TDD, these must pass immediately: they describe code that already exists. If one fails, the fixture does not match the assertion — fix the *assertion* to match observed reality. The goal here is to capture actual behavior, not desired behavior.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures tests/conftest.py tests/test_characterization_mbox.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "test: characterize current mbox parsing against real lore fixtures"
```

---

### Task 5: Extract mbox parsing (pure)

**Purpose:** Move the mbox logic out of `scraper.py` under the Task 4 safety net, and fix two defects.

**Intentional behavior changes in this task (both are defect fixes, both are tested):**
- **Defect 8 — empty mbox crash.** `iter_messages("")` used to raise `RuntimeError` (PEP 479 converting `StopIteration` from `next(seps)`), which propagated up and aborted the whole scrape. It now yields nothing.
- **Defect 9 — orphaned replies were silently dropped.** The old root rule was `[e for e in entries if not e.is_reply]`, and children were attached only when the parent was present in the same mbox. A reply whose `In-Reply-To` pointed outside the mbox was therefore neither a root nor anybody's child: it disappeared from the tree and from the "N new entries" count. It is now promoted to a root. This changes the tree shape only for threads containing such replies; the `thread_orphan_reply.mbox` fixture produces an identical result either way (old code fell back to `entries[0]`, new code promotes the same message), which is why the Task 4 characterization assertions still hold unchanged.

**Files:**
- Create: `kernel_lore_bot/sources/__init__.py`, `kernel_lore_bot/sources/base.py`, `kernel_lore_bot/sources/lore/__init__.py`, `kernel_lore_bot/sources/lore/mbox.py`
- Modify: `tests/test_characterization_mbox.py` (repoint imports; invert the empty-mbox test)
- Test: `tests/test_mbox.py`

**Interfaces:**
- Consumes: `models.Entry`, `models.Node`, `models.Reply`, `models.Thread` (Task 2).
- Produces:
  - `sources/base.py`: `Source` protocol with `fetch_threads(since: datetime) -> Iterable[Thread]`.
  - `mbox.LORE_BASE_URL: str = "https://lore.kernel.org"`.
  - `mbox.iter_messages(mbox_text: str) -> Iterator[email.message.Message]` — yields nothing for input with no `From ` separator (no exception).
  - `mbox.decode_header_value(raw: str) -> str`.
  - `mbox.parse_message(msg: Message) -> Entry | None`.
  - `mbox.build_thread(entries: list[Entry], mailing_list: str = "") -> Thread | None`.
  - `mbox.parse_thread(mbox_text: str, mailing_list: str = "") -> Thread | None`.

- [ ] **Step 1: Write the failing test for the new module**

`tests/test_mbox.py`:

```python
from kernel_lore_bot.sources.lore import mbox


def test_iter_messages_yields_each_message(fixture_text):
    assert len(list(mbox.iter_messages(fixture_text("thread_mt6392.mbox")))) == 6


def test_iter_messages_on_empty_input_yields_nothing():
    # Regression: this used to raise RuntimeError (PEP 479) and abort the scrape.
    assert list(mbox.iter_messages("")) == []


def test_iter_messages_on_garbage_without_separator_yields_nothing():
    assert list(mbox.iter_messages("<html>503 Service Unavailable</html>")) == []


def test_parse_thread_builds_a_single_root_tree(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"), "linux-input")
    assert len(thread.roots) == 1
    assert thread.id == "20260621081634.467858-1-l.scorcia@gmail.com"
    assert thread.mailing_list == "linux-input"


def test_parse_thread_nests_replies_under_their_parent(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"))
    root = thread.roots[0]
    # every other message in this thread replies directly to the root
    assert len(root.children) == 5
    assert len(list(thread.walk())) == 6


def test_parse_thread_children_are_sorted_by_date(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_mt6392.mbox"))
    dates = [c.entry.updated for c in thread.roots[0].children]
    assert dates == sorted(dates)


def test_parse_thread_keeps_multiple_roots(fixture_text):
    thread = mbox.parse_thread(fixture_text("thread_multi_root.mbox"))
    assert {r.entry.id for r in thread.roots} == {"root-a@example.com", "root-b@example.com"}


def test_parse_thread_falls_back_to_first_message_when_no_root(fixture_text):
    # Every message is a reply to something outside the mbox.
    thread = mbox.parse_thread(fixture_text("thread_orphan_reply.mbox"))
    assert len(thread.roots) == 1
    assert thread.roots[0].entry.id == "orphan-1@example.com"


def test_parse_thread_returns_none_for_empty_input():
    assert mbox.parse_thread("") is None


def test_parse_thread_returns_none_when_nothing_parses(fixture_text):
    # A message with no Message-ID is dropped; if that leaves nothing, no thread.
    assert mbox.parse_thread("From mboxrd@z Thu Jan  1 00:00:00 1970\nSubject: x\n\nbody\n") is None


def test_build_thread_promotes_orphan_replies_to_roots():
    # DEFECT 9 fix: the old code kept a reply only if its parent was present in
    # the same mbox, so a reply pointing outside vanished from the tree entirely
    # and was silently missing from the "N new entries" count.
    from datetime import datetime, timezone

    from kernel_lore_bot.models import Entry, Reply

    def entry(msg_id, ref=None):
        return Entry(
            id=msg_id,
            title=msg_id,
            url="u",
            author="a",
            updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            reply=Reply(ref=ref) if ref else None,
        )

    thread = mbox.build_thread([entry("root"), entry("stray", ref="not-here")])
    assert {r.entry.id for r in thread.roots} == {"root", "stray"}
    assert len(list(thread.walk())) == 2  # neither message is lost


def test_build_thread_returns_none_for_no_entries():
    assert mbox.build_thread([]) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mbox.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.sources'`

- [ ] **Step 3: Create the package markers and the Source protocol**

`kernel_lore_bot/sources/__init__.py`:

```python
"""Thread sources. Each source produces Thread objects from somewhere."""
```

`kernel_lore_bot/sources/lore/__init__.py`:

```python
"""lore.kernel.org source."""
```

`kernel_lore_bot/sources/base.py`:

```python
"""The Source protocol. One implementation today: LoreSource."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Protocol

from kernel_lore_bot.models import Thread


class Source(Protocol):
    def fetch_threads(self, since: datetime) -> Iterable[Thread]:
        """Yield every thread with activity at or after `since`."""
        ...
```

- [ ] **Step 4: Write the mbox implementation**

`kernel_lore_bot/sources/lore/mbox.py`:

```python
"""
Pure mbox parsing. This module performs no I/O.

lore serves each thread as a gzipped mbox at
`<base>/all/<message-id>/t.mbox.gz`, using mboxrd: messages are separated by a
constant `From mboxrd@z ...` line, and body lines beginning with "From " are
escaped to ">From ". That is why splitting on `^From ` is safe here.
"""

from __future__ import annotations

import email
import email.header
import logging
import re
from datetime import datetime, timezone
from email.message import Message
from email.utils import parseaddr, parsedate_to_datetime
from typing import Iterator, Optional

from kernel_lore_bot.models import Entry, Node, Reply, Thread

log = logging.getLogger(__name__)

LORE_BASE_URL = "https://lore.kernel.org"

_MBOX_SEP_RE = re.compile(r"^From ", re.MULTILINE)


def iter_messages(mbox_text: str) -> Iterator[Message]:
    """
    Split an mbox into messages.

    Yields nothing when the text contains no separator line — an empty body or
    an HTML error page from lore must not raise.
    """
    starts = [m.start() for m in _MBOX_SEP_RE.finditer(mbox_text)]
    if not starts:
        return
    bounds = starts + [len(mbox_text)]
    for i in range(len(starts)):
        yield email.message_from_string(mbox_text[bounds[i]:bounds[i + 1]])


def decode_header_value(raw: str) -> str:
    """Decode an RFC 2047 encoded header into plain text."""
    parts = email.header.decode_header(raw)
    return "".join(
        part.decode(enc or "utf-8", errors="replace") if isinstance(part, bytes) else part
        for part, enc in parts
    )


def parse_message(msg: Message) -> Optional[Entry]:
    """Convert one mbox message into an Entry, or None if it is unusable."""
    try:
        msgid = (msg["Message-ID"] or "").strip().strip("<>")
        if not msgid:
            return None

        title = decode_header_value((msg["Subject"] or "").strip())

        display_name, addr = parseaddr(msg["From"] or "")
        author = display_name.strip() or addr.strip() or "Unknown"

        try:
            updated = parsedate_to_datetime(msg["Date"] or "").astimezone(timezone.utc)
        except Exception:
            updated = datetime.now(timezone.utc)

        in_reply_to = (msg["In-Reply-To"] or "").strip().strip("<>")

        return Entry(
            id=msgid,
            title=title,
            url=f"{LORE_BASE_URL}/all/{msgid}",
            author=author,
            updated=updated,
            reply=Reply(ref=in_reply_to) if in_reply_to else None,
        )
    except Exception as exc:  # noqa: BLE001 - one bad message must not kill a thread
        log.debug("Skipping malformed mbox message: %s", exc)
        return None


def build_thread(entries: list[Entry], mailing_list: str = "") -> Optional[Thread]:
    """
    Assemble entries into a thread tree.

    A reply whose In-Reply-To does not resolve inside this mbox is promoted to a
    root rather than dropped. More than one root signals a split thread and is
    kept as-is.
    """
    if not entries:
        return None

    by_id = {e.id: e for e in entries}
    children_map: dict[str, list[Entry]] = {e.id: [] for e in entries}

    for entry in entries:
        if entry.is_reply and entry.reply.ref in children_map:
            children_map[entry.reply.ref].append(entry)

    roots = [e for e in entries if not e.is_reply or e.reply.ref not in by_id]
    if not roots:
        # Every message replies to another in a cycle; fall back to the first.
        roots = [entries[0]]
        log.debug("No root found in thread — using first message as root")
    elif len(roots) > 1:
        log.debug("%d roots found — grouping under a single Thread", len(roots))

    def _build(entry: Entry) -> Node:
        kids = sorted(children_map.get(entry.id, []), key=lambda e: e.updated)
        return Node(entry=entry, children=tuple(_build(k) for k in kids))

    return Thread(roots=tuple(_build(r) for r in roots), mailing_list=mailing_list)


def parse_thread(mbox_text: str, mailing_list: str = "") -> Optional[Thread]:
    """Parse a whole mbox into a Thread, or None if nothing usable is present."""
    entries = [e for e in map(parse_message, iter_messages(mbox_text)) if e is not None]
    return build_thread(entries, mailing_list)
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mbox.py -v`
Expected: PASS — 12 passed

- [ ] **Step 6: Repoint the characterization tests at the new module**

This is the moment the safety net proves the extraction. In `tests/test_characterization_mbox.py`, replace the import and the helper:

```python
from kernel_lore_bot.sources.lore import mbox


def _entries(text):
    return [e for e in map(mbox.parse_message, mbox.iter_messages(text)) if e is not None]
```

Delete `import scraper`. Then invert the empty-mbox test, since Task 5 fixes that defect:

```python
def test_empty_mbox_no_longer_raises():
    # Was: RuntimeError from PEP 479, which aborted the entire scrape.
    assert list(mbox.iter_messages("")) == []
```

Leave every other assertion in the file untouched — that is the point. Update the module docstring to say the tests now cover the extracted module.

- [ ] **Step 7: Run the full suite to verify the extraction preserved behavior**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS — all tests pass, including the 10 unchanged characterization assertions now running against the new module.

If any characterization assertion fails here, the extraction changed behavior. Fix `mbox.py` to match the old behavior; do not edit the assertion.

- [ ] **Step 8: Commit**

```bash
git add kernel_lore_bot/sources tests/test_mbox.py tests/test_characterization_mbox.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract pure mbox parsing; fix empty-mbox RuntimeError"
```

---

### Task 6: Extract Atom feed parsing (pure)

**Purpose:** Turn the feed XML handling into a pure function over bytes.

**Files:**
- Create: `kernel_lore_bot/sources/lore/atom.py`
- Create: `tests/fixtures/lore/new_page1.atom`, `tests/fixtures/lore/new_page2.atom`, `tests/fixtures/lore/new_empty.atom`, `tests/fixtures/lore/not_xml.atom`
- Modify: `tests/conftest.py` (add `fixture_bytes`)
- Test: `tests/test_atom.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `atom.ATOM_NS: str = "http://www.w3.org/2005/Atom"`.
  - `atom.FeedParseError(Exception)`.
  - `atom.FeedEntry(entry_id: str, updated: datetime)` — frozen dataclass.
  - `atom.parse_feed_page(data: bytes) -> list[FeedEntry]` — in document order; raises `FeedParseError` on malformed XML.
- Also produces `conftest.fixture_bytes` — a callable `(name: str) -> bytes`.

**Intentional behavior change (defect 10):** the old `_fetch_list_entries` called `datetime.fromisoformat(updated_raw)` with no guard, so a single malformed `<updated>` raised an uncaught `ValueError` that killed the scrape. Entries with an unparseable or missing date, or with no resolvable message-id in their `<link href>`, are now skipped with a debug log. Malformed XML for the whole page still stops that list (as before), now via `FeedParseError`.

- [ ] **Step 1: Write the atom fixtures**

The real feed nests `<entry>` under `<feed>` with the Atom namespace, puts the message-id in the `<link href>` path, and formats `<updated>` as `2026-07-16T15:55:52Z`. These fixtures mirror that shape with `<content>` stripped, since the parser never reads it.

`tests/fixtures/lore/new_page1.atom`:

```xml
<?xml version="1.0" encoding="us-ascii"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:thr="http://purl.org/syndication/thread/1.0">
  <title>linux-input.vger.kernel.org archive mirror</title>
  <updated>2026-07-16T15:55:52Z</updated>
  <entry>
    <author><name>Lee Jones</name><email>lee@kernel.org</email></author>
    <title>Re: [PATCH v9 0/9] Add support for MT6392 PMIC</title>
    <updated>2026-07-16T15:55:52Z</updated>
    <link href="https://lore.kernel.org/linux-input/newest-1@example.com/"/>
    <id>urn:uuid:3ba6483b-0ff4-5dad-9f3e-76d20551c159</id>
  </entry>
  <entry>
    <author><name>Mario Limonciello</name><email>superm1@kernel.org</email></author>
    <title type="html">Re: [PATCH v4] HID: i2c-hid: Fix &#34;(null)&#34; output</title>
    <updated>2026-07-16T13:50:26Z</updated>
    <link href="https://lore.kernel.org/linux-input/newest-2@example.com/"/>
    <id>urn:uuid:704e576e-c618-56c9-577c-359b47d04750</id>
  </entry>
</feed>
```

`tests/fixtures/lore/new_page2.atom` — the next page, older entries:

```xml
<?xml version="1.0" encoding="us-ascii"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>linux-input.vger.kernel.org archive mirror</title>
  <entry>
    <author><name>Alice Adams</name><email>alice@example.com</email></author>
    <title>[PATCH] older still</title>
    <updated>2026-07-16T10:00:00Z</updated>
    <link href="https://lore.kernel.org/linux-input/older-1@example.com/"/>
    <id>urn:uuid:aaaaaaaa-0000-0000-0000-000000000001</id>
  </entry>
  <entry>
    <author><name>Bob Brown</name><email>bob@example.com</email></author>
    <title>[PATCH] way past the cutoff</title>
    <updated>2026-07-01T09:00:00Z</updated>
    <link href="https://lore.kernel.org/linux-input/ancient-1@example.com/"/>
    <id>urn:uuid:aaaaaaaa-0000-0000-0000-000000000002</id>
  </entry>
</feed>
```

`tests/fixtures/lore/new_empty.atom` — lore's end-of-pagination response:

```xml
<?xml version="1.0" encoding="us-ascii"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>linux-input.vger.kernel.org archive mirror</title>
  <updated>2026-07-16T15:55:52Z</updated>
</feed>
```

`tests/fixtures/lore/not_xml.atom` — what a proxy or outage returns:

```
<html><body><h1>503 Service Unavailable</h1></body></html>
```

- [ ] **Step 2: Add `fixture_bytes` to conftest.py**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def fixture_bytes():
    """Read a checked-in lore fixture by filename, as raw bytes."""

    def _read(name: str) -> bytes:
        return (FIXTURE_DIR / name).read_bytes()

    return _read
```

- [ ] **Step 3: Write the failing test**

`tests/test_atom.py`:

```python
from datetime import datetime, timezone

import pytest

from kernel_lore_bot.sources.lore.atom import FeedParseError, parse_feed_page


def test_parses_every_entry_in_document_order(fixture_bytes):
    entries = parse_feed_page(fixture_bytes("new_page1.atom"))
    assert [e.entry_id for e in entries] == [
        "newest-1@example.com",
        "newest-2@example.com",
    ]


def test_extracts_message_id_from_link_href_path(fixture_bytes):
    first = parse_feed_page(fixture_bytes("new_page1.atom"))[0]
    # href ends in a trailing slash; the id is the last path segment.
    assert first.entry_id == "newest-1@example.com"


def test_parses_zulu_timestamps_as_aware_utc(fixture_bytes):
    first = parse_feed_page(fixture_bytes("new_page1.atom"))[0]
    assert first.updated == datetime(2026, 7, 16, 15, 55, 52, tzinfo=timezone.utc)


def test_empty_feed_yields_no_entries(fixture_bytes):
    assert parse_feed_page(fixture_bytes("new_empty.atom")) == []


def test_malformed_xml_raises_feed_parse_error(fixture_bytes):
    with pytest.raises(FeedParseError):
        parse_feed_page(fixture_bytes("not_xml.atom"))


def test_entry_with_unparseable_date_is_skipped_not_fatal():
    # DEFECT 10: this used to raise an uncaught ValueError and kill the scrape.
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <updated>not-a-date</updated>
        <link href="https://lore.kernel.org/linux-input/bad@example.com/"/>
      </entry>
      <entry>
        <updated>2026-07-16T15:00:00Z</updated>
        <link href="https://lore.kernel.org/linux-input/good@example.com/"/>
      </entry>
    </feed>"""
    assert [e.entry_id for e in parse_feed_page(data)] == ["good@example.com"]


def test_entry_without_link_is_skipped():
    data = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><updated>2026-07-16T15:00:00Z</updated></entry>
    </feed>"""
    assert parse_feed_page(data) == []
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_atom.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.sources.lore.atom'`

- [ ] **Step 5: Write the implementation**

`kernel_lore_bot/sources/lore/atom.py`:

```python
"""Pure parsing of lore's per-list `new.atom` feed. This module performs no I/O."""

from __future__ import annotations

import logging
import pathlib
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

ATOM_NS = "http://www.w3.org/2005/Atom"


class FeedParseError(Exception):
    """The feed page was not parseable XML."""


@dataclass(frozen=True)
class FeedEntry:
    """One `<entry>` in a feed page: just what pagination needs."""

    entry_id: str  # Message-ID taken from the link href
    updated: datetime


def _tag(ns: str, name: str) -> str:
    return f"{{{ns}}}{name}"


def parse_feed_page(data: bytes) -> list[FeedEntry]:
    """
    Parse one feed page into FeedEntry objects, in document order.

    Individual entries missing a usable date or link are skipped: one bad entry
    must not abort a whole list. Malformed XML raises FeedParseError, which the
    caller treats as the end of that list.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise FeedParseError(f"Malformed feed XML: {exc}") from exc

    entries: list[FeedEntry] = []

    for entry_el in root.findall(_tag(ATOM_NS, "entry")):
        updated_raw = (entry_el.findtext(_tag(ATOM_NS, "updated")) or "").strip()
        try:
            updated = datetime.fromisoformat(updated_raw)
        except ValueError:
            log.debug("Skipping feed entry with bad <updated>: %r", updated_raw)
            continue

        link_el = entry_el.find(_tag(ATOM_NS, "link"))
        href = link_el.get("href", "") if link_el is not None else ""
        entry_id = pathlib.Path(urllib.parse.urlparse(href).path).name
        if not entry_id:
            log.debug("Skipping feed entry with no usable link href: %r", href)
            continue

        entries.append(FeedEntry(entry_id=entry_id, updated=updated))

    return entries
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_atom.py -v`
Expected: PASS — 7 passed

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS — everything green.

- [ ] **Step 8: Commit**

```bash
git add kernel_lore_bot/sources/lore/atom.py tests/test_atom.py tests/fixtures tests/conftest.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract pure Atom feed parsing; skip bad entries instead of crashing"
```

---

### Task 7: LoreSource — pagination, mbox fetch, cross-list dedupe

**Purpose:** The orchestration that ties the two pure parsers to the network. This is the subtlest code in the project and has never been tested.

**Files:**
- Create: `kernel_lore_bot/sources/lore/source.py`
- Modify: `tests/conftest.py` (add `FakeHttpClient`)
- Test: `tests/test_lore_source.py`

**Interfaces:**
- Consumes: `http.HttpClient`, `http.FetchError` (Task 3); `progress.Progress`, `progress.NullProgress` (Task 3); `mbox.parse_thread`, `mbox.LORE_BASE_URL` (Task 5); `atom.parse_feed_page`, `atom.FeedParseError`, `atom.FeedEntry` (Task 6); `models.Thread` (Task 2).
- Produces:
  - `source.LoreSource(client, mailing_lists, progress=NullProgress(), base_url=LORE_BASE_URL)` implementing the `Source` protocol.
  - `LoreSource.fetch_threads(since: datetime) -> Iterator[Thread]`.
- Also produces `conftest.FakeHttpClient(routes: dict[str, list[bytes]])` — records `.calls`, pops responses per URL, raises `FetchError` when a URL has no queued response.

**Behavior being preserved from `_fetch_list_entries` (do not "improve" these):**
- Pagination starts at `t = now`, and each subsequent page requests `t = <updated of last entry on the previous page> - 1 second`.
- Iteration stops for that list as soon as an entry older than `since` is seen — the feed is newest-first, so everything after it is older too.
- A page with zero entries ends that list.
- A fetch or parse failure ends that list without killing the other lists.
- `seen` is only populated for threads that actually parsed; an entry whose mbox fails is marked seen so it is not retried within the run.
- A response body that is not gzip is used as-is (lore sometimes serves plain mbox).

**Intentional behavior change (defect 11):** a truncated gzip body makes `gzip.decompress` raise `EOFError`, which is neither `gzip.BadGzipFile` nor an `OSError`, so the old `except gzip.BadGzipFile` did not catch it and a cut connection killed the entire scrape. That thread is now skipped and the run continues.

**Step 5 expects 12 passing tests**, not 11, once the truncated-gzip test below is included.

- [ ] **Step 1: Add FakeHttpClient to conftest.py**

Append to `tests/conftest.py`:

```python
from kernel_lore_bot.http import FetchError


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
```

- [ ] **Step 2: Write the failing test**

`tests/test_lore_source.py`:

```python
import gzip
from datetime import datetime, timedelta, timezone

from kernel_lore_bot.http import FetchError
from kernel_lore_bot.sources.lore.source import LoreSource

BASE = "https://lore.kernel.org"
FEED = f"{BASE}/linux-input/new.atom"

SINCE = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _mbox_gz(text: str) -> bytes:
    return gzip.compress(text.encode("utf-8"))


def _thread_mbox(msg_id: str, date: str = "Thu, 16 Jul 2026 15:00:00 +0000") -> str:
    return (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: Alice Adams <alice@example.com>\n"
        f"Subject: [PATCH] {msg_id}\n"
        f"Date: {date}\n"
        f"Message-ID: <{msg_id}>\n"
        "\n"
        "body\n"
    )


def _feed(*entries: tuple[str, str]) -> bytes:
    body = "".join(
        f'<entry><updated>{updated}</updated>'
        f'<link href="{BASE}/linux-input/{msg_id}/"/></entry>'
        for msg_id, updated in entries
    )
    return (
        '<?xml version="1.0"?>'
        f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'
    ).encode()


def _source(client, lists=("linux-input",)):
    return LoreSource(client=client, mailing_lists=lists, base_url=BASE)


def test_fetches_thread_for_each_feed_entry(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["a@x.com"]
    assert threads[0].mailing_list == "linux-input"


def test_stops_paginating_at_an_entry_older_than_since(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("new@x.com", "2026-07-16T15:00:00Z"),
                    ("old@x.com", "2026-07-01T09:00:00Z"),  # older than SINCE
                    ("never@x.com", "2026-07-16T14:00:00Z"),  # unreachable
                )
            ],
            f"{BASE}/all/new@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("new@x.com"))],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["new@x.com"]
    # It must not have fetched the mbox for the older entry.
    assert not any("old@x.com" in c["url"] for c in client.calls)


def test_requests_next_page_one_second_before_last_entry(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(("a@x.com", "2026-07-16T15:00:00Z")),
                _feed(("b@x.com", "2026-07-16T14:00:00Z")),
                _feed(),
            ],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("a@x.com"))],
            f"{BASE}/all/b@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("b@x.com"))],
        }
    )
    list(_source(client).fetch_threads(SINCE))
    feed_calls = [c for c in client.calls if c["url"] == FEED]
    # Page 2 asks for one second before page 1's last entry (15:00:00 -> 14:59:59).
    assert feed_calls[1]["params"]["t"] == "20260716145959"
    assert feed_calls[2]["params"]["t"] == "20260716135959"


def test_empty_page_ends_the_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [_feed()]})
    assert list(_source(client).fetch_threads(SINCE)) == []
    assert len([c for c in client.calls if c["url"] == FEED]) == 1


def test_same_thread_in_two_lists_is_fetched_once(conftest_fake_client):
    other_feed = f"{BASE}/netdev/new.atom"
    client = conftest_fake_client(
        {
            FEED: [_feed(("dup@x.com", "2026-07-16T15:00:00Z")), _feed()],
            other_feed: [_feed(("dup@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/dup@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("dup@x.com"))],
        }
    )
    threads = list(_source(client, lists=("linux-input", "netdev")).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["dup@x.com"]
    mbox_calls = [c for c in client.calls if "t.mbox.gz" in c["url"]]
    assert len(mbox_calls) == 1


def test_reply_message_id_seen_via_thread_is_not_refetched(conftest_fake_client):
    # The feed lists a reply; its thread mbox contains both root and reply, so
    # the reply's own feed entry must not trigger a second fetch.
    thread_text = _thread_mbox("root@x.com") + (
        "From mboxrd@z Thu Jan  1 00:00:00 1970\n"
        "From: Bob Brown <bob@example.com>\n"
        "Subject: Re: [PATCH] root\n"
        "Date: Thu, 16 Jul 2026 15:30:00 +0000\n"
        "Message-ID: <reply@x.com>\n"
        "In-Reply-To: <root@x.com>\n"
        "\n"
        "reply body\n"
    )
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("root@x.com", "2026-07-16T15:00:00Z"),
                    ("reply@x.com", "2026-07-16T14:30:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/root@x.com/t.mbox.gz": [_mbox_gz(thread_text)],
        }
    )
    threads = list(_source(client).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["root@x.com"]
    assert len([c for c in client.calls if "t.mbox.gz" in c["url"]]) == 1


def test_uncompressed_mbox_is_accepted(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [_feed(("a@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/a@x.com/t.mbox.gz": [_thread_mbox("a@x.com").encode()],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["a@x.com"]


def test_feed_failure_skips_that_list_but_not_the_others(conftest_fake_client):
    other_feed = f"{BASE}/netdev/new.atom"
    client = conftest_fake_client(
        {
            FEED: FetchError("boom"),
            other_feed: [_feed(("ok@x.com", "2026-07-16T15:00:00Z")), _feed()],
            f"{BASE}/all/ok@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("ok@x.com"))],
        }
    )
    threads = list(_source(client, lists=("linux-input", "netdev")).fetch_threads(SINCE))
    assert [t.id for t in threads] == ["ok@x.com"]


def test_malformed_feed_xml_skips_that_list(conftest_fake_client):
    client = conftest_fake_client({FEED: [b"<html>503</html>"]})
    assert list(_source(client).fetch_threads(SINCE)) == []


def test_mbox_fetch_failure_skips_only_that_thread(conftest_fake_client):
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("bad@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/bad@x.com/t.mbox.gz": FetchError("gone"),
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]


def test_empty_mbox_body_skips_only_that_thread(conftest_fake_client):
    # Regression for the defect-8 crash path, now exercised end to end.
    # gzip.decompress(b"") returns b"" rather than raising, so this reaches
    # parse_thread("") -> None.
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("empty@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/empty@x.com/t.mbox.gz": [b""],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]


def test_truncated_gzip_skips_only_that_thread(conftest_fake_client):
    # DEFECT 11: a cut connection yields a truncated gzip. gzip.decompress raises
    # EOFError, which is neither BadGzipFile nor OSError, so the old
    # `except gzip.BadGzipFile` missed it and the whole scrape died.
    truncated = _mbox_gz(_thread_mbox("trunc@x.com"))[:8]
    client = conftest_fake_client(
        {
            FEED: [
                _feed(
                    ("trunc@x.com", "2026-07-16T15:00:00Z"),
                    ("good@x.com", "2026-07-16T14:00:00Z"),
                ),
                _feed(),
            ],
            f"{BASE}/all/trunc@x.com/t.mbox.gz": [truncated],
            f"{BASE}/all/good@x.com/t.mbox.gz": [_mbox_gz(_thread_mbox("good@x.com"))],
        }
    )
    assert [t.id for t in _source(client).fetch_threads(SINCE)] == ["good@x.com"]
```

Add this fixture to `tests/conftest.py` so the tests above can build clients:

```python
@pytest.fixture
def conftest_fake_client():
    return FakeHttpClient
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_lore_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.sources.lore.source'`

- [ ] **Step 4: Write the implementation**

`kernel_lore_bot/sources/lore/source.py`:

```python
"""
LoreSource: the only Source implementation.

Walks each configured list's `new.atom` backwards in time, and for every entry
newer than the cutoff downloads that thread's full mbox and parses it into a
Thread. Threads are deduplicated across lists by message-id, since one thread is
frequently posted to several lists.
"""

from __future__ import annotations

import gzip
import logging
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from kernel_lore_bot.http import FetchError, HttpClient
from kernel_lore_bot.models import Thread
from kernel_lore_bot.progress import NullProgress, Progress
from kernel_lore_bot.sources.lore import mbox as mbox_parser
from kernel_lore_bot.sources.lore.atom import FeedEntry, FeedParseError, parse_feed_page

log = logging.getLogger(__name__)


class LoreSource:
    """Fetches threads from lore.kernel.org."""

    def __init__(
        self,
        client: HttpClient,
        mailing_lists: tuple[str, ...],
        progress: Progress | None = None,
        base_url: str = mbox_parser.LORE_BASE_URL,
    ) -> None:
        self.client = client
        self.mailing_lists = tuple(mailing_lists)
        self.progress = progress if progress is not None else NullProgress()
        self.base_url = base_url.rstrip("/")

    # -- public API ---------------------------------------------------

    def fetch_threads(self, since: datetime) -> Iterator[Thread]:
        """Yield every thread with activity at or after `since`, deduplicated."""
        seen: set[str] = set()

        for list_name in self.mailing_lists:
            with self.progress.bar(f"  {list_name}") as bar:
                for feed_entry in self._iter_feed_entries(list_name, since):
                    bar.update(1)

                    if feed_entry.entry_id in seen:
                        continue

                    thread = self._fetch_thread(feed_entry.entry_id, list_name)
                    if thread is None:
                        seen.add(feed_entry.entry_id)
                        continue

                    seen.update(node.entry.id for node in thread.walk())
                    yield thread

    # -- internals ----------------------------------------------------

    def _iter_feed_entries(self, list_name: str, since: datetime) -> Iterator[FeedEntry]:
        """
        Page through `<base>/<list>/new.atom`, newest first, until an entry older
        than `since` appears or a page comes back empty.
        """
        url = f"{self.base_url}/{list_name}/new.atom"
        timestamp = datetime.now(timezone.utc)

        while True:
            try:
                data = self.client.get(url, params={"t": timestamp.strftime("%Y%m%d%H%M%S")})
                entries = parse_feed_page(data)
            except (FetchError, FeedParseError) as exc:
                log.warning("Skipping list %s at t=%s: %s", list_name, timestamp, exc)
                return

            if not entries:
                return

            for entry in entries:
                if entry.updated < since:
                    return
                yield entry

            # Next page starts just before the oldest entry we just saw.
            timestamp = entries[-1].updated - timedelta(seconds=1)

    def _fetch_thread(self, entry_id: str, list_name: str) -> Optional[Thread]:
        url = f"{self.base_url}/all/{entry_id}/t.mbox.gz"
        try:
            raw = self.client.get(url)
        except FetchError as exc:
            log.warning("Could not fetch mbox %s: %s", url, exc)
            return None

        try:
            raw = gzip.decompress(raw)
        except gzip.BadGzipFile:
            pass  # server sent an uncompressed mbox; use the bytes as-is
        except EOFError as exc:
            # Truncated gzip (connection cut mid-download). Not a BadGzipFile,
            # not even an OSError, so it must be caught by name.
            log.warning("Truncated gzip mbox at %s: %s", url, exc)
            return None

        thread = mbox_parser.parse_thread(raw.decode("utf-8", errors="replace"), list_name)
        if thread is None:
            log.debug("No usable messages in mbox at %s", url)
        return thread
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_lore_source.py -v`
Expected: PASS — 12 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/sources/lore/source.py tests/test_lore_source.py tests/conftest.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract LoreSource with tested pagination and dedupe"
```

---

### Task 8: Storage — Store protocol, InMemoryStore, JsonStore

**Purpose:** Replace `subscribers.py` + `follows.py` with one cached, atomically-written store behind a protocol.

**Files:**
- Create: `kernel_lore_bot/storage/__init__.py`, `kernel_lore_bot/storage/base.py`, `kernel_lore_bot/storage/memory.py`, `kernel_lore_bot/storage/json_store.py`
- Test: `tests/test_store.py`, `tests/test_json_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `storage.base.Store` protocol: `subscribers() -> set[int]`, `add_subscriber(chat_id: int) -> bool`, `remove_subscriber(chat_id: int) -> bool`, `remove_subscribers(chat_ids: Iterable[int]) -> None`, `follow(thread_id: str, chat_id: int) -> bool`, `unfollow(thread_id: str, chat_id: int) -> bool`, `followers(thread_id: str) -> list[int]`, `following_count(chat_id: int) -> int`.
  - `storage.base.BaseStore` — implements all of the above over `dict[int, set[str]]` plus a reverse index; calls `self._flush()` after each mutation (no-op by default).
  - `storage.memory.InMemoryStore(subs: dict[int, set[str]] | None = None)`.
  - `storage.json_store.JsonStore(path: Path)`; `storage.json_store.STATE_VERSION = 1`.
- Re-export from `storage/__init__.py`: `Store`, `InMemoryStore`, `JsonStore`.

**Semantics that must hold (they differ from today's code):**
- A chat can be a subscriber with zero follows; presence as a key means subscribed.
- `follow()` on a non-subscriber implicitly adds them as a subscriber (button presses can arrive from a chat that sent `/stop`).
- `remove_subscriber()` drops the chat and all its follows in **one** write (fixes defect 3).
- Every write is atomic via temp file + `os.replace` (fixes defect 4).
- `followers()` order is unspecified; tests must sort.

- [ ] **Step 1: Write the failing protocol/behavior test**

`tests/test_store.py`. It is parameterized so both implementations must satisfy identical semantics — that is what makes `InMemoryStore` a trustworthy test double.

```python
import pytest

from kernel_lore_bot.storage import InMemoryStore, JsonStore


@pytest.fixture(params=["memory", "json"])
def store(request, tmp_path):
    if request.param == "memory":
        return InMemoryStore()
    return JsonStore(tmp_path / "state.json")


def test_new_store_has_no_subscribers(store):
    assert store.subscribers() == set()


def test_add_subscriber_returns_true_once(store):
    assert store.add_subscriber(1) is True
    assert store.add_subscriber(1) is False
    assert store.subscribers() == {1}


def test_remove_subscriber_returns_whether_they_were_present(store):
    store.add_subscriber(1)
    assert store.remove_subscriber(1) is True
    assert store.remove_subscriber(1) is False
    assert store.subscribers() == set()


def test_remove_subscriber_also_drops_their_follows(store):
    store.add_subscriber(1)
    store.follow("t1", 1)
    store.remove_subscriber(1)
    assert store.followers("t1") == []
    assert store.following_count(1) == 0


def test_remove_subscribers_removes_many(store):
    for chat in (1, 2, 3):
        store.add_subscriber(chat)
    store.remove_subscribers([1, 3])
    assert store.subscribers() == {2}


def test_remove_subscribers_tolerates_unknown_ids(store):
    store.add_subscriber(1)
    store.remove_subscribers([99])
    assert store.subscribers() == {1}


def test_follow_returns_true_only_the_first_time(store):
    store.add_subscriber(1)
    assert store.follow("t1", 1) is True
    assert store.follow("t1", 1) is False


def test_follow_implicitly_subscribes_an_unknown_chat(store):
    assert store.follow("t1", 7) is True
    assert 7 in store.subscribers()


def test_unfollow_returns_whether_they_were_following(store):
    store.follow("t1", 1)
    assert store.unfollow("t1", 1) is True
    assert store.unfollow("t1", 1) is False


def test_unfollow_unknown_thread_is_false(store):
    store.add_subscriber(1)
    assert store.unfollow("never", 1) is False


def test_followers_lists_every_follower_of_a_thread(store):
    store.follow("t1", 1)
    store.follow("t1", 2)
    store.follow("t2", 3)
    assert sorted(store.followers("t1")) == [1, 2]
    assert store.followers("t2") == [3]
    assert store.followers("unknown") == []


def test_following_count_counts_threads_per_chat(store):
    store.follow("t1", 1)
    store.follow("t2", 1)
    store.follow("t1", 2)
    assert store.following_count(1) == 2
    assert store.following_count(2) == 1
    assert store.following_count(999) == 0


def test_unfollow_keeps_the_chat_subscribed(store):
    store.add_subscriber(1)
    store.follow("t1", 1)
    store.unfollow("t1", 1)
    assert store.subscribers() == {1}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.storage'`

- [ ] **Step 3: Write base.py**

`kernel_lore_bot/storage/base.py`:

```python
"""
The disk boundary.

State is subscriber-centric: `{chat_id: {thread_id, ...}}`. A chat present as a
key is subscribed, even with no follows. A reverse index (thread -> chats) is
maintained in memory so the broadcast hot path does not scan subscribers.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Protocol

log = logging.getLogger(__name__)


class Store(Protocol):
    def subscribers(self) -> set[int]: ...
    def add_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscribers(self, chat_ids: Iterable[int]) -> None: ...
    def follow(self, thread_id: str, chat_id: int) -> bool: ...
    def unfollow(self, thread_id: str, chat_id: int) -> bool: ...
    def followers(self, thread_id: str) -> list[int]: ...
    def following_count(self, chat_id: int) -> int: ...


class BaseStore:
    """In-memory implementation of Store. Subclasses add persistence via _flush."""

    def __init__(self, subs: dict[int, set[str]] | None = None) -> None:
        self._subs: dict[int, set[str]] = {
            int(chat): set(threads) for chat, threads in (subs or {}).items()
        }
        self._index: dict[str, set[int]] = defaultdict(set)
        for chat, threads in self._subs.items():
            for thread_id in threads:
                self._index[thread_id].add(chat)

    # -- persistence hook ---------------------------------------------

    def _flush(self) -> None:
        """Called after every mutation. No-op in memory."""

    # -- reads ---------------------------------------------------------

    def subscribers(self) -> set[int]:
        return set(self._subs)

    def followers(self, thread_id: str) -> list[int]:
        return list(self._index.get(thread_id, ()))

    def following_count(self, chat_id: int) -> int:
        return len(self._subs.get(chat_id, ()))

    # -- writes --------------------------------------------------------

    def add_subscriber(self, chat_id: int) -> bool:
        if chat_id in self._subs:
            return False
        self._subs[chat_id] = set()
        self._flush()
        log.info("New subscriber: chat_id=%d (total: %d)", chat_id, len(self._subs))
        return True

    def remove_subscriber(self, chat_id: int) -> bool:
        threads = self._subs.pop(chat_id, None)
        if threads is None:
            return False
        for thread_id in threads:
            followers = self._index.get(thread_id)
            if followers:
                followers.discard(chat_id)
                if not followers:
                    del self._index[thread_id]
        self._flush()
        log.info("Unsubscribed: chat_id=%d (total: %d)", chat_id, len(self._subs))
        return True

    def remove_subscribers(self, chat_ids: Iterable[int]) -> None:
        changed = False
        for chat_id in chat_ids:
            threads = self._subs.pop(chat_id, None)
            if threads is None:
                continue
            changed = True
            for thread_id in threads:
                followers = self._index.get(thread_id)
                if followers:
                    followers.discard(chat_id)
                    if not followers:
                        del self._index[thread_id]
        if changed:
            self._flush()

    def follow(self, thread_id: str, chat_id: int) -> bool:
        threads = self._subs.setdefault(chat_id, set())
        if thread_id in threads:
            return False
        threads.add(thread_id)
        self._index[thread_id].add(chat_id)
        self._flush()
        log.info("chat_id=%d now following thread %s", chat_id, thread_id)
        return True

    def unfollow(self, thread_id: str, chat_id: int) -> bool:
        threads = self._subs.get(chat_id)
        if not threads or thread_id not in threads:
            return False
        threads.discard(thread_id)
        followers = self._index.get(thread_id)
        if followers:
            followers.discard(chat_id)
            if not followers:
                del self._index[thread_id]
        self._flush()
        log.info("chat_id=%d unfollowed thread %s", chat_id, thread_id)
        return True
```

- [ ] **Step 4: Write memory.py and __init__.py**

`kernel_lore_bot/storage/memory.py`:

```python
"""Store implementation with no persistence. Used by tests."""

from __future__ import annotations

from kernel_lore_bot.storage.base import BaseStore


class InMemoryStore(BaseStore):
    """BaseStore with _flush left as a no-op."""
```

`kernel_lore_bot/storage/__init__.py`:

```python
"""Persistence for subscribers and thread follows."""

from kernel_lore_bot.storage.base import BaseStore, Store
from kernel_lore_bot.storage.json_store import STATE_VERSION, JsonStore
from kernel_lore_bot.storage.memory import InMemoryStore

__all__ = ["BaseStore", "InMemoryStore", "JsonStore", "STATE_VERSION", "Store"]
```

- [ ] **Step 5: Write json_store.py**

`kernel_lore_bot/storage/json_store.py`:

```python
"""
JsonStore: the whole state in one file, loaded once, written atomically.

    {
      "version": 1,
      "subscribers": {"12345": {"follows": ["msgid-a@example.com"]}}
    }

One file means one atomic write per mutation, so /stop cannot half-apply. Reads
are served from memory; this is safe because python-telegram-bot runs the job
queue and handlers on a single event loop, so there is exactly one owner.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from kernel_lore_bot.storage.base import BaseStore

log = logging.getLogger(__name__)

STATE_VERSION = 1


def _load_state(path: Path) -> dict[int, set[str]]:
    if not path.exists():
        return _migrate_legacy(path.parent)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {
            int(chat): set(rec.get("follows", []))
            for chat, rec in raw.get("subscribers", {}).items()
        }
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        log.warning("Could not load %s: %s — starting fresh", path, exc)
        return {}


def _migrate_legacy(state_dir: Path) -> dict[int, set[str]]:
    """
    Import the old two-file format, if present.

    The old files are left on disk so the first deploy stays rollback-able.
    """
    subs_file = state_dir / "subscribers.json"
    follows_file = state_dir / "follows.json"
    if not subs_file.exists() and not follows_file.exists():
        return {}

    state: dict[int, set[str]] = {}

    try:
        for chat in json.loads(subs_file.read_text(encoding="utf-8")):
            state[int(chat)] = set()
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError) as exc:
        log.warning("Could not migrate subscribers.json: %s", exc)

    try:
        legacy = json.loads(follows_file.read_text(encoding="utf-8"))
        for thread_id, chats in legacy.items():
            for chat in chats:
                state.setdefault(int(chat), set()).add(thread_id)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError, AttributeError) as exc:
        log.warning("Could not migrate follows.json: %s", exc)

    log.info("Migrated %d subscriber(s) from the legacy two-file state", len(state))
    return state


class JsonStore(BaseStore):
    """Store backed by a single JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        super().__init__(_load_state(self._path))
        if not self._path.exists() and self._subs:
            self._flush()  # persist a completed migration immediately

    def _flush(self) -> None:
        payload = {
            "version": STATE_VERSION,
            "subscribers": {
                str(chat): {"follows": sorted(threads)}
                for chat, threads in sorted(self._subs.items())
            },
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)
```

- [ ] **Step 6: Run the shared behavior tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_store.py -v`
Expected: PASS — 26 passed (13 tests x 2 implementations)

- [ ] **Step 7: Write the JsonStore-specific tests**

`tests/test_json_store.py`:

```python
import json

from kernel_lore_bot.storage import STATE_VERSION, JsonStore


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_state_survives_a_reload(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.add_subscriber(1)
    store.follow("t1", 1)

    reloaded = JsonStore(tmp_path / "state.json")
    assert reloaded.subscribers() == {1}
    assert reloaded.followers("t1") == [1]
    assert reloaded.following_count(1) == 1


def test_written_file_has_the_documented_shape(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.follow("t1", 42)
    assert _state(tmp_path) == {
        "version": STATE_VERSION,
        "subscribers": {"42": {"follows": ["t1"]}},
    }


def test_creates_missing_state_dir(tmp_path):
    store = JsonStore(tmp_path / "nested" / "deeper" / "state.json")
    store.add_subscriber(1)
    assert (tmp_path / "nested" / "deeper" / "state.json").exists()


def test_no_file_is_written_before_the_first_mutation(tmp_path):
    JsonStore(tmp_path / "state.json")
    assert not (tmp_path / "state.json").exists()


def test_corrupt_state_starts_fresh_instead_of_crashing(tmp_path):
    (tmp_path / "state.json").write_text("{not json", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == set()


def test_write_leaves_no_temp_file_behind(tmp_path):
    store = JsonStore(tmp_path / "state.json")
    store.add_subscriber(1)
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_migrates_both_legacy_files(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "follows.json").write_text(
        json.dumps({"t1": [1, 2], "t2": [2]}), encoding="utf-8"
    )

    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {1, 2}
    assert sorted(store.followers("t1")) == [1, 2]
    assert store.following_count(2) == 2


def test_migration_is_written_to_disk_immediately(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    JsonStore(tmp_path / "state.json")
    assert _state(tmp_path)["subscribers"] == {"1": {"follows": []}}


def test_migration_leaves_legacy_files_in_place(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    JsonStore(tmp_path / "state.json")
    assert (tmp_path / "subscribers.json").exists()


def test_migrates_subscribers_only(tmp_path):
    (tmp_path / "subscribers.json").write_text("[5]", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {5}
    assert store.following_count(5) == 0


def test_migrates_follows_only_and_implies_subscription(tmp_path):
    (tmp_path / "follows.json").write_text(json.dumps({"t1": [9]}), encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {9}
    assert store.followers("t1") == [9]


def test_no_legacy_files_means_empty_state(tmp_path):
    assert JsonStore(tmp_path / "state.json").subscribers() == set()


def test_corrupt_legacy_follows_does_not_lose_subscribers(tmp_path):
    (tmp_path / "subscribers.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "follows.json").write_text("{broken", encoding="utf-8")
    store = JsonStore(tmp_path / "state.json")
    assert store.subscribers() == {1}


def test_existing_state_file_wins_over_legacy_files(tmp_path):
    (tmp_path / "subscribers.json").write_text("[111]", encoding="utf-8")
    (tmp_path / "state.json").write_text(
        json.dumps({"version": 1, "subscribers": {"222": {"follows": []}}}),
        encoding="utf-8",
    )
    assert JsonStore(tmp_path / "state.json").subscribers() == {222}
```

- [ ] **Step 8: Run the JsonStore tests and the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_json_store.py -v`
Expected: PASS — 14 passed

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add kernel_lore_bot/storage tests/test_store.py tests/test_json_store.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: single-file JsonStore with atomic writes and legacy migration"
```

---

### Task 9: Filters and digest classification (pure)

**Purpose:** The two decision points of a scrape run — what gets dropped, and what counts as new — become pure functions. `filters.py` is the extension point for the planned richer matching.

**Files:**
- Create: `kernel_lore_bot/filters.py`, `kernel_lore_bot/digest.py`
- Test: `tests/test_filters.py`, `tests/test_digest.py`

**Interfaces:**
- Consumes: `models.Thread`, `models.Classified`, `models.ThreadStatus` (Task 2).
- Produces:
  - `filters.Filter` protocol: `allows(thread: Thread) -> bool`.
  - `filters.BlockedAuthors(names: tuple[str, ...])` — case-insensitive substring match on `thread.author`.
  - `filters.apply_filters(threads: Iterable[Thread], filters: Sequence[Filter]) -> list[Thread]`.
  - `digest.classify(threads: Iterable[Thread], cutoff: datetime) -> list[Classified]`.
  - `digest.count_entries_since(thread: Thread, cutoff: datetime) -> int`.

**Behavior being preserved:** a thread is `NEW` when its **first root's** entry is at or after the cutoff, otherwise `UPDATED`. Sort order is new threads first, then newest first within each group.

- [ ] **Step 1: Write the failing filter test**

`tests/test_filters.py`:

```python
from datetime import datetime, timezone

from kernel_lore_bot.filters import BlockedAuthors, apply_filters
from kernel_lore_bot.models import Entry, Node, Thread


def _thread(author: str) -> Thread:
    entry = Entry(
        id=f"{author}@x.com",
        title="t",
        url="u",
        author=author,
        updated=datetime(2026, 7, 16, tzinfo=timezone.utc),
        reply=None,
    )
    return Thread(roots=(Node(entry=entry),))


def test_blocked_authors_matches_case_insensitively():
    f = BlockedAuthors(("kernel test robot",))
    assert f.allows(_thread("Kernel Test Robot")) is False
    assert f.allows(_thread("Linus Torvalds")) is True


def test_blocked_authors_matches_a_substring():
    f = BlockedAuthors(("robot",))
    assert f.allows(_thread("kernel test robot")) is False


def test_blocked_authors_with_no_names_allows_everything():
    assert BlockedAuthors(()).allows(_thread("anyone")) is True


def test_apply_filters_drops_only_rejected_threads():
    threads = [_thread("kernel test robot"), _thread("Linus Torvalds")]
    kept = apply_filters(threads, [BlockedAuthors(("kernel test robot",))])
    assert [t.author for t in kept] == ["Linus Torvalds"]


def test_apply_filters_requires_every_filter_to_allow():
    class RejectAll:
        def allows(self, thread):
            return False

    kept = apply_filters([_thread("Linus Torvalds")], [BlockedAuthors(()), RejectAll()])
    assert kept == []


def test_apply_filters_with_no_filters_keeps_everything():
    threads = [_thread("a"), _thread("b")]
    assert apply_filters(threads, []) == threads
```

- [ ] **Step 2: Write the failing digest test**

`tests/test_digest.py`:

```python
from datetime import datetime, timedelta, timezone

from kernel_lore_bot.digest import classify, count_entries_since
from kernel_lore_bot.models import Entry, Node, Thread, ThreadStatus

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _entry(msg_id: str, updated: datetime) -> Entry:
    return Entry(
        id=msg_id, title=msg_id, url="u", author="a", updated=updated, reply=None
    )


def _thread(msg_id: str, updated: datetime, children=()) -> Thread:
    return Thread(roots=(Node(entry=_entry(msg_id, updated), children=children),))


def test_thread_at_the_cutoff_is_new():
    result = classify([_thread("a", CUTOFF)], CUTOFF)
    assert result[0].status is ThreadStatus.NEW


def test_thread_one_second_before_the_cutoff_is_updated():
    result = classify([_thread("a", CUTOFF - timedelta(seconds=1))], CUTOFF)
    assert result[0].status is ThreadStatus.UPDATED


def test_new_threads_sort_before_updated_threads():
    threads = [
        _thread("old", CUTOFF - timedelta(hours=1)),
        _thread("new", CUTOFF + timedelta(hours=1)),
    ]
    assert [c.thread.id for c in classify(threads, CUTOFF)] == ["new", "old"]


def test_newest_first_within_each_group():
    threads = [
        _thread("newer", CUTOFF + timedelta(hours=2)),
        _thread("newest", CUTOFF + timedelta(hours=3)),
        _thread("older", CUTOFF - timedelta(hours=3)),
        _thread("oldest", CUTOFF - timedelta(hours=4)),
    ]
    assert [c.thread.id for c in classify(threads, CUTOFF)] == [
        "newest",
        "newer",
        "older",
        "oldest",
    ]


def test_classify_of_nothing_is_nothing():
    assert classify([], CUTOFF) == []


def test_classify_does_not_mutate_the_input_threads():
    thread = _thread("a", CUTOFF)
    classify([thread], CUTOFF)
    assert not hasattr(thread, "status")


def test_count_entries_since_counts_the_whole_subtree():
    leaf = Node(entry=_entry("leaf", CUTOFF + timedelta(hours=1)))
    mid = Node(entry=_entry("mid", CUTOFF - timedelta(hours=5)), children=(leaf,))
    thread = _thread("root", CUTOFF + timedelta(hours=2), children=(mid,))
    # root and leaf are within the cutoff; mid is not.
    assert count_entries_since(thread, CUTOFF) == 2


def test_count_entries_since_counts_across_multiple_roots():
    thread = Thread(
        roots=(
            Node(entry=_entry("a", CUTOFF + timedelta(hours=1))),
            Node(entry=_entry("b", CUTOFF + timedelta(hours=2))),
        )
    )
    assert count_entries_since(thread, CUTOFF) == 2


def test_count_entries_since_can_be_zero():
    thread = _thread("root", CUTOFF - timedelta(days=1))
    assert count_entries_since(thread, CUTOFF) == 0
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_filters.py tests/test_digest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.filters'`

- [ ] **Step 4: Write filters.py**

`kernel_lore_bot/filters.py`:

```python
"""
Thread filters. Pure; no I/O.

To add a filter, write a class with an `allows` method and pass it to
apply_filters. Nothing else needs to change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence

from kernel_lore_bot.models import Thread

log = logging.getLogger(__name__)


class Filter(Protocol):
    def allows(self, thread: Thread) -> bool:
        """Return False to drop the thread."""
        ...


@dataclass(frozen=True)
class BlockedAuthors:
    """Drops threads whose author matches any name, case-insensitive substring."""

    names: tuple[str, ...]

    def allows(self, thread: Thread) -> bool:
        author = thread.author.lower()
        for blocked in self.names:
            if blocked.lower() in author:
                log.debug("Blocked by author filter: %r (%s)", thread.title, thread.author)
                return False
        return True


def apply_filters(threads: Iterable[Thread], filters: Sequence[Filter]) -> list[Thread]:
    """Keep only threads that every filter allows."""
    return [t for t in threads if all(f.allows(t) for f in filters)]
```

- [ ] **Step 5: Write digest.py**

`kernel_lore_bot/digest.py`:

```python
"""
Turning fetched threads into a ranked digest. Pure; no I/O.

A thread is NEW when its root arrived at or after the cutoff, and UPDATED when
the root is older but the thread saw activity within the window. New threads go
to every subscriber; updated ones only to that thread's followers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from kernel_lore_bot.models import Classified, Thread, ThreadStatus


def count_entries_since(thread: Thread, cutoff: datetime) -> int:
    """Count messages anywhere in the thread that arrived at or after cutoff."""
    return sum(1 for node in thread.walk() if node.entry.updated >= cutoff)


def classify(threads: Iterable[Thread], cutoff: datetime) -> list[Classified]:
    """Pair each thread with its status, newest and newest-first."""
    classified = [
        Classified(
            thread=t,
            status=ThreadStatus.NEW if t.updated >= cutoff else ThreadStatus.UPDATED,
        )
        for t in threads
    ]
    classified.sort(
        key=lambda c: (c.status is not ThreadStatus.NEW, -c.thread.updated.timestamp())
    )
    return classified
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_filters.py tests/test_digest.py -v`
Expected: PASS — 15 passed

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/filters.py kernel_lore_bot/digest.py tests/test_filters.py tests/test_digest.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract pure filters and digest classification"
```

---

### Task 10: Message formatting and keyboards (pure)

**Purpose:** Message text becomes a pure function of a thread, so the dry run and the broadcast can share it instead of duplicating it, and hostile subjects can be tested.

**Files:**
- Create: `kernel_lore_bot/delivery/__init__.py`, `kernel_lore_bot/delivery/formatting.py`, `kernel_lore_bot/delivery/keyboards.py`
- Test: `tests/test_formatting.py`, `tests/test_keyboards.py`

**Interfaces:**
- Consumes: `models.Classified`, `models.Thread`, `models.ThreadStatus` (Task 2); `digest.count_entries_since` (Task 9).
- Produces:
  - `formatting.STATUS_BADGE: dict[ThreadStatus, str]`.
  - `formatting.format_thread(classified: Classified, cutoff: datetime) -> str`.
  - `formatting.format_update_notification(thread: Thread) -> str`.
  - `formatting.format_header(total: int, now: datetime) -> str`.
  - `keyboards.CB_FOLLOW: str = "follow:"`, `keyboards.CB_UNFOLLOW: str = "unfollow:"`.
  - `keyboards.follow_keyboard(thread_id: str) -> InlineKeyboardMarkup`.
  - `keyboards.unfollow_keyboard(thread_id: str) -> InlineKeyboardMarkup`.
  - `keyboards.parse_callback(data: object) -> tuple[str, str] | None` — returns `("follow"|"unfollow", thread_id)`, or `None` for anything else (including non-str data).

**Output that must be preserved byte-for-byte** (copied from the current `bot.py`):

```
{badge} <b>{title}</b>
👤 {author}  🕐 {date}
📬 {mailing_list}          <- only when mailing_list is non-empty
<i>... {n} new {entry|entries}</i>   <- only when n > 0
<a href="{url}">🔗 View thread</a>
```

Note the **two spaces** before 🕐. Dates are `%Y-%m-%d %H:%M UTC`. Badges are 🆕 for new and 🔄 for updated.

**Intentional behavior change (defect 12):** the old code interpolated `thread.url` into an `href="..."` attribute unescaped. The URL is built from the Message-ID, which comes from an untrusted email header, so a Message-ID containing a quote could break out of the attribute and corrupt the message (Telegram parses this as HTML). The URL is now escaped with `html.escape(url, quote=True)`.

**Signature change:** `format_header` takes `now` as an argument instead of calling `time.gmtime()` internally, so the output is testable. `format_thread` takes the `Classified` pair rather than reading a mutable `status` attribute off the thread.

- [ ] **Step 1: Write the failing formatting test**

`tests/test_formatting.py`:

```python
from datetime import datetime, timedelta, timezone

from kernel_lore_bot.delivery.formatting import (
    format_header,
    format_thread,
    format_update_notification,
)
from kernel_lore_bot.models import Classified, Entry, Node, Thread, ThreadStatus

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _entry(msg_id, updated, title="A patch", author="Alice Adams", url=None):
    return Entry(
        id=msg_id,
        title=title,
        url=url or f"https://lore.kernel.org/all/{msg_id}",
        author=author,
        updated=updated,
        reply=None,
    )


def _thread(updated=None, mailing_list="netdev", children=(), **kw):
    root = Node(entry=_entry("root@x.com", updated or CUTOFF, **kw), children=children)
    return Thread(roots=(root,), mailing_list=mailing_list)


def _new(thread):
    return Classified(thread=thread, status=ThreadStatus.NEW)


def test_new_thread_layout_is_exact():
    text = format_thread(_new(_thread()), CUTOFF)
    assert text == (
        "🆕 <b>A patch</b>\n"
        "👤 Alice Adams  🕐 2026-07-16 12:00 UTC\n"
        "📬 netdev\n"
        "<i>... 1 new entry</i>\n"
        '<a href="https://lore.kernel.org/all/root@x.com">🔗 View thread</a>'
    )


def test_updated_thread_uses_the_updated_badge():
    thread = _thread(updated=CUTOFF - timedelta(hours=5))
    text = format_thread(Classified(thread=thread, status=ThreadStatus.UPDATED), CUTOFF)
    assert text.startswith("🔄 <b>A patch</b>")


def test_mailing_list_line_is_omitted_when_empty():
    text = format_thread(_new(_thread(mailing_list="")), CUTOFF)
    assert "📬" not in text


def test_entry_count_line_is_omitted_when_nothing_is_new():
    thread = _thread(updated=CUTOFF - timedelta(days=1))
    text = format_thread(Classified(thread=thread, status=ThreadStatus.UPDATED), CUTOFF)
    assert "new entry" not in text and "new entries" not in text


def test_entry_count_is_pluralised():
    reply = Node(entry=_entry("r@x.com", CUTOFF + timedelta(minutes=5)))
    text = format_thread(_new(_thread(children=(reply,))), CUTOFF)
    assert "<i>... 2 new entries</i>" in text


def test_html_special_characters_in_subject_are_escaped():
    # LKML subjects legitimately contain < > &.
    text = format_thread(_new(_thread(title="[PATCH] fix <foo> & <bar>")), CUTOFF)
    assert "&lt;foo&gt; &amp; &lt;bar&gt;" in text
    assert "<foo>" not in text


def test_html_special_characters_in_author_are_escaped():
    text = format_thread(_new(_thread(author="<script>alert(1)</script>")), CUTOFF)
    assert "<script>" not in text


def test_url_is_escaped_inside_the_href_attribute():
    # DEFECT 12: Message-IDs come from an untrusted header.
    hostile = 'https://lore.kernel.org/all/x"><b>oops'
    text = format_thread(_new(_thread(url=hostile)), CUTOFF)
    assert '"><b>oops' not in text
    assert "&quot;&gt;&lt;b&gt;oops" in text


def test_update_notification_layout_is_exact():
    text = format_update_notification(_thread())
    assert text == (
        "🔔 <b>Thread update</b>\n"
        "<b>A patch</b>\n"
        "👤 Alice Adams  🕐 2026-07-16 12:00 UTC\n"
        "📬 netdev\n"
        '<a href="https://lore.kernel.org/all/root@x.com">🔗 View thread</a>'
    )


def test_update_notification_omits_empty_mailing_list():
    assert "📬" not in format_update_notification(_thread(mailing_list=""))


def test_header_layout_is_exact():
    text = format_header(3, datetime(2026, 7, 16, 8, 30, tzinfo=timezone.utc))
    assert text == (
        "🐧 <b>Kernel Lore Digest</b>\n<i>2026-07-16 08:30 UTC</i> — <b>3</b> new thread(s)"
    )
```

- [ ] **Step 2: Write the failing keyboards test**

`tests/test_keyboards.py`:

```python
from kernel_lore_bot.delivery.keyboards import (
    follow_keyboard,
    parse_callback,
    unfollow_keyboard,
)


def test_follow_keyboard_has_one_follow_button():
    markup = follow_keyboard("t1@x.com")
    button = markup.inline_keyboard[0][0]
    assert button.text == "🔔 Follow"
    assert button.callback_data == "follow:t1@x.com"


def test_unfollow_keyboard_has_one_unfollow_button():
    button = unfollow_keyboard("t1@x.com").inline_keyboard[0][0]
    assert button.text == "🔕 Unfollow"
    assert button.callback_data == "unfollow:t1@x.com"


def test_parse_callback_reads_a_follow():
    assert parse_callback("follow:t1@x.com") == ("follow", "t1@x.com")


def test_parse_callback_reads_an_unfollow():
    assert parse_callback("unfollow:t1@x.com") == ("unfollow", "t1@x.com")


def test_parse_callback_round_trips_the_keyboards():
    markup = follow_keyboard("weird:id:with:colons@x.com")
    assert parse_callback(markup.inline_keyboard[0][0].callback_data) == (
        "follow",
        "weird:id:with:colons@x.com",
    )


def test_parse_callback_rejects_unknown_data():
    assert parse_callback("nonsense") is None
    assert parse_callback("") is None


def test_parse_callback_rejects_non_string_data():
    # After a restart PTB hands back an InvalidCallbackData object, not a str.
    assert parse_callback(None) is None
    assert parse_callback(object()) is None
```

- [ ] **Step 3: Run both to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_formatting.py tests/test_keyboards.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.delivery'`

- [ ] **Step 4: Write the package marker and formatting.py**

`kernel_lore_bot/delivery/__init__.py`:

```python
"""Telegram delivery: formatting, keyboards, handlers, broadcast, wiring."""
```

`kernel_lore_bot/delivery/formatting.py`:

```python
"""
Telegram message text. Pure; no I/O and no clock access.

Everything interpolated here comes from an email header, so every field is
HTML-escaped: Telegram renders these messages with parse_mode=HTML.
"""

from __future__ import annotations

import html
from datetime import datetime

from kernel_lore_bot.digest import count_entries_since
from kernel_lore_bot.models import Classified, Thread, ThreadStatus

STATUS_BADGE: dict[ThreadStatus, str] = {
    ThreadStatus.NEW: "🆕",
    ThreadStatus.UPDATED: "🔄",
}

_DATE_FMT = "%Y-%m-%d %H:%M UTC"


def _h(text: str) -> str:
    return html.escape(text)


def _link(url: str) -> str:
    # quote=True matters: the URL carries an untrusted Message-ID.
    return f'<a href="{html.escape(url, quote=True)}">🔗 View thread</a>'


def format_thread(classified: Classified, cutoff: datetime) -> str:
    """The per-thread digest message."""
    thread = classified.thread
    lines = [
        f"{STATUS_BADGE.get(classified.status, '')} <b>{_h(thread.title)}</b>",
        f"👤 {_h(thread.author)}  🕐 {_h(thread.updated.strftime(_DATE_FMT))}",
    ]

    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")

    new_count = count_entries_since(thread, cutoff)
    if new_count:
        noun = "entry" if new_count == 1 else "entries"
        lines.append(f"<i>... {new_count} new {noun}</i>")

    lines.append(_link(thread.url))
    return "\n".join(lines)


def format_update_notification(thread: Thread) -> str:
    """The message a follower gets when a thread they follow moves."""
    lines = [
        "🔔 <b>Thread update</b>",
        f"<b>{_h(thread.title)}</b>",
        f"👤 {_h(thread.author)}  🕐 {_h(thread.updated.strftime(_DATE_FMT))}",
    ]
    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")
    lines.append(_link(thread.url))
    return "\n".join(lines)


def format_header(total: int, now: datetime) -> str:
    """The digest header sent before the per-thread messages."""
    return (
        f"🐧 <b>Kernel Lore Digest</b>\n"
        f"<i>{now.strftime(_DATE_FMT)}</i> — <b>{total}</b> new thread(s)"
    )
```

- [ ] **Step 5: Write keyboards.py**

`kernel_lore_bot/delivery/keyboards.py`:

```python
"""Inline follow/unfollow buttons and their callback data."""

from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

CB_FOLLOW = "follow:"
CB_UNFOLLOW = "unfollow:"


def follow_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔔 Follow", callback_data=f"{CB_FOLLOW}{thread_id}")]]
    )


def unfollow_keyboard(thread_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔕 Unfollow", callback_data=f"{CB_UNFOLLOW}{thread_id}")]]
    )


def parse_callback(data: object) -> Optional[tuple[str, str]]:
    """
    Decode button callback data into (action, thread_id).

    Returns None for anything unrecognised. Note `data` is typed `object`: after
    a restart python-telegram-bot hands back an InvalidCallbackData instance
    rather than a string, and that must not raise.
    """
    if not isinstance(data, str):
        return None
    if data.startswith(CB_FOLLOW):
        return ("follow", data[len(CB_FOLLOW):])
    if data.startswith(CB_UNFOLLOW):
        return ("unfollow", data[len(CB_UNFOLLOW):])
    return None
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_formatting.py tests/test_keyboards.py -v`
Expected: PASS — 18 passed

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/delivery tests/test_formatting.py tests/test_keyboards.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract pure message formatting and keyboards; escape URLs"
```

---

### Task 11: Command and button handlers

**Purpose:** Handlers become a class holding its dependencies, so a test can drive `/start` with an `InMemoryStore` and a fake bot. This is where new commands will be added.

**Files:**
- Create: `kernel_lore_bot/delivery/handlers.py`
- Modify: `tests/conftest.py` (add `FakeBot`, `FakeUpdate`)
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `settings.Settings` (Task 1); `storage.Store` (Task 8); `keyboards.*` (Task 10).
- Produces:
  - `handlers.Handlers(settings: Settings, store: Store, on_scrape: Callable[[Bot], Awaitable[None]] | None = None)`.
  - async methods `start(update, context)`, `stop(update, context)`, `status(update, context)`, `scrape(update, context)`, `on_button(update, context)`.
  - `handlers.WELCOME_TEXT: str`.
- Also produces test doubles in `conftest.py`: `FakeBot` (records `sent`), `FakeMessage`, `FakeUpdate`, `FakeQuery`, `FakeContext`.

**Design note:** `on_scrape` is injected rather than importing `broadcast`, so `handlers` does not depend on `broadcast` and the `/scrape` test does not need a real scrape. Task 12 wires the real broadcaster in.

**Intentional behavior change (defect 14):** the old `_is_admin` did `if chat_id != config.ADMIN_CHAT_ID: return False`. With `ADMIN_CHAT_ID = 0` — which `config.py` documents as "disable privileged commands entirely" — a chat with id 0 would have compared equal and been granted admin. No real Telegram chat has id 0, so this was never exploitable, but the code contradicted its own documentation. `_is_admin` now returns False whenever `admin_chat_id == 0`.

**Intentional behavior change (defect 13):** the old `on_follow_button` did `data = query.data or ""` then `data.startswith(...)`. With `arbitrary_callback_data(True)`, python-telegram-bot keeps callback data in an in-memory cache, so after a restart it hands back an `InvalidCallbackData` **object**, not a string — and `.startswith` raised `AttributeError`, leaving the user with a spinner and no reply. `parse_callback` now returns `None` for non-string data, and the handler answers with an "expired button" notice.

- [ ] **Step 1: Add the Telegram test doubles to conftest.py**

Append to `tests/conftest.py`:

```python
class FakeBot:
    """Records outgoing messages instead of calling Telegram."""

    def __init__(self, fail_for: set[int] | None = None):
        self.sent: list[dict] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_for:
            from telegram.error import Forbidden

            raise Forbidden("bot was blocked by the user")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    def texts_to(self, chat_id: int) -> list[str]:
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]


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
        self.message = SimpleNamespace(chat_id=chat_id)
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
    def __init__(self, bot=None):
        self.bot = bot or FakeBot()
```

Add `from types import SimpleNamespace` to the imports at the top of `conftest.py`.

- [ ] **Step 2: Write the failing test**

`tests/test_handlers.py`:

```python
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


async def test_unknown_callback_data_is_ignored(handlers, store):
    update = FakeUpdate(chat_id=1, callback_data="garbage")
    await handlers.on_button(update, FakeContext())
    assert store.subscribers() == set()
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_handlers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.delivery.handlers'`

- [ ] **Step 4: Write the implementation**

`kernel_lore_bot/delivery/handlers.py`:

```python
"""
Telegram command and button handlers.

Handlers holds its dependencies rather than importing globals, so tests drive it
with an InMemoryStore and a fake bot. Add new commands as methods here and
register them in app.py.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from kernel_lore_bot.delivery.keyboards import follow_keyboard, unfollow_keyboard, parse_callback
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

WELCOME_TEXT = (
    "👋 <b>Welcome to Kernel Lore Bot!</b>\n\n"
    "You'll receive a daily digest of <b>new</b> Linux kernel mailing list threads.\n\n"
    "🆕 = new thread  🔄 = updated thread\n\n"
    "Tap <b>🔔 Follow</b> on any thread to get notified when it receives updates.\n\n"
    "Commands:\n"
    "<code>/start</code>  — subscribe to the daily digest\n"
    "<code>/stop</code>   — unsubscribe\n"
    "<code>/status</code> — check your subscription status\n"
)


class Handlers:
    """Every user-facing interaction lives here."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        on_scrape: Optional[Callable[[object], Awaitable[None]]] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._on_scrape = on_scrape

    # -- commands ------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "there"

        if self.store.add_subscriber(chat_id):
            log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)
            await update.message.reply_html(WELCOME_TEXT)
        else:
            await update.message.reply_text(
                "✅ You're already subscribed! "
                "You'll receive the next digest at the scheduled time."
            )

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "someone"
        log.info("/stop from %s (chat=%d)", first_name, chat_id)

        # One call, one atomic write: subscription and follows go together.
        if self.store.remove_subscriber(chat_id):
            await update.message.reply_text(
                "👋 You've been unsubscribed and removed from all thread follows.\n"
                "Send /start any time to re-subscribe."
            )
        else:
            await update.message.reply_text("ℹ️ You weren't subscribed.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id

        if chat_id not in self.store.subscribers():
            await update.message.reply_text(
                "❌ You are not subscribed. Send /start to subscribe."
            )
            return

        await update.message.reply_html(
            f"✅ You are subscribed to the daily kernel digest.\n"
            f"🔔 Following <b>{self.store.following_count(chat_id)}</b> thread(s) for updates."
        )

    async def scrape(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "someone"

        if not self._is_admin(chat_id):
            log.warning(
                "/scrape ignored for %s (chat=%d) — not admin", first_name, chat_id
            )
            return

        log.info("/scrape triggered by %s (chat=%d)", first_name, chat_id)
        await update.message.reply_text("🔧 Scraping feeds now…")

        try:
            if self._on_scrape is not None:
                await self._on_scrape(context.bot)
            await update.message.reply_text("✅ Scrape complete.")
        except Exception as exc:  # noqa: BLE001 - report to the admin, stay alive
            log.exception("Error during /scrape: %s", exc)
            await update.message.reply_text(f"❌ Scrape failed: {str(exc)[:200]}")

    def _is_admin(self, chat_id: int) -> bool:
        # admin_chat_id == 0 disables privileged commands entirely.
        return self.settings.admin_chat_id != 0 and chat_id == self.settings.admin_chat_id

    # -- buttons -------------------------------------------------------

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        chat_id = query.message.chat_id
        parsed = parse_callback(query.data)

        if parsed is None:
            # Either genuinely unknown data, or an InvalidCallbackData object
            # from a button that predates the last restart.
            await query.answer("This button has expired — please use a newer message.")
            log.warning("Unusable callback data: %r", query.data)
            return

        await query.answer()  # drop the spinner before doing any work

        action, thread_id = parsed
        if action == "follow":
            is_new = self.store.follow(thread_id, chat_id)
            notice = (
                "🔔 You'll be notified when this thread is updated."
                if is_new
                else "🔔 Already following this thread."
            )
            new_markup = unfollow_keyboard(thread_id)
        else:
            was_following = self.store.unfollow(thread_id, chat_id)
            notice = (
                "🔕 Unfollowed. You won't receive further updates for this thread."
                if was_following
                else "ℹ️ You weren't following this thread."
            )
            new_markup = follow_keyboard(thread_id)

        try:
            await query.edit_message_reply_markup(reply_markup=new_markup)
        except TelegramError:
            pass  # message too old to edit — harmless

        await context.bot.send_message(chat_id=chat_id, text=notice)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_handlers.py -v`
Expected: PASS — 17 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/delivery/handlers.py tests/test_handlers.py tests/conftest.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract Handlers class; handle expired callback data"
```

---

### Task 12: Broadcaster

**Purpose:** The scrape-and-send job, with the event-loop-blocking sleep fixed and the whole flow testable against fakes.

**Files:**
- Create: `kernel_lore_bot/delivery/broadcast.py`
- Modify: `tests/conftest.py` (`FakeBot` records failed attempts too)
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Consumes: `settings.Settings` (Task 1); `storage.Store` (Task 8); `sources.base.Source` (Task 5); `filters.Filter`, `filters.apply_filters` (Task 9); `digest.classify` (Task 9); `formatting.*`, `keyboards.*` (Task 10); `models.ThreadStatus` (Task 2).
- Produces:
  - `broadcast.Broadcaster(settings: Settings, store: Store, source: Source, filters: Sequence[Filter] = ())`.
  - `Broadcaster.cutoff(now: datetime | None = None) -> datetime`.
  - `Broadcaster.collect(cutoff: datetime) -> list[Classified]` — fetch, filter, classify. Sync; no Telegram.
  - `async Broadcaster.run(bot, now: datetime | None = None) -> None` — the job.
  - `async broadcast.send_to(bot, chat_id: int, text: str, reply_markup=None) -> bool`.

**Behavior being preserved:**
- Nothing is fetched when there are no subscribers.
- New threads: a header goes to every subscriber, then one message per thread with a Follow button.
- Updated threads: only that thread's followers are notified, with an Unfollow button.
- A chat that has blocked the bot (`Forbidden`) is skipped for the rest of the run and removed at the end, along with its follows.
- A `Forbidden` from a follower notification unfollows that chat from that thread.

**Intentional behavior change (defect 1):** the old job called blocking `time.sleep(0.01)` inside an async function, which froze the entire event loop — including button presses — for the duration of the broadcast. It is now `await asyncio.sleep(0.01)`, which yields to the loop.

- [ ] **Step 1: Teach FakeBot to record attempts**

`sent` only records successes, so it cannot show whether a blocked chat was retried. In `tests/conftest.py`, replace the `FakeBot` from Task 11 with:

```python
class FakeBot:
    """Records outgoing messages instead of calling Telegram."""

    def __init__(self, fail_for: set[int] | None = None):
        self.sent: list[dict] = []
        self.attempts: list[int] = []
        self.fail_for = fail_for or set()

    async def send_message(self, chat_id, text, **kwargs):
        self.attempts.append(chat_id)
        if chat_id in self.fail_for:
            from telegram.error import Forbidden

            raise Forbidden("bot was blocked by the user")
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})

    def texts_to(self, chat_id: int) -> list[str]:
        return [m["text"] for m in self.sent if m["chat_id"] == chat_id]

    def attempts_to(self, chat_id: int) -> int:
        return sum(1 for c in self.attempts if c == chat_id)
```

- [ ] **Step 2: Write the failing test**

`tests/test_broadcast.py`:

```python
from datetime import datetime, timedelta, timezone

from telegram.error import Forbidden

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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broadcast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.delivery.broadcast'`

- [ ] **Step 4: Write the implementation**

`kernel_lore_bot/delivery/broadcast.py`:

```python
"""
The scrape-and-send job.

New threads go to every subscriber; updated threads go only to the people who
followed them. Chats that have blocked the bot are pruned as they are found.
"""

from __future__ import annotations

import asyncio
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


async def send_to(bot, chat_id: int, text: str, reply_markup=None) -> bool:
    """Send one HTML message. False means the chat is gone or blocked us."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_markup=reply_markup,
        )
        return True
    except Forbidden:
        log.warning("chat_id=%d blocked the bot — will unsubscribe", chat_id)
        return False
    except TelegramError as exc:
        log.error("Telegram error sending to chat_id=%d: %s", chat_id, exc)
        return False


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

    def cutoff(self, now: Optional[datetime] = None) -> datetime:
        now = now or datetime.now(timezone.utc)
        return now - timedelta(hours=self.settings.loopback_hours)

    def collect(self, cutoff: datetime) -> list[Classified]:
        """Fetch, filter, and classify. No Telegram, no async."""
        threads = list(self.source.fetch_threads(cutoff))
        kept = apply_filters(threads, self.filters)
        if len(kept) < len(threads):
            log.info(
                "Filtered out %d thread(s) by blocklist (%d remaining)",
                len(threads) - len(kept),
                len(kept),
            )
        return classify(kept, cutoff)

    async def run(self, bot, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(timezone.utc)

        subscriber_ids = self.store.subscribers()
        if not subscriber_ids:
            log.info("No subscribers yet — nothing to send.")
            return

        cutoff = self.cutoff(now)
        classified = self.collect(cutoff)
        if not classified:
            log.info("No new threads to send.")
            return

        new = [c for c in classified if c.status is ThreadStatus.NEW]
        updated = [c for c in classified if c.status is ThreadStatus.UPDATED]

        log.info(
            "Broadcast: %d new thread(s) to %d subscriber(s); "
            "%d updated thread(s) → follower notifications",
            len(new), len(subscriber_ids), len(updated),
        )

        blocked: set[int] = set()
        await self._send_digest(bot, new, subscriber_ids, cutoff, blocked)
        await self._notify_followers(bot, updated)

        if blocked:
            self.store.remove_subscribers(blocked)
            log.info("Auto-removed %d blocked subscriber(s)", len(blocked))

        log.info("Broadcast complete.")

    async def _send_digest(self, bot, new, subscriber_ids, cutoff, blocked) -> None:
        if not new:
            return

        header = format_header(len(new), datetime.now(timezone.utc))
        for chat_id in subscriber_ids:
            if not await send_to(bot, chat_id, header):
                blocked.add(chat_id)

        for i, item in enumerate(new, start=1):
            text = format_thread(item, cutoff)
            markup = follow_keyboard(item.thread.id)

            for chat_id in subscriber_ids:
                if chat_id in blocked:
                    continue
                if not await send_to(bot, chat_id, text, reply_markup=markup):
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
                if not await send_to(bot, chat_id, text, reply_markup=markup):
                    self.store.unfollow(thread_id, chat_id)

            await asyncio.sleep(_YIELD_SECONDS)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_broadcast.py -v`
Expected: PASS — 14 passed

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/delivery/broadcast.py tests/test_broadcast.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: extract Broadcaster; replace blocking sleep with await"
```

---

### Task 13: App wiring and CLI

**Purpose:** Compose everything in exactly one place. `cli.py` is the only module that reads the environment or touches the real network.

**Files:**
- Create: `kernel_lore_bot/delivery/app.py`, `kernel_lore_bot/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1-12.
- Produces:
  - `app.build_application(settings, store, source, filters=())` — returns a configured PTB `Application` without running it.
  - `app.run_bot(settings, store, source, filters=())` — builds and calls `run_polling`.
  - `cli.build_components(settings) -> tuple[Store, LoreSource, list[Filter]]`.
  - `cli.format_dry_run(classified, cutoff) -> str` — the dry-run report as one string.
  - `cli.check_config(settings, dry: bool) -> list[str]` — returns error strings; empty means OK.
  - `cli.main(argv: list[str] | None = None) -> int` — returns an exit code rather than calling `sys.exit`, so it is testable.

**`arbitrary_callback_data` stays enabled.** It looks removable — the callback data is a short string — but Message-IDs can push `follow:<msgid>` past Telegram's 64-byte callback_data limit, which is why it is on. The cost is that buttons from before a restart come back as `InvalidCallbackData`; Task 11's `parse_callback` now handles that gracefully instead of raising.

**Console encoding:** this machine's console encoding is **cp1255**, so `print()` of an emoji raises `UnicodeEncodeError` and would crash `--dry` outright. `main()` reconfigures stdout to UTF-8 with `errors="replace"` before printing. Telegram delivery is unaffected — that path never touches the console.

**Dry-run behavior change:** the old `_dry_run` in `main.py` re-implemented its own formatting. It now renders the same `formatting.format_thread` output the real digest uses, so the dry run shows what would actually be sent.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
from datetime import datetime, timedelta, timezone

from kernel_lore_bot import cli
from kernel_lore_bot.models import Classified, Entry, Node, Thread, ThreadStatus
from kernel_lore_bot.settings import PLACEHOLDER_TOKEN, Settings

CUTOFF = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)


def _classified(msg_id, status, updated=None):
    entry = Entry(
        id=msg_id,
        title=f"[PATCH] {msg_id}",
        url=f"https://lore.kernel.org/all/{msg_id}",
        author="Alice Adams",
        updated=updated or CUTOFF,
        reply=None,
    )
    thread = Thread(roots=(Node(entry=entry),), mailing_list="netdev")
    return Classified(thread=thread, status=status)


# -- config checks --------------------------------------------------

def test_missing_token_is_an_error_in_normal_mode():
    errors = cli.check_config(Settings(telegram_bot_token=PLACEHOLDER_TOKEN), dry=False)
    assert len(errors) == 1
    assert "TELEGRAM_BOT_TOKEN" in errors[0]


def test_missing_token_is_fine_in_dry_mode():
    assert cli.check_config(Settings(telegram_bot_token=PLACEHOLDER_TOKEN), dry=True) == []


def test_a_real_token_passes():
    assert cli.check_config(Settings(telegram_bot_token="123:abc"), dry=False) == []


def test_main_exits_nonzero_when_the_token_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli, "load_settings", lambda: Settings(state_dir=tmp_path)
    )
    assert cli.main([]) == 1


# -- dry run --------------------------------------------------------

def test_dry_run_reports_nothing_found():
    assert "No new threads" in cli.format_dry_run([], CUTOFF)


def test_dry_run_lists_new_threads_using_the_real_formatter():
    report = cli.format_dry_run([_classified("a@x.com", ThreadStatus.NEW)], CUTOFF)
    assert "1 new thread(s)" in report
    # The same markup the digest would send.
    assert "🆕 <b>[PATCH] a@x.com</b>" in report


def test_dry_run_separates_updated_threads():
    items = [
        _classified("new@x.com", ThreadStatus.NEW),
        _classified("old@x.com", ThreadStatus.UPDATED, CUTOFF - timedelta(days=1)),
    ]
    report = cli.format_dry_run(items, CUTOFF)
    assert "1 updated thread(s)" in report
    assert "🔄 <b>[PATCH] old@x.com</b>" in report


def test_dry_run_with_only_updated_threads_says_zero_new():
    items = [_classified("old@x.com", ThreadStatus.UPDATED, CUTOFF - timedelta(days=1))]
    report = cli.format_dry_run(items, CUTOFF)
    assert "0 new thread(s)" in report


# -- component wiring -----------------------------------------------

def test_build_components_returns_a_store_source_and_filters(tmp_path):
    settings = Settings(
        state_dir=tmp_path, mailing_lists=("netdev",), blocked_authors=("robot",)
    )
    store, source, filters = cli.build_components(settings)
    assert store.subscribers() == set()
    assert source.mailing_lists == ("netdev",)
    assert len(filters) == 1
    assert filters[0].names == ("robot",)


def test_build_components_makes_no_filters_when_none_configured(tmp_path):
    settings = Settings(state_dir=tmp_path, blocked_authors=())
    _, _, filters = cli.build_components(settings)
    assert filters == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.cli'`

- [ ] **Step 3: Write app.py**

`kernel_lore_bot/delivery/app.py`:

```python
"""Telegram application wiring. The only module that knows the handler names."""

from __future__ import annotations

import datetime
import logging
from typing import Sequence

from telegram import BotCommandScopeChat
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.delivery.handlers import Handlers
from kernel_lore_bot.filters import Filter
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.base import Source
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    ("start", "Subscribe to the daily kernel digest"),
    ("stop", "Unsubscribe"),
    ("status", "Check your subscription status"),
]
ADMIN_COMMANDS = [("scrape", "Trigger an immediate scrape")]


def build_application(
    settings: Settings,
    store: Store,
    source: Source,
    filters: Sequence[Filter] = (),
) -> Application:
    """Build the PTB application. Does not start it."""
    broadcaster = Broadcaster(settings=settings, store=store, source=source, filters=filters)
    handlers = Handlers(settings=settings, store=store, on_scrape=broadcaster.run)

    async def set_command_menus(app: Application) -> None:
        await app.bot.set_my_commands(PUBLIC_COMMANDS)
        if settings.admin_chat_id != 0:
            await app.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=settings.admin_chat_id),
            )

    async def scheduled_broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
        await broadcaster.run(context.bot)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(set_command_menus)
        # Message-IDs can push "follow:<msgid>" past Telegram's 64-byte
        # callback_data limit, so PTB caches the payload and sends a UUID.
        .arbitrary_callback_data(True)
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("stop", handlers.stop))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(CommandHandler("scrape", handlers.scrape))
    # Must come after the command handlers.
    app.add_handler(CallbackQueryHandler(handlers.on_button))

    app.job_queue.run_repeating(
        scheduled_broadcast,
        interval=datetime.timedelta(hours=settings.schedule_interval_hours),
        first=0,
    )

    return app


def run_bot(
    settings: Settings,
    store: Store,
    source: Source,
    filters: Sequence[Filter] = (),
) -> None:
    app = build_application(settings, store, source, filters)
    log.info("Bot is running. Send /start to the bot on Telegram to subscribe.")
    app.run_polling(drop_pending_updates=True)
```

- [ ] **Step 4: Write cli.py**

`kernel_lore_bot/cli.py`:

```python
"""
Entry point.

Usage:
    python -m kernel_lore_bot          # scheduler + Telegram poller
    python -m kernel_lore_bot --dry    # print what would be sent; send nothing
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

from kernel_lore_bot.delivery.app import run_bot
from kernel_lore_bot.delivery.formatting import format_thread
from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.filters import BlockedAuthors, Filter
from kernel_lore_bot.http import RequestsClient
from kernel_lore_bot.models import Classified, ThreadStatus
from kernel_lore_bot.progress import TqdmProgress
from kernel_lore_bot.settings import PLACEHOLDER_TOKEN, Settings, load_settings
from kernel_lore_bot.sources.lore.source import LoreSource
from kernel_lore_bot.storage import JsonStore, Store

log = logging.getLogger("kernel-bot")


def build_components(settings: Settings) -> tuple[Store, LoreSource, list[Filter]]:
    """Construct the real, I/O-touching implementations."""
    store = JsonStore(settings.state_file)
    source = LoreSource(
        client=RequestsClient(timeout=settings.request_timeout),
        mailing_lists=settings.mailing_lists,
        progress=TqdmProgress(),
    )
    filters: list[Filter] = []
    if settings.blocked_authors:
        filters.append(BlockedAuthors(settings.blocked_authors))
    return store, source, filters


def check_config(settings: Settings, dry: bool) -> list[str]:
    """Return a list of configuration errors. Empty means good to go."""
    errors = []
    if not dry and settings.telegram_bot_token == PLACEHOLDER_TOKEN:
        errors.append("TELEGRAM_BOT_TOKEN is not set (env var or Docker secret)")
    return errors


def format_dry_run(classified: Sequence[Classified], cutoff: datetime) -> str:
    """Render what a real run would send, using the real formatter."""
    if not classified:
        return "[DRY RUN] No new threads found."

    new = [c for c in classified if c.status is ThreadStatus.NEW]
    updated = [c for c in classified if c.status is ThreadStatus.UPDATED]

    lines = [f"[DRY RUN] {len(new)} new thread(s) would be broadcast:", ""]
    for item in new:
        lines.append(format_thread(item, cutoff))
        lines.append("")

    if updated:
        lines.append(
            f"[DRY RUN] {len(updated)} updated thread(s) — followers only:"
        )
        lines.append("")
        for item in updated:
            lines.append(format_thread(item, cutoff))
            lines.append("")

    return "\n".join(lines)


def _configure_console() -> None:
    """
    Force UTF-8 on stdout.

    The digest is full of emoji and the default Windows console encoding here is
    cp1255, which cannot encode them: printing would raise UnicodeEncodeError.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _configure_console()

    parser = argparse.ArgumentParser(description="Kernel Lore Telegram Bot")
    parser.add_argument("--dry", action="store_true", help="Dry-run: print, don't send")
    args = parser.parse_args(argv)

    settings = load_settings()

    errors = check_config(settings, dry=args.dry)
    if errors:
        for err in errors:
            log.error("Config error: %s", err)
        return 1

    store, source, filters = build_components(settings)

    if args.dry:
        broadcaster = Broadcaster(settings, store, source, filters)
        cutoff = broadcaster.cutoff(datetime.now(timezone.utc))
        print(format_dry_run(broadcaster.collect(cutoff), cutoff))
        return 0

    log.info("Starting Telegram bot…")
    run_bot(settings, store, source, filters)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Create `kernel_lore_bot/__main__.py` so `python -m kernel_lore_bot` works:

```python
from kernel_lore_bot.cli import main

raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_cli.py -v`
Expected: PASS — 10 passed

- [ ] **Step 6: Verify the dry run actually runs against live lore**

This is the first end-to-end exercise of the new package. It hits the network.

Run: `.venv\Scripts\python.exe -m kernel_lore_bot --dry`

Expected: a progress bar per list, then a `[DRY RUN] N new thread(s) would be broadcast:` report with emoji rendering correctly (this is what `_configure_console` buys). It must not raise `UnicodeEncodeError`.

If it is slow, temporarily narrow the lists: `KERNEL_BOT_STATE_DIR=data MAILING_LISTS` is not env-driven, so instead run a one-off check:

```bash
.venv\Scripts\python.exe -c "from datetime import datetime,timedelta,timezone; from kernel_lore_bot.cli import *; from kernel_lore_bot.settings import Settings; s=Settings(mailing_lists=('linux-input',)); st,so,f=build_components(s); from kernel_lore_bot.delivery.broadcast import Broadcaster; b=Broadcaster(s,st,so,f); c=b.cutoff(datetime.now(timezone.utc)); print(format_dry_run(b.collect(c),c)[:2000])"
```

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add kernel_lore_bot/delivery/app.py kernel_lore_bot/cli.py kernel_lore_bot/__main__.py tests/test_cli.py
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: add app wiring and CLI with shared dry-run formatting"
```

---

### Task 14: Delete the old modules and rewrite the docs

**Purpose:** Remove the superseded code and fix documentation that describes an architecture which has not existed for several commits.

**Files:**
- Delete: `bot.py`, `scraper.py`, `config.py`, `follows.py`, `subscribers.py`, `main.py`
- Modify: `requirements.txt`, `Dockerfile`, `compose.yaml`, `README.md`, `.gitignore`
- Modify: `tests/test_characterization_mbox.py` (rename — it no longer characterizes anything)

**Interfaces:**
- Consumes: everything.
- Produces: nothing new.

- [ ] **Step 1: Confirm nothing still imports the old modules**

```bash
grep -rn "^import \(bot\|scraper\|config\|follows\|subscribers\|main\)\|^from \(bot\|scraper\|config\|follows\|subscribers\|main\) import" --include=*.py .
```

Expected: no matches outside the files being deleted. If `tests/` still references `scraper`, Task 5 Step 6 was skipped — go back and do it.

- [ ] **Step 2: Delete the superseded modules**

```bash
git rm bot.py scraper.py config.py follows.py subscribers.py main.py
```

- [ ] **Step 3: Rename the characterization test**

It has served its purpose; the assertions are now ordinary regression tests of `mbox.py`. Keeping the name would mislead the next reader into thinking `scraper.py` still exists.

```bash
git mv tests/test_characterization_mbox.py tests/test_mbox_real_fixtures.py
```

Update its module docstring to:

```python
"""
Regression tests for mbox parsing against real lore fixtures.

These began as characterization tests written against the original scraper.py to
prove the extraction preserved behavior. scraper.py is gone; the assertions
remain as the contract for kernel_lore_bot.sources.lore.mbox.
"""
```

- [ ] **Step 4: Drop the unused dependency**

`pynntp` is in `requirements.txt` but nothing imports it — it was speculative groundwork for an NNTP source. It goes back in when that source is actually built. Remove this line from `requirements.txt`:

```
pynntp==2.0.1
```

Leave `APScheduler` alone: python-telegram-bot's job queue depends on it transitively, and the scheduled broadcast needs it.

- [ ] **Step 5: Point the container at the new entry point**

In `Dockerfile`, change the last line from `CMD ["python", "main.py"]` to:

```dockerfile
CMD ["python", "-m", "kernel_lore_bot"]
```

Also set UTF-8 output in the image so log/console emoji are safe regardless of the base locale. Add above the `CMD`:

```dockerfile
ENV PYTHONIOENCODING=utf-8
```

- [ ] **Step 6: Verify the container still builds its dependency layer**

Docker is not installed on this machine, so this cannot be verified locally. Confirm by inspection that `requirements.txt` still lists `python-telegram-bot`, `requests`, and `tqdm`, and note in the PR that the image build is unverified.

Run: `.venv\Scripts\python.exe -c "import telegram, requests, tqdm; print('runtime deps intact')"`
Expected: `runtime deps intact`

- [ ] **Step 7: Ignore the venv and state file**

Add to `.gitignore` if not already present:

```
.venv/
data/
state.json
```

(`.venv/` and `data/` are already there; add `state.json` for stray local runs.)

- [ ] **Step 8: Rewrite README.md**

The current README documents `notifier.py`, `state.py`, `seen_threads.json`, keyword classification, `--test`/`--now` flags, `TELEGRAM_CHAT_ID`, `SCHEDULE_CRON`, and `WATCHED_LISTS`. **None of these exist.** Replace the whole file with:

````markdown
# 🐧 Kernel Lore Telegram Bot

Watches [lore.kernel.org](https://lore.kernel.org) mailing lists and sends new
kernel threads to Telegram subscribers. Tap **🔔 Follow** on a thread to be
notified when it gets replies.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN="123456:ABC-your-token-here"
export ADMIN_CHAT_ID="123456789"        # optional; enables /scrape

python -m kernel_lore_bot --dry         # print what would be sent
python -m kernel_lore_bot               # run for real
```

Get a token from [@BotFather](https://t.me/BotFather); get your chat ID from
[@userinfobot](https://t.me/userinfobot).

## Commands

| Command | Who | Effect |
|---|---|---|
| `/start` | anyone | Subscribe to the digest |
| `/stop` | anyone | Unsubscribe and drop all follows |
| `/status` | anyone | Show subscription and follow count |
| `/scrape` | admin only | Run a scrape immediately |

## Configuration

All configuration is environment variables, read once at startup into a frozen
`Settings` object (`kernel_lore_bot/settings.py`). A Docker secret at
`/run/secrets/telegram_bot_token` takes precedence over the env var.

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Bot token from @BotFather |
| `ADMIN_CHAT_ID` | `0` | Chat allowed to run `/scrape`; `0` disables it |
| `LOOPBACK_HOURS` | `4` | How far back a scrape looks |
| `SCHEDULE_INTERVAL_HOURS` | `LOOPBACK_HOURS` | Hours between scrapes; fractions allowed |
| `KERNEL_BOT_STATE_DIR` | `data` | Directory holding `state.json` |

The watched mailing lists and blocked authors are defaults in `settings.py`
(`DEFAULT_MAILING_LISTS`, `DEFAULT_BLOCKED_AUTHORS`).

## How it works

```
LoreSource.fetch_threads(since)
    │
    ├─ GET /<list>/new.atom?t=…      paginate backwards until older than `since`
    ├─ GET /all/<msgid>/t.mbox.gz    one full thread per entry
    ├─ parse mbox → Thread tree      (deduplicated across lists by Message-ID)
    │
    ▼
apply_filters()  →  digest.classify(cutoff)  →  Broadcaster.run(bot)
                        │                             │
                        ├─ NEW      → every subscriber, with a Follow button
                        └─ UPDATED  → that thread's followers only
```

A thread is **new** if its root arrived within the lookback window, and
**updated** if the root is older but the thread has recent activity.

## State

Everything lives in one file, `$KERNEL_BOT_STATE_DIR/state.json`:

```json
{
  "version": 1,
  "subscribers": {"12345": {"follows": ["msgid@example.com"]}}
}
```

It is written atomically (temp file + `os.replace`). On first run, an older
`subscribers.json` + `follows.json` pair is migrated automatically; the old
files are left in place so a rollback stays possible.

## Layout

```
kernel_lore_bot/
├── settings.py      frozen Settings + env loading
├── models.py        Entry / Node / Thread / ThreadStatus  (pure)
├── http.py          HttpClient protocol + RequestsClient
├── progress.py      Progress protocol + tqdm / null bars
├── filters.py       Filter protocol + BlockedAuthors      (pure)
├── digest.py        new-vs-updated classification         (pure)
├── sources/
│   ├── base.py      Source protocol
│   └── lore/        atom.py + mbox.py (pure) and source.py (I/O)
├── storage/         Store protocol, JsonStore, InMemoryStore
├── delivery/        formatting, keyboards, handlers, broadcast, app
└── cli.py           entry point
tests/               pytest suite; fixtures/ holds real lore samples
```

## Extending it

- **A new mailing list:** add it to `DEFAULT_MAILING_LISTS` in `settings.py`.
- **A new filter:** write a class with `allows(thread) -> bool` and add it in
  `cli.build_components`.
- **A new command:** add an async method to `delivery/handlers.py` and register
  it in `delivery/app.py`.
- **A new source:** implement `Source.fetch_threads(since) -> Iterable[Thread]`.
  Nothing downstream changes.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests never hit the network or the real filesystem: `FakeHttpClient` serves the
checked-in fixtures in `tests/fixtures/lore/`, and `InMemoryStore` replaces
`JsonStore`. Both are in `tests/conftest.py`.

## Docker

```bash
docker build -t kernel-lore-bot .
docker run -d \
  -v $(pwd)/data:/app/data \
  -e TELEGRAM_BOT_TOKEN=… \
  -e ADMIN_CHAT_ID=… \
  kernel-lore-bot
```

Or `docker compose up -d` using the bundled `compose.yaml`.
````

- [ ] **Step 9: Check compose.yaml matches reality**

Read `compose.yaml`. Ensure the state volume still maps to `/app/data` and that
`KERNEL_BOT_STATE_DIR` is set (or relies on the Dockerfile's `ENV`). Fix any
reference to `main.py` or to the removed `TELEGRAM_CHAT_ID`/`SCHEDULE_CRON`
variables. If it declares a `telegram_bot_token` secret, leave it — the loader
still reads `/run/secrets/telegram_bot_token`.

- [ ] **Step 10: Run the full suite one final time**

Run: `.venv\Scripts\python.exe -m pytest -v`
Expected: PASS — the whole suite green with the old modules gone.

Then confirm the package still starts end to end:

Run: `.venv\Scripts\python.exe -m kernel_lore_bot --dry`
Expected: a dry-run report, no traceback.

- [ ] **Step 11: Commit**

```bash
git add -A
git -c user.name="Tom Why" -c user.email="tomwhy2@gmail.com" commit -m "refactor: remove superseded modules; rewrite README to match the code"
```

---

## Done criteria

- `.venv\Scripts\python.exe -m pytest` is green.
- `python -m kernel_lore_bot --dry` prints a report against live lore.
- No file at the repo root imports `config`, `scraper`, `bot`, `follows`, or `subscribers`.
- `README.md` describes only files that exist.
- Each behavioral defect in the spec's list (1-4 and 8-14) is fixed and pinned by a test that names it. Items 5-7 (dead code, unused dependency, stale README) are cleanup with no test.
