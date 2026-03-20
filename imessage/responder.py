"""
Sends iMessage replies via AppleScript.
Handles chunking of long responses to stay within iMessage limits.
"""

import subprocess
import time
import logging

from security import sanitize_for_applescript

logger = logging.getLogger(__name__)

MAX_CHUNK = 4000
CHUNK_DELAY = 0.6  # seconds between chunks

# Prefix on every outgoing message — used by watcher to skip Claude's own replies
RESPONSE_MARKER = "✦claude✦"


def send_message(phone_number: str, text: str, project_name: str | None = None) -> None:
    """
    Send an iMessage to the given phone number.
    Prepends a header with project context and a marker so the watcher ignores it.
    Automatically chunks long messages.
    """
    header = RESPONSE_MARKER
    if project_name:
        header += f" {project_name}"
    header += "\n"

    full_text = header + text
    chunks = _chunk_text(full_text, MAX_CHUNK)
    total = len(chunks)

    for i, chunk in enumerate(chunks, start=1):
        if total > 1:
            # Marker already on chunk 1 via header; add pagination to subsequent chunks
            if i > 1:
                chunk = f"{RESPONSE_MARKER} [{i}/{total}]\n" + chunk
            else:
                # Replace header on first chunk with paginated version
                chunk = chunk.replace(header, f"{RESPONSE_MARKER} {project_name} [{i}/{total}]\n", 1)

        _send_chunk(phone_number, chunk)

        if i < total:
            time.sleep(CHUNK_DELAY)


def _send_chunk(phone_number: str, text: str) -> None:
    """Send a single chunk via AppleScript."""
    safe_number = sanitize_for_applescript(phone_number)
    safe_text = sanitize_for_applescript(text)

    script = f'''
        tell application "Messages"
            set targetBuddy to "{safe_number}"
            set theService to 1st service whose service type = iMessage
            set theBuddy to buddy targetBuddy of theService
            send "{safe_text}" to theBuddy
        end tell
    '''

    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.error("AppleScript send failed: %s", result.stderr.strip())
    except subprocess.TimeoutExpired:
        logger.error("AppleScript timed out sending message")
    except Exception as e:
        logger.error("Unexpected error sending message: %s", e)


def _chunk_text(text: str, max_size: int) -> list[str]:
    """
    Split text into chunks of at most max_size characters.
    Prefers splitting on paragraph boundaries, then sentence boundaries, then hard-wrapping.
    """
    if len(text) <= max_size:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_size:
        # Try paragraph break
        split_at = remaining.rfind("\n\n", 0, max_size)
        if split_at == -1:
            # Try newline
            split_at = remaining.rfind("\n", 0, max_size)
        if split_at == -1:
            # Try sentence end
            split_at = remaining.rfind(". ", 0, max_size)
            if split_at != -1:
                split_at += 1  # include the period
        if split_at == -1:
            # Hard wrap
            split_at = max_size

        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks
