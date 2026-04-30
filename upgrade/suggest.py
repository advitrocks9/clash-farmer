"""Builder & Lab in-game suggestion flows.

Verified flow on 2026-04-30 against live game:
  1. Tap (i) info button on the builder/lab top-bar icon
       Builder (i): (575, 17)
       Lab (i):     (747, 17)
  2. A tooltip opens showing 'Upgrades in progress' + 'Suggested upgrades:'
     with one row per upgrade (icon + name + cost).
  3. Tap inside a row → game pans to the suggested building and opens its
     info card at the bottom of the screen.
  4. Tap the Upgrade button on the card. Position varies by building:
     - Wall:   3 buttons (gold 760, elixir 875, gem 1095) all at y=622
     - Builder's Hut / Pet House / Defense / Hero altar:
               1 large green Upgrade button — rightmost on the card,
               typically around (845, 615)
  5. Confirm dialog appears with green Confirm pill at (897, 629).

Pet House is its own icon (no suggestion list of its own — the Builder
tooltip lists 'Pet House' as a suggested upgrade and tapping the row
takes you there).
"""

from __future__ import annotations

import logging
import time
from typing import Literal

import cv2
import numpy as np

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.state import find_red_close_x

log = logging.getLogger(__name__)

BUILDER_INFO_POS = (575, 17)
LAB_INFO_POS = (747, 17)
TOOLTIP_ROW_X = 550  # left-of-centre column, lands inside any row
CONFIRM_BTN = (897, 629)


def _suggestion_row_ys(frame: np.ndarray) -> list[int]:
    """Y positions of suggested-upgrade rows in the open tooltip.

    Each row has a coloured cost icon (gold / elixir / dark / gem) in a
    narrow vertical band. We mask the band for any saturated colour and
    cluster connected blobs into row centres. The 'Suggested upgrades:'
    section starts ~y=200 and 'Other upgrades:' is the divider — we keep
    everything in the upper half (y < 380) which empirically maps to the
    suggested section.
    """
    # Cost-icon column is around x=680-710 inside the tooltip.
    band = frame[180:380, 670:720]
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    # Any saturated pixel: gold-yellow, elixir-pink, dark-purple, gem-pink
    sat = cv2.inRange(hsv, np.array([0, 90, 80]), np.array([180, 255, 255]))
    # Vertical projection — sum saturated pixels per row
    proj = sat.sum(axis=1)
    # Find local maxima above a threshold
    rows: list[int] = []
    last_y = -100
    for y, v in enumerate(proj):
        if v > 100 and y - last_y > 18:
            rows.append(y + 180)
            last_y = y
    return rows


def _find_upgrade_button(frame: np.ndarray) -> tuple[int, int] | None:
    """Find the green Upgrade button on the bottom info card.

    Returns the rightmost large green pill in the card strip y=590-660.
    Wall cards have 3 Upgrade buttons (gold/elixir/gem) — the rightmost
    is the gem one which usually isn't what we want, so we prefer the
    leftmost LARGE green button (gold) when multiple are present.
    For non-wall cards there's only one large Upgrade button.
    """
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 100, 80]), np.array([85, 255, 255]))
    mask[:585] = 0
    mask[665:] = 0
    mask[:, :420] = 0
    mask[:, 1170:] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    btns: list[tuple[int, int, int, int, float]] = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        a = cv2.contourArea(c)
        if a > 1500 and w > 60 and h > 30:
            btns.append((x, y, w, h, a))
    if not btns:
        return None
    btns.sort(key=lambda b: b[0])  # left-to-right
    x, y, w, h, _ = btns[0]
    return x + w // 2, y + h // 2


def _confirm_button_visible(frame: np.ndarray) -> bool:
    """Detect the green Confirm pill in the upgrade-confirmation dialog."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 150, 80]), np.array([75, 255, 255]))
    region = mask[600:680, 800:1000]
    return int(region.sum()) > 5000


def _dismiss(adb: ADB) -> None:
    """Tap a safe empty area to close any building card or tooltip."""
    adb.tap_precise(100, 100)
    adb.wait_random(0.4, 0.8)


def upgrade_top_suggestion(
    adb: ADB,
    template_set: dict[str, "tmpl.Template"],
    kind: Literal["builder", "lab"],
) -> bool:
    """Open the suggestion tooltip, tap the first suggested-upgrades row,
    upgrade the building it navigates to. Returns True if upgrade started.
    """
    info_pos = BUILDER_INFO_POS if kind == "builder" else LAB_INFO_POS

    adb.tap_precise(*info_pos)
    adb.wait_random(0.8, 1.4)

    frame = grab_frame_bgr(adb)
    rows = _suggestion_row_ys(frame)
    if not rows:
        log.info(f"suggest[{kind}]: no suggestion rows visible")
        _dismiss(adb)
        return False

    row_y = rows[0]
    log.info(f"suggest[{kind}]: tapping first suggestion row at y={row_y}")
    adb.tap_precise(TOOLTIP_ROW_X, row_y)
    adb.wait_random(2.0, 3.0)

    frame = grab_frame_bgr(adb)
    upgrade_pos = _find_upgrade_button(frame)
    if upgrade_pos is None:
        log.warning(f"suggest[{kind}]: no Upgrade button on building card")
        _dismiss(adb)
        return False

    log.info(f"suggest[{kind}]: tapping Upgrade at {upgrade_pos}")
    adb.tap_precise(*upgrade_pos)
    adb.wait_random(1.0, 1.5)

    frame = grab_frame_bgr(adb)
    if not _confirm_button_visible(frame):
        log.warning(f"suggest[{kind}]: no Confirm dialog (insufficient resources?)")
        # Close the upgrade dialog if it appeared without Confirm, then dismiss
        x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
        if x:
            adb.tap_precise(x[0], x[1])
        _dismiss(adb)
        return False

    adb.tap_precise(*CONFIRM_BTN)
    adb.wait_random(1.5, 2.5)
    log.info(f"suggest[{kind}]: upgrade started")
    return True


PET_HOUSE_ICON_POS = (820, 17)


def upgrade_pet_house(adb: ADB, template_set: dict[str, "tmpl.Template"]) -> bool:
    """Navigate to Pet House via its dedicated top-bar icon and try to
    start the next pet upgrade. The Pet House does not show up in the
    Builder suggestions list — it has its own slot in the top bar.
    """
    adb.tap_precise(*PET_HOUSE_ICON_POS)
    adb.wait_random(2.0, 3.0)

    frame = grab_frame_bgr(adb)
    upgrade_pos = _find_upgrade_button(frame)
    if upgrade_pos is None:
        log.info("pet_house: no upgrade available right now")
        _dismiss(adb)
        return False

    adb.tap_precise(*upgrade_pos)
    adb.wait_random(1.0, 1.5)

    frame = grab_frame_bgr(adb)
    if not _confirm_button_visible(frame):
        log.warning("pet_house: confirm dialog not shown")
        _dismiss(adb)
        return False

    adb.tap_precise(*CONFIRM_BTN)
    adb.wait_random(1.5, 2.5)
    log.info("pet_house: upgrade started")
    return True
