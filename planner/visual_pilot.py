"""Last-resort visual pilot — uses Gemini Vision to look at the screen and
decide the next tap when the deterministic state machine is stuck.

This is the "never fails" escape hatch. If the bot has been in UNKNOWN state
for >N seconds, has tried BACK-walks and CoC restart and is still lost,
hand the screenshot to Gemini and ask it where we are and what to tap.
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from typing import Literal

from google import genai
from google.genai import types
from PIL import Image
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class PilotAction(BaseModel):
    current_screen: str = Field(description="What screen we appear to be on (e.g. 'home', 'settings', 'shop', 'login', 'unknown')")
    action: Literal["tap", "back", "wait", "give_up"] = Field(description="What to do next")
    x: int = Field(default=0, description="Tap x in 1280x720 frame, 0 if not tap")
    y: int = Field(default=0, description="Tap y in 1280x720 frame, 0 if not tap")
    reasoning: str = Field(description="One sentence: why this action moves us toward the home village")


SYSTEM_PROMPT = """\
You are looking at a Clash of Clans game screen running at 1280x720.
The bot's goal is to return to the home village screen so it can attack.

The home village shows the player's base with an orange "Attack!" button at
the bottom-left (around x=60, y=670) and a "Shop" button bottom-right.

Decide ONE action that moves toward home:
- "tap" with x,y if you can see a close button, "X", "Home", "Back", "Confirm", "Continue", or similar that exits the current screen
- "back" if you'd press Android Back to dismiss a modal/menu
- "wait" if a transition is in progress (loading screen, animation)
- "give_up" only if the screen looks like a hard-stop (server maintenance, login required, ban) that needs human attention

Coordinates must be inside 1280x720. Keep reasoning to one sentence.
"""


def _client() -> genai.Client | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


@dataclass
class _Cache:
    last_call_ts: float = 0.0
    calls_this_minute: int = 0
    minute_anchor: float = 0.0


_cache = _Cache()


def _rate_limited() -> bool:
    import time
    now = time.time()
    if now - _cache.minute_anchor > 60:
        _cache.minute_anchor = now
        _cache.calls_this_minute = 0
    if _cache.calls_this_minute >= 6:
        return True
    _cache.calls_this_minute += 1
    _cache.last_call_ts = now
    return False


def ask(frame: Image.Image, history: list[str] | None = None) -> PilotAction | None:
    """Ask Gemini what to do given the current screen. Returns None if
    Gemini is unavailable or the call fails.
    """
    client = _client()
    if client is None:
        log.info("visual_pilot: no GEMINI_API_KEY — skipping")
        return None
    if _rate_limited():
        log.warning("visual_pilot: rate-limited (>6 calls/min) — skipping")
        return None

    buf = io.BytesIO()
    frame.save(buf, format="PNG")

    history_block = ""
    if history:
        history_block = "\n\nRecent actions tried (most recent last):\n" + "\n".join(f"- {h}" for h in history[-5:])

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=buf.getvalue(), mime_type="image/png"),
                "Look at the screen. What single action gets us closer to the home village?" + history_block,
            ],
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                response_mime_type="application/json",
                response_schema=PilotAction,
            ),
        )
        action = PilotAction.model_validate_json(response.text)
        if action.action == "tap":
            action.x = max(0, min(1279, action.x))
            action.y = max(0, min(719, action.y))
        return action
    except Exception as e:
        log.warning(f"visual_pilot: Gemini call failed: {e}")
        return None
