"""Telegram application wiring. The only module that knows the handler names."""

from __future__ import annotations

import datetime
import logging
from typing import Sequence

from telegram import BotCommandScopeChat
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from kernel_lore_bot.delivery.broadcast import Broadcaster
from kernel_lore_bot.delivery.handlers import Handlers
from kernel_lore_bot.filters import Filter
from kernel_lore_bot.settings import Settings
from kernel_lore_bot.sources.base import Source
from kernel_lore_bot.storage import Store

log = logging.getLogger(__name__)

PUBLIC_COMMANDS = [
    ("start", "Subscribe to the daily kernel digest"),
    ("stop", "Unsubscribe"),
    ("status", "Check your subscription status"),
]
ADMIN_COMMANDS = [("scrape", "Trigger an immediate scrape")]


def build_application(
    settings: Settings,
    store: Store,
    source: Source,
    filters: Sequence[Filter] = (),
) -> Application:
    """Build the PTB application. Does not start it."""
    broadcaster = Broadcaster(settings=settings, store=store, source=source)
    handlers = Handlers(settings=settings, store=store, on_scrape=broadcaster.run)

    async def set_command_menus(app: Application) -> None:
        await app.bot.set_my_commands(PUBLIC_COMMANDS)
        if settings.admin_chat_id != 0:
            await app.bot.set_my_commands(
                PUBLIC_COMMANDS + ADMIN_COMMANDS,
                scope=BotCommandScopeChat(chat_id=settings.admin_chat_id),
            )

    async def scheduled_broadcast(context: ContextTypes.DEFAULT_TYPE) -> None:
        await broadcaster.run(context.bot)

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(set_command_menus)
        # Message-IDs can push "follow:<msgid>" past Telegram's 64-byte
        # callback_data limit, so PTB caches the payload and sends a UUID.
        .arbitrary_callback_data(True)
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.start))
    app.add_handler(CommandHandler("stop", handlers.stop))
    app.add_handler(CommandHandler("status", handlers.status))
    app.add_handler(CommandHandler("scrape", handlers.scrape))
    # Must come after the command handlers.
    app.add_handler(CallbackQueryHandler(handlers.on_button))

    app.job_queue.run_repeating(
        scheduled_broadcast,
        interval=datetime.timedelta(hours=settings.schedule_interval_hours),
        first=0,
    )

    return app


def run_bot(
    settings: Settings,
    store: Store,
    source: Source,
    filters: Sequence[Filter] = (),
) -> None:
    app = build_application(settings, store, source, filters)
    log.info("Bot is running. Send /start to the bot on Telegram to subscribe.")
    app.run_polling(drop_pending_updates=True)
