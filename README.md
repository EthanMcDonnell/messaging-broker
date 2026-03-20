# claude-through-messaging-platform

Use Claude Code from your phone via iMessage or Telegram. Send a message, get a response — with full project-switching, intent routing, and rate limiting. Runs as a background daemon on your Mac.

## How it works

A single `main.py` entry point handles both platforms. When a message arrives it is routed through shared logic:

1. **Intent detection** — is this "list projects", "switch to X", or a question for Claude?
2. **Project resolution** — each project maps to a directory on disk with its own allowed tools
3. **Claude invocation** — runs `claude -p` in the project directory via subprocess
4. **Reply** — sends the response back through the platform's transport layer

```
main.py                  ← entry point + shared process_message()
├── claude_bridge.py     ← runs Claude CLI
├── router.py            ← intent detection (switch / list / ask)
├── security.py          ← rate limiting, input sanitization
├── state.py             ← persists current project across restarts
│
├── imessage/
│   ├── watcher.py       ← polls ~/Library/Messages/chat.db
│   ├── responder.py     ← sends replies via AppleScript
│   └── message_parser.py ← parses iMessage attributedBody blobs
│
└── telegram/
    └── bot.py           ← Telegram bot, shares all routing/Claude logic
```

---

## Prerequisites

- macOS (iMessage platform requires this; Telegram works on any OS)
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (`claude` on PATH)
- Python 3.11+
- For iMessage: Full Disk Access granted to Terminal (so it can read `chat.db`)

---

## Installation

```bash
git clone https://github.com/yourusername/claude-through-messaging-platform
cd claude-through-messaging-platform
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Configuration

Copy the example config and fill in your details:

```bash
cp config.example.yaml config.yaml
```

The config has three sections: `imessage` (sender number, poll interval), `telegram` (bot token, user ID), and shared settings (`claude`, `rate_limits`, `projects`). See `config.example.yaml` for the full annotated reference.

### Finding your iMessage phone number

```bash
sqlite3 ~/Library/Messages/chat.db \
  "SELECT DISTINCT chat_identifier FROM chat;"
```

Use the value that matches your phone number for both `allowed_sender` and `self_chat_id`.

### Getting a Telegram bot token

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Copy the token into `config.yaml`

### Finding your Telegram user ID

Message [@userinfobot](https://t.me/userinfobot) — it replies with your user ID. Put that in `allowed_user_id`.

### Projects

Each project maps a name to a directory on disk with a set of allowed tools. Claude runs inside that directory using only those tools. `allowed_tools` is passed directly to `claude --allowedTools` — use standard tool names (`Read`, `Edit`, `Bash`, etc.) or MCP tools in the form `mcp__servername__toolname`. See `config.example.yaml` for examples.

---

## Running

### Selecting a platform

Set `platform:` in `config.yaml` (persisted default):

```yaml
platform: telegram   # or imessage
```

Or pass it at runtime to override:

```bash
python main.py --platform telegram
python main.py --platform imessage
```

One of the two must be set — the process will exit with an error if neither is specified.

### iMessage

```bash
python main.py --platform imessage
```

Send a message **to yourself** in iMessage. The bridge monitors your self-chat.

### Telegram

```bash
python main.py --platform telegram
```

Open your bot in Telegram and start sending messages.

### Other options

```
--config      path to config.yaml  (default: config.yaml in repo root)
--dry-run     log responses without sending or calling Claude
--log-level   DEBUG | INFO | WARNING | ERROR
```

---

## Messaging commands

These work on both platforms and are matched with fuzzy intent detection (designed for Siri voice dictation on iMessage):

| Say | What happens |
|-----|-------------|
| `list projects` | Shows all configured projects |
| `current project` / `where am I` | Shows the active project and path |
| `switch to [name]` | Switches to that project |
| `use [name]` | Also switches project |
| Anything else | Sent to Claude in the current project |

Project names and aliases are fuzzy-matched, so Siri-dictated phrases like "hey switch to the bridge project" will resolve correctly.

---

## Running as a background service (iMessage, macOS)

A launchd plist is included at `imessage/launchd/com.ethan.claude-imessage.plist`. It starts the bridge on login and restarts it if it crashes.

**1. Edit the plist** — update the Python path and working directory to match your setup:

```xml
<string>/Users/YOU/Documents/claude-through-messaging-platform/.venv/bin/python3</string>
<string>/Users/YOU/Documents/claude-through-messaging-platform/main.py</string>
...
<string>/Users/YOU/Documents/claude-through-messaging-platform</string>
```

**2. Install it:**

```bash
cp imessage/launchd/com.ethan.claude-imessage.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.ethan.claude-imessage.plist
```

**3. Check logs:**

```bash
tail -f /tmp/claude-imessage.log
tail -f /tmp/claude-imessage-error.log
```

**4. Stop / restart:**

```bash
launchctl unload ~/Library/LaunchAgents/com.ethan.claude-imessage.plist
launchctl load   ~/Library/LaunchAgents/com.ethan.claude-imessage.plist
```

---

## iMessage setup notes

### Full Disk Access

The bridge reads `~/Library/Messages/chat.db`. On macOS Ventura+ this requires Full Disk Access:

**System Settings → Privacy & Security → Full Disk Access** — add Terminal (or your IDE / Python binary if running via launchd).

### How the self-chat works

Send messages to yourself in iMessage. The bridge only reads messages where `is_from_me = 1` in your self-chat, so your normal conversations are never touched.

Replies are prefixed with `✦claude✦` so the bridge can recognise and skip its own outgoing messages on the next poll.

---

## Tests

```bash
python -m pytest imessage/tests/
```

Tests cover intent routing, input sanitization, and message parsing. They import from the shared root modules automatically.

---

## Security

- **Single-user only** — both platforms reject any sender/user that isn't the configured one
- **Rate limiting** — configurable cap on messages per minute; exceeding it exits the process (iMessage) or sends an error reply (Telegram)
- **Input sanitization** — null bytes stripped, prompts truncated at 8000 chars before being passed to Claude
- **AppleScript escaping** — iMessage responses escape backslashes and double-quotes before interpolation
- **Path validation** — project paths must exist and must be inside your home directory; symlinks that escape are rejected at startup
- **Subprocess, not shell** — Claude is invoked with `subprocess.run(..., shell=False)`, preventing shell injection
