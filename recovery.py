"""Three-tier recovery: get the bot back to HOME no matter what.

When the deterministic state machine gets lost (UNKNOWN for too long, modal
chain that won't close, etc.), this module walks through escalating
primitives until HOME is detected:

  Tier 1 — panic_walk: BACK x5 with state checks. Closes most modals,
           exits Settings/Shop/Treasure/Builder UI.
  Tier 2 — coc_restart: kill + relaunch the CoC app.
  Tier 3 — visual_pilot: hand the screen to Gemini Vision and execute
           whatever action it suggests, up to N attempts.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PIL import Image

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.state import GameState, StateDetector, find_red_close_x

log = logging.getLogger(__name__)


def panic_walk(
    adb: ADB,
    detector: StateDetector,
    max_attempts: int = 6,
    pause: float = 1.2,
) -> bool:
    """Hit BACK repeatedly, checking state after each press. Returns True if HOME."""
    for i in range(max_attempts):
        adb.back()
        time.sleep(pause)
        state = detector.detect(grab_frame_bgr(adb))
        if state == GameState.HOME:
            log.info(f"panic_walk: HOME reached after {i + 1} BACK(s)")
            return True
        if state == GameState.MODAL:
            frame = grab_frame_bgr(adb)
            pos = find_red_close_x(frame)
            if pos:
                adb.tap_precise(pos[0], pos[1])
                time.sleep(0.6)
    return detector.detect(grab_frame_bgr(adb)) == GameState.HOME


def coc_restart(adb: ADB, detector: StateDetector, post_launch_wait: float = 25.0) -> bool:
    """Kill CoC, relaunch, wait for HOME. Returns True if HOME detected."""
    log.warning("coc_restart: killing CoC")
    adb.kill_coc()
    time.sleep(3.0)
    adb.launch_coc()
    deadline = time.time() + post_launch_wait
    while time.time() < deadline:
        state = detector.detect(grab_frame_bgr(adb))
        if state == GameState.HOME:
            log.info("coc_restart: HOME reached")
            return True
        time.sleep(1.0)
    log.error("coc_restart: HOME not reached after relaunch")
    return False


def visual_pilot_recovery(
    adb: ADB,
    detector: StateDetector,
    max_steps: int = 6,
) -> bool:
    """Hand the screen to Gemini Vision; execute its suggested taps until HOME."""
    try:
        from planner import visual_pilot
    except Exception as e:
        log.warning(f"visual_pilot import failed: {e}")
        return False

    history: list[str] = []
    for step in range(max_steps):
        state = detector.detect(grab_frame_bgr(adb))
        if state == GameState.HOME:
            return True

        pil_frame = adb.screencap()
        action = visual_pilot.ask(pil_frame, history=history)
        if action is None:
            return False
        log.warning(
            f"visual_pilot[{step + 1}/{max_steps}]: screen={action.current_screen!r} "
            f"action={action.action} reason={action.reasoning!r}"
        )
        if action.action == "tap":
            adb.tap_precise(action.x, action.y)
            history.append(f"tap({action.x},{action.y}) — {action.reasoning}")
        elif action.action == "back":
            adb.back()
            history.append(f"back — {action.reasoning}")
        elif action.action == "wait":
            history.append(f"wait — {action.reasoning}")
        elif action.action == "give_up":
            log.error(f"visual_pilot gave up: {action.reasoning}")
            return False
        time.sleep(2.0)

    return detector.detect(grab_frame_bgr(adb)) == GameState.HOME


def recover_to_home(
    adb: ADB,
    detector: StateDetector,
    enable_visual_pilot: bool = True,
) -> bool:
    """Full three-tier recovery. Returns True if HOME is reached."""
    log.warning("recover_to_home: tier 1 — panic walk")
    if panic_walk(adb, detector):
        return True

    log.warning("recover_to_home: tier 2 — CoC restart")
    if coc_restart(adb, detector):
        return True

    if enable_visual_pilot:
        log.warning("recover_to_home: tier 3 — visual pilot (Gemini)")
        if visual_pilot_recovery(adb, detector):
            return True

    log.error("recover_to_home: all tiers exhausted")
    return False
