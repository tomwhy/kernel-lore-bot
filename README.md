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

Filtering (e.g. blocked authors) is applied to the thread as a whole, before
the new-vs-updated split — there is no per-subscriber filtering; every
subscriber sees the same digest.

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

No test makes a real network call: `FakeHttpClient` (in `tests/conftest.py`)
serves the checked-in fixtures from `tests/fixtures/lore/`. Most tests also stay
off disk by using `InMemoryStore` (`kernel_lore_bot/storage/memory.py`) in place
of `JsonStore`. The exception is `JsonStore`'s own tests, which use pytest's
`tmp_path`: the file format, the atomic write, and the legacy migration are the
things under test, so they need a real directory — never your actual state.

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
