"""
Lightweight HTTP API server for posting jobs to Telegram from external projects.

POST /send
  Body: {
    "content": "text to send",
    "topic_id": 123,                        // optional; Telegram topic thread ID
    "buttons": ["Delete", "Keep"],          // optional; strings or {"label":..,"action":..}
    "on_response": {                        // optional; what to do when a button is pressed
      "type": "webhook",                    //   "webhook" | "script" | "claude"
      "url": "http://localhost:5001/cb"    //   webhook: POST with {job_id, action, content}
    }
    // script:  {"type":"script","path":"/abs/path/handler.py"}  — job JSON via stdin
    // claude:  {"type":"claude","project":"name",
    //           "prompt_template":"User said {action} about: {content}",
    //           "result_webhook":"http://..."}  — optional result_webhook for Claude's reply
  }
  Returns: {"job_id": "abc12345"}

GET /jobs/<id>
  Returns the full job record including status ("pending" | "responded") and action.
"""

import json
import logging
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)


def _send_telegram_message(
    bot_token: str,
    chat_id: int,
    text: str,
    reply_markup: dict | None = None,
    message_thread_id: int | None = None,
) -> int:
    """Send a message via the Telegram Bot API. Returns the message_id."""
    payload: dict = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id

    data = json.dumps(payload).encode()
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return result["result"]["message_id"]


def _build_inline_keyboard(job_id: str, buttons: list) -> dict:
    rows = []
    for btn in buttons:
        if isinstance(btn, str):
            label, action = btn, btn.lower()
        else:
            label, action = btn["label"], btn["action"]
        rows.append([{"text": label, "callback_data": f"job:{job_id}:{action}"}])
    return {"inline_keyboard": rows}


def _resolve_topic_id(name: str, projects: list[dict]) -> int | None:
    """Look up a topic_id by project name."""
    for p in projects:
        if p["name"] == name:
            return p.get("telegram_topic_id")
    return None


def _make_handler(config: dict, job_store):
    tg_cfg = config.get("telegram", {})
    bot_token = tg_cfg["bot_token"]
    dm_chat_id = int(tg_cfg["allowed_user_id"])
    group_chat_id = int(tg_cfg["telegram_group_id"]) if tg_cfg.get("telegram_group_id") else None
    projects = config.get("projects", [])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            logger.debug("API %s", fmt % args)

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}

        def do_GET(self):
            if self.path.startswith("/jobs/"):
                job_id = self.path[len("/jobs/"):]
                job = job_store.get(job_id)
                self._json(200 if job else 404, job or {"error": "not found"})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/send":
                self._json(404, {"error": "not found"})
                return

            try:
                body = self._read_body()
            except Exception:
                self._json(400, {"error": "invalid JSON"})
                return

            content = body.get("content", "").strip()
            if not content:
                self._json(400, {"error": "content is required"})
                return

            # Resolve topic: accept name ("topic": "articles") or raw ID ("topic_id": 123)
            topic_name = body.get("topic")
            topic_id = body.get("topic_id")
            if topic_name:
                topic_id = _resolve_topic_id(topic_name, projects)
                if topic_id is None:
                    self._json(400, {"error": f"unknown topic name: {topic_name!r}"})
                    return

            buttons = body.get("buttons")
            on_response = body.get("on_response")
            metadata = body.get("metadata")

            # Topic posts go to the group; plain posts go to DM
            target_chat_id = group_chat_id if topic_id and group_chat_id else dm_chat_id

            job_id = job_store.create(content, buttons, on_response, topic_id=topic_id, metadata=metadata)

            try:
                keyboard = _build_inline_keyboard(job_id, buttons) if buttons else None
                tg_msg_id = _send_telegram_message(
                    bot_token, target_chat_id, content, keyboard,
                    message_thread_id=topic_id,
                )
                job_store.set_tg_msg_id(job_id, tg_msg_id)
            except Exception as e:
                logger.error("Failed to send Telegram message: %s", e)
                self._json(500, {"error": str(e)})
                return

            self._json(200, {"job_id": job_id})

    return Handler


def start(config: dict, job_store, host: str = "127.0.0.1", port: int = 8765) -> None:
    handler = _make_handler(config, job_store)
    server = HTTPServer((host, port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("API server listening on http://%s:%d", host, port)
