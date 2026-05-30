# 🐧 Kernel Lore Telegram Bot

Scrapes [lore.kernel.org](https://lore.kernel.org) Atom feeds daily and forwards
**security fixes** (🔴) and **new features** (🟢) to a Telegram channel or chat.

---

## Quick Start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) → `/newbot`
2. Copy the **HTTP API token** you receive.
3. Add the bot to your channel/group, or start a DM.
4. Get your **chat ID**:
   - For a private chat: message [@userinfobot](https://t.me/userinfobot)
   - For a channel: use `@channelusername` (e.g. `@my_kernel_news`) or the numeric ID

### 3. Configure the bot

Open **`config.py`** and set:

```python
TELEGRAM_BOT_TOKEN = "123456:ABC-your-token-here"
TELEGRAM_CHAT_ID   = "-100123456789"   # channel / group / user ID
```

All other settings are optional — the defaults work out of the box.

### 4. Run

```bash
# Test without sending anything (dry-run):
python bot.py --test

# Run once right now (send real messages):
python bot.py --now

# Start the daily scheduler (runs at 08:00 UTC by default):
python bot.py
```

---

## Configuration reference (`config.py`)

| Variable | Default | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Target chat/channel/user |
| `SCHEDULE_CRON` | `"0 8 * * *"` | When to run (minute hour) |
| `LOOKBACK_HOURS_FIRST_RUN` | `24` | How far back to look on first run |
| `WATCHED_LISTS` | 9 lists | Atom feeds to scrape |
| `SECURITY_KEYWORDS` | list | Subjects that trigger 🔴 |
| `FEATURE_KEYWORDS` | list | Subjects that trigger 🟢 |
| `MAX_MESSAGES_PER_RUN` | `30` | Telegram flood cap |
| `REQUEST_DELAY_SECONDS` | `2` | Politeness delay between feeds |

### Adding more mailing lists

```python
WATCHED_LISTS = [
    ...
    ("rust-for-linux", "https://lore.kernel.org/rust-for-linux/new.atom"),
]
```

Find available lists at: https://lore.kernel.org/

---

## Docker

```bash
docker build -t kernel-lore-bot .
docker run -d \
  -v $(pwd)/data:/app/data \
  -e KERNEL_BOT_STATE_DIR=/app/data \
  kernel-lore-bot
```

Or with docker-compose:

```yaml
services:
  bot:
    build: .
    volumes:
      - ./data:/app/data
    restart: unless-stopped
```

---

## How it works

```
fetch_all_feeds()
    │
    ├─ GET lore.kernel.org/<list>/new.atom  (Atom XML)
    │      for each configured list
    │
    ├─ Parse <entry> elements
    │      id, title, author, updated, link, summary
    │
    ├─ Classify by title keywords
    │      security keywords → label="security" 🔴
    │      feature keywords  → label="feature"  🟢
    │      no match          → dropped
    │
    ├─ Filter already-seen IDs  (seen_threads.json)
    │
    └─ Send to Telegram
           digest header → individual thread messages
```

State is persisted in `seen_threads.json` (configurable via `STATE_FILE`).
IDs older than 30 days are automatically pruned.

---

## File layout

```
kernel-lore-bot/
├── bot.py          # entry point & scheduler
├── config.py       # ← edit this
├── scraper.py      # Atom feed fetcher + classifier
├── notifier.py     # Telegram message formatter + sender
├── state.py        # seen-thread persistence
├── requirements.txt
├── Dockerfile
└── README.md
```
