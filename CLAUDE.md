# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

Bridges Claude Code CLI to iMessage and Telegram. Messages arrive via a platform transport layer, pass through shared intent routing and security, then invoke `claude -p` as a subprocess in a configured project directory. The reply is sent back through the platform.

## Running

```bash
python main.py --platform telegram
python main.py --platform imessage
python main.py --dry-run --log-level DEBUG   # no Claude calls, no sends
```

Requires `config.yaml` (copy from `config.example.yaml`). The `platform:` key can also be set in the config instead of passing `--platform`.

## Tests

```bash
python -m pytest imessage/tests/
```

## Architecture

All message handling flows through `process_message()` in `main.py` — this is the platform-agnostic core. Platform modules only handle I/O:

- **`router.py`** — fuzzy keyword intent detection (LIST_PROJECTS / CURRENT_STATUS / SWITCH_PROJECT / ASK_CLAUDE). Uses `difflib` with score thresholds; designed to tolerate Siri voice dictation.
- **`claude_bridge.py`** — runs `claude -p <prompt> --allowedTools <tools>` via `subprocess.run(shell=False)` in the project directory.
- **`security.py`** — sender validation, rate limiting, prompt sanitization (null-byte strip, 8000-char truncation).
- **`state.py`** — persists current project and seen message GUIDs across restarts.
- **`imessage/`** — polls `~/Library/Messages/chat.db` (requires Full Disk Access), sends replies via AppleScript. Replies are prefixed `✦claude✦` so the bridge skips its own outgoing messages.
- **`telegram/bot.py`** — async Telegram bot that calls `process_message()` for every allowed user message.
- **`telegram/ask.py`** — standalone utility (CLI or library) for sending inline keyboard questions and blocking on user response. Uses raw `get_updates()` polling so it coexists with the main bot daemon on the same token.

## Config structure

```yaml
platform: telegram | imessage
imessage:
  allowed_sender, self_chat_id, poll_interval, max_chunk_size
telegram:
  bot_token, allowed_user_id
claude:
  timeout, max_response_length
rate_limits:
  messages_per_minute
projects:
  - name, path, aliases, allowed_tools   # allowed_tools → --allowedTools
default_project: <name>
```

Project paths must exist and must be inside the home directory — validated at startup. `allowed_tools` accepts standard Claude tool names (`Read`, `Edit`, `Bash`, etc.) and MCP tools (`mcp__servername__toolname`).

## launchd (background service, iMessage)

Install/manage via `imessage/launchd/com.ethan.claude-imessage.plist`. Logs go to `/tmp/claude-imessage.log` and `/tmp/claude-imessage-error.log`.
