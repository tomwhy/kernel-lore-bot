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
| `/lists` | anyone | Show or change which mailing lists you receive |
| `/filters` | anyone | Show or change your blocked sender addresses |
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

Mailing lists and blocked senders are **per subscriber**, managed with `/lists`
and `/filters`. `DEFAULT_MAILING_LISTS` and `DEFAULT_BLOCKED_AUTHORS` in
`settings.py` only seed a new subscriber, and serve as the fallback list index
when lore's manifest cannot be fetched. List names are validated against
lore's `manifest.js.gz`.

Blocks are **email addresses, matched in full** against a thread's `From:`
address — not display names, and not substrings. Blocking `lkp@intel.com`
mutes that sender and nothing else; a thread whose `From:` carried no
parseable address is never blocked.

## How it works

```
LoreSource.fetch_threads(since, mailing_lists)
    │
    ├─ GET /<list>/new.atom?t=…      paginate backwards until older than `since`
    ├─ GET /all/<msgid>/t.mbox.gz    one full thread per entry
    ├─ parse mbox → Thread tree      (deduplicated across lists by Message-ID)
    │
    ▼
digest.classify(cutoff)  →  Broadcaster.run(bot)
                                 │
                                 ├─ NEW      → filtered per subscriber, with a Follow button
                                 └─ UPDATED  → that thread's followers only
```

A thread is **new** if its root arrived within the lookback window, and
**updated** if the root is older but the thread has recent activity.

One scrape covers the union of every subscriber's lists. Filtering is
per-subscriber and happens at send time: you receive a thread if one of the
lists it appeared on is one of yours and you have not blocked its author. A
thread cross-posted to several lists carries all of them, so it reaches
everyone who asked for any of them. Following a thread outranks both — a
follow notification arrives regardless of your lists and blocks. A followed
thread that the list scrape didn't already cover is fetched separately, by
its Message-ID (`LoreSource.fetch_threads_by_id`), so following a thread
keeps working even for a subscriber with no mailing lists at all.

## State

Everything lives in one file, `$KERNEL_BOT_STATE_DIR/state.json`:

```json
{
  "version": 3,
  "subscribers": {
    "12345": {
      "follows": ["msgid@example.com"],
      "mailing_lists": ["netdev", "rcu"],
      "blocked_authors": ["lkp@intel.com"]
    }
  }
}
```

It is written atomically (temp file + `os.replace`). Older files are migrated
on load:

- **`version: 1`** predates `mailing_lists`/`blocked_authors` entirely. A
  record missing either key inherits the currently configured
  `DEFAULT_MAILING_LISTS`/`DEFAULT_BLOCKED_AUTHORS` rather than starting
  empty. An explicit empty list is left alone — that means the subscriber
  deliberately removed everything.
- **`version: 2`** held `blocked_authors` as display names, matched as
  case-insensitive substrings. Under address matching a name can never match,
  so name entries are dropped on load (logged at WARNING) and surviving
  addresses are normalized. A record left with nothing is reseeded from
  `DEFAULT_BLOCKED_AUTHORS`; a record that was already empty is untouched.

A file with no `version` key, or a non-integer one, is treated as the oldest
schema — migrating is safe to repeat, whereas assuming "current" would leave
dead name blocks in place forever.

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
│   └── lore/        atom.py + mbox.py (pure), source.py + index.py (I/O)
├── storage/         Store protocol, JsonStore, InMemoryStore
├── delivery/        formatting, keyboards, handlers, broadcast, app
└── cli.py           entry point
tests/               pytest suite; fixtures/ holds real lore samples
```

## Extending it

- **A new mailing list:** none needed — any list in lore's manifest can be
  added with `/lists add`.
- **A new filter:** write a class with `allows(thread) -> bool` and use it in
  `Broadcaster.visible_for`, which is where per-subscriber filtering happens.
- **A new command:** add an async method to `delivery/handlers.py` and register
  it in `delivery/app.py`.
- **A new source:** implement `Source.fetch_threads(since, mailing_lists) -> Iterable[Thread]`.
  Nothing downstream changes.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No test makes a real network call: `FakeHttpClient` (in `tests/conftest.py`)
serves the checked-in fixtures from `tests/fixtures/lore/`. Most tests also stay
off disk by using `InMemoryStore` (`kernel_lore_bot/storage/memory.py`) in place
of `JsonStore`. The exception is `JsonStore`'s own tests, which use pytest's
`tmp_path`: the file format, the atomic write, and the v1-to-v2 migration are
the things under test, so they need a real directory — never your actual state.

## Docker

```bash
docker build -t kernel-lore-bot .
docker run -d \
  -v $(pwd)/data:/app/data \
  -e TELEGRAM_BOT_TOKEN=… \
  -e ADMIN_CHAT_ID=… \
  kernel-lore-bot
```

Or `docker compose up -d --build` using the bundled `compose.yaml`, which picks
up the `build:` block from `compose.override.yaml` automatically.

## Deploying

The host runs the image from the registry, so it needs no source tree — only
`compose.yaml`, `.env`, and `secrets/telegram_bot_token`. The latter two are
gitignored and must be placed by hand; note `-n`, since a trailing newline in
the token file breaks auth against the Telegram API.

Build and push from a development machine:

```bash
docker build -t ghcr.io/tomwhy/kernel-lore-bot:latest .
echo $GITHUB_TOKEN | docker login ghcr.io -u tomwhy --password-stdin
docker push ghcr.io/tomwhy/kernel-lore-bot:latest
```

Then, on the host:

```bash
scp compose.yaml root@<host>:~/kernel-lore-bot/
mkdir -p secrets
echo -n '<bot-token>' > secrets/telegram_bot_token
chmod 600 secrets/telegram_bot_token
echo 'ADMIN_CHAT_ID=<id>' > .env

docker compose up -d
```

To update: `docker compose pull && docker compose up -d`.
