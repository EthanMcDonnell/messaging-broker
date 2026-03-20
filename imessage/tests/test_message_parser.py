import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import plistlib
from message_parser import extract_text, normalize_text


def _make_attributed_body(text: str) -> bytes:
    """Build a minimal NSKeyedArchiver-style plist blob for testing."""
    plist = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            text,
        ],
    }
    return plistlib.dumps(plist, fmt=plistlib.FMT_BINARY)


def test_extract_text_from_attributed_body():
    blob = _make_attributed_body("Hello from Ventura")
    row = {"text": None, "attributedBody": blob}
    assert extract_text(row) == "Hello from Ventura"


def test_extract_text_falls_back_to_text_column():
    row = {"text": "Plain text fallback", "attributedBody": None}
    assert extract_text(row) == "Plain text fallback"


def test_extract_text_empty_row():
    row = {"text": None, "attributedBody": None}
    assert extract_text(row) == ""


def test_extract_text_prefers_attributed_body():
    blob = _make_attributed_body("From attributedBody")
    row = {"text": "From text column", "attributedBody": blob}
    assert extract_text(row) == "From attributedBody"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  hello   world  ") == "hello world"
    assert normalize_text("line1\n\nline2") == "line1 line2"


def test_normalize_text_strips():
    assert normalize_text("  hello  ") == "hello"
