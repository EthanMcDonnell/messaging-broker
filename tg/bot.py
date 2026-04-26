"""
Telegram platform adapter.
Handles Telegram-specific I/O; delegates all message routing and Claude
invocation to the process_message function injected from main.py.

Job callbacks (inline keyboard buttons posted via the HTTP API) are handled
here and routed through dispatcher.py. Callback data format: "job:<id>:<action>".
"""

import logging
from typing import Callable

from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

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
    job_store=None,
    process_for_project_fn: Callable[[str, dict], str] | None = None,
) -> None:
    """
    Start the Telegram bot.

    config:                  shared config dict
    state:                   shared project state
    process_fn:              process_message(text, config, state) → reply str  (fallback/DM)
    dry_run:                 if True, log replies instead of sending them
    job_store:               JobStore instance for async job callbacks
    process_for_project_fn:  fn(text, project) → reply str, used when topic routing resolves project
    """
    tg_cfg = config.get("telegram", {})
    bot_token = tg_cfg["bot_token"]
    allowed_user_id = int(tg_cfg["allowed_user_id"])

    # topic_id → project dict for direct routing (bypasses router + global state)
    topic_map: dict[int, dict] = {
        p["telegram_topic_id"]: p
        for p in config.get("projects", [])
        if p.get("telegram_topic_id")
    }

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

        thread_id = update.message.message_thread_id
        topic_project = topic_map.get(thread_id) if thread_id else None

        if dry_run:
            label = f"topic:{thread_id} ({topic_project['name']})" if topic_project else "DM/general"
            reply = f"[DRY RUN] [{label}] Would process: {text[:100]}"
            logger.info("[DRY RUN] %s", reply)
            await update.message.reply_text(reply)
            return

        placeholder = await update.message.reply_text("thinking...")

        if topic_project and process_for_project_fn:
            reply = process_for_project_fn(text, topic_project)
        else:
            reply = process_fn(text, config, state)

        state.save()

        logger.info("Reply (%d chars): %s...", len(reply), reply[:60])

        chunks = _split_message(reply)
        await placeholder.edit_text(chunks[0])
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk)

    async def handle_job_callback(update: Update, context) -> None:
        try:
            cq = update.callback_query
            logger.debug("Callback query received: data=%r", cq.data if cq else None)

            if not cq or not cq.data or not cq.data.startswith("job:"):
                return

            parts = cq.data.split(":", 2)
            if len(parts) != 3:
                logger.warning("Malformed callback data: %r", cq.data)
                await cq.answer()
                return

            _, job_id, action = parts
            logger.info("Job callback: job_id=%s action=%s", job_id, action)

            if not job_store:
                await cq.answer(text="No job store available.")
                return

            job = job_store.get(job_id)
            if not job:
                logger.warning("Job not found: %s", job_id)
                await cq.answer(text="Job not found.")
                return

            if job["status"] == "responded":
                logger.info("Job %s already responded", job_id)
                await cq.answer(text="Already responded.")
                return

            job_store.respond(job_id, action)
            await cq.answer()
            await cq.edit_message_reply_markup(reply_markup=None)

            chat_id = update.effective_chat.id
            thread_id = cq.message.message_thread_id if cq.message else None
            logger.info("Dispatching job %s: action=%s chat_id=%s thread_id=%s", job_id, action, chat_id, thread_id)

            from dispatcher import dispatch
            await dispatch(job, action, config, bot=context.bot, chat_id=chat_id, thread_id=thread_id)
            logger.info("Job %s dispatched successfully", job_id)

        except Exception:
            logger.exception("Error handling job callback for update: %s", update)

    app = Application.builder().token(bot_token).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    if job_store:
        app.add_handler(CallbackQueryHandler(handle_job_callback, pattern=r"^job:"))

    topic_count = len(topic_map)
    if topic_count:
        logger.info("Telegram bot started. %d topic(s) configured.", topic_count)
    else:
        logger.info("Telegram bot started. Project: %s", state.current_project)
    if dry_run:
        logger.info("DRY RUN MODE — messages will not be sent")
    app.run_polling()
