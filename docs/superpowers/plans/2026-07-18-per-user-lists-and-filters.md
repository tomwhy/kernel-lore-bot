# Per-User Mailing Lists and Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every subscriber their own mailing lists and their own blocked authors, managed by `/lists` and `/filters` commands, with list names validated against lore.kernel.org's real list index.

**Architecture:** A thread carries the set of every list it appeared on (unioned during cross-post dedup) instead of just the first one. Subscriber records grow `mailing_lists` and `blocked_authors` fields, persisted in a v2 state file that migrates v1 records to the configured defaults. Filtering moves out of the shared scrape and into the per-subscriber send path, so one scrape — over the union of every subscriber's lists — feeds differently-filtered digests.

**Tech Stack:** Python 3.12, python-telegram-bot 22, pytest, `requests`. No new dependencies.

## Global Constraints

- **Python 3.12.** Every module starts with `from __future__ import annotations`.
- **No test may make a real network call.** `FakeHttpClient` in `tests/conftest.py` serves checked-in fixtures from `tests/fixtures/lore/`.
- **Tests stay off disk** by using `InMemoryStore`, except `JsonStore`'s own tests which use pytest's `tmp_path`.
- **Everything interpolated into a Telegram message is HTML-escaped** — message text is sent with `parse_mode="HTML"` and every field originates in an email header.
- **Pure modules perform no I/O:** `models.py`, `filters.py`, `digest.py`, `sources/lore/mbox.py`, `sources/lore/atom.py`, `delivery/formatting.py`.
- **`storage/` must not import `settings`.** Defaults reach the store as constructor arguments.
- **Run tests with** `python -m pytest` from the repo root.
- **Commit after every task.** Conventional-commit prefixes (`feat:`, `refactor:`, `test:`, `docs:`).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `kernel_lore_bot/models.py` | `Thread.mailing_lists: frozenset[str]` | 1 |
| `kernel_lore_bot/sources/lore/mbox.py` | wraps one list name into a frozenset | 1 |
| `kernel_lore_bot/delivery/formatting.py` | renders the list set as a sorted label | 1 |
| `kernel_lore_bot/sources/lore/source.py` | unions lists on dedup; takes lists per call | 2 |
| `kernel_lore_bot/storage/base.py` | `Subscriber` fields + `Store` protocol methods | 3 |
| `kernel_lore_bot/storage/json_store.py` | v2 on-disk format + v1 migration | 4 |
| `kernel_lore_bot/sources/lore/index.py` | **new** — manifest fetch, `ListIndex`, `ListRegistry` | 5 |
| `kernel_lore_bot/delivery/broadcast.py` | per-subscriber routing and filtering | 6 |
| `kernel_lore_bot/delivery/handlers.py` | `/lists` router | 7 |
| `kernel_lore_bot/delivery/handlers.py` | `/filters` router | 8 |
| `kernel_lore_bot/delivery/app.py`, `cli.py`, `README.md` | wiring, command menu, docs | 9 |

Tasks are ordered so each one leaves the suite green. Tasks 1–2 change the domain model, 3–4 the storage layer, 5 adds validation, 6 rewires delivery, 7–8 add the commands, 9 wires it together and documents it.

---

## Task 1: Thread carries a set of mailing lists

**Files:**
- Modify: `kernel_lore_bot/models.py:54-89`
- Modify: `kernel_lore_bot/sources/lore/mbox.py:93-160`
- Modify: `kernel_lore_bot/delivery/formatting.py:33-63`
- Test: `tests/test_models.py`, `tests/test_formatting.py`, `tests/test_mbox.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Thread(roots: tuple[Node, ...], mailing_lists: frozenset[str] = frozenset())` — frozen dataclass; existing properties `title`, `author`, `updated`, `url`, `id` and method `walk()` are unchanged.
  - `mbox.build_thread(entries: list[Entry], mailing_list: str = "") -> Thread | None` — signature unchanged; an empty `mailing_list` produces `frozenset()`, a non-empty one produces `frozenset({name})`.
  - `mbox.parse_thread(mbox_text: str, mailing_list: str = "", base_url: str = LORE_BASE_URL) -> Thread | None` — signature unchanged.
  - `formatting.format_thread(classified, cutoff) -> str` and `formatting.format_update_notification(thread) -> str` — signatures unchanged; the `📬` line now shows every list, sorted and comma-joined.

- [ ] **Step 1: Write the failing tests**

Replace the `mailing_list` assertions in `tests/test_models.py`. Find the existing test around line 56 that builds `Thread(roots=(root,), mailing_list="netdev")` and asserting `thread.mailing_list == "netdev"`, and change it to:

```python
def test_thread_exposes_root_fields_and_mailing_lists():
    entry = _entry("root@example.com")
    root = Node(entry=entry)
    thread = Thread(roots=(root,), mailing_lists=frozenset({"netdev", "lkml"}))

    assert thread.id == "root@example.com"
    assert thread.mailing_lists == frozenset({"netdev", "lkml"})


def test_thread_mailing_lists_defaults_to_empty():
    thread = Thread(roots=(Node(entry=_entry("a@example.com")),))
    assert thread.mailing_lists == frozenset()
```

(`_entry` is the existing helper in that file — reuse it rather than writing a new one. If the surrounding test asserted `title`/`author`/`updated`, keep those assertions too.)

Add to `tests/test_formatting.py`, and update its `_thread` helper:

```python
def _thread(updated=None, mailing_lists=frozenset({"netdev"}), children=(), **kw):
    root = Node(entry=_entry(updated=updated, **kw), children=children)
    return Thread(roots=(root,), mailing_lists=frozenset(mailing_lists))


def test_mailing_list_line_is_omitted_when_empty():
    text = format_thread(_new(_thread(mailing_lists=frozenset())), CUTOFF)
    assert "📬" not in text


def test_all_mailing_lists_are_shown_sorted():
    text = format_thread(_new(_thread(mailing_lists={"netdev", "lkml"})), CUTOFF)
    assert "📬 lkml, netdev" in text


def test_update_notification_omits_empty_mailing_list():
    assert "📬" not in format_update_notification(_thread(mailing_lists=frozenset()))
```

Update the existing assertion in `tests/test_mbox.py` around line 29:

```python
    assert thread.mailing_lists == frozenset({"linux-input"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_models.py tests/test_formatting.py tests/test_mbox.py -v`
Expected: FAIL — `TypeError: Thread.__init__() got an unexpected keyword argument 'mailing_lists'`.

- [ ] **Step 3: Change the model**

In `kernel_lore_bot/models.py`, in the `Thread` dataclass, replace the `mailing_list` field:

```python
@dataclass(frozen=True)
class Thread:
    """
    An email thread reconstructed from its mbox archive.

    `roots` is normally exactly one node; more than one signals a split or
    malformed thread, which is kept rather than dropped.

    `mailing_lists` holds every list the thread was seen on. One thread is
    frequently cross-posted, and subscribers pick lists individually, so the
    full set — not just whichever list surfaced it first — decides who
    receives it.
    """

    roots: tuple[Node, ...]
    mailing_lists: frozenset[str] = frozenset()
```

- [ ] **Step 4: Update the mbox parser**

In `kernel_lore_bot/sources/lore/mbox.py`, change the final line of `build_thread` (currently `return Thread(roots=tuple(root_nodes), mailing_list=mailing_list)`) to:

```python
    lists = frozenset({mailing_list}) if mailing_list else frozenset()
    return Thread(roots=tuple(root_nodes), mailing_lists=lists)
```

`build_thread` and `parse_thread` keep their `mailing_list: str = ""` parameter — a parsed mbox always comes from exactly one list, and the set is built by `LoreSource` in Task 2.

- [ ] **Step 5: Update the formatter**

In `kernel_lore_bot/delivery/formatting.py`, add a helper below `_link`:

```python
def _lists_label(thread: Thread) -> str:
    """Every list the thread was seen on, sorted for a stable message."""
    return ", ".join(sorted(thread.mailing_lists))
```

Then in `format_thread` replace:

```python
    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")
```

with:

```python
    if thread.mailing_lists:
        lines.append(f"📬 {_h(_lists_label(thread))}")
```

Make the identical replacement in `format_update_notification`.

- [ ] **Step 6: Fix the remaining call sites**

`tests/test_broadcast.py:19-28`, `tests/test_cli.py:19`, and `tests/test_lore_source.py:54` still pass or assert `mailing_list=`. Update each:

```python
# tests/test_broadcast.py
def _thread(msg_id, updated, author="Alice Adams", mailing_lists=frozenset({"netdev"})):
    ...
    return Thread(roots=(Node(entry=entry),), mailing_lists=frozenset(mailing_lists))

# tests/test_cli.py:19
    thread = Thread(roots=(Node(entry=entry),), mailing_lists=frozenset({"netdev"}))

# tests/test_lore_source.py:54
    assert threads[0].mailing_lists == frozenset({"linux-input"})
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest`
Expected: PASS, no failures.

- [ ] **Step 8: Commit**

```bash
git add kernel_lore_bot/models.py kernel_lore_bot/sources/lore/mbox.py kernel_lore_bot/delivery/formatting.py tests/
git commit -m "refactor: a thread carries every mailing list it appeared on"
```

---

## Task 2: LoreSource unions cross-posted lists and takes lists per call

**Files:**
- Modify: `kernel_lore_bot/sources/lore/source.py:34-70`
- Modify: `kernel_lore_bot/sources/base.py`
- Test: `tests/test_lore_source.py`

**Interfaces:**
- Consumes: `Thread.mailing_lists: frozenset[str]` (Task 1).
- Produces:
  - `Source.fetch_threads(self, since: datetime, mailing_lists: Sequence[str]) -> Iterable[Thread]` — the protocol gains a second parameter.
  - `LoreSource(client: HttpClient, progress: Progress | None = None, base_url: str = LORE_BASE_URL)` — the `mailing_lists` constructor argument is **removed**; lists are now passed per call.
  - `LoreSource.fetch_threads(since, mailing_lists)` returns a `list[Thread]`, each with the full set of lists it was seen on.

**Context for the implementer:** today `fetch_threads` is a generator that yields a thread the moment it is fetched, and keeps a `seen: set[str]` of every Message-ID in every thread already handled (so a reply's ID does not trigger a refetch of its own thread). A thread cross-posted to `lkml` and `netdev` is therefore yielded once, tagged only with whichever list came first. To union the lists we must be able to find the already-yielded thread — so `seen` becomes `dict[node_id, root_id]` and results accumulate in a dict before being returned.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_lore_source.py`. Study the existing helpers at the top of that file (`_source`, `BASE`, and how atom/mbox fixture bodies are built) and reuse them; the test below assumes an existing helper that builds a one-entry atom page and an mbox body.

```python
def test_cross_posted_thread_carries_every_list(conftest_fake_client):
    """One thread on two lists is fetched once and tagged with both lists."""
    atom = _atom_page([("shared@example.com", "2026-07-18T10:00:00Z")])
    mbox = _mbox_bytes("shared@example.com", "A shared patch")
    client = conftest_fake_client({
        f"{BASE}/linux-input/new.atom": [atom, _atom_page([])],
        f"{BASE}/netdev/new.atom": [atom, _atom_page([])],
        f"{BASE}/all/shared@example.com/t.mbox.gz": [mbox],
    })
    source = LoreSource(client=client, base_url=BASE)

    threads = list(source.fetch_threads(SINCE, ("linux-input", "netdev")))

    assert len(threads) == 1
    assert threads[0].mailing_lists == frozenset({"linux-input", "netdev"})
    # The mbox is downloaded once, not once per list.
    mbox_calls = [c for c in client.calls if "t.mbox.gz" in c["url"]]
    assert len(mbox_calls) == 1
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_lore_source.py::test_cross_posted_thread_carries_every_list -v`
Expected: FAIL — `TypeError: fetch_threads() takes 2 positional arguments but 3 were given`.

- [ ] **Step 3: Update the Source protocol**

In `kernel_lore_bot/sources/base.py`:

```python
class Source(Protocol):
    def fetch_threads(
        self, since: datetime, mailing_lists: Sequence[str]
    ) -> Iterable[Thread]:
        """Every thread with activity at or after `since`, across `mailing_lists`."""
        ...
```

Add `Sequence` to the `typing` import if it is not already there.

- [ ] **Step 4: Rewrite LoreSource**

In `kernel_lore_bot/sources/lore/source.py`, drop `mailing_lists` from `__init__`:

```python
    def __init__(
        self,
        client: HttpClient,
        progress: Progress | None = None,
        base_url: str = mbox_parser.LORE_BASE_URL,
    ) -> None:
        self.client = client
        self.progress = progress if progress is not None else NullProgress()
        self.base_url = base_url.rstrip("/")
```

Replace `fetch_threads` entirely:

```python
    def fetch_threads(
        self, since: datetime, mailing_lists: Sequence[str]
    ) -> list[Thread]:
        """
        Every thread with activity at or after `since`, deduplicated.

        A thread cross-posted to several lists is downloaded once and carries
        all of their names. That means results cannot be yielded as they are
        found — a later list may add to a thread already seen — so this
        collects fully before returning.
        """
        # node Message-ID -> root Message-ID of the thread that contains it.
        # Keyed on every node, not just roots, so a reply appearing in a feed
        # resolves to its thread instead of triggering a second download.
        seen: dict[str, str] = {}
        threads: dict[str, Thread] = {}

        for list_name in mailing_lists:
            with self.progress.bar(f"  {list_name}") as bar:
                for feed_entry in self._iter_feed_entries(list_name, since):
                    bar.update(1)

                    root_id = seen.get(feed_entry.entry_id)
                    if root_id is not None:
                        existing = threads.get(root_id)
                        if existing is not None:
                            threads[root_id] = replace(
                                existing,
                                mailing_lists=existing.mailing_lists | {list_name},
                            )
                        continue

                    thread = self._fetch_thread(feed_entry.entry_id, list_name)
                    if thread is None:
                        # Remember the failure so the next list does not retry it.
                        seen[feed_entry.entry_id] = ""
                        continue

                    threads[thread.id] = thread
                    for node in thread.walk():
                        seen[node.entry.id] = thread.id

        return list(threads.values())
```

Add the import at the top of the file:

```python
from dataclasses import replace
```

and add `Sequence` to the `typing` import line.

**Why `seen[...] = ""` for a failed fetch:** `""` is never a real root id, so `threads.get("")` returns `None` and the `continue` above skips it — the entry is remembered as handled without pretending a thread exists.

- [ ] **Step 5: Fix the existing LoreSource tests**

Every `LoreSource(client=client, mailing_lists=lists, base_url=BASE)` construction must drop the `mailing_lists=` argument and pass the lists to `fetch_threads` instead. The `_source` helper at `tests/test_lore_source.py:42` becomes:

```python
def _source(client, lists=("linux-input",)):
    """Return (source, lists) — lists are now a fetch_threads argument."""
    return LoreSource(client=client, base_url=BASE), tuple(lists)
```

Update each caller accordingly, and the standalone construction at line 287. Every `source.fetch_threads(SINCE)` call becomes `source.fetch_threads(SINCE, lists)`.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_lore_source.py -v`
Expected: PASS, including the new cross-post test.

- [ ] **Step 7: Fix the broadcaster call site so the suite stays green**

`kernel_lore_bot/delivery/broadcast.py:100` calls `self.source.fetch_threads(cutoff)`. Task 6 rewrites this method properly; for now make `collect` take the lists and pass them through:

```python
    def collect(self, cutoff: datetime, mailing_lists: Sequence[str]) -> list[Classified]:
        """Fetch, filter, and classify. No Telegram, no async."""
        threads = list(self.source.fetch_threads(cutoff, mailing_lists))
        kept = apply_filters(threads, self.filters)
        ...
```

and in `_run_locked` change the offload call to:

```python
        classified = await asyncio.to_thread(
            self.collect, cutoff, self.settings.mailing_lists
        )
```

In `kernel_lore_bot/cli.py`, `build_components` no longer passes `mailing_lists` to `LoreSource`:

```python
    source = LoreSource(
        client=RequestsClient(timeout=settings.request_timeout),
        progress=TqdmProgress(),
    )
```

and the dry-run call becomes `broadcaster.collect(cutoff, settings.mailing_lists)`.

Update `tests/test_cli.py:83` (`assert source.mailing_lists == ("netdev",)`) — `LoreSource` no longer has that attribute. Assert on what `build_components` still controls instead:

```python
    assert source.base_url == "https://lore.kernel.org"
```

and update any `broadcaster.collect(cutoff)` call in `tests/test_broadcast.py` to `broadcaster.collect(cutoff, ("netdev",))`.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add kernel_lore_bot/sources/ kernel_lore_bot/delivery/broadcast.py kernel_lore_bot/cli.py tests/
git commit -m "feat: union cross-posted lists and take the list set per fetch"
```

---

## Task 3: Subscriber gains lists and blocks

**Files:**
- Modify: `kernel_lore_bot/storage/base.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Subscriber(chat_id: int, follows: set[str] = …, mailing_lists: set[str] = …, blocked_authors: set[str] = …)`
  - `BaseStore(subs: dict[int, Subscriber] | None = None, default_lists: Iterable[str] = (), default_blocks: Iterable[str] = ())`
  - `Store.mailing_lists(chat_id: int) -> set[str]`
  - `Store.add_lists(chat_id: int, names: Iterable[str]) -> set[str]` — returns the names actually added
  - `Store.remove_lists(chat_id: int, names: Iterable[str]) -> set[str]` — returns the names actually removed
  - `Store.blocked_authors(chat_id: int) -> set[str]`
  - `Store.block(chat_id: int, name: str) -> bool`
  - `Store.unblock(chat_id: int, name: str) -> bool`
  - `Store.all_mailing_lists() -> set[str]`

**Design notes for the implementer:**
- The store validates nothing. It stores what it is handed; checking a list name against lore's index is the handler's job (Task 7), which keeps `storage/` free of any knowledge of lore.
- `add_subscriber` seeds a new subscriber from `default_lists` / `default_blocks`.
- Unknown `chat_id` reads return an empty set; unknown-`chat_id` writes return `False`/empty and create nothing. (`follow()` does create via `setdefault` — that is existing behavior, leave it alone.)
- Author blocks match case-insensitively, so `block` must reject a case-insensitive duplicate and `unblock` must find a case-insensitive match. The originally-typed casing is what gets stored and displayed.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
def test_new_subscriber_is_seeded_with_defaults():
    store = InMemoryStore(default_lists=("netdev", "lkml"), default_blocks=("bot",))
    store.add_subscriber(1)

    assert store.mailing_lists(1) == {"netdev", "lkml"}
    assert store.blocked_authors(1) == {"bot"}


def test_seeded_defaults_are_not_shared_between_subscribers():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.add_lists(1, ["lkml"])

    assert store.mailing_lists(2) == {"netdev"}


def test_add_lists_returns_only_newly_added():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)

    assert store.add_lists(1, ["netdev", "lkml"]) == {"lkml"}
    assert store.mailing_lists(1) == {"netdev", "lkml"}


def test_remove_lists_returns_only_actually_removed():
    store = InMemoryStore(default_lists=("netdev", "lkml"))
    store.add_subscriber(1)

    assert store.remove_lists(1, ["lkml", "rcu"]) == {"lkml"}
    assert store.mailing_lists(1) == {"netdev"}


def test_lists_of_unknown_chat_are_empty():
    store = InMemoryStore(default_lists=("netdev",))
    assert store.mailing_lists(999) == set()
    assert store.add_lists(999, ["lkml"]) == set()
    assert 999 not in store.subscribers()


def test_block_is_case_insensitively_unique():
    store = InMemoryStore()
    store.add_subscriber(1)

    assert store.block(1, "Kernel Test Robot") is True
    assert store.block(1, "kernel test robot") is False
    assert store.blocked_authors(1) == {"Kernel Test Robot"}


def test_unblock_matches_case_insensitively():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.block(1, "Kernel Test Robot")

    assert store.unblock(1, "KERNEL TEST ROBOT") is True
    assert store.blocked_authors(1) == set()
    assert store.unblock(1, "nobody") is False


def test_all_mailing_lists_is_the_union_across_subscribers():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.add_lists(2, ["rcu"])

    assert store.all_mailing_lists() == {"netdev", "rcu"}


def test_all_mailing_lists_is_empty_with_no_subscribers():
    assert InMemoryStore(default_lists=("netdev",)).all_mailing_lists() == set()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_store.py -v`
Expected: FAIL — `TypeError: BaseStore.__init__() got an unexpected keyword argument 'default_lists'`.

- [ ] **Step 3: Extend Subscriber and the Store protocol**

In `kernel_lore_bot/storage/base.py`:

```python
@dataclass
class Subscriber:
    """One subscribed chat: the threads it follows, the lists it wants, and
    the authors it has muted."""

    chat_id: int
    follows: set[str] = field(default_factory=set)
    mailing_lists: set[str] = field(default_factory=set)
    blocked_authors: set[str] = field(default_factory=set)


class Store(Protocol):
    def subscribers(self) -> set[int]: ...
    def add_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscriber(self, chat_id: int) -> bool: ...
    def remove_subscribers(self, chat_ids: Iterable[int]) -> None: ...
    def follow(self, thread_id: str, chat_id: int) -> bool: ...
    def unfollow(self, thread_id: str, chat_id: int) -> bool: ...
    def followers(self, thread_id: str) -> list[int]: ...
    def following_count(self, chat_id: int) -> int: ...
    def mailing_lists(self, chat_id: int) -> set[str]: ...
    def add_lists(self, chat_id: int, names: Iterable[str]) -> set[str]: ...
    def remove_lists(self, chat_id: int, names: Iterable[str]) -> set[str]: ...
    def blocked_authors(self, chat_id: int) -> set[str]: ...
    def block(self, chat_id: int, name: str) -> bool: ...
    def unblock(self, chat_id: int, name: str) -> bool: ...
    def all_mailing_lists(self) -> set[str]: ...
```

- [ ] **Step 4: Extend BaseStore**

Change `BaseStore.__init__` to accept and remember the defaults. The existing `replace(sub, follows=set(sub.follows))` copy must also copy the two new mutable sets, or subscribers would alias each other:

```python
    def __init__(
        self,
        subs: dict[int, Subscriber] | None = None,
        default_lists: Iterable[str] = (),
        default_blocks: Iterable[str] = (),
    ) -> None:
        # Copy each Subscriber and every mutable set it owns so a caller's
        # objects are not aliased into our state. `replace` rather than
        # Subscriber(...) so fields added later are carried over for free.
        self._subs: dict[int, Subscriber] = {
            sub.chat_id: replace(
                sub,
                follows=set(sub.follows),
                mailing_lists=set(sub.mailing_lists),
                blocked_authors=set(sub.blocked_authors),
            )
            for sub in (subs or {}).values()
        }
        self._default_lists = frozenset(default_lists)
        self._default_blocks = frozenset(default_blocks)
        self._index: dict[str, set[int]] = defaultdict(set)
        for chat, sub in self._subs.items():
            for thread_id in sub.follows:
                self._index[thread_id].add(chat)
```

Seed new subscribers in `add_subscriber`:

```python
    def add_subscriber(self, chat_id: int) -> bool:
        if chat_id in self._subs:
            return False
        self._subs[chat_id] = Subscriber(
            chat_id,
            mailing_lists=set(self._default_lists),
            blocked_authors=set(self._default_blocks),
        )
        self._flush()
        log.info("New subscriber: chat_id=%d (total: %d)", chat_id, len(self._subs))
        return True
```

Add the new methods in the reads/writes sections:

```python
    # -- reads ---------------------------------------------------------

    def mailing_lists(self, chat_id: int) -> set[str]:
        sub = self._subs.get(chat_id)
        return set(sub.mailing_lists) if sub else set()

    def blocked_authors(self, chat_id: int) -> set[str]:
        sub = self._subs.get(chat_id)
        return set(sub.blocked_authors) if sub else set()

    def all_mailing_lists(self) -> set[str]:
        """Every list at least one subscriber wants — the scrape's scope.

        Scans subscribers rather than keeping an index: this runs once per
        scrape, not once per delivered message, so it is not a hot path.
        """
        union: set[str] = set()
        for sub in self._subs.values():
            union |= sub.mailing_lists
        return union

    # -- writes --------------------------------------------------------

    def add_lists(self, chat_id: int, names: Iterable[str]) -> set[str]:
        sub = self._subs.get(chat_id)
        if sub is None:
            return set()
        added = {name for name in names if name not in sub.mailing_lists}
        if not added:
            return set()
        sub.mailing_lists |= added
        self._flush()
        log.info("chat_id=%d added list(s): %s", chat_id, ", ".join(sorted(added)))
        return added

    def remove_lists(self, chat_id: int, names: Iterable[str]) -> set[str]:
        sub = self._subs.get(chat_id)
        if sub is None:
            return set()
        removed = {name for name in names if name in sub.mailing_lists}
        if not removed:
            return set()
        sub.mailing_lists -= removed
        self._flush()
        log.info("chat_id=%d removed list(s): %s", chat_id, ", ".join(sorted(removed)))
        return removed

    def block(self, chat_id: int, name: str) -> bool:
        sub = self._subs.get(chat_id)
        if sub is None:
            return False
        # Blocks match case-insensitively (see filters.BlockedAuthors), so two
        # spellings of one name would be a duplicate rule, not two rules.
        if any(existing.lower() == name.lower() for existing in sub.blocked_authors):
            return False
        sub.blocked_authors.add(name)
        self._flush()
        log.info("chat_id=%d blocked author %r", chat_id, name)
        return True

    def unblock(self, chat_id: int, name: str) -> bool:
        sub = self._subs.get(chat_id)
        if sub is None:
            return False
        match = next(
            (e for e in sub.blocked_authors if e.lower() == name.lower()), None
        )
        if match is None:
            return False
        sub.blocked_authors.discard(match)
        self._flush()
        log.info("chat_id=%d unblocked author %r", chat_id, match)
        return True
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest`
Expected: PASS. `InMemoryStore` needs no change — it inherits the new constructor.

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/storage/base.py tests/test_store.py
git commit -m "feat: subscribers own their mailing lists and blocked authors"
```

---

## Task 4: JsonStore v2 format and v1 migration

**Files:**
- Modify: `kernel_lore_bot/storage/json_store.py`
- Test: `tests/test_json_store.py`

**Interfaces:**
- Consumes: `Subscriber` fields and `BaseStore.__init__` defaults (Task 3).
- Produces:
  - `JsonStore(path: Path, default_lists: Iterable[str] = (), default_blocks: Iterable[str] = ())`
  - `STATE_VERSION = 2`
  - On-disk record: `{"follows": [...], "mailing_lists": [...], "blocked_authors": [...]}`, every list sorted.

**Migration rule:** a record missing `mailing_lists` or `blocked_authors` — i.e. any v1 record — gets the store's defaults for the missing key. Existing subscribers therefore keep receiving exactly what they receive today. The upgraded file is written on the next mutation; reading alone does not rewrite it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_json_store.py`:

```python
def test_v1_record_is_migrated_to_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "subscribers": {"7": {"follows": ["t@example.com"]}},
        }),
        encoding="utf-8",
    )

    store = JsonStore(path, default_lists=("netdev",), default_blocks=("bot",))

    assert store.mailing_lists(7) == {"netdev"}
    assert store.blocked_authors(7) == {"bot"}
    assert store.followers("t@example.com") == [7]


def test_v2_record_round_trips(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStore(path, default_lists=("netdev",))
    store.add_subscriber(7)
    store.add_lists(7, ["rcu"])
    store.block(7, "Noisy Bot")

    reloaded = JsonStore(path, default_lists=("netdev",))

    assert reloaded.mailing_lists(7) == {"netdev", "rcu"}
    assert reloaded.blocked_authors(7) == {"Noisy Bot"}


def test_written_file_is_version_2_and_sorted(tmp_path):
    path = tmp_path / "state.json"
    store = JsonStore(path, default_lists=("netdev", "lkml"))
    store.add_subscriber(7)

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["version"] == 2
    assert raw["subscribers"]["7"]["mailing_lists"] == ["lkml", "netdev"]
    assert raw["subscribers"]["7"]["blocked_authors"] == []


def test_explicitly_empty_lists_are_not_refilled_with_defaults(tmp_path):
    """A subscriber who removed every list must stay empty across a restart."""
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({
            "version": 2,
            "subscribers": {
                "7": {"follows": [], "mailing_lists": [], "blocked_authors": []}
            },
        }),
        encoding="utf-8",
    )

    store = JsonStore(path, default_lists=("netdev",))

    assert store.mailing_lists(7) == set()
```

Add `import json` at the top of the test file if it is not already imported.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_json_store.py -v`
Expected: FAIL — `TypeError: JsonStore.__init__() got an unexpected keyword argument 'default_lists'`.

- [ ] **Step 3: Implement the v2 format**

In `kernel_lore_bot/storage/json_store.py`, update the module docstring's example payload to the v2 shape, bump the version, and rewrite the serialization helpers. Note the sentinel in `_subscriber_from_json`: `rec.get("mailing_lists")` returning `None` means the key is absent (v1, apply defaults), while `[]` means the user deliberately emptied it.

```python
STATE_VERSION = 2


def _subscriber_from_json(
    chat: str,
    rec: dict,
    default_lists: frozenset[str],
    default_blocks: frozenset[str],
) -> Subscriber:
    """Build a Subscriber from one entry of the "subscribers" object.

    A missing key means a v1 record, which predates the field: fall back to
    the configured defaults so an existing subscriber's digest is unchanged
    by the upgrade. An empty list is NOT missing — it means the subscriber
    deliberately removed everything, and must survive a restart.
    """
    lists = rec.get("mailing_lists")
    blocks = rec.get("blocked_authors")
    return Subscriber(
        chat_id=int(chat),
        follows=set(rec.get("follows", [])),
        mailing_lists=set(default_lists) if lists is None else set(lists),
        blocked_authors=set(default_blocks) if blocks is None else set(blocks),
    )


def _subscriber_to_json(sub: Subscriber) -> dict:
    """The on-disk shape of one subscriber. Sorted so writes are stable."""
    return {
        "follows": sorted(sub.follows),
        "mailing_lists": sorted(sub.mailing_lists),
        "blocked_authors": sorted(sub.blocked_authors),
    }
```

`_load_state` gains the same two parameters and passes them through:

```python
def _load_state(
    path: Path,
    default_lists: frozenset[str],
    default_blocks: frozenset[str],
) -> dict[int, Subscriber]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        subs = [
            _subscriber_from_json(chat, rec, default_lists, default_blocks)
            for chat, rec in raw.get("subscribers", {}).items()
        ]
        return {sub.chat_id: sub for sub in subs}
    except (json.JSONDecodeError, ValueError, AttributeError, TypeError) as exc:
        ...  # unchanged corrupt-file backup path
```

Leave the entire corrupt-file backup `except` block exactly as it is.

`JsonStore.__init__`:

```python
    def __init__(
        self,
        path: Path,
        default_lists: Iterable[str] = (),
        default_blocks: Iterable[str] = (),
    ) -> None:
        self._path = Path(path)
        lists = frozenset(default_lists)
        blocks = frozenset(default_blocks)
        super().__init__(_load_state(self._path, lists, blocks), lists, blocks)
```

Add `from typing import Iterable` to the imports.

`_flush` needs no change — it already calls `_subscriber_to_json` and writes `STATE_VERSION`.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_json_store.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kernel_lore_bot/storage/json_store.py tests/test_json_store.py
git commit -m "feat: v2 state format with per-subscriber lists and blocks"
```

---

## Task 5: Validate list names against lore's index

**Files:**
- Create: `kernel_lore_bot/sources/lore/index.py`
- Create: `tests/test_list_index.py`
- Create: `tests/fixtures/lore/manifest.js.gz`
- Modify: `kernel_lore_bot/sources/lore/__init__.py` (if it re-exports submodules — check first)

**Interfaces:**
- Consumes: `HttpClient` / `FetchError` from `kernel_lore_bot.http`.
- Produces:
  - `MANIFEST_PATH = "/manifest.js.gz"`
  - `class ListIndexError(Exception)`
  - `fetch_list_names(client: HttpClient, base_url: str = LORE_BASE_URL) -> frozenset[str]` — raises `FetchError` or `ListIndexError`
  - `ListIndex(names: frozenset[str])` — frozen dataclass; `is_valid(name: str) -> bool`, `search(query: str, limit: int = 20) -> list[str]`
  - `ListRegistry(client, base_url=LORE_BASE_URL, fallback: Iterable[str] = ())` — `.index -> ListIndex` property, `.refresh() -> bool`

**Background:** lore.kernel.org publishes `manifest.js.gz` at its root — a gzipped JSON object whose keys are repository paths like `/linux-media/git/0.git`. The list name is the first path segment. A list with several epochs appears under several keys (`/0.git`, `/1.git`), so the set collapses duplicates naturally.

`ListRegistry` exists because the index must be refreshable without a restart: it holds the current `ListIndex` and swaps it on a successful refresh, keeping the previous one on failure. It starts from `fallback` so the bot is usable before the first successful fetch.

- [ ] **Step 1: Create the fixture**

Run this from the repo root:

```bash
python -c "
import gzip, json, pathlib
manifest = {
    '/lkml/git/0.git': {'description': 'lkml'},
    '/lkml/git/1.git': {'description': 'lkml epoch 1'},
    '/netdev/git/0.git': {'description': 'netdev'},
    '/linux-input/git/0.git': {'description': 'linux-input'},
    '/linux-media/git/0.git': {'description': 'linux-media'},
}
p = pathlib.Path('tests/fixtures/lore/manifest.js.gz')
p.write_bytes(gzip.compress(json.dumps(manifest).encode()))
print(p, p.stat().st_size, 'bytes')
"
```

Expected: prints the path and a size of roughly 150 bytes.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_list_index.py`:

```python
"""The mailing-list index used to validate user-supplied list names."""

from __future__ import annotations

import gzip
import json

import pytest

from kernel_lore_bot.http import FetchError
from kernel_lore_bot.sources.lore.index import (
    ListIndex,
    ListIndexError,
    ListRegistry,
    fetch_list_names,
)

BASE = "https://lore.example.org"
MANIFEST_URL = f"{BASE}/manifest.js.gz"


def _manifest(*names: str) -> bytes:
    return gzip.compress(
        json.dumps({f"/{n}/git/0.git": {"description": n} for n in names}).encode()
    )


def test_fetch_list_names_takes_the_first_path_segment(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("lkml", "netdev")]})

    assert fetch_list_names(client, BASE) == frozenset({"lkml", "netdev"})


def test_multiple_epochs_collapse_to_one_name(conftest_fake_client):
    raw = gzip.compress(
        json.dumps({"/lkml/git/0.git": {}, "/lkml/git/1.git": {}}).encode()
    )
    client = conftest_fake_client({MANIFEST_URL: [raw]})

    assert fetch_list_names(client, BASE) == frozenset({"lkml"})


def test_real_fixture_parses(conftest_fake_client, fixture_bytes):
    client = conftest_fake_client({MANIFEST_URL: [fixture_bytes("manifest.js.gz")]})

    names = fetch_list_names(client, BASE)

    assert "lkml" in names
    assert "linux-media" in names


def test_malformed_manifest_raises(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [gzip.compress(b"not json")]})

    with pytest.raises(ListIndexError):
        fetch_list_names(client, BASE)


def test_ungzipped_manifest_is_accepted(conftest_fake_client):
    """Some mirrors serve the manifest already decompressed."""
    client = conftest_fake_client({MANIFEST_URL: [json.dumps({"/rcu/git/0.git": {}}).encode()]})

    assert fetch_list_names(client, BASE) == frozenset({"rcu"})


def test_is_valid_is_case_insensitive():
    index = ListIndex(frozenset({"netdev"}))

    assert index.is_valid("NetDev") is True
    assert index.is_valid("nope") is False


def test_search_returns_sorted_substring_matches():
    index = ListIndex(frozenset({"linux-media", "linux-input", "netdev"}))

    assert index.search("linux") == ["linux-input", "linux-media"]
    assert index.search("LINUX") == ["linux-input", "linux-media"]
    assert index.search("nothing") == []


def test_search_respects_the_limit():
    index = ListIndex(frozenset({f"linux-{i}" for i in range(50)}))

    assert len(index.search("linux", limit=5)) == 5


def test_registry_starts_on_the_fallback(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: FetchError("lore is down")})
    registry = ListRegistry(client, BASE, fallback=("netdev",))

    assert registry.index.is_valid("netdev") is True
    assert registry.refresh() is False
    assert registry.index.is_valid("netdev") is True


def test_registry_swaps_in_a_successful_refresh(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("rcu")]})
    registry = ListRegistry(client, BASE, fallback=("netdev",))

    assert registry.refresh() is True
    assert registry.index.is_valid("rcu") is True
    assert registry.index.is_valid("netdev") is False


def test_registry_keeps_the_previous_index_when_a_refresh_fails(conftest_fake_client):
    client = conftest_fake_client({MANIFEST_URL: [_manifest("rcu")]})
    registry = ListRegistry(client, BASE, fallback=("netdev",))
    registry.refresh()

    # The route is exhausted, so the second refresh raises FetchError.
    assert registry.refresh() is False
    assert registry.index.is_valid("rcu") is True
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python -m pytest tests/test_list_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kernel_lore_bot.sources.lore.index'`.

- [ ] **Step 4: Implement the module**

Create `kernel_lore_bot/sources/lore/index.py`:

```python
"""
The set of mailing lists lore.kernel.org actually serves.

lore publishes `manifest.js.gz` at its root: a gzipped JSON object keyed by
repository path, e.g. `/linux-media/git/0.git`. The list name is the first
path segment; a list with several epochs appears under several keys, which
collapse into one name.

This exists so a user cannot subscribe to a list that does not exist — a typo
would otherwise become a silent dead subscription that 404s on every scrape.
"""

from __future__ import annotations

import gzip
import json
import logging
import zlib
from dataclasses import dataclass
from typing import Iterable

from kernel_lore_bot.http import FetchError, HttpClient
from kernel_lore_bot.sources.lore.mbox import LORE_BASE_URL

log = logging.getLogger(__name__)

MANIFEST_PATH = "/manifest.js.gz"


class ListIndexError(Exception):
    """The manifest was fetched but could not be understood."""


def fetch_list_names(
    client: HttpClient, base_url: str = LORE_BASE_URL
) -> frozenset[str]:
    """Every list name in lore's manifest. Raises FetchError or ListIndexError."""
    raw = client.get(f"{base_url.rstrip('/')}{MANIFEST_PATH}")

    try:
        raw = gzip.decompress(raw)
    except gzip.BadGzipFile:
        pass  # already decompressed; use the bytes as-is
    except (EOFError, zlib.error) as exc:
        raise ListIndexError(f"corrupt gzip manifest: {exc}") from exc

    try:
        manifest = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ListIndexError(f"manifest is not JSON: {exc}") from exc

    if not isinstance(manifest, dict):
        raise ListIndexError(f"manifest is a {type(manifest).__name__}, expected object")

    names = {
        key.strip("/").split("/")[0].lower()
        for key in manifest
        if key.strip("/")
    }
    if not names:
        raise ListIndexError("manifest contained no list names")
    return frozenset(names)


@dataclass(frozen=True)
class ListIndex:
    """An immutable snapshot of the valid list names. Names are lowercase."""

    names: frozenset[str]

    def is_valid(self, name: str) -> bool:
        return name.strip().lower() in self.names

    def search(self, query: str, limit: int = 20) -> list[str]:
        """Substring matches, sorted. lore has ~300 lists — browsing is not viable."""
        needle = query.strip().lower()
        if not needle:
            return []
        return sorted(n for n in self.names if needle in n)[:limit]


class ListRegistry:
    """
    Holds the current ListIndex and can refresh it in place.

    Starts on `fallback` so the bot is usable before — or without — a
    successful fetch, and keeps the previous index when a refresh fails, so a
    transient lore outage repairs itself on the next scrape rather than
    needing a restart.
    """

    def __init__(
        self,
        client: HttpClient,
        base_url: str = LORE_BASE_URL,
        fallback: Iterable[str] = (),
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._index = ListIndex(frozenset(n.lower() for n in fallback))

    @property
    def index(self) -> ListIndex:
        return self._index

    def refresh(self) -> bool:
        """Fetch a fresh index. Returns False and keeps the old one on failure."""
        try:
            names = fetch_list_names(self._client, self._base_url)
        except (FetchError, ListIndexError) as exc:
            log.error(
                "Could not refresh the lore list index (%s) — keeping %d known list(s)",
                exc,
                len(self._index.names),
            )
            return False
        self._index = ListIndex(names)
        log.info("Lore list index refreshed: %d list(s)", len(names))
        return True
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_list_index.py -v`
Expected: PASS, 12 tests.

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add kernel_lore_bot/sources/lore/index.py tests/test_list_index.py tests/fixtures/lore/manifest.js.gz
git commit -m "feat: fetch and cache lore's mailing list index"
```

---

## Task 6: Route and filter the digest per subscriber

**Files:**
- Modify: `kernel_lore_bot/delivery/broadcast.py`
- Test: `tests/test_broadcast.py`

**Interfaces:**
- Consumes: `Thread.mailing_lists` (1), `fetch_threads(since, lists)` (2), `Store.mailing_lists` / `blocked_authors` / `all_mailing_lists` (3), `ListRegistry` (5).
- Produces:
  - `Broadcaster(settings, store, source, list_registry: ListRegistry | None = None)` — the `filters` constructor argument is **removed**.
  - `Broadcaster.collect(cutoff: datetime, mailing_lists: Sequence[str]) -> list[Classified]` — no longer filters.
  - `Broadcaster.visible_for(chat_id: int, classified: Sequence[Classified]) -> list[Classified]`

**Behavior being built:**
- One scrape covers `store.all_mailing_lists()`; no subscribers or no lists means nothing to fetch.
- Each subscriber's digest is the classified threads whose lists intersect theirs and whose author they have not blocked.
- A subscriber with nothing visible receives no message at all — not a header announcing zero threads.
- `format_header` is called per subscriber, with that subscriber's own count.
- Followers of an updated thread are notified regardless of their lists and blocks: they asked for that thread by name, and a follow that silently stopped firing would be worse than the noise.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_broadcast.py`. Reuse the existing `_thread` helper (updated in Task 1) and the file's existing broadcaster-construction helper.

```python
@pytest.mark.asyncio
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
    await _broadcaster(store, threads).run(bot, now=NOW)

    assert any("net@example.com" in t for t in bot.texts_to(1))
    assert not any("rcu@example.com" in t for t in bot.texts_to(1))
    assert any("rcu@example.com" in t for t in bot.texts_to(2))
    assert not any("net@example.com" in t for t in bot.texts_to(2))


@pytest.mark.asyncio
async def test_a_cross_posted_thread_reaches_both_subscribers():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["netdev"])
    store.add_subscriber(2)
    store.add_lists(2, ["lkml"])

    threads = [_thread("x@example.com", NOW, mailing_lists={"netdev", "lkml"})]
    bot = FakeBot()
    await _broadcaster(store, threads).run(bot, now=NOW)

    assert any("x@example.com" in t for t in bot.texts_to(1))
    assert any("x@example.com" in t for t in bot.texts_to(2))


@pytest.mark.asyncio
async def test_a_personal_block_hides_a_thread_from_only_that_subscriber():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    store.add_subscriber(2)
    store.block(1, "kernel test robot")

    threads = [_thread("bot@example.com", NOW, author="Kernel Test Robot")]
    bot = FakeBot()
    await _broadcaster(store, threads).run(bot, now=NOW)

    assert bot.texts_to(1) == []
    assert any("bot@example.com" in t for t in bot.texts_to(2))


@pytest.mark.asyncio
async def test_a_subscriber_with_nothing_visible_gets_no_header():
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["rcu"])

    threads = [_thread("net@example.com", NOW, mailing_lists={"netdev"})]
    bot = FakeBot()
    await _broadcaster(store, threads).run(bot, now=NOW)

    assert bot.texts_to(1) == []


@pytest.mark.asyncio
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
    await _broadcaster(store, threads).run(bot, now=NOW)

    assert "<b>1</b> new thread(s)" in bot.texts_to(1)[0]
    assert "<b>2</b> new thread(s)" in bot.texts_to(2)[0]


@pytest.mark.asyncio
async def test_followers_are_notified_about_threads_outside_their_lists():
    """An explicit follow outranks the follower's list and block settings."""
    store = InMemoryStore()
    store.add_subscriber(1)
    store.add_lists(1, ["rcu"])
    store.follow("old@example.com", 1)
    store.block(1, "Alice Adams")

    updated = _thread("old@example.com", NOW, mailing_lists={"netdev"}, root_age_hours=48)
    bot = FakeBot()
    await _broadcaster(store, [updated]).run(bot, now=NOW)

    assert any("Thread update" in t for t in bot.texts_to(1))


@pytest.mark.asyncio
async def test_nothing_is_fetched_when_no_subscriber_wants_a_list():
    store = InMemoryStore()
    store.add_subscriber(1)  # subscribed, but zero lists

    source = _RecordingSource([])
    broadcaster = Broadcaster(settings=_settings(), store=store, source=source)
    await broadcaster.run(FakeBot(), now=NOW)

    assert source.calls == []
```

Three helpers this needs. Add each only if the file does not already have an equivalent — reuse beats duplication:

```python
class _RecordingSource:
    """A Source that records the arguments it was called with."""

    def __init__(self, threads):
        self._threads = list(threads)
        self.calls: list[tuple] = []

    def fetch_threads(self, since, mailing_lists):
        self.calls.append((since, tuple(mailing_lists)))
        return list(self._threads)


def _settings(**kw):
    return Settings(loopback_hours=4.0, **kw)


def _broadcaster(store, threads):
    return Broadcaster(
        settings=_settings(), store=store, source=_RecordingSource(threads)
    )
```

`_thread` also needs a `root_age_hours` knob so a thread can be classified UPDATED — root older than the cutoff, with recent activity below it. This file already produces updated threads for the existing follower-notification tests; find that mechanism and extend `_thread` to use it. Do not introduce a second way of building an updated thread, or the two will drift.

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_broadcast.py -v`
Expected: FAIL — subscribers receive every thread regardless of their lists.

- [ ] **Step 3: Drop the construction-time filters**

In `kernel_lore_bot/delivery/broadcast.py`, rewrite `__init__`:

```python
    def __init__(
        self,
        settings: Settings,
        store: Store,
        source: Source,
        list_registry: Optional["ListRegistry"] = None,
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
```

Update the imports at the top of the file:

```python
from kernel_lore_bot.filters import BlockedAuthors
from kernel_lore_bot.sources.lore.index import ListRegistry
```

and remove the now-unused `Filter, apply_filters` import.

- [ ] **Step 4: Make collect() fetch without filtering**

```python
    def collect(self, cutoff: datetime, mailing_lists: Sequence[str]) -> list[Classified]:
        """
        Fetch and classify. No Telegram, no async, no filtering.

        Filtering is per-subscriber now (see visible_for), so it cannot happen
        here — one scrape feeds differently-filtered digests.
        """
        if self.list_registry is not None:
            self.list_registry.refresh()
        threads = list(self.source.fetch_threads(cutoff, mailing_lists))
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
```

- [ ] **Step 5: Scrape the union and invert the digest loop**

In `_run_locked`, replace the collect call and everything up to the send:

```python
        subscriber_ids = self.store.subscribers()
        if not subscriber_ids:
            log.info("No subscribers yet — nothing to send.")
            return

        wanted = sorted(self.store.all_mailing_lists())
        if not wanted:
            log.info("No subscriber wants any mailing list — nothing to fetch.")
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
        classified = await asyncio.to_thread(self.collect, cutoff, wanted)
        if not classified:
            log.info("No new threads to send.")
            return
```

Leave the `new` / `updated` split and the rest of `_run_locked` as they are.

Then rewrite `_send_digest` to loop subscribers on the outside:

```python
    async def _send_digest(self, bot, new, subscriber_ids, cutoff, blocked, now) -> None:
        if not new:
            return

        for chat_id in subscriber_ids:
            visible = self.visible_for(chat_id, new)
            if not visible:
                # Say nothing rather than sending a header announcing zero
                # threads — that reads like a bug to the person receiving it.
                continue

            if await send_to(bot, chat_id, format_header(len(visible), now)) is (
                SendResult.BLOCKED
            ):
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
```

`_notify_followers` is unchanged — an explicit follow outranks list and block settings.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_broadcast.py -v`
Expected: PASS. Existing tests in the file that add a subscriber without lists will now receive nothing — give those subscribers lists via `InMemoryStore(default_lists=("netdev",))` so they match the `_thread` helper's default list.

- [ ] **Step 7: Keep the callers compiling**

`kernel_lore_bot/delivery/app.py` and `cli.py` still pass `filters=`. Task 9 rewires them properly; for now delete the `filters` argument from the `Broadcaster(...)` call in `build_application` so the suite runs.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add kernel_lore_bot/delivery/broadcast.py kernel_lore_bot/delivery/app.py tests/test_broadcast.py
git commit -m "feat: route and filter the digest per subscriber"
```

---

## Task 7: The /lists command

**Files:**
- Modify: `kernel_lore_bot/delivery/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `Store.mailing_lists` / `add_lists` / `remove_lists` (3), `ListRegistry` and `ListIndex.is_valid` / `search` (5).
- Produces:
  - `Handlers(settings, store, list_registry: ListRegistry, on_scrape=None)` — `list_registry` is a new required argument, inserted before `on_scrape`.
  - `Handlers.lists(update, context)` — the `/lists` handler.
  - `LISTS_USAGE: str` — the usage text, reused by the bare form and the error paths.

**Command surface:**

```
/lists                 show your lists
/lists add <a> <b> …   validated against the index
/lists del <a> <b> …
/lists search <query>
```

**Behavior:**
- Not subscribed → tell them to send `/start` first; change nothing.
- Bare `/lists` → current lists, sorted, plus the usage text.
- `add` / `del` with no names → usage text.
- Names are lowercased before use — lore list names are lowercase and `is_valid` is case-insensitive.
- Each name reports its own result; one bad name does not abort the good ones.
- An invalid name suggests matches from the index when there are any.
- If `del` empties the list, warn that no digest will arrive until a list is added.
- `context.args` is python-telegram-bot's already-split argument list. Every name is HTML-escaped before interpolation.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_handlers.py`. Check the file's existing helper for building `Handlers` and extend it to pass a registry; the tests below assume a `_handlers(store, **kw)` helper and the `FakeUpdate`/`FakeContext` fakes from `conftest.py`.

```python
def _registry(names=("netdev", "lkml", "linux-media", "linux-input")):
    """A ListRegistry whose fallback is the whole index — no fetch needed."""
    from kernel_lore_bot.sources.lore.index import ListRegistry

    client = FakeHttpClient({})  # every refresh fails; the fallback stands
    return ListRegistry(client, "https://lore.example.org", fallback=names)


@pytest.mark.asyncio
async def test_lists_requires_a_subscription():
    store = InMemoryStore()
    update, context = FakeUpdate(chat_id=1), FakeContext()

    await _handlers(store).lists(update, context)

    assert "/start" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_bare_lists_shows_current_lists():
    store = InMemoryStore(default_lists=("netdev", "lkml"))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = []

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "lkml" in text and "netdev" in text


@pytest.mark.asyncio
async def test_lists_add_accepts_a_valid_name():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}
    assert "✅" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_lists_add_rejects_an_unknown_name_and_keeps_the_good_ones():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "netdev", "netdevv"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}
    text = update.message.replies[0]["text"]
    assert "netdevv" in text and "❌" in text


@pytest.mark.asyncio
async def test_lists_add_suggests_near_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "linux"]

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "linux-media" in text and "linux-input" in text


@pytest.mark.asyncio
async def test_lists_add_is_case_insensitive():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add", "NetDev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == {"netdev"}


@pytest.mark.asyncio
async def test_lists_del_removes_and_warns_when_empty():
    store = InMemoryStore(default_lists=("netdev",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["del", "netdev"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == set()
    assert "no lists" in update.message.replies[0]["text"].lower()


@pytest.mark.asyncio
async def test_lists_del_does_not_validate_against_the_index():
    """Removing a name you somehow hold must work even if lore dropped it."""
    store = InMemoryStore(default_lists=("retired-list",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["del", "retired-list"]

    await _handlers(store).lists(update, context)

    assert store.mailing_lists(1) == set()


@pytest.mark.asyncio
async def test_lists_search_shows_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["search", "linux"]

    await _handlers(store).lists(update, context)

    text = update.message.replies[0]["text"]
    assert "linux-media" in text and "linux-input" in text


@pytest.mark.asyncio
async def test_lists_search_reports_no_matches():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["search", "zzzz"]

    await _handlers(store).lists(update, context)

    assert "no lists match" in update.message.replies[0]["text"].lower()


@pytest.mark.asyncio
async def test_lists_rejects_an_unknown_subcommand():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["frobnicate", "netdev"]

    await _handlers(store).lists(update, context)

    assert "/lists add" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_lists_add_without_names_shows_usage():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["add"]

    await _handlers(store).lists(update, context)

    assert "/lists add" in update.message.replies[0]["text"]
```

`FakeContext` in `tests/conftest.py` has no `args` attribute. Add one so handlers can read it:

```python
class FakeContext:
    def __init__(self, bot=None, args=None):
        self.bot = bot or FakeBot()
        self.args = list(args or [])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: FAIL — `AttributeError: 'Handlers' object has no attribute 'lists'`.

- [ ] **Step 3: Implement the handler**

In `kernel_lore_bot/delivery/handlers.py`, add the import and the usage constant:

```python
import html

from kernel_lore_bot.sources.lore.index import ListRegistry

LISTS_USAGE = (
    "<code>/lists</code> — your lists\n"
    "<code>/lists add &lt;name&gt; …</code>\n"
    "<code>/lists del &lt;name&gt; …</code>\n"
    "<code>/lists search &lt;query&gt;</code>"
)
```

Add `list_registry` to `__init__`:

```python
    def __init__(
        self,
        settings: Settings,
        store: Store,
        list_registry: ListRegistry,
        on_scrape: Optional[Callable[[object], Awaitable[None]]] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.list_registry = list_registry
        self._on_scrape = on_scrape
```

Add a shared guard and the handler:

```python
    def _subscribed(self, chat_id: int) -> bool:
        return chat_id in self.store.subscribers()

    async def lists(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self._subscribed(chat_id):
            await update.message.reply_text(
                "❌ You are not subscribed. Send /start first."
            )
            return

        args = list(context.args or [])
        if not args:
            await update.message.reply_html(self._render_lists(chat_id))
            return

        action, names = args[0].lower(), [a.lower() for a in args[1:]]

        if action == "search":
            await update.message.reply_html(self._render_search(" ".join(names)))
        elif action == "add" and names:
            await update.message.reply_html(self._add_lists(chat_id, names))
        elif action == "del" and names:
            await update.message.reply_html(self._remove_lists(chat_id, names))
        else:
            await update.message.reply_html(LISTS_USAGE)

    # -- /lists helpers ------------------------------------------------

    def _render_lists(self, chat_id: int) -> str:
        current = sorted(self.store.mailing_lists(chat_id))
        if not current:
            body = "📭 You have <b>no lists</b> — you will not receive a digest."
        else:
            shown = "\n".join(f"• <code>{html.escape(n)}</code>" for n in current)
            body = f"📬 <b>Your lists ({len(current)}):</b>\n{shown}"
        return f"{body}\n\n{LISTS_USAGE}"

    def _render_search(self, query: str) -> str:
        if not query:
            return LISTS_USAGE
        matches = self.list_registry.index.search(query)
        if not matches:
            return f"🔍 No lists match <code>{html.escape(query)}</code>."
        shown = "\n".join(f"• <code>{html.escape(n)}</code>" for n in matches)
        return f"🔍 <b>{len(matches)} match(es):</b>\n{shown}"

    def _add_lists(self, chat_id: int, names: list[str]) -> str:
        index = self.list_registry.index
        valid = [n for n in names if index.is_valid(n)]
        added = self.store.add_lists(chat_id, valid)

        lines = []
        for name in names:
            safe = html.escape(name)
            if not index.is_valid(name):
                # Suggest rather than just rejecting: a typo and a half-
                # remembered name look identical from here.
                hints = index.search(name, limit=5)
                suffix = f" — did you mean {', '.join(hints)}?" if hints else ""
                lines.append(f"❌ unknown list: <code>{safe}</code>{suffix}")
            elif name in added:
                lines.append(f"✅ added <code>{safe}</code>")
            else:
                lines.append(f"ℹ️ already subscribed to <code>{safe}</code>")
        return "\n".join(lines)

    def _remove_lists(self, chat_id: int, names: list[str]) -> str:
        # Deliberately not validated against the index: a name already in
        # your state must be removable even if lore has since dropped it.
        removed = self.store.remove_lists(chat_id, names)

        lines = []
        for name in names:
            safe = html.escape(name)
            if name in removed:
                lines.append(f"✅ removed <code>{safe}</code>")
            else:
                lines.append(f"ℹ️ you were not subscribed to <code>{safe}</code>")

        if not self.store.mailing_lists(chat_id):
            lines.append(
                "\n📭 You now have <b>no lists</b> — you will not receive a "
                "digest until you add one."
            )
        return "\n".join(lines)
```

- [ ] **Step 4: Route every test construction through one helper**

`Handlers` now takes a required `list_registry`, so every existing construction in `tests/test_handlers.py` breaks. Add this helper near the top of the file and rewrite each existing test to use it, rather than adding the argument at a dozen call sites:

```python
def _handlers(store, settings=None, on_scrape=None):
    return Handlers(
        settings=settings or Settings(),
        store=store,
        list_registry=_registry(),
        on_scrape=on_scrape,
    )
```

Leave `kernel_lore_bot/delivery/app.py` alone — it is Task 9's job, and `app.py` is not exercised by this task's tests.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kernel_lore_bot/delivery/handlers.py tests/test_handlers.py tests/conftest.py
git commit -m "feat: /lists command for per-subscriber mailing lists"
```

---

## Task 8: The /filters command

**Files:**
- Modify: `kernel_lore_bot/delivery/handlers.py`
- Test: `tests/test_handlers.py`

**Interfaces:**
- Consumes: `Store.blocked_authors` / `block` / `unblock` (3), `Handlers._subscribed` (7).
- Produces:
  - `Handlers.filters(update, context)` — the `/filters` handler.
  - `FILTERS_USAGE: str`

**Command surface:**

```
/filters                  show your blocked authors
/filters block <name>     remainder of the line is one name
/filters unblock <name>
```

**Behavior:**
- Author names contain spaces, so `block` / `unblock` take **the whole remainder joined by a space** as one name — unlike `/lists`, which splits on whitespace.
- Blocks match case-insensitively as a substring of the author (that is `BlockedAuthors`' existing rule), so blocking `robot` mutes `Kernel Test Robot`. Say so in the confirmation.
- Not subscribed → tell them to send `/start` first.
- Bare `/filters` → current blocks plus usage. Missing name or unknown subcommand → usage.
- Only blocked authors are supported; the `Filter` protocol already makes other filter kinds additive, and this plan does not add any.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_handlers.py`:

```python
@pytest.mark.asyncio
async def test_filters_requires_a_subscription():
    store = InMemoryStore()
    update, context = FakeUpdate(chat_id=1), FakeContext()

    await _handlers(store).filters(update, context)

    assert "/start" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_bare_filters_lists_current_blocks():
    store = InMemoryStore(default_blocks=("kernel test robot",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = []

    await _handlers(store).filters(update, context)

    assert "kernel test robot" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_filters_block_takes_the_whole_remainder_as_one_name():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "Kernel", "Test", "Robot"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == {"Kernel Test Robot"}


@pytest.mark.asyncio
async def test_filters_block_reports_a_duplicate():
    store = InMemoryStore(default_blocks=("Kernel Test Robot",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "kernel", "test", "robot"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == {"Kernel Test Robot"}
    assert "already" in update.message.replies[0]["text"].lower()


@pytest.mark.asyncio
async def test_filters_unblock_removes_case_insensitively():
    store = InMemoryStore(default_blocks=("Kernel Test Robot",))
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["unblock", "KERNEL", "TEST", "ROBOT"]

    await _handlers(store).filters(update, context)

    assert store.blocked_authors(1) == set()
    assert "✅" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_filters_unblock_reports_a_miss():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["unblock", "nobody"]

    await _handlers(store).filters(update, context)

    assert "ℹ️" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_filters_block_without_a_name_shows_usage():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block"]

    await _handlers(store).filters(update, context)

    assert "/filters block" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_filters_rejects_an_unknown_subcommand():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["frobnicate", "someone"]

    await _handlers(store).filters(update, context)

    assert "/filters block" in update.message.replies[0]["text"]


@pytest.mark.asyncio
async def test_filters_escapes_html_in_an_author_name():
    store = InMemoryStore()
    store.add_subscriber(1)
    update, context = FakeUpdate(chat_id=1), FakeContext()
    context.args = ["block", "<b>evil</b>"]

    await _handlers(store).filters(update, context)

    assert "&lt;b&gt;evil&lt;/b&gt;" in update.message.replies[0]["text"]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: FAIL — `AttributeError: 'Handlers' object has no attribute 'filters'`.

- [ ] **Step 3: Implement the handler**

Add the usage constant next to `LISTS_USAGE` in `kernel_lore_bot/delivery/handlers.py`:

```python
FILTERS_USAGE = (
    "<code>/filters</code> — your blocked authors\n"
    "<code>/filters block &lt;name&gt;</code>\n"
    "<code>/filters unblock &lt;name&gt;</code>\n\n"
    "<i>Matching is case-insensitive and partial: blocking "
    "<code>robot</code> mutes “Kernel Test Robot”.</i>"
)
```

Add the handler and its helpers:

```python
    async def filters(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self._subscribed(chat_id):
            await update.message.reply_text(
                "❌ You are not subscribed. Send /start first."
            )
            return

        args = list(context.args or [])
        if not args:
            await update.message.reply_html(self._render_blocks(chat_id))
            return

        action = args[0].lower()
        # Unlike a list name, an author name contains spaces — take the whole
        # remainder as one name rather than splitting it.
        name = " ".join(args[1:]).strip()

        if action == "block" and name:
            await update.message.reply_html(self._block_author(chat_id, name))
        elif action == "unblock" and name:
            await update.message.reply_html(self._unblock_author(chat_id, name))
        else:
            await update.message.reply_html(FILTERS_USAGE)

    # -- /filters helpers ----------------------------------------------

    def _render_blocks(self, chat_id: int) -> str:
        current = sorted(self.store.blocked_authors(chat_id))
        if not current:
            body = "🔇 You have <b>no blocked authors</b>."
        else:
            shown = "\n".join(f"• <code>{html.escape(n)}</code>" for n in current)
            body = f"🔇 <b>Blocked authors ({len(current)}):</b>\n{shown}"
        return f"{body}\n\n{FILTERS_USAGE}"

    def _block_author(self, chat_id: int, name: str) -> str:
        safe = html.escape(name)
        if self.store.block(chat_id, name):
            return f"✅ Blocked <code>{safe}</code> — their threads will be hidden."
        return f"ℹ️ You already block <code>{safe}</code>."

    def _unblock_author(self, chat_id: int, name: str) -> str:
        safe = html.escape(name)
        if self.store.unblock(chat_id, name):
            return f"✅ Unblocked <code>{safe}</code>."
        return f"ℹ️ You were not blocking <code>{safe}</code>."
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_handlers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kernel_lore_bot/delivery/handlers.py tests/test_handlers.py
git commit -m "feat: /filters command for per-subscriber blocked authors"
```

---

## Task 9: Wire it together and document it

**Files:**
- Modify: `kernel_lore_bot/delivery/app.py`
- Modify: `kernel_lore_bot/cli.py`
- Modify: `kernel_lore_bot/settings.py`
- Modify: `kernel_lore_bot/delivery/handlers.py` (`WELCOME_TEXT`)
- Modify: `README.md`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–8.
- Produces:
  - `cli.build_components(settings) -> tuple[Store, LoreSource, ListRegistry]` — the third element is now a `ListRegistry`, not a filter list.
  - `app.build_application(settings, store, source, list_registry)` and `app.run_bot(settings, store, source, list_registry)` — the `filters` parameter is **removed** from both.

- [ ] **Step 1: Write the failing test**

Update `tests/test_cli.py`. The existing test around line 79 asserts on `source.mailing_lists`; replace it and add coverage for the new wiring:

```python
def test_build_components_seeds_the_store_from_settings(tmp_path):
    settings = Settings(
        state_dir=tmp_path, mailing_lists=("netdev",), blocked_authors=("robot",)
    )
    store, source, registry = build_components(settings)
    store.add_subscriber(1)

    assert store.mailing_lists(1) == {"netdev"}
    assert store.blocked_authors(1) == {"robot"}


def test_build_components_falls_back_to_the_configured_lists(tmp_path):
    """No network here, so the registry must start on the settings fallback."""
    settings = Settings(state_dir=tmp_path, mailing_lists=("netdev",))
    _, _, registry = build_components(settings)

    assert registry.index.is_valid("netdev") is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `build_components` still returns a filter list, so `registry.index` raises `AttributeError`.

- [ ] **Step 3: Rewire cli.build_components**

In `kernel_lore_bot/cli.py`:

```python
def build_components(settings: Settings) -> tuple[Store, LoreSource, ListRegistry]:
    """Construct the real, I/O-touching implementations."""
    client = RequestsClient(timeout=settings.request_timeout)
    store = JsonStore(
        settings.state_file,
        default_lists=settings.mailing_lists,
        default_blocks=settings.blocked_authors,
    )
    source = LoreSource(client=client, progress=TqdmProgress())
    # Starts on the configured lists so the bot works before the first
    # successful manifest fetch. Deliberately NOT refreshed here:
    # build_components must stay free of network I/O so it is testable, and
    # the first scheduled scrape runs with `first=0` — i.e. immediately at
    # startup — so the real index lands within seconds anyway.
    registry = ListRegistry(client, fallback=settings.mailing_lists)
    return store, source, registry
```

Update the imports: drop `from kernel_lore_bot.filters import BlockedAuthors, Filter`, add `from kernel_lore_bot.sources.lore.index import ListRegistry`.

In `main`, rename the unpacked third element and update both call sites:

```python
    store, source, registry = build_components(settings)

    if args.dry:
        broadcaster = Broadcaster(settings, store, source)
        cutoff = broadcaster.cutoff(datetime.now(timezone.utc))
        print(format_dry_run(broadcaster.collect(cutoff, settings.mailing_lists), cutoff))
        return 0

    log.info("Starting Telegram bot…")
    run_bot(settings, store, source, registry)
    return 0
```

A dry run deliberately passes `settings.mailing_lists` rather than the store's union: it is a preview of the configured feed, not of any one subscriber's digest.

- [ ] **Step 4: Rewire app.py**

In `kernel_lore_bot/delivery/app.py`, replace the `filters` parameter with `list_registry`, register the two commands, and extend the menu:

```python
PUBLIC_COMMANDS = [
    ("start", "Subscribe to the daily kernel digest"),
    ("stop", "Unsubscribe"),
    ("status", "Check your subscription status"),
    ("lists", "Choose which mailing lists you receive"),
    ("filters", "Manage your blocked authors"),
]
ADMIN_COMMANDS = [("scrape", "Trigger an immediate scrape")]


def build_application(
    settings: Settings,
    store: Store,
    source: Source,
    list_registry: ListRegistry,
) -> Application:
    """Build the PTB application. Does not start it."""
    broadcaster = Broadcaster(
        settings=settings, store=store, source=source, list_registry=list_registry
    )
    handlers = Handlers(
        settings=settings,
        store=store,
        list_registry=list_registry,
        on_scrape=broadcaster.run,
    )
    ...
```

Add the handler registrations next to the existing ones, before the `CallbackQueryHandler`:

```python
    app.add_handler(CommandHandler("lists", handlers.lists))
    app.add_handler(CommandHandler("filters", handlers.filters))
```

Update `run_bot` the same way:

```python
def run_bot(
    settings: Settings,
    store: Store,
    source: Source,
    list_registry: ListRegistry,
) -> None:
    app = build_application(settings, store, source, list_registry)
    log.info("Bot is running. Send /start to the bot on Telegram to subscribe.")
    app.run_polling(drop_pending_updates=True)
```

Swap the `Filter` import for `from kernel_lore_bot.sources.lore.index import ListRegistry`, and drop the now-unused `Sequence` import if nothing else uses it.

- [ ] **Step 5: Update the settings comments and the welcome text**

In `kernel_lore_bot/settings.py`, document what the two fields now mean — they no longer decide what anyone receives:

```python
@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    telegram_bot_token: str = PLACEHOLDER_TOKEN
    admin_chat_id: int = 0
    # Seeds a new subscriber's own lists, and is the fallback list index when
    # lore's manifest cannot be fetched. Does NOT decide what an existing
    # subscriber receives — that is per-subscriber state (see /lists).
    mailing_lists: tuple[str, ...] = DEFAULT_MAILING_LISTS
    # Seeds a new subscriber's own blocklist. See /filters.
    blocked_authors: tuple[str, ...] = DEFAULT_BLOCKED_AUTHORS
```

In `kernel_lore_bot/delivery/handlers.py`, extend `WELCOME_TEXT`:

```python
WELCOME_TEXT = (
    "👋 <b>Welcome to Kernel Lore Bot!</b>\n\n"
    "You'll receive a daily digest of <b>new</b> Linux kernel mailing list threads.\n\n"
    "🆕 = new thread  🔄 = updated thread\n\n"
    "Tap <b>🔔 Follow</b> on any thread to get notified when it receives updates.\n\n"
    "Commands:\n"
    "<code>/start</code>   — subscribe to the daily digest\n"
    "<code>/stop</code>    — unsubscribe\n"
    "<code>/status</code>  — check your subscription status\n"
    "<code>/lists</code>   — choose which mailing lists you receive\n"
    "<code>/filters</code> — mute authors you don't want to see\n"
)
```

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest`
Expected: PASS, no failures.

- [ ] **Step 7: Update the README**

Four edits to `README.md`:

1. The commands table gains two rows:

```markdown
| `/lists` | anyone | Show or change which mailing lists you receive |
| `/filters` | anyone | Show or change your blocked authors |
```

2. Replace the sentence "The watched mailing lists and blocked authors are defaults in `settings.py` (`DEFAULT_MAILING_LISTS`, `DEFAULT_BLOCKED_AUTHORS`)." with:

```markdown
Mailing lists and blocked authors are **per subscriber**, managed with `/lists`
and `/filters`. `DEFAULT_MAILING_LISTS` and `DEFAULT_BLOCKED_AUTHORS` in
`settings.py` only seed a new subscriber, and serve as the fallback list index
when lore's manifest cannot be fetched. List names are validated against
lore's `manifest.js.gz`.
```

3. In "How it works", replace the filtering paragraph — it currently claims there is no per-subscriber filtering, which this change makes false:

```markdown
One scrape covers the union of every subscriber's lists. Filtering is
per-subscriber and happens at send time: you receive a thread if one of the
lists it appeared on is one of yours and you have not blocked its author. A
thread cross-posted to several lists carries all of them, so it reaches
everyone who asked for any of them. Following a thread outranks both — a
follow notification arrives regardless of your lists and blocks.
```

Also update the ASCII diagram: `apply_filters()` no longer sits between the source and `classify`.

4. Update the state example to v2:

```json
{
  "version": 2,
  "subscribers": {
    "12345": {
      "follows": ["msgid@example.com"],
      "mailing_lists": ["netdev", "rcu"],
      "blocked_authors": ["kernel test robot"]
    }
  }
}
```

Note in that section that a v1 file is migrated on load: records without the new keys inherit the configured defaults.

5. In "Extending it", replace "**A new mailing list:** add it to `DEFAULT_MAILING_LISTS` in `settings.py`." with "**A new mailing list:** none needed — any list in lore's manifest can be added with `/lists add`."

- [ ] **Step 8: Verify the docs against the code**

Re-read the README sections you changed alongside `broadcast.py` and `json_store.py`. Every claim must be true of the code as it now stands — the previous README carried a filtering claim that silently went stale, which is exactly what this step exists to prevent.

- [ ] **Step 9: Commit**

```bash
git add kernel_lore_bot/ README.md tests/
git commit -m "feat: wire per-user lists and filters through the app"
```

---

## Verification

After Task 9, confirm end to end:

- [ ] `python -m pytest` — full suite green.
- [ ] `python -m kernel_lore_bot --dry` — prints a digest preview without a token.
- [ ] Manual smoke test with a real token: `/start`, then `/lists` (shows the ~18 defaults), `/lists del lkml`, `/lists search bluetooth`, `/lists add linux-bluetooth`, `/lists add nonsense` (rejected with suggestions), `/filters block robot`, `/filters` (shows it), `/filters unblock robot`.
- [ ] Inspect `data/state.json` — `"version": 2`, each subscriber carrying `mailing_lists` and `blocked_authors`.
- [ ] Migration check: point `KERNEL_BOT_STATE_DIR` at a copy of a real v1 state file, start the bot, send `/lists`, and confirm the defaults were inherited rather than the subscriber going empty.
