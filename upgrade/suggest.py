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


def _read_card_text(frame: np.ndarray) -> str:
    """OCR the bottom info card to verify it offers a real Upgrade.

    Cards have completely different button sets depending on building state:
        - Idle    → Info / Select Row / Upgrade More / Upgrade (gold/elixir/gem)
        - Upgrading → Info / Boost Builders / Cancel / Finish Now (GEMS)
                                                      / Assign Apprentice
        - Pet House (Pet upgrading) → Info / Boost / Cancel / Finish Now / Pets

    We refuse to proceed unless the card text contains 'Upgrade' AND does
    NOT contain any gem-pay action ('Boost', 'Finish', 'Cancel', 'Apprentice').
    """
    from screen.ocr import _get_reader
    crop = frame[580:670, 400:1200]
    upscaled = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    reader = _get_reader()
    results = reader.readtext(upscaled, detail=0, paragraph=False)
    return " ".join(results).lower() if results else ""


# Words on a building card that mean tapping a button SPENDS GEMS.
# If any of these appear, abort the upgrade flow entirely.
GEM_BUTTON_WORDS = ("boost", "finish", "cancel", "apprentice", "pets")


def _safe_to_upgrade(frame: np.ndarray) -> tuple[bool, str]:
    """Return (safe, reason). Only tap Upgrade if card text is unambiguous."""
    text = _read_card_text(frame)
    if not text:
        return False, "no_text"
    for word in GEM_BUTTON_WORDS:
        if word in text:
            return False, f"refused (gem button '{word}' on card): {text!r}"
    if "upgrade" not in text:
        return False, f"no Upgrade text on card: {text!r}"
    return True, text


def _find_upgrade_button(frame: np.ndarray) -> tuple[int, int] | None:
    """Find the green Upgrade button on the bottom info card.

    SAFETY: this is only called after _safe_to_upgrade(frame) returns True,
    which guarantees the card has 'Upgrade' text and NO gem buttons.

    Returns the leftmost large green pill in the card strip y=590-660.
    On wall cards (3 Upgrade buttons gold/elixir/gem) we want gold = leftmost.
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
    btns.sort(key=lambda b: b[0])
    x, y, w, h, _ = btns[0]
    return x + w // 2, y + h // 2


def _confirm_button_visible(frame: np.ndarray) -> bool:
    """Detect the green Confirm pill in the upgrade-confirmation dialog."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 150, 80]), np.array([75, 255, 255]))
    region = mask[600:680, 800:1000]
    return int(region.sum()) > 5000


def _confirm_pays_resources_only(frame: np.ndarray) -> tuple[bool, str]:
    """SAFETY GATE for the upgrade Confirm dialog.

    The dialog has a green Confirm pill at the bottom-right showing the
    cost as 'NUMBER + ICON'. If the icon is a gem (pink/cyan diamond)
    rather than gold/elixir/dark, abort.

    We detect by colour band of pixels next to the cost text in the
    dialog. Gold dominates yellow ~25°. Elixir dominates pink ~310°.
    Dark dominates near-black saturated purple. Gems dominate cyan
    ~180° (with a strong saturation that beats elixir's pink).

    Returns (safe, reason).
    """
    # Cost icon sits at the right edge of the green Confirm pill,
    # roughly (940-990, 615-650) in the upgrade dialog.
    region = frame[615:660, 920:1000]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    h, s, v = cv2.split(hsv)

    # Resource hue masks
    gold_px   = int(cv2.inRange(hsv, np.array([20, 150, 150]), np.array([35, 255, 255])).sum() / 255)
    elixir_px = int(cv2.inRange(hsv, np.array([140, 100, 100]), np.array([175, 255, 255])).sum() / 255)
    dark_px   = int(cv2.inRange(hsv, np.array([110, 30, 20]), np.array([140, 200, 110])).sum() / 255)
    gem_px    = int(cv2.inRange(hsv, np.array([85, 100, 100]), np.array([110, 255, 255])).sum() / 255)

    counts = {"gold": gold_px, "elixir": elixir_px, "dark": dark_px, "gem": gem_px}
    log.info(f"confirm-cost icon counts: {counts}")
    if gem_px > max(gold_px, elixir_px, dark_px) and gem_px > 30:
        return False, f"gem cost detected (gem={gem_px}, others={counts})"
    if max(gold_px, elixir_px, dark_px) < 30:
        return False, f"no resource icon detected ({counts})"
    return True, f"resource cost ({counts})"


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

    # SAFETY GATE 1: building card text. Refuse to tap if it shows any
    # gem-pay action (Boost / Finish / Cancel / Apprentice / Pets).
    safe, reason = _safe_to_upgrade(frame)
    if not safe:
        log.warning(f"suggest[{kind}]: card not safe — {reason}")
        _dismiss(adb)
        return False

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
        x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
        if x:
            adb.tap_precise(x[0], x[1])
        _dismiss(adb)
        return False

    # SAFETY GATE 2: confirm cost icon. Refuse to confirm if cost is gems.
    safe_cost, cost_reason = _confirm_pays_resources_only(frame)
    if not safe_cost:
        log.error(f"suggest[{kind}]: REFUSING confirm — {cost_reason}")
        x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
        if x:
            adb.tap_precise(x[0], x[1])
        _dismiss(adb)
        return False

    log.info(f"suggest[{kind}]: confirm cost is resources ({cost_reason}) — proceeding")
    adb.tap_precise(*CONFIRM_BTN)
    adb.wait_random(1.5, 2.5)
    _dismiss(adb)
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
    safe, reason = _safe_to_upgrade(frame)
    if not safe:
        log.warning(f"pet_house: card not safe — {reason}")
        _dismiss(adb)
        return False

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

    safe_cost, cost_reason = _confirm_pays_resources_only(frame)
    if not safe_cost:
        log.error(f"pet_house: REFUSING confirm — {cost_reason}")
        x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
        if x:
            adb.tap_precise(x[0], x[1])
        _dismiss(adb)
        return False

    adb.tap_precise(*CONFIRM_BTN)
    adb.wait_random(1.5, 2.5)
    _dismiss(adb)
    log.info("pet_house: upgrade started")
    return True
