"""Wall upgrades — the "spend excess resources" mechanism.

Walls upgrade INSTANTLY in TH15+ (no builder consumed) and absorb large
amounts of gold or elixir per tap (3M-5M each). When storages are maxed,
attacking wastes loot we can't store. This module spends excess into walls
to keep storages flowing.

Flow (verified live on 2026-04-30):
  1. Tap any wall on the base — info card appears at the bottom.
  2. Card shows three Upgrade buttons: Gold (x≈760), Elixir (x≈875),
     Gem (x≈1095), all at y=622. Each pre-selects its resource.
  3. Tap the matching Upgrade button → confirm dialog.
  4. Tap green Confirm at (897, 629) → wall upgrades.
  5. Card refreshes to next level's cost, ready for next tap.

The bot calls `try_wall_upgrade(adb, kind="gold")` when gold is maxed,
and `try_wall_upgrade(adb, kind="elixir")` when elixir is maxed.
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.state import GameState, find_red_close_x

log = logging.getLogger(__name__)


WALL_TAP_POS = (380, 250)
UPGRADE_BTN_GOLD = (760, 622)
UPGRADE_BTN_ELIXIR = (875, 622)
UPGRADE_BTN_GEM = (1095, 622)
CONFIRM_BTN = (897, 629)


def try_wall_upgrade(
    adb: ADB,
    template_set: dict[str, "tmpl.Template"],
    kind: Literal["gold", "elixir"],
    max_attempts: int = 3,
) -> bool:
    """Try to upgrade a wall using `kind` resource. Returns True on success."""
    upgrade_pos = UPGRADE_BTN_GOLD if kind == "gold" else UPGRADE_BTN_ELIXIR

    for attempt in range(max_attempts):
        log.info(f"wall_upgrade[{kind}] attempt {attempt + 1}/{max_attempts}")

        # Tap a wall to open the info card. Some walls may be maxed at the
        # current TH level — the card shows only Info+SelectRow with no
        # Upgrade buttons in that case, so retry on a slightly different
        # position to land on a different wall.
        wx = WALL_TAP_POS[0] + (attempt * 90)
        wy = WALL_TAP_POS[1] + (attempt * 30)
        adb.tap_precise(wx, wy)
        adb.wait_random(1.0, 1.6)

        # Tap the Gold/Elixir Upgrade button. If we hit a non-wall (cannon,
        # storage, etc.) the layout differs and this tap will land on empty
        # space — confirm dialog won't appear and we bail next iter.
        adb.tap_precise(*upgrade_pos)
        adb.wait_random(1.0, 1.5)

        # Look for the green Confirm pill. If it isn't there, this wasn't a
        # wall card — close any open dialog and try a different position.
        frame = grab_frame_bgr(adb)
        if not _confirm_button_visible(frame):
            log.info("  no confirm dialog — wrong building or wall maxed; closing")
            close_x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
            if close_x:
                adb.tap_precise(close_x[0], close_x[1])
            else:
                adb.back()
            adb.wait_random(0.6, 1.0)
            continue

        adb.tap_precise(*CONFIRM_BTN)
        adb.wait_random(1.5, 2.5)
        log.info(f"wall upgraded with {kind}")
        return True

    log.warning(f"wall_upgrade[{kind}] gave up after {max_attempts} attempts")
    return False


def _confirm_button_visible(frame) -> bool:
    """Detect the green Confirm button in the upgrade dialog by colour."""
    import cv2
    import numpy as np
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 150, 80]), np.array([75, 255, 255]))
    region = mask[600:680, 800:1000]
    return int(region.sum()) > 5000


def dismiss_card(adb: ADB) -> None:
    """Close the building info card by tapping a safe empty area."""
    adb.tap_precise(100, 200)
    adb.wait_random(0.4, 0.8)
