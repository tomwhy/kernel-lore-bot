# ============================================================
#  Kernel Lore Bot — Configuration
#  Edit this file to customize the bot's behavior
# ============================================================

import os
import pathlib
import datetime
import zoneinfo

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
# Run every day at 00:00 IST
SCHEDULE_TIME = datetime.time(hour=0, tzinfo=zoneinfo.ZoneInfo("Asia/Jerusalem"))

# How many hours back to look for "new" posts on first run
# (prevents a flood of old messages on initial startup)
LOOPBACK_HOURS = 24

# -----------------------------------------------------------
# Mailing list display names
# Maps List-Id address (e.g. "linux-kernel@vger.kernel.org")
# to a short human-readable name shown in the digest.
# If an address is not listed here, the display name from the
# List-Id header is used, falling back to the raw address.
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
# Examples: ["noreply@kernel.org", "Some Bot"]
BLOCKED_AUTHORS: list[str] = [
    "kernel test robot"
]

# Mailing lists to fetch
MAILLING_LISTS: list[str] = [
    "linux-media",
    "lkml",
    "stable",
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
