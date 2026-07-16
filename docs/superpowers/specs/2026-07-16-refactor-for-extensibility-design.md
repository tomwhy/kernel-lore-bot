# Refactor kernel-lore-bot for extensibility and testability

**Date:** 2026-07-16
**Status:** Approved

## Goal

Restructure the bot so that three planned feature areas — additional sources
beyond lore, richer filtering and matching, and more bot commands — can each be
added by writing one new unit rather than editing existing ones. Make the
existing logic testable, since today none of it is covered.

Delivery stays Telegram-only. No notifier abstraction is in scope.

## Problems with the current code

- **No tests exist**, and no test framework is installed.
- **`config` is a global module** imported directly by `scraper`, `bot`,
  `follows`, and `subscribers`. Tests cannot vary configuration without
  monkeypatching module attributes, which leaks state between tests.
- **Storage is module-level functions doing whole-file I/O per call.** Every
  read re-parses the file; every write re-serializes it. Tests must touch the
  real filesystem.
- **`bot.py` does four jobs**: formatting, sending, broadcast orchestration, and
  application wiring. Growth in bot commands lands here.
- **`scraper.py` mixes** HTTP, mbox parsing, tree construction, filtering,
  orchestration, and progress bars in one flow.
- **`main.py::_dry_run` duplicates** the formatting logic from `bot.py`.

### Defects found while reading

1. `bot.broadcast_new_threads` calls blocking `time.sleep()` inside an async
   function, stalling the event loop (including button presses) for the whole
   broadcast.
2. `bot.status` reaches into the private `follows._load_raw()`.
3. `/stop` mutates `subscribers.json` and `follows.json` in two separate writes;
   a crash in between leaves a follower with no subscription.
4. Writes are non-atomic (`Path.write_text`); a crash mid-write truncates state.
5. Dead code: `scraper.THREAD_NS`, `config.MAILING_LIST_NAMES`,
   `follows.is_following`, `follows.all_followed_thread_ids`,
   `follows.prune_threads`.
6. `pynntp` is in `requirements.txt` but unused. (`APScheduler` is used
   transitively by python-telegram-bot's job queue and stays.)
7. `README.md` describes an architecture that no longer exists (`notifier.py`,
   `state.py`, `seen_threads.json`, keyword classification, `--test`/`--now`).

### Defects found while writing the plan

These were found by running the current code against real lore data, after this
spec was first approved. All are fixed by the plan and pinned by named tests.

8. **Empty mbox aborts the whole scrape.** `iter_mbox_emails("")` calls
   `next(seps)` on an empty match iterator; PEP 479 converts the resulting
   `StopIteration` into `RuntimeError`. `_fetch_thread_tree` only guards
   `mbox_text is None`, so an empty body or an HTML error page from lore
   propagates up and kills the run — no digest is sent. Verified by execution.
9. **Orphaned replies are silently dropped.** Roots are `[e for e in entries if
   not e.is_reply]` and children are attached only when the parent is present in
   the same mbox, so a reply whose `In-Reply-To` points outside the mbox is
   neither a root nor anybody's child. It vanishes from the tree and from the
   "N new entries" count.
10. **A malformed `<updated>` in a feed aborts the scrape.**
    `_fetch_list_entries` calls `datetime.fromisoformat(updated_raw)` with no
    guard; the `except` clause covers only `RequestException` and `ParseError`,
    so one bad entry raises an uncaught `ValueError`.
11. **A truncated download aborts the scrape.** `gzip.decompress` raises
    `EOFError` on a truncated gzip, which is neither `gzip.BadGzipFile` nor an
    `OSError`, so `except gzip.BadGzipFile` misses it. Verified by execution.
12. **The thread URL is interpolated into `href="..."` unescaped.** The URL is
    built from the Message-ID, an untrusted email header, and Telegram renders
    the message as HTML, so a quote in a Message-ID can break out of the
    attribute.
13. **Buttons from before a restart crash their handler.** With
    `arbitrary_callback_data(True)`, python-telegram-bot caches callback payloads
    in memory and returns an `InvalidCallbackData` **object** after a restart.
    `on_follow_button` does `data = query.data or ""` then `data.startswith(...)`,
    raising `AttributeError` and leaving the user with a spinner and no reply.
14. **`ADMIN_CHAT_ID = 0` grants admin to chat 0.** `_is_admin` compares
    `chat_id != config.ADMIN_CHAT_ID`, so with the documented "disabled" value of
    `0`, a chat with id `0` compares equal. Not exploitable (no real Telegram
    chat has id 0), but the code contradicts its own documentation.

### Environment findings

- **lore.kernel.org returns HTTP 403 without a `User-Agent`.** Confirmed by
  request. The bot already sends one; this constrains how fixtures are gathered.
- **The console encoding on the development machine is cp1255**, so `print()` of
  an emoji raises `UnicodeEncodeError`. `main.py --dry` prints emoji directly and
  would crash. The new `cli.py` reconfigures stdout to UTF-8. Telegram delivery
  is unaffected.
- **`Path.read_text()` with no encoding** (used by `subscribers.py` and
  `follows.py`) decodes using the locale codepage, not UTF-8. `JsonStore` passes
  `encoding="utf-8"` explicitly.

## Guiding principle

Everything that decides is a pure function; everything that touches the world
(HTTP, disk, Telegram) sits behind a small injected interface. Tests exercise
real logic against fakes at three boundaries only: `InMemoryStore`,
`FakeHttpClient`, and `NullProgress`.

## Module layout

```
kernel_lore_bot/
  settings.py        Settings frozen dataclass + load_settings(env)
  models.py          Reply, Entry, Node, Thread, ThreadStatus   (pure data)
  http.py            HttpClient protocol + RequestsClient + FetchError
  progress.py        Progress protocol + TqdmProgress / NullProgress
  sources/
    base.py          Source protocol
    lore/
      atom.py        pure: parse_feed_page(bytes) -> list[FeedEntry]
      mbox.py        pure: iter_messages / parse_message / build_thread
      source.py      LoreSource: pagination, mbox fetch, cross-list dedupe
  filters.py         Filter protocol + BlockedAuthors + apply_filters
  digest.py          pure: classify(threads, cutoff) -> list[Classified]
  delivery/
    formatting.py    pure: thread -> HTML string
    keyboards.py     follow / unfollow markup
    handlers.py      start / stop / status / scrape / button callback
    broadcast.py     the scrape-and-send job
    app.py           run_bot() wiring
  cli.py             main(), --dry
tests/
  conftest.py        settings / store / client fixtures
  fixtures/          real .atom and .mbox.gz samples from lore
  test_*.py
```

`models.py`, `atom.py`, `mbox.py`, `filters.py`, `digest.py`, and
`formatting.py` import no I/O and are testable with plain function calls.

## Interfaces

```python
@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_chat_id: int = 0
    mailing_lists: tuple[str, ...] = ()
    blocked_authors: tuple[str, ...] = ()
    loopback_hours: float = 4
    schedule_interval_hours: float = 4
    request_timeout: float = 15
    state_dir: Path = Path("data")

def load_settings(env=os.environ) -> Settings: ...
```

`load_settings` takes `env` as a parameter so secret/env precedence is testable
without monkeypatching. Sequences are tuples: frozen config should not be
mutated at runtime.

```python
class Store(Protocol):
    def subscribers(self) -> set[int]: ...
    def add_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscriber(self, chat_id: int) -> bool: ...      # also drops follows
    def remove_subscribers(self, chat_ids: Iterable[int]) -> None: ...
    def follow(self, thread_id: str, chat_id: int) -> bool: ...
    def unfollow(self, thread_id: str, chat_id: int) -> bool: ...
    def followers(self, thread_id: str) -> list[int]: ...
    def following_count(self, chat_id: int) -> int: ...

class Source(Protocol):
    def fetch_threads(self, since: datetime) -> Iterable[Thread]: ...

class HttpClient(Protocol):
    def get(self, url: str, params: dict | None = None) -> bytes: ...

class Filter(Protocol):
    def allows(self, thread: Thread) -> bool: ...
```

`HttpClient.get` returns bytes and raises `FetchError` on any transport failure,
keeping `requests` from leaking past `http.py` so a future NNTP source is not
forced to pretend it speaks HTTP.

`Thread` becomes frozen. `digest.classify()` returns `Classified` pairs of
(thread, status) rather than mutating `Thread.status` after construction, so a
`Thread` is always valid once built. `ThreadStatus` becomes an enum.

`BlockedAuthors(names)` is the only `Filter` today. Per-subscriber interests
later become an additional `Filter`, not a rewrite.

## Storage

Single file, `state.json`, subscriber-centric:

```json
{
  "version": 1,
  "subscribers": {
    "12345": { "follows": ["msgid-a@example.com", "msgid-b@example.com"] }
  }
}
```

Rationale:

- **One atomic write per mutation.** Collapsing two files into one retires
  defect 3; `os.replace` retires defect 4.
- **Matches planned features.** Per-subscriber interests become a `"filters"`
  key beside `"follows"`, not a third file. `version` gives future shape changes
  a real migration path.
- **Cached, not re-read per call.** `JsonStore` loads once at construction,
  serves reads from memory, and write-throughs atomically on mutation. This is
  safe because python-telegram-bot is single-threaded async: the job queue and
  handlers share one event loop, so there is exactly one owner of the state.
  This also retires the read-modify-write race, with no locking.

`followers(thread_id)` is served by an in-memory reverse index built at load and
maintained on mutation, so `broadcast`'s hot path stays O(1) while the on-disk
shape stays easy to read and extend. The index is private to `JsonStore`; the
`Store` protocol does not change.

`InMemoryStore` implements the same protocol for tests.

**Migration:** on first load, if `state.json` is absent and the old
`subscribers.json` / `follows.json` exist, `JsonStore` imports them and writes
`state.json`. The old files are left on disk so the first deploy is
rollback-able. Tested with both files present, one present, and neither.

## Testing strategy

`pytest` + `pytest-asyncio` in a new `requirements-dev.txt`. No other new
dependencies.

Fixtures are real `.mbox.gz` and `new.atom` responses downloaded from lore and
trimmed to a sane size, so the parser is tested against lore's actual quirks
rather than hand-written guesses.

Coverage targets:

- **`mbox.py`** — multiple roots; no root (falls back to first message); replies
  whose `In-Reply-To` points outside the mbox; malformed dates falling back to
  `now()`; RFC 2047 encoded subjects; missing `Message-ID`.
- **`atom.py` + `LoreSource` pagination** — the re-request loop
  (`t=last_updated - 1s`), stop on empty page, stop on crossing cutoff, and the
  cross-list dedupe when one thread appears in two lists. Driven by
  `FakeHttpClient` returning a scripted sequence of fixture pages.
- **`digest.py`** — new-vs-updated classification at the cutoff boundary; sort
  order (new first, then newest first).
- **`formatting.py`** — HTML escaping of hostile subjects and authors (a subject
  containing `<b>` or `&` is plausible on LKML and currently breaks the
  message); subtree entry counting; singular/plural.
- **`JsonStore`** — round-trips against `tmp_path`; corrupt-JSON recovery;
  migration.
- **`handlers.py`** — `/start` twice is idempotent; `/stop` clears follows;
  `/scrape` rejects non-admins; follow button flips to unfollow. Uses
  `InMemoryStore` and a fake bot object.

### Refactoring risk

There is no characterization test protecting this refactor; it restructures
untested code. Step 2 below carries the most risk, so the parser tests are
written against the **current** `scraper.py` behavior first, using the fixtures,
and the code is moved under them afterward. The tests then prove the extraction
preserved behavior rather than documenting whatever emerged.

## Sequencing

Bottom-up, suite green at every step, one commit per step:

1. `models.py`, `settings.py`, `http.py`, `progress.py` — foundations.
2. `sources/lore/*` extracted from `scraper.py`, with fixtures and parser tests.
   Largest step; tests first per the risk note above.
3. `storage/*` behind `Store`: single-file format, migration, atomic writes,
   caching, `following_count`.
4. `filters.py` + `digest.py`.
5. `delivery/*` split out of `bot.py`; async-sleep fix; handler tests.
6. `cli.py`; `--dry` reuses `formatting.py` instead of duplicating it.
7. Delete old modules; drop `pynntp`; rewrite `README.md` to match reality.

## Behavior contract

Preserved exactly: message wording and layout, command semantics, scrape
schedule, and observable bot behavior.

Intentional changes, all of them defect fixes named above: blocking sleep
becomes `await asyncio.sleep`; writes become atomic; the two state files become
one (with migration); `/status` stops using a private function; the four crash
paths (8, 10, 11, 13) degrade to skipping one item instead of killing the run;
orphaned replies survive (9); the thread URL is escaped (12); `ADMIN_CHAT_ID = 0`
disables admin as documented (14).

Two signature changes fall out of making formatting pure: `format_header` takes
`now` as an argument rather than reading the clock, and `format_thread` takes a
`Classified` pair rather than a mutable `status` attribute on `Thread`.

One cosmetic loss: `HttpClient.get` returns whole bytes, so the per-download
byte-level progress bar inside the old `_fetch_mbox` goes away. Progress is kept
at the list and entry level. This is the price of keeping `requests` from leaking
past `http.py`.

## Out of scope

- SQLite (the `Store` protocol makes it a drop-in later if needed).
- A second `Source` implementation.
- A source registry / plugin configuration.
- Any delivery target other than Telegram.
