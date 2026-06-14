"""
bot.py – Telegram api for updates and handle bot commands

Commands available to all users:
  /start   – subscribe to the daily digest
  /stop    – unsubscribe
  /status  – show subscription status

Admin-only commands (ADMIN_CHAT_ID in config.py):
  /scrape  – trigger an immediate scrape and broadcast right now
"""

import datetime
import html
import logging
import time

from telegram import Update, Bot, BotCommandScopeChat
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.error import Forbidden, TelegramError

import config
import scraper
import subscribers

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Constants                                                           #
# ------------------------------------------------------------------ #


STATUS_BADGE = {"new": "🆕", "updated": "🔄"}


# ------------------------------------------------------------------ #
#  Formatting helpers                                                  #
# ------------------------------------------------------------------ #

def _h(text: str) -> str:
    """Minimal HTML escaping for user-supplied strings."""
    return html.escape(text)


def _cutoff() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=config.LOOPBACK_HOURS)


def _count_new_entries(node: scraper.Node, cutoff: datetime.datetime) -> int:
    """Count all entries in a node's subtree (including the node itself) that are within the cutoff."""
    count = 0
    stack = [node]
    while stack:
        n = stack.pop()
        if n.entry.updated >= cutoff:
            count += 1
        stack.extend(n.children)
    return count


def _format_thread(thread: scraper.Thread) -> str:
    cutoff   = _cutoff()
    badge    = STATUS_BADGE.get(thread.status, "")
    date_str = thread.updated.strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"{badge} <b>{_h(thread.title)}</b>",
        f"👤 {_h(thread.author)}  🕐 {_h(date_str)}",
    ]

    if thread.mailing_list:
        lines.append(f"📬 {_h(thread.mailing_list)}")

    new_count = sum(_count_new_entries(r, cutoff) for r in thread.roots)
    if new_count:
        reply_str = "entry" if new_count == 1 else "entries"
        lines.append(f"<i>... {new_count} new {reply_str}</i>")

    lines.append(f'<a href="{thread.url}">🔗 View thread</a>')

    return "\n".join(lines)


def _format_header(total: int) -> str:
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return (
        f"🐧 <b>Kernel Lore Digest</b>\n"
        f"<i>{now}</i> — <b>{total}</b> new thread(s)"
    )


# ------------------------------------------------------------------ #
#  Scrape job                                                          #
# ------------------------------------------------------------------ #

async def send_to(bot: Bot, chat_id: int, text: str) -> bool:
    """Send one HTML message to one chat. Returns True on success."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        return True
    except Forbidden:
        log.warning("chat_id=%d blocked the bot — will unsubscribe", chat_id)
        return False
    except TelegramError as exc:
        log.error("Telegram error sending to chat_id=%d: %s", chat_id, exc)
        return False


async def broadcast_new_threads(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_ids = subscribers.load()
    if not chat_ids:
        log.info("No subscribers yet — nothing to send.")
        return

    threads = scraper.fetch_new_threads()
    if not threads:
        log.info("No new threads to send.")
        return

    log.info(
        "Broadcasting %d thread(s) to %d subscriber(s)",
        len(threads), len(chat_ids),
    )

    all_blocked: set[int] = set()
    total_sent = 0

    # Send header
    header = _format_header(len(threads))
    for chat_id in chat_ids:
        ok = await send_to(context.bot, chat_id, header)
        if not ok:
            all_blocked.add(chat_id)

    # Send one message per thread
    for i, thread in enumerate(threads, start=1):
        msg = _format_thread(thread)
        for chat_id in chat_ids:
            if chat_id in all_blocked:
                continue
            ok = await send_to(context.bot, chat_id, msg)
            if ok:
                total_sent += 1
            else:
                all_blocked.add(chat_id)

        log.debug("Thread #%d/%d done", i, len(threads))
        time.sleep(0.01)

    if all_blocked:
        subscribers.remove_many(all_blocked)
        log.info("Auto-removed %d blocked subscriber(s)", len(all_blocked))

    log.info("Broadcast complete — %d total send(s)", total_sent)


# ------------------------------------------------------------------ #
#  Command handlers                                                    #
# ------------------------------------------------------------------ #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "there"
    is_new     = subscribers.add(chat_id)

    if is_new:
        log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)
        await update.message.reply_html(
            f"👋 <b>Welcome to Kernel Lore Bot!</b>\n\n"
            f"You'll receive a daily digest of Linux kernel mailing list threads.\n\n"
            f"🆕 = new thread  🔄 = updated thread\n"
            f"<b>Bold lines</b> = new since last digest\n\n"
            f"Commands:\n"
            f"<code>/start</code>  — subscribe to the daily digest\n"
            f"<code>/stop</code>   — unsubscribe\n"
            f"<code>/status</code> — check your subscription status\n"
        )
    else:
        await update.message.reply_text(
            "✅ You're already subscribed! "
            "You'll receive the next digest at the scheduled time."
        )


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "someone"
    was_subbed = subscribers.remove(chat_id)

    log.info("/stop from %s (chat=%d)", first_name, chat_id)

    if was_subbed:
        await update.message.reply_text(
            "👋 You've been unsubscribed.\n"
            "Send /start any time to re-subscribe."
        )
    else:
        await update.message.reply_text("ℹ️ You weren't subscribed.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if chat_id in subscribers.load():
        await update.message.reply_text("✅ You are subscribed to the daily kernel digest.")
    else:
        await update.message.reply_text("❌ You are not subscribed. Send /start to subscribe.")


def _is_admin(cmd: str, update: Update) -> bool:
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "someone"

    if chat_id != config.ADMIN_CHAT_ID:
        log.warning(
            "/%s ignored for %s (chat=%d) — not admin",
            cmd, first_name, chat_id,
        )
        return False
    return True


async def scrape(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "someone"

    if not _is_admin("scrape", update):
        return

    log.info("/scrape triggered by %s (chat=%d)", first_name, chat_id)
    await update.message.reply_text("🔧 Scraping feeds now…")

    try:
        await broadcast_new_threads(context)
        await update.message.reply_text("✅ Scrape complete.")
    except Exception as exc:  # noqa: BLE001
        log.exception("Error during /scrape: %s", exc)
        await update.message.reply_text(f"❌ Scrape failed: {str(exc)[:200]}")


# ------------------------------------------------------------------ #
#  Bot setup                                                           #
# ------------------------------------------------------------------ #

async def set_command_menus(app: Application) -> None:
    """
    Set the Telegram command menu:
    - All users see: /start, /stop, /status
    - Admin chat additionally sees: /scrape
    Called once at startup via post_init.
    """
    public_commands = [
        ("start",  "Subscribe to the daily kernel digest"),
        ("stop",   "Unsubscribe"),
        ("status", "Check your subscription status"),
    ]

    await app.bot.set_my_commands(public_commands)

    if config.ADMIN_CHAT_ID != 0:
        await app.bot.set_my_commands(
            public_commands + [("scrape", "Trigger an immediate scrape")],
            scope=BotCommandScopeChat(chat_id=config.ADMIN_CHAT_ID),
        )


def run_bot() -> None:
    app = (
        ApplicationBuilder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .post_init(set_command_menus)
        .build()
    )

    app.add_handler(CommandHandler("start",  start))
    app.add_handler(CommandHandler("stop",   stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("scrape", scrape))

    app.job_queue.run_repeating(
        broadcast_new_threads,
        interval=datetime.timedelta(hours=config.SCHEDULE_INTERVAL_HOURS),
        first=0,
    )

    log.info("Bot is running. Send /start to the bot on Telegram to subscribe.")
    app.run_polling(drop_pending_updates=True)