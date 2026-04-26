"""
Routes job responses to the configured handler: webhook POST, Python script,
or Claude invocation (with the result optionally sent back to Telegram).
"""

import asyncio
import json
import logging
import subprocess

logger = logging.getLogger(__name__)


async def dispatch(
    job: dict,
    action: str,
    config: dict,
    bot=None,
    chat_id: int | None = None,
    thread_id: int | None = None,
) -> None:
    on_response = job.get("on_response")
    if not on_response:
        return

    payload = {"job_id": job["id"], "action": action, "content": job["content"]}
    if job.get("metadata"):
        payload["metadata"] = job["metadata"]
    handler_type = on_response.get("type")

    if handler_type == "webhook":
        await _webhook(on_response["url"], payload)
    elif handler_type == "script":
        _script(on_response["path"], payload)
    elif handler_type == "command":
        _command(on_response["command"], payload)
    elif handler_type == "claude":
        await _claude(on_response, payload, config, bot, chat_id, thread_id)
    else:
        logger.warning("Unknown on_response type: %s", handler_type)


async def _webhook(url: str, payload: dict) -> None:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json=payload)
    except Exception as e:
        logger.error("Webhook dispatch failed to %s: %s", url, e)


def _command(command: str, payload: dict) -> None:
    try:
        subprocess.run(command, shell=True, input=json.dumps(payload), text=True)
    except Exception as e:
        logger.error("Command dispatch failed (%s): %s", command, e)


def _script(path: str, payload: dict) -> None:
    try:
        proc = subprocess.Popen(
            ["python", path],
            stdin=subprocess.PIPE,
            text=True,
        )
        proc.communicate(input=json.dumps(payload))
    except Exception as e:
        logger.error("Script dispatch failed (%s): %s", path, e)


async def _claude(
    on_response: dict,
    payload: dict,
    config: dict,
    bot,
    chat_id: int | None,
    thread_id: int | None = None,
) -> None:
    from claude_bridge import ask_claude

    project_name = on_response.get("project")
    project = next(
        (p for p in config.get("projects", []) if p["name"] == project_name), None
    )
    if not project:
        logger.error("Project not found for claude dispatch: %s", project_name)
        return

    template = on_response.get(
        "prompt_template", 'User selected "{action}" for: {content}'
    )
    prompt = template.format(**payload)
    timeout = config.get("claude", {}).get("timeout", 120)

    response = await asyncio.to_thread(
        ask_claude, prompt, project["path"], project.get("allowed_tools", []), timeout
    )

    if bot and chat_id:
        chunk_size = 4096
        chunks = [response[i:i + chunk_size] for i in range(0, len(response), chunk_size)]
        for chunk in chunks:
            kwargs = {"chat_id": chat_id, "text": chunk}
            if thread_id:
                kwargs["message_thread_id"] = thread_id
            await bot.send_message(**kwargs)

    result_webhook = on_response.get("result_webhook")
    if result_webhook:
        await _webhook(result_webhook, {**payload, "claude_response": response})
