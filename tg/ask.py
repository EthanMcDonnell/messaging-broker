#!/usr/bin/env python3
"""
@mc
ask.py — Send an inline keyboard question to Telegram and return the user's choice.

Usage (CLI):
    python3 ask.py "Question?" "Option A" "Option B" "Option C"
    python3 ask.py --message-prefix "Context header" "Question?" "A" "B" "C"
    python3 ask.py --timeout 3600 "Question?" "A" "B"
    python3 ask.py --config /path/to/config.yaml "Question?" "A" "B"
    python3 ask.py --multi "Pick all that apply?" "A" "B" "C"

Stdout:
    Single-select: 0-based index of the selected option (e.g. "0", "1", "2")
    Multi-select:  space-separated indices of selected options (e.g. "0 2")
    "skip" if the user tapped Skip or the timeout elapsed

Exit codes:
    0  — success (including skip/timeout)
    1  — Telegram API error
    2  — configuration error

Library usage:
    from tg.ask import ask_user
    import asyncio
    index = asyncio.run(ask_user(
        question="Pick one?",
        options=["A", "B", "C"],
        bot_token="...",
        chat_id=123456789,
        message_prefix="Optional header",
        timeout_seconds=86400,
    ))
    # index is int (0-based) or None (skip/timeout)

Design note:
    Uses raw bot.get_updates() rather than Application.run_polling() so it
    coexists with the main bot daemon on the same token. The daemon has no
    CallbackQueryHandler registered, so callback queries are silently ignored
    by it and handled exclusively here.
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

import yaml


def load_config(config_path: Optional[Path] = None) -> dict:
    """Load config.yaml. Defaults to <script_dir>/../config.yaml."""
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    if not config_path.exists():
        print(f"Error: config file not found: {config_path}", file=sys.stderr)
        sys.exit(2)
    with open(config_path) as f:
        return yaml.safe_load(f)


def _build_keyboard(options: list[str], callback_prefix: str):
    """Build InlineKeyboardMarkup with one button per option plus a Skip button."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for i, option in enumerate(options):
        label = str(i + 1)
        rows.append([InlineKeyboardButton(label, callback_data=f"{callback_prefix}:{i}")])
    rows.append([InlineKeyboardButton("Skip ↩", callback_data=f"{callback_prefix}:-1")])
    return InlineKeyboardMarkup(rows)


def _build_keyboard_multi(options: list[str], callback_prefix: str, selected: set[int]):
    """Build InlineKeyboardMarkup with toggle buttons for multi-select."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for i, option in enumerate(options):
        check = "✓ " if i in selected else ""
        rows.append([InlineKeyboardButton(f"{check}{i + 1}", callback_data=f"{callback_prefix}:{i}")])
    rows.append([
        InlineKeyboardButton("✅ Confirm", callback_data=f"{callback_prefix}:confirm"),
        InlineKeyboardButton("Skip ↩", callback_data=f"{callback_prefix}:-1"),
    ])
    return InlineKeyboardMarkup(rows)


def _format_message_text(
    question: str,
    options: list[str],
    message_prefix: Optional[str],
) -> str:
    """Compose the full message body."""
    parts = []
    if message_prefix:
        parts.append(message_prefix)
        parts.append("")
    parts.append(question)
    parts.append("")
    for i, option in enumerate(options, 1):
        parts.append(f"{i}. {option}")
    return "\n".join(parts)


async def _send_question_message(bot, chat_id: int, text: str, keyboard) -> int:
    """Send the formatted message; return the message_id."""
    msg = await bot.send_message(chat_id=chat_id, text=text, reply_markup=keyboard)
    return msg.message_id


async def _confirm_selection(
    bot,
    chat_id: int,
    message_id: int,
    callback_query_id: str,
    selected_label: str,
) -> None:
    """Edit message to show confirmed choice and dismiss the loading indicator."""
    await bot.answer_callback_query(callback_query_id)
    await bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=f"✅ Selected: {selected_label}",
        reply_markup=None,
    )


async def _poll_for_answer(
    bot,
    chat_id: int,
    message_id: int,
    callback_prefix: str,
    options: list[str],
    timeout_seconds: int,
) -> Optional[int]:
    """
    Manual long-poll loop. Returns 0-based option index, or None for skip/timeout.
    """
    offset = None
    deadline = time.monotonic() + timeout_seconds

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None  # timeout

        server_timeout = min(30, max(1, int(remaining)))

        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=server_timeout,
                read_timeout=server_timeout + 10,
                allowed_updates=["callback_query"],
            )
        except Exception:
            await asyncio.sleep(2)
            continue

        for update in updates:
            offset = update.update_id + 1
            cq = update.callback_query
            if cq is None:
                continue
            if cq.from_user.id != chat_id:
                continue
            if cq.message.message_id != message_id:
                continue
            if not cq.data.startswith(callback_prefix + ":"):
                continue

            index = int(cq.data.split(":", 1)[1])
            label = options[index] if index >= 0 else "Skip"
            await _confirm_selection(bot, chat_id, message_id, cq.id, label)
            return index if index >= 0 else None


async def _poll_for_answer_multi(
    bot,
    chat_id: int,
    message_id: int,
    callback_prefix: str,
    options: list[str],
    timeout_seconds: int,
) -> Optional[list[int]]:
    """
    Multi-select poll loop. Toggles selection on each tap; Confirm submits.
    Returns sorted list of selected indices, or None for skip/timeout.
    """
    offset = None
    deadline = time.monotonic() + timeout_seconds
    selected: set[int] = set()

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None  # timeout

        server_timeout = min(30, max(1, int(remaining)))

        try:
            updates = await bot.get_updates(
                offset=offset,
                timeout=server_timeout,
                read_timeout=server_timeout + 10,
                allowed_updates=["callback_query"],
            )
        except Exception:
            await asyncio.sleep(2)
            continue

        for update in updates:
            offset = update.update_id + 1
            cq = update.callback_query
            if cq is None:
                continue
            if cq.from_user.id != chat_id:
                continue
            if cq.message.message_id != message_id:
                continue
            if not cq.data.startswith(callback_prefix + ":"):
                continue

            payload = cq.data.split(":", 1)[1]

            if payload == "-1":  # Skip
                await _confirm_selection(bot, chat_id, message_id, cq.id, "Skip")
                return None

            if payload == "confirm":
                labels = ", ".join(options[i] for i in sorted(selected)) or "none"
                await _confirm_selection(bot, chat_id, message_id, cq.id, labels)
                return sorted(selected)

            # Toggle selection
            index = int(payload)
            if index in selected:
                selected.discard(index)
            else:
                selected.add(index)

            await bot.answer_callback_query(cq.id)
            keyboard = _build_keyboard_multi(options, callback_prefix, selected)
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=message_id, reply_markup=keyboard
            )


async def ask_user(
    question: str,
    options: list[str],
    bot_token: str,
    chat_id: int,
    message_prefix: Optional[str] = None,
    timeout_seconds: int = 86400,
    multi_select: bool = False,
) -> "Optional[int] | Optional[list[int]]":
    """
    Send an inline keyboard question and block until the user responds.

    Single-select (default): returns 0-based index, or None for skip/timeout.
    Multi-select: returns sorted list of selected indices, or None for skip/timeout.
    """
    from telegram import Bot

    callback_prefix = str(uuid.uuid4())[:8]
    text = _format_message_text(question, options, message_prefix)

    if multi_select:
        keyboard = _build_keyboard_multi(options, callback_prefix, set())
        async with Bot(token=bot_token) as bot:
            message_id = await _send_question_message(bot, chat_id, text, keyboard)
            return await _poll_for_answer_multi(
                bot, chat_id, message_id, callback_prefix, options, timeout_seconds
            )
    else:
        keyboard = _build_keyboard(options, callback_prefix)
        async with Bot(token=bot_token) as bot:
            message_id = await _send_question_message(bot, chat_id, text, keyboard)
            return await _poll_for_answer(
                bot, chat_id, message_id, callback_prefix, options, timeout_seconds
            )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send an inline keyboard question to Telegram and return the user's choice."
    )
    parser.add_argument("question", help="The question to ask")
    parser.add_argument("options", nargs="+", help="The options to present as buttons")
    parser.add_argument(
        "--message-prefix",
        default=None,
        help="Optional header line shown above the question",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=86400,
        help="Seconds to wait for a response (default: 86400 = 24h)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: <script_dir>/../config.yaml)",
    )
    parser.add_argument(
        "--multi",
        action="store_true",
        default=False,
        help="Allow selecting multiple options; prints space-separated indices or 'skip'",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    tg = config.get("telegram", {})
    bot_token = tg.get("bot_token", "")
    chat_id = tg.get("allowed_user_id")

    if not bot_token:
        print("Error: telegram.bot_token not set in config.yaml", file=sys.stderr)
        sys.exit(2)
    if not chat_id:
        print("Error: telegram.allowed_user_id not set in config.yaml", file=sys.stderr)
        sys.exit(2)

    try:
        result = asyncio.run(
            ask_user(
                question=args.question,
                options=args.options,
                bot_token=bot_token,
                chat_id=int(chat_id),
                message_prefix=args.message_prefix,
                timeout_seconds=args.timeout,
                multi_select=args.multi,
            )
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if result is None:
        print("skip")
    elif isinstance(result, list):
        print(" ".join(str(i) for i in result))
    else:
        print(result)


if __name__ == "__main__":
    main()
