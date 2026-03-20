"""
Parses iMessage message text, including the attributedBody blob used in macOS Ventura+.
The attributedBody column stores an NSKeyedArchiver plist as a binary blob.
"""

import plistlib
import re


def extract_text(row: dict) -> str:
    """
    Extract plain text from a message row.
    Tries attributedBody first (Ventura+), falls back to text column.
    Returns empty string if no text found.
    """
    text = _try_attributed_body(row.get("attributedBody"))
    if text:
        return text.strip()

    text = row.get("text") or ""
    return text.strip()


def _try_attributed_body(blob: bytes | None) -> str:
    """
    Parse the NSKeyedArchiver blob from attributedBody.
    Returns extracted string or empty string on failure.
    """
    if not blob:
        return ""

    try:
        plist = plistlib.loads(blob)
        # NSKeyedArchiver structure: $objects array, with the string at index 1
        objects = plist.get("$objects", [])
        if len(objects) > 1:
            candidate = objects[1]
            if isinstance(candidate, str) and candidate != "$null":
                return candidate
        return ""
    except Exception:
        return ""


def normalize_text(text: str) -> str:
    """Normalize whitespace and strip common voice-dictation artifacts."""
    # Collapse multiple spaces/newlines
    text = re.sub(r"\s+", " ", text)
    # Strip leading/trailing whitespace
    text = text.strip()
    return text
