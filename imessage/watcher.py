"""
Polls ~/Library/Messages/chat.db for new self-sent messages.
Opens the database read-only to avoid conflicts with Messages.app.
"""

import sqlite3
import logging
from pathlib import Path
from typing import Iterator

from .message_parser import extract_text, normalize_text
from .responder import RESPONSE_MARKER

DB_PATH = Path.home() / "Library/Messages/chat.db"

logger = logging.getLogger(__name__)

# iMessage epoch starts 2001-01-01 (Mac absolute time), not Unix epoch
IMESSAGE_EPOCH_OFFSET = 978307200  # seconds between 1970-01-01 and 2001-01-01


def get_db_connection() -> sqlite3.Connection:
    """Open chat.db in read-only mode."""
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def fetch_new_messages(conn: sqlite3.Connection, since_timestamp: float, self_chat_id: str) -> Iterator[dict]:
    """
    Yield new messages from the self-chat since the given Unix timestamp.
    Only returns is_from_me=1 messages in the self-chat.

    since_timestamp: Unix timestamp (float)
    self_chat_id: the chat_identifier for your self-chat (your phone number)
    """
    # Convert Unix timestamp to iMessage Mac absolute time (nanoseconds in newer versions)
    # chat.db stores dates as seconds since 2001-01-01 * 1e9 (nanoseconds) on newer macOS
    # or as seconds since 2001-01-01 on older macOS
    # We detect scale by checking if values are > 1e10
    imessage_ts = (since_timestamp - IMESSAGE_EPOCH_OFFSET) * 1e9

    query = """
        SELECT
            m.ROWID,
            m.guid,
            m.text,
            m.attributedBody,
            m.date,
            m.is_from_me,
            h.id AS sender,
            c.chat_identifier
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE m.date > ?
          AND m.is_from_me = 1
          AND c.chat_identifier = ?
        ORDER BY m.date ASC
    """

    try:
        cursor = conn.execute(query, (imessage_ts, self_chat_id))
        for row in cursor:
            text = normalize_text(extract_text(dict(row)))
            if not text:
                continue
            # Skip messages sent by this bridge (they start with the response marker)
            if text.startswith(RESPONSE_MARKER):
                continue
            yield {
                "guid": row["guid"],
                "text": text,
                "date": row["date"],
                "chat_identifier": row["chat_identifier"],
            }
    except sqlite3.OperationalError as e:
        logger.warning("DB query failed (may be locked): %s", e)


def current_db_timestamp(conn: sqlite3.Connection) -> float:
    """
    Return the max message date in the DB as a Unix timestamp.
    Used to initialize the polling cursor.
    """
    try:
        row = conn.execute("SELECT MAX(date) as max_date FROM message").fetchone()
        if row and row["max_date"]:
            mac_ts_ns = row["max_date"]
            return (mac_ts_ns / 1e9) + IMESSAGE_EPOCH_OFFSET
    except sqlite3.OperationalError:
        pass
    import time
    return time.time()
