"""Telegram client.

- `send(text)` posts a message to the configured chat.
- `send_photo(path)` posts a screenshot.
- `CommandPoller` runs a background thread that pulls /commands the user
  sends to the bot, dispatches them to registered handlers, and replies.

`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` come from `.env`. Without them,
every function silently no-ops so the bot keeps running even before
Telegram is wired up.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

API = "https://api.telegram.org"


def _config() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    return token, chat


def send(text: str, *, silent: bool = False) -> bool:
    cfg = _config()
    if cfg is None:
        return False
    token, chat = cfg
    body = urlencode({
        "chat_id": chat,
        "text": text,
        "disable_notification": "true" if silent else "false",
        "parse_mode": "HTML",
    }).encode()
    try:
        with urlopen(Request(f"{API}/bot{token}/sendMessage", data=body, method="POST"), timeout=5) as r:
            return r.status == 200
    except Exception as e:
        log.warning(f"telegram send failed: {e}")
        return False


def send_photo(path: Path, caption: str = "") -> bool:
    cfg = _config()
    if cfg is None:
        return False
    token, chat = cfg
    boundary = "----CFBoundary" + str(int(time.time()))
    parts: list[bytes] = []

    def _field(name: str, value: str) -> None:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )

    _field("chat_id", chat)
    if caption:
        _field("caption", caption)
        _field("parse_mode", "HTML")
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"frame.png\"\r\nContent-Type: image/png\r\n\r\n".encode()
    )
    parts.append(Path(path).read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    try:
        req = Request(
            f"{API}/bot{token}/sendPhoto",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen(req, timeout=10) as r:
            return r.status == 200
    except Exception as e:
        log.warning(f"telegram send_photo failed: {e}")
        return False


class CommandPoller:
    """Background long-poll for /commands the user sends to the bot.

    Register handlers via `.on("/cmd", fn)` where `fn` takes the message
    text (everything after the command) and returns a string reply
    (HTML allowed) or None to skip replying.
    """

    def __init__(self, poll_interval: float = 2.0) -> None:
        self._handlers: dict[str, Callable[[str], str | None]] = {}
        self._offset = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._interval = poll_interval

    def on(self, command: str, handler: Callable[[str], str | None]) -> None:
        self._handlers[command.lstrip("/").lower()] = handler

    def start(self) -> None:
        if _config() is None:
            log.info("telegram disabled — no TELEGRAM_BOT_TOKEN/CHAT_ID")
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tg-poll")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning(f"telegram poll: {e}")
            time.sleep(self._interval)

    def _tick(self) -> None:
        cfg = _config()
        if cfg is None:
            return
        token, chat = cfg
        url = f"{API}/bot{token}/getUpdates?timeout=10&offset={self._offset}"
        try:
            with urlopen(url, timeout=15) as r:
                payload = json.loads(r.read())
        except Exception:
            return
        for update in payload.get("result", []):
            self._offset = max(self._offset, update["update_id"] + 1)
            msg = update.get("message") or update.get("edited_message") or {}
            if str(msg.get("chat", {}).get("id")) != chat:
                continue
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue
            cmd, _, rest = text[1:].partition(" ")
            cmd = cmd.split("@", 1)[0].lower()  # strip @botname
            handler = self._handlers.get(cmd)
            if handler is None:
                send(f"unknown command: /{cmd}\nsend /help to see what's available")
                continue
            try:
                reply = handler(rest.strip())
            except Exception as e:
                reply = f"<b>error</b>: {e}"
            if reply:
                send(reply)
