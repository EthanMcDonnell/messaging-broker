"""
Telegram platform adapter.
Handles Telegram-specific I/O; delegates all message routing and Claude
invocation to the process_message function injected from main.py.
"""

import logging
from typing import Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from security import RateLimiter
from state import State

logger = logging.getLogger(__name__)


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split a long response into Telegram-sized chunks."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def run_telegram_bot(
    config: dict,
    state: State,
    process_fn: Callable[[str, dict, State], str],
    dry_run: bool = False,
) -> None:
    """
    Start the Telegram bot.

    config:      shared config dict
    state:       shared project state
    process_fn:  process_message(text, config, state) → reply str
    dry_run:     if True, log replies instead of sending them
    """
    tg_cfg = config.get("telegram", {})
    bot_token = tg_cfg["bot_token"]
    allowed_user_id = int(tg_cfg["allowed_user_id"])

    rate_limits = config.get("rate_limits", {})
    msg_limiter = RateLimiter(
        max_count=rate_limits.get("messages_per_minute", 10),
        window_seconds=60,
    )

    def _is_authorized(update: Update) -> bool:
        if update.effective_user.id == allowed_user_id:
            return True
        logger.warning("Unauthorized Telegram access from user %s", update.effective_user.id)
        return False

    async def cmd_start(update: Update, context) -> None:
        if not _is_authorized(update):
            return
        await update.message.reply_text(
            f"Connected. Current project: {state.current_project or '(none)'}.\n"
            'Send a message, or say "list projects" / "switch to [name]".'
        )

    async def cmd_status(update: Update, context) -> None:
        if not _is_authorized(update):
            return
        import shutil
        claude_found = shutil.which("claude") is not None
        projects = config.get("projects", [])
        proj_list = "\n".join(
            f"  {'→' if p['name'] == state.current_project else '•'} {p['name']}"
            for p in projects
        )
        await update.message.reply_text(
            f"Platform: telegram\n"
            f"Current project: {state.current_project or '(none)'}\n"
            f"Claude CLI: {'found' if claude_found else 'NOT FOUND'}\n"
            f"Projects:\n{proj_list or '  (none configured)'}"
        )

    async def handle_message(update: Update, context) -> None:
        if not _is_authorized(update):
            return

        text = update.message.text
        if not text:
            return

        if not msg_limiter.allow():
            await update.message.reply_text("Rate limit exceeded. Please slow down.")
            return

        if dry_run:
            reply = f"[DRY RUN] Would process: {text[:100]}"
            logger.info("[DRY RUN] %s", reply)
            await update.message.reply_text(reply)
            return

        placeholder = await update.message.reply_text("thinking...")

        reply = process_fn(text, config, state)
        state.save()

        logger.info("Reply (%d chars): %s...", len(reply), reply[:60])

        chunks = _split_message(reply)
        await placeholder.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Telegram bot started. Project: %s", state.current_project)
    if dry_run:
        logger.info("DRY RUN MODE — messages will not be sent")
    app.run_polling()
