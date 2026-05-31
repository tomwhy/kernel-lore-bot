"""
bot.py – Telegram api for updates and handle bot commands

Commands available to all users:
  /start   – subscribe to the daily digest
  /stop    – unsubscribe
  /status  – show subscription status

Admin-only commands (ADMIN_CHAT_ID in config.py):
  /debug   – trigger an immediate scrape and broadcast right now
"""

import logging
from telegram import Update, Bot, BotCommandScopeChat
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, Application
from telegram.error import Forbidden, TelegramError
from typing import Callable, Generator
import datetime
import html
import time

import config
import scraper
import subscribers

log = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Scraper reply constants                                           #
# ------------------------------------------------------------------ #

TELEGRAM_MAX_CHARS = 4096   # Telegram hard limit per message

LABEL_ICON = {"security": "🔴", "feature": "🟢"}
 
THREAD_SEPARATOR = "\n\n" + "─" * 19 + "\n\n"

# ------------------------------------------------------------------ #
#  Formatting                                                        #
# ------------------------------------------------------------------ #
 
def _h(text: str) -> str:
    """Minimal HTML escaping for user-supplied strings."""
    return html.escape(text)
 
 
def _format_thread(thread: scraper.Thread) -> str:
    icon     = LABEL_ICON.get(thread.label, "⚪")
    date_str = thread.updated.strftime("%Y-%m-%d %H:%M UTC")
 
    lines = [
        f'{icon} <b>{_h(thread.title)}</b>',
        f'📋 <code>{_h(thread.list_name)}</code>  '
        f'👤 {_h(thread.author)}  🕐 {_h(date_str)}',
    ]
    if thread.summary:
        snip = thread.summary[:160].replace("\n", " ")
        lines.append(f"<i>{_h(snip)}</i>")
    lines.append(f'<a href="{thread.url}">🔗 View thread</a>')
 
    return "\n".join(lines)
 
 
def _format_header(total: int) -> str:
    now = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    return (
        f"🐧 <b>Kernel Lore Daily Digest</b>\n"
        f"<i>{now}</i> — <b>{total}</b> new thread(s)\n\n"
    )
 
# ------------------------------------------------------------------ #
#  Batching                                                            #
# ------------------------------------------------------------------ #
 
def _build_batches(threads: list[scraper.Thread]) -> Generator[str, None, None]:
    """
    Pack threads into as few messages as possible, splitting only on
    thread boundaries so no thread is ever truncated.
    Each batch is guaranteed to be ≤ TELEGRAM_MAX_CHARS characters.
    """

    msg: str = _format_header(len(threads))
    current: str = None
    need_sep = False

    for thread in threads:
        current = _format_thread(thread)
        needed = len(current)
        if need_sep:
            needed += len(THREAD_SEPARATOR)

        if len(msg) + needed > TELEGRAM_MAX_CHARS:
            yield msg
            msg = current
        else:
            if need_sep:
                msg += THREAD_SEPARATOR
            msg += current
            need_sep = True

    yield msg

# ------------------------------------------------------------------ #
#  Scrape Job                                                        #
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
 

async def broadcast_new_threads(context: ContextTypes.DEFAULT_TYPE):
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
    for i, msg in enumerate(_build_batches(threads), start=1):
        for chat_id in chat_ids:
            ok = await send_to(context.bot, chat_id, msg)
            if ok:
                total_sent += 1
            else:
                all_blocked.add(chat_id)
 
        log.debug("Batch #%d done", i)
        time.sleep(0.1)

    if all_blocked:
        subscribers.remove_many(all_blocked)
        log.info("Auto-removed %d blocked subscriber(s)", len(all_blocked))
 
    log.info("Broadcast complete — %d total send(s)", total_sent)

# ------------------------------------------------------------------ #
#  Command Handlers                                                  #
# ------------------------------------------------------------------ #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "there"
    is_new     = subscribers.add(chat_id)
 
    if is_new:
        log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)
        await update.message.reply_html(
            f"👋 <b>Welcome to Kernel Lore Bot!</b>\n\n"
            f"You'll receive a daily digest of interesting Linux kernel threads:\n"
            f"🔴 Security fixes &amp; CVEs\n"
            f"🟢 New features &amp; patches\n\n"
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

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "there"
    was_subbed = subscribers.remove(chat_id)
 
    log.info("/stop from %s (chat=%d)", first_name, chat_id)
 
    if was_subbed:
        await update.message.reply_text(
            "👋 You've been unsubscribed.\n"
            "Send /start any time to re-subscribe."
        )
    else:
        await update.message.reply_text("ℹ️ You weren't subscribed.")
 
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in subscribers.load():
        await update.message.reply_text("✅ You are subscribed to the daily kernel digest.")
    else:
        await update.message.reply_text("❌ You are not subscribed. Send /start to subscribe.")
 
def is_admin(cmd: str, update: Update) -> bool:
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "someone"

    if chat_id != config.ADMIN_CHAT_ID:
        log.warning(
            "/%s ignored for %s (chat=%d) — not admin", 
            cmd,
            first_name,
            chat_id
        )
        return False
    return True


async def scrape(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id    = update.effective_chat.id
    first_name = update.effective_user.first_name or "someone"

    if not is_admin("scrape", update):
        return

    log.info("/scrape triggered by %s (chat=%d)", first_name, chat_id)
    await update.message.reply_text("🔧 scraping feeds now...")
 
    try:
        await broadcast_new_threads(context)
        await update.message.reply_text("✅ Debug run complete.")
    except Exception as exc:  # noqa: BLE001
        log.exception("Error during /debug run_job: %s", exc)
        await update.message.reply_text(f"❌ Run failed: {str(exc)[:200]}")


async def purge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass


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


def run_bot():
    app = (
            ApplicationBuilder()
            .token(config.TELEGRAM_BOT_TOKEN)
            .post_init(set_command_menus)
            .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("scrape", scrape))
    app.add_handler(CommandHandler("purge", purge))

    app.job_queue.run_daily(broadcast_new_threads, config.SCHEDULE_TIME)

    log.info("Bot is running. Send /start to the bot on Telegram to subscribe.")

    app.run_polling(drop_pending_updates=True)
