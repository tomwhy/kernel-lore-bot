# Per-user mailing lists and filters

**Date:** 2026-07-18
**Status:** approved, ready for planning

## Problem

Mailing lists and author blocks are global. `settings.mailing_lists` decides what
every subscriber receives, and `apply_filters` runs once over the whole digest
before the new-vs-updated split, so every subscriber sees exactly the same
threads. A user who only cares about `netdev` gets seventeen other lists, and a
user who wants to mute a noisy author cannot.

This makes both per-subscriber, adds commands to manage them, and validates list
names against lore's real list index.

## Goals

- Each subscriber chooses their own mailing lists and their own blocked authors.
- List names are validated against lore.kernel.org's actual list index.
- Existing subscribers see no change in behavior after the upgrade.
- Each scrape fetches only lists somebody actually subscribes to.

## Non-goals

- Filter kinds beyond blocked authors (keyword, subsystem, path). The `Filter`
  protocol already makes these additive; this spec does not add them.
- An inline-keyboard settings UI. Typed commands handle lore's ~300 lists;
  a keyboard does not without paging plus search.
- Per-subscriber scrape scheduling. One shared scrape, routed at send time.

## Design

### 1. Domain model: a thread belongs to many lists

`Thread.mailing_list: str` becomes `Thread.mailing_lists: frozenset[str]`.

This is load-bearing, not cosmetic. Threads are deduplicated across lists by
Message-ID, so today a thread cross-posted to `lkml` and `netdev` keeps only
whichever list was fetched first. Under per-user routing that would silently
drop the thread for every `netdev`-only subscriber — and most kernel threads are
cross-posted, so this is the common case rather than an edge case.

`mbox.parse_thread` still takes a single list name and wraps it in a
one-element frozenset.

`LoreSource.fetch_threads` stops streaming and materializes into
`dict[root_id, Thread]`, unioning the current list into an already-seen thread's
set instead of skipping it. The existing `seen` set covers *every* node id in a
thread (so a reply's Message-ID does not trigger a refetch); it becomes
`dict[node_id, root_id]` so a hit can find which thread to union into. Progress
bars are unaffected.

`LoreSource` takes its list set per call — `fetch_threads(since, lists)` — rather
than at construction, so the caller can pass the current union.

### 2. Storage

`Subscriber` gains two fields:

```python
mailing_lists: set[str]
blocked_authors: set[str]
```

On-disk record becomes `{"follows": [...], "mailing_lists": [...],
"blocked_authors": [...]}`, all sorted for stable writes. `STATE_VERSION` goes
1 → 2.

**Migration.** A record missing either key gets the defaults, so existing
subscribers keep receiving exactly what they receive today. Storage must not
import `settings`, so the defaults are constructor arguments to the store
(`JsonStore(path, default_lists=…, default_blocks=…)`); `add_subscriber` seeds
new subscribers from the same values. A v1 file is upgraded in place on first
write.

`Store` protocol additions:

```python
mailing_lists(chat_id) -> set[str]
add_lists(chat_id, names) -> set[str]        # returns those actually added
remove_lists(chat_id, names) -> set[str]     # returns those actually removed
blocked_authors(chat_id) -> set[str]
block(chat_id, name) -> bool
unblock(chat_id, name) -> bool
all_mailing_lists() -> set[str]              # union across subscribers
```

`all_mailing_lists` is computed by scanning subscribers. Unlike `followers()`
this is not a hot path — it runs once per scrape, not once per delivered
message — so it needs no reverse index.

The store does not validate list names; it stores what it is given. Validation
against the index belongs to the handler, so the store stays free of any
knowledge of lore.

`Settings.mailing_lists` and `Settings.blocked_authors` survive as the seed
values handed to the store and as the index fallback. They no longer decide what
anyone receives.

### 3. Validating list names

New module `sources/lore/index.py`:

- `fetch_list_names(client, base_url) -> frozenset[str]` — GETs `manifest.js.gz`
  from the lore root, gunzips it, parses the JSON object, and takes the first
  path segment of each key (`/linux-media/git/0.git` → `linux-media`).
- `ListIndex` — wraps the frozenset with `is_valid(name)` and `search(query)`.

Built in `cli.build_components`. If the fetch or the parse fails, log an error
and fall back to `DEFAULT_MAILING_LISTS` as the allowlist: the bot still starts
and existing subscribers still get their digests, with only the ability to add
exotic lists degraded. The scheduled job refreshes the index before each scrape —
one cheap request — and a failed refresh keeps the previous value, so a transient
lore outage repairs itself without a restart.

### 4. Routing and filtering

`apply_filters` moves out of `Broadcaster.collect()` and into the send path.
Per subscriber:

```python
visible = [c for c in classified
           if c.thread.mailing_lists & sub_lists
           and BlockedAuthors(tuple(sub_blocks)).allows(c.thread)]
```

The digest loop inverts from *for thread → for subscriber* to *for subscriber →
for thread*, and `format_header` is called per subscriber since each now sees
their own thread count. A subscriber whose `visible` is empty receives nothing
at all — not a header announcing zero threads.

**Explicit follows beat filters.** An updated thread still notifies its
followers regardless of their lists or blocks. They asked for that specific
thread by name, and a follow that silently stops firing because the list was
dropped would be worse than the noise.

There is no global filter anymore: `DEFAULT_BLOCKED_AUTHORS` is only the seed
for a new subscriber's personal list, which means a user can unblock the kernel
test robot if they genuinely want it. Consequently the `filters` parameter
threaded through `cli.build_components`, `run_bot`, `build_application`, and
`Broadcaster.__init__` is removed — filters are now per-subscriber state, not
construction-time config.

Cost: filtering is now O(subscribers × threads) rather than O(threads). At this
bot's scale that is trivially cheaper than the Telegram sends it precedes.

### 5. Commands

Two subcommand routers. The bare form shows current state.

```
/lists                    show your lists
/lists add <a> <b> …      validated against the index
/lists del <a> <b> …
/lists search <query>     lore has ~300 lists; browsing is not viable
/filters                  show your blocked authors
/filters block <name>     remainder of the line is one name
/filters unblock <name>
```

Author names contain spaces, so `/filters block` takes the whole remainder as a
single name. List names never contain spaces, so `/lists add|del` takes
space-separated tokens.

Behavior:

- Multi-name add/del reports per name: `✅ added netdev` / `❌ unknown list: netdevv`.
- An unknown or missing subcommand replies with the usage text above.
- A chat that is not subscribed is told to send `/start` first.
- Removing your last list warns that you will receive nothing until you add one.
- `WELCOME_TEXT`, `PUBLIC_COMMANDS` in `app.py`, and the README command table
  all list the new commands.

Telegram's command menu cannot autocomplete subcommands; `/lists` and `/filters`
with no arguments showing both current state and usage is the mitigation.

### 6. Testing

New `tests/test_list_index.py` — manifest parsing, a malformed manifest, and the
fallback path — with a small gzipped manifest fixture under `tests/fixtures/lore/`.

Extended:

- `test_json_store.py` — v1 → v2 migration fills in defaults; v2 round-trips.
- `test_store.py` — the new protocol methods, including `all_mailing_lists`.
- `test_handlers.py` — each subcommand, validation failure, unknown subcommand,
  not-subscribed, last-list-removed warning.
- `test_broadcast.py` — two subscribers on disjoint lists each receive only
  their own threads; a subscriber's personal block hides a thread from them but
  not from others; a follower is notified about a thread outside their lists.
- `test_lore_source.py` — a cross-posted thread ends up with both lists.
- `test_models.py`, `test_filters.py`, `test_mbox.py` — the model change.

`FakeHttpClient` gains the manifest URL so no test makes a real network call.

## Files touched

| File | Change |
|---|---|
| `models.py` | `mailing_list` → `mailing_lists: frozenset[str]` |
| `sources/lore/mbox.py` | wrap list name in a frozenset |
| `sources/lore/source.py` | union lists on dedup; per-call list set |
| `sources/lore/index.py` | **new** — manifest fetch + `ListIndex` |
| `storage/base.py` | `Subscriber` fields, new `Store` methods |
| `storage/json_store.py` | v2 format, v1 migration, seeded defaults |
| `filters.py` | unchanged protocol; `BlockedAuthors` built per subscriber |
| `delivery/broadcast.py` | per-subscriber routing and filtering; drop `filters` arg |
| `delivery/handlers.py` | `/lists` and `/filters` routers |
| `delivery/app.py` | register commands; extend `PUBLIC_COMMANDS` |
| `cli.py` | build `ListIndex`; drop global filter construction |
| `README.md` | commands table, state format, "How it works" |

## Open risks

- **State format change.** A v2 file read by an older build loses the new fields.
  Acceptable: the same is already true of the v1 migration, and a rollback
  degrades to global behavior rather than losing subscribers.
- **Scrape scope now depends on user input.** A subscriber adding a large list
  lengthens every scrape for everyone. Validation prevents typos but not
  enthusiasm; if it becomes a problem, a per-subscriber list cap is the fix.
