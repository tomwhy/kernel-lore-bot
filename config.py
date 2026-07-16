# ============================================================
#  Kernel Lore Bot — Configuration
#  Edit this file to customize the bot's behavior
# ============================================================

import os
import pathlib
import datetime

# -----------------------------------------------------------
# Telegram
# -----------------------------------------------------------
def _read_secret(name: str, fallback: str = "") -> str:
    secret_path = f"/run/secrets/{name}"
    try:
        with open(secret_path) as _f:
            return _f.read().strip()
    except FileNotFoundError:
        pass
    return os.environ.get(name.upper(), fallback)

TELEGRAM_BOT_TOKEN: str = _read_secret("telegram_bot_token", "YOUR_BOT_TOKEN_HERE")

# Chat ID of the bot administrator.
# Only this user can run privileged commands like /scrape.
# Find yours by messaging @userinfobot on Telegram.
# Leave as 0 to disable privileged commands entirely.
ADMIN_CHAT_ID: int = int(os.environ.get("ADMIN_CHAT_ID", "0"))

# -----------------------------------------------------------
# Scraping schedule
# -----------------------------------------------------------

# How many hours back to look for "new" posts on first run
# (prevents a flood of old messages on initial startup)
LOOPBACK_HOURS = 4

# How often to run the scrape, in hours. Supports fractions (e.g. 0.5 = every 30 min).
SCHEDULE_INTERVAL_HOURS: float = float(os.environ.get("SCHEDULE_INTERVAL_HOURS", LOOPBACK_HOURS))

# -----------------------------------------------------------
# Mailing list display names
# Maps List-Id address to a short human-readable name shown in the digest.
# Threads whose List-Id is not present here will have mailing_list=None
# and will pass the whitelist filter (shown without a list label).
# -----------------------------------------------------------
MAILING_LIST_NAMES: dict[str, str] = {
    "dev.dpdk.org": "dpdk"
}

# -----------------------------------------------------------
# Filters (blocklist)
# Threads matching ANY of these are silently dropped.
# -----------------------------------------------------------

# Authors to block — case-insensitive substring match against the
# display name / email in the From header.
BLOCKED_AUTHORS: list[str] = [
    "kernel test robot"
]

# Mailing lists to fetch
MAILLING_LISTS: list[str] = [
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
]

# -----------------------------------------------------------
# Rate limiting / politeness
# -----------------------------------------------------------
REQUEST_TIMEOUT       = 15      # HTTP timeout per feed

# -----------------------------------------------------------
# State file (tracks subscribers across runs)
# -----------------------------------------------------------
STATE_DIR        = pathlib.Path(os.environ.get("KERNEL_BOT_STATE_DIR", "data"))
SUBSCRIBERS_FILE = STATE_DIR / "subscribers.json"
FOLLOWS_FILE     = STATE_DIR / "follows.json"