"""
Telegram command and button handlers.

Handlers holds its dependencies rather than importing globals, so tests drive it
with an InMemoryStore and a fake bot. Add new commands as methods here and
register them in app.py.
"""

from __future__ import annotations

import html
import logging
from typing import Awaitable, Callable, Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from kernel_lore_bot.delivery.keyboards import follow_keyboard, unfollow_keyboard, parse_callback
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.lore.index import ListRegistry
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

LISTS_USAGE = (
    "<code>/lists</code> — your lists\n"
    "<code>/lists add &lt;name&gt; …</code>\n"
    "<code>/lists del &lt;name&gt; …</code>\n"
    "<code>/lists search &lt;query&gt;</code>"
)

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
        list_registry: ListRegistry,
        on_scrape: Optional[Callable[[object], Awaitable[None]]] = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.list_registry = list_registry
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

    def _subscribed(self, chat_id: int) -> bool:
        return chat_id in self.store.subscribers()

    async def lists(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        if not self._subscribed(chat_id):
            await update.message.reply_text(
                "❌ You are not subscribed. Send /start first."
            )
            return

        args = list(context.args or [])
        if not args:
            await update.message.reply_html(self._render_lists(chat_id))
            return

        action, names = args[0].lower(), [a.lower() for a in args[1:]]

        if action == "search":
            await update.message.reply_html(self._render_search(" ".join(names)))
        elif action == "add" and names:
            await update.message.reply_html(self._add_lists(chat_id, names))
        elif action == "del" and names:
            await update.message.reply_html(self._remove_lists(chat_id, names))
        else:
            await update.message.reply_html(LISTS_USAGE)

    # -- /lists helpers ------------------------------------------------

    def _render_lists(self, chat_id: int) -> str:
        current = sorted(self.store.mailing_lists(chat_id))
        if not current:
            body = "📭 You have <b>no lists</b> — you will not receive a digest."
        else:
            shown = "\n".join(f"• <code>{html.escape(n)}</code>" for n in current)
            body = f"📬 <b>Your lists ({len(current)}):</b>\n{shown}"
        return f"{body}\n\n{LISTS_USAGE}"

    def _render_search(self, query: str) -> str:
        if not query:
            return LISTS_USAGE
        matches = self.list_registry.index.search(query)
        if not matches:
            return f"🔍 No lists match <code>{html.escape(query)}</code>."
        shown = "\n".join(f"• <code>{html.escape(n)}</code>" for n in matches)
        return f"🔍 <b>{len(matches)} match(es):</b>\n{shown}"

    def _add_lists(self, chat_id: int, names: list[str]) -> str:
        # dict.fromkeys dedupes while preserving the order the user typed
        # names in, so "add netdev netdev" reports netdev once, not twice.
        names = list(dict.fromkeys(names))
        index = self.list_registry.index
        valid = [n for n in names if index.is_valid(n)]
        added = self.store.add_lists(chat_id, valid)

        lines = []
        for name in names:
            safe = html.escape(name)
            if not index.is_valid(name):
                # Suggest rather than just rejecting: a typo and a half-
                # remembered name look identical from here.
                hints = index.suggest(name, limit=5)
                suffix = f" — did you mean {', '.join(hints)}?" if hints else ""
                lines.append(f"❌ unknown list: <code>{safe}</code>{suffix}")
            elif name in added:
                lines.append(f"✅ added <code>{safe}</code>")
            else:
                lines.append(f"ℹ️ already subscribed to <code>{safe}</code>")
        return "\n".join(lines)

    def _remove_lists(self, chat_id: int, names: list[str]) -> str:
        # Same dedupe treatment as _add_lists, for the same reason.
        names = list(dict.fromkeys(names))
        # Deliberately not validated against the index: a name already in
        # your state must be removable even if lore has since dropped it.
        removed = self.store.remove_lists(chat_id, names)

        lines = []
        for name in names:
            safe = html.escape(name)
            if name in removed:
                lines.append(f"✅ removed <code>{safe}</code>")
            else:
                lines.append(f"ℹ️ you were not subscribed to <code>{safe}</code>")

        if not self.store.mailing_lists(chat_id):
            lines.append(
                "\n📭 You now have <b>no lists</b> — you will not receive a "
                "digest until you add one."
            )
        return "\n".join(lines)

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
        # query.message.chat_id, not .chat_id: python-telegram-bot 22 hands
        # back an InaccessibleMessage for a deleted/too-old message, and
        # InaccessibleMessage has no .chat_id attribute — only .chat. Both
        # Message and InaccessibleMessage expose .chat.id, so that's the
        # surface that works for either.
        chat_id = query.message.chat.id
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
