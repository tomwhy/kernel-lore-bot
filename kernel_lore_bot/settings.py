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

# Email addresses, not display names — matched in full against a thread's
# From: address. lkp@intel.com is the Intel 0-day/kernel test robot, which
# posts high-volume automated build reports.
DEFAULT_BLOCKED_AUTHORS: tuple[str, ...] = ("lkp@intel.com",)


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
