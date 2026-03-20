import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import time
from security import sanitize_for_applescript, sanitize_prompt, validate_sender, RateLimiter


def test_applescript_escapes_quotes():
    result = sanitize_for_applescript('say "hello"')
    assert '\\"' in result
    assert '"hello"' not in result


def test_applescript_escapes_backslashes():
    result = sanitize_for_applescript("path\\to\\file")
    assert result == "path\\\\to\\\\file"


def test_sanitize_prompt_strips_null_bytes():
    result = sanitize_prompt("hello\x00world")
    assert "\x00" not in result
    assert "hello" in result


def test_sanitize_prompt_truncates():
    long_text = "a" * 10000
    result = sanitize_prompt(long_text)
    assert len(result) <= 8000


def test_validate_sender_match():
    assert validate_sender("+15551234567", "+15551234567") is True


def test_validate_sender_case_insensitive_email():
    assert validate_sender("user@example.com", "USER@EXAMPLE.COM") is True


def test_validate_sender_mismatch():
    assert validate_sender("+15551234567", "+15559999999") is False


def test_rate_limiter_allows_under_limit():
    rl = RateLimiter(max_count=5, window_seconds=60)
    for _ in range(5):
        assert rl.allow() is True


def test_rate_limiter_blocks_over_limit():
    rl = RateLimiter(max_count=3, window_seconds=60)
    for _ in range(3):
        rl.allow()
    assert rl.allow() is False


def test_rate_limiter_resets_after_window():
    rl = RateLimiter(max_count=2, window_seconds=1)
    assert rl.allow() is True
    assert rl.allow() is True
    assert rl.allow() is False
    time.sleep(1.1)
    assert rl.allow() is True
