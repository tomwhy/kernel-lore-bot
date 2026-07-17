"""
Telegram command and button handlers.

Handlers holds its dependencies rather than importing globals, so tests drive it
with an InMemoryStore and a fake bot. Add new commands as methods here and
register them in app.py.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from kernel_lore_bot.delivery.keyboards import follow_keyboard, unfollow_keyboard, parse_callback
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

WELCOME_TEXT = (
    "👋 <b>Welcome to Kernel Lore Bot!</b>\n\n"
    "You'll receive a daily digest of <b>new</b> Linux kernel mailing list threads.\n\n"
    "🆕 = new thread  🔄 = updated thread\n\n"
    "Tap <b>🔔 Follow</b> on any thread to get notified when it receives updates.\n\n"
    "Commands:\n"
    "<code>/start</code>  — subscribe to the daily digest\n"
    "<code>/stop</code>   — unsubscribe\n"
    "<code>/status</code> — check your subscription status\n"
)


class Handlers:
    """Every user-facing interaction lives here."""

    def __init__(
        self,
        settings: Settings,
        store: Store,
        on_scrape: Optional[Callable[[object], Awaitable[None]]] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self._on_scrape = on_scrape

    # -- commands ------------------------------------------------------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "there"

        if self.store.add_subscriber(chat_id):
            log.info("/start from %s (chat=%d) — subscribed", first_name, chat_id)
            await update.message.reply_html(WELCOME_TEXT)
        else:
            await update.message.reply_text(
                "✅ You're already subscribed! "
                "You'll receive the next digest at the scheduled time."
            )

    async def stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "someone"
        log.info("/stop from %s (chat=%d)", first_name, chat_id)

        # One call, one atomic write: subscription and follows go together.
        if self.store.remove_subscriber(chat_id):
            await update.message.reply_text(
                "👋 You've been unsubscribed and removed from all thread follows.\n"
                "Send /start any time to re-subscribe."
            )
        else:
            await update.message.reply_text("ℹ️ You weren't subscribed.")

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id

        if chat_id not in self.store.subscribers():
            await update.message.reply_text(
                "❌ You are not subscribed. Send /start to subscribe."
            )
            return

        await update.message.reply_html(
            f"✅ You are subscribed to the daily kernel digest.\n"
            f"🔔 Following <b>{self.store.following_count(chat_id)}</b> thread(s) for updates."
        )

    async def scrape(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        first_name = update.effective_user.first_name or "someone"

        if not self._is_admin(chat_id):
            log.warning(
                "/scrape ignored for %s (chat=%d) — not admin", first_name, chat_id
            )
            return

        log.info("/scrape triggered by %s (chat=%d)", first_name, chat_id)
        await update.message.reply_text("🔧 Scraping feeds now…")

        try:
            if self._on_scrape is not None:
                await self._on_scrape(context.bot)
            await update.message.reply_text("✅ Scrape complete.")
        except Exception as exc:  # noqa: BLE001 - report to the admin, stay alive
            log.exception("Error during /scrape: %s", exc)
            await update.message.reply_text(f"❌ Scrape failed: {str(exc)[:200]}")

    def _is_admin(self, chat_id: int) -> bool:
        # admin_chat_id == 0 disables privileged commands entirely.
        return self.settings.admin_chat_id != 0 and chat_id == self.settings.admin_chat_id

    # -- buttons -------------------------------------------------------

    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        chat_id = query.message.chat_id
        parsed = parse_callback(query.data)

        if parsed is None:
            # Either genuinely unknown data, or an InvalidCallbackData object
            # from a button that predates the last restart.
            await query.answer("This button has expired — please use a newer message.")
            log.warning("Unusable callback data: %r", query.data)
            return

        await query.answer()  # drop the spinner before doing any work

        action, thread_id = parsed
        if action == "follow":
            is_new = self.store.follow(thread_id, chat_id)
            notice = (
                "🔔 You'll be notified when this thread is updated."
                if is_new
                else "🔔 Already following this thread."
            )
            new_markup = unfollow_keyboard(thread_id)
        else:
            was_following = self.store.unfollow(thread_id, chat_id)
            notice = (
                "🔕 Unfollowed. You won't receive further updates for this thread."
                if was_following
                else "ℹ️ You weren't following this thread."
            )
            new_markup = follow_keyboard(thread_id)

        try:
            await query.edit_message_reply_markup(reply_markup=new_markup)
        except TelegramError:
            pass  # message too old to edit — harmless

        await context.bot.send_message(chat_id=chat_id, text=notice)
