from __future__ import annotations

import logging
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)


def _config() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return None
    return token, chat


def send(text: str, *, silent: bool = False) -> bool:
    """Post a message to the configured Telegram chat. Returns True on success.

    Silently no-ops when TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID isn't set,
    so the bot keeps running even if you haven't wired up the bot yet.
    """
    cfg = _config()
    if cfg is None:
        return False
    token, chat = cfg
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urlencode({
        "chat_id": chat,
        "text": text,
        "disable_notification": "true" if silent else "false",
        "parse_mode": "HTML",
    }).encode()
    try:
        req = Request(url, data=body, method="POST")
        with urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        log.warning(f"telegram send failed: {e}")
        return False
