"""Builder & Lab in-game suggestion flows.

Verified flow on 2026-04-30 against live game:
  1. Tap (i) info button on the builder/lab top-bar icon
       Lab (i):     (413, 18)  — LEFTMOST in the top-center strip
       Builder (i): (575, 17)  — middle
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

# Top-center icons left-to-right (verified by user 2026-04-30 22:30):
#   - LEFTMOST (i)  → LAB         (research queue, e.g. '1/1')
#   - MIDDLE   (i)  → BUILDER     (free/total builders, e.g. '4/6')
#   - RIGHTMOST(i)  → IRRELEVANT  (don't tap)
LAB_INFO_POS = (413, 18)
BUILDER_INFO_POS = (575, 17)
TOOLTIP_ROW_X = 550  # left-of-centre column, lands inside any row
CONFIRM_BTN = (897, 629)


def _row_cost_kind(frame: np.ndarray, y: int) -> str:
    """Inspect the cost-icon hue at row y to determine 'gold'/'elixir'/
    'dark'/'unknown'. The icon sits at approximately (697, y).

    Only positively detect gold (saturated yellow) and elixir (saturated
    pink). Everything else with a small saturated signal is assumed
    'dark' since dark elixir's icon is near-black with low saturation
    (hard to mask reliably against the dark tooltip background).
    """
    # Sample a small box around the icon position
    crop = frame[max(0, y - 10):y + 10, 685:715]
    if crop.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # Gold coin: bright saturated yellow.
    gold   = int(cv2.inRange(hsv, np.array([22, 180, 180]), np.array([35, 255, 255])).sum() / 255)
    # Elixir drop: bright saturated pink/magenta — opencv hue ~150-175.
    elixir = int(cv2.inRange(hsv, np.array([150, 150, 150]), np.array([175, 255, 255])).sum() / 255)
    # Dark elixir is nearly black so masks aren't reliable; we infer it
    # from absence of gold and elixir rather than positive detection.
    if gold > 15 and gold > elixir:
        return "gold"
    if elixir > 15 and elixir > gold:
        return "elixir"
    return "dark"


def _suggestion_row_ys(frame: np.ndarray) -> list[int]:
    """Y positions of rows in the 'Suggested upgrades' section ONLY.

    The tooltip has three sections, top to bottom:
      Upgrades in progress:  ← clock-icon rows (skip)
      Suggested upgrades:    ← gold/elixir/dark cost rows (target)
      Other upgrades:        ← more cost rows (skip — these are too
                              expensive or low-priority per game)

    We OCR the tooltip text column to locate the 'Suggested upgrades:'
    header line, then return only the rows below it (and above the
    'Other upgrades:' header).
    """
    from screen.ocr import _get_reader
    # Run OCR on a wider crop to capture row text + headers
    crop = frame[100:430, 320:920]
    upscaled = cv2.resize(crop, (crop.shape[1] * 2, crop.shape[0] * 2), interpolation=cv2.INTER_CUBIC)
    reader = _get_reader()
    results = reader.readtext(upscaled, detail=1, paragraph=False)
    # Find Y bounds of 'Suggested' section in the upscaled-then-mapped frame.
    suggest_y = None
    other_y = None
    for bbox, text, _ in results:
        text_l = text.lower()
        # 'sugg' covers OCR variants like 'sugges' / '@uggested'
        if "sugg" in text_l and suggest_y is None:
            ys = [pt[1] for pt in bbox]
            suggest_y = (min(ys) // 2) + 100  # back to frame coords
        if "other" in text_l and other_y is None:
            ys = [pt[1] for pt in bbox]
            other_y = (min(ys) // 2) + 100
    if suggest_y is None:
        suggest_y = 200  # fallback
    if other_y is None:
        other_y = 380

    # Start scanning AFTER the 'Suggested upgrades:' header line itself —
    # the header text sits at the suggest_y baseline, the first actual row
    # begins ~22px below it.
    scan_start = suggest_y + 22
    scan_end = max(scan_start + 30, other_y - 5)
    band = frame[scan_start:scan_end, 670:720]
    if band.size == 0:
        return []
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    sat = cv2.inRange(hsv, np.array([0, 90, 80]), np.array([180, 255, 255]))
    proj = sat.sum(axis=1)
    rows: list[int] = []
    last_y = -100
    for y, v in enumerate(proj):
        if v > 100 and y - last_y > 24:  # min 24px between rows
            rows.append(y + scan_start)
            last_y = y
    # The 'Suggested upgrades:' section in CoC's tooltip caps at 3 rows
    # before the 'Other upgrades:' divider. OCR sometimes misses the
    # divider header (small text), so we hard-cap the row list at 3
    # to avoid leaking into the Other section.
    rows = rows[:3]
    log.info(f"suggestion rows: header={suggest_y} scan={scan_start}-{scan_end} rows={rows}")
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


# Words on a building card that mean tapping a button SPENDS GEMS or
# does something we don't want. If ANY of these appear, abort entirely.
# - 'ready' / 'work'   → Builder's Apprentice card ("Ready to work!")
# - 'boost' / 'finish' → gem-cost time-skip buttons
# - 'cancel'           → cancels an in-progress upgrade (we don't want this)
# - 'apprentice'       → Assign Apprentice (gem cost)
# - 'pets'             → Pet House sub-menu (different card structure)
# - 'recogn' / 'assign'→ Builder's Apprentice "Recognize Builder's Hut" header
GEM_BUTTON_WORDS = (
    "boost", "finish", "cancel", "apprentice", "pets",
    "ready to", "recogn", "assign",
)

# OCR variations of "Upgrade" — we accept any of these. EasyOCR commonly
# returns '@pgrade' / 'upykade' / 'upgkade' due to the cartoon font.
# A clean wall card has 'Upgrade More' + 3× 'Upgrade' so 'grade' should
# appear at least twice.
UPGRADE_PATTERNS = ("upgrade", "@pgrade", "upykade", "upgkade", "@grade")


def _safe_to_upgrade(frame: np.ndarray) -> tuple[bool, str]:
    """Return (safe, reason). Only tap Upgrade if card text is unambiguous."""
    text = _read_card_text(frame)
    if not text:
        return False, "no_text"
    for word in GEM_BUTTON_WORDS:
        if word in text:
            return False, f"refused (gem/wrong button '{word}' on card): {text!r}"
    if not any(p in text for p in UPGRADE_PATTERNS):
        return False, f"no Upgrade pattern on card: {text!r}"
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


def _confirm_cost_kind(frame: np.ndarray) -> str:
    """Return 'gold' / 'elixir' / 'dark' / 'gem' / 'unknown' for the
    cost icon next to the Confirm pill (920-1000, 615-660). Used to
    skip rows whose cost doesn't match the maxed resource."""
    region = frame[615:660, 920:1000]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    gold   = int(cv2.inRange(hsv, np.array([20, 150, 150]), np.array([35, 255, 255])).sum() / 255)
    elixir = int(cv2.inRange(hsv, np.array([140, 100, 100]), np.array([175, 255, 255])).sum() / 255)
    dark   = int(cv2.inRange(hsv, np.array([110, 30, 20]), np.array([140, 200, 110])).sum() / 255)
    gem    = int(cv2.inRange(hsv, np.array([85, 100, 100]), np.array([110, 255, 255])).sum() / 255)
    if gem > 10:
        return "gem"
    counts = {"gold": gold, "elixir": elixir, "dark": dark}
    best = max(counts, key=counts.get)
    if counts[best] < 50:
        return "unknown"
    return best


def _confirm_pays_resources_only(frame: np.ndarray) -> tuple[bool, str]:
    """SAFETY GATE 2 for the upgrade Confirm dialog.

    The dialog's green Confirm pill shows 'NUMBER + ICON' for the cost.
    We REQUIRE the icon to be gold (yellow) / elixir (pink) / dark (dark
    purple). Anything else (gems, magic items, training potions) → abort.

    Per user instruction: only allow if gold/elixir/dark symbols are
    present. No exceptions.

    Returns (safe, reason).
    """
    # Cost icon sits at the right edge of the green Confirm pill,
    # roughly (940-990, 615-650) in the upgrade dialog.
    region = frame[615:660, 920:1000]
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)

    gold_px   = int(cv2.inRange(hsv, np.array([20, 150, 150]), np.array([35, 255, 255])).sum() / 255)
    elixir_px = int(cv2.inRange(hsv, np.array([140, 100, 100]), np.array([175, 255, 255])).sum() / 255)
    dark_px   = int(cv2.inRange(hsv, np.array([110, 30, 20]), np.array([140, 200, 110])).sum() / 255)
    gem_px    = int(cv2.inRange(hsv, np.array([85, 100, 100]), np.array([110, 255, 255])).sum() / 255)

    counts = {"gold": gold_px, "elixir": elixir_px, "dark": dark_px, "gem": gem_px}
    log.info(f"confirm-cost icon counts: {counts}")

    # Hard reject: any gem signal at all.
    if gem_px > 10:
        return False, f"GEM ICON DETECTED — refusing ({counts})"
    # Hard require: a confident gold/elixir/dark signal.
    best_resource = max(gold_px, elixir_px, dark_px)
    if best_resource < 100:
        return False, f"no clear gold/elixir/dark icon ({counts})"
    return True, f"resource cost ({counts})"


def _dismiss(adb: ADB) -> None:
    """Tap a safe empty area to close any building card or tooltip."""
    adb.tap_precise(100, 100)
    adb.wait_random(0.4, 0.8)


def upgrade_top_suggestion(
    adb: ADB,
    template_set: dict[str, "tmpl.Template"],
    kind: Literal["builder", "lab"],
    prefer_cost: str | None = None,
) -> bool:
    """Open the suggestion tooltip, tap a row, start the upgrade.

    `prefer_cost` ('gold' / 'elixir' / 'dark'): if given, pick the first
    row whose cost icon matches. Falls back to the first row if no match.
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

    # Order rows by preference. If prefer_cost is given, dark-cost rows
    # first when prefer_cost='dark', etc. Then fall through to others.
    if prefer_cost:
        row_costs = [(y, _row_cost_kind(frame, y)) for y in rows]
        log.info(f"suggest[{kind}]: rows={row_costs} prefer={prefer_cost}")
        ordered = (
            [y for y, c in row_costs if c == prefer_cost]
            + [y for y, c in row_costs if c != prefer_cost]
        )
    else:
        ordered = list(rows)

    # Try rows sequentially until one succeeds. Cap at 3 attempts so a
    # broken card flow doesn't burn the whole cycle.
    for attempt, row_y in enumerate(ordered[:3]):
        log.info(f"suggest[{kind}]: attempt {attempt + 1} row y={row_y}")
        adb.tap_precise(TOOLTIP_ROW_X, row_y)
        adb.wait_random(2.0, 3.0)

        frame = grab_frame_bgr(adb)
        safe, reason = _safe_to_upgrade(frame)
        if not safe:
            log.info(f"suggest[{kind}]: row {attempt + 1} not safe — {reason}")
            _dismiss(adb)
            # Reopen the tooltip for the next attempt.
            if attempt + 1 < min(3, len(ordered)):
                adb.tap_precise(*info_pos)
                adb.wait_random(0.8, 1.4)
            continue

        upgrade_pos = _find_upgrade_button(frame)
        if upgrade_pos is None:
            log.info(f"suggest[{kind}]: row {attempt + 1} — no Upgrade button")
            _dismiss(adb)
            if attempt + 1 < min(3, len(ordered)):
                adb.tap_precise(*info_pos)
                adb.wait_random(0.8, 1.4)
            continue

        log.info(f"suggest[{kind}]: tapping Upgrade at {upgrade_pos}")
        adb.tap_precise(*upgrade_pos)
        adb.wait_random(1.0, 1.5)

        frame = grab_frame_bgr(adb)
        if not _confirm_button_visible(frame):
            log.info(f"suggest[{kind}]: row {attempt + 1} — no confirm dialog")
            x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
            if x:
                adb.tap_precise(x[0], x[1])
            _dismiss(adb)
            if attempt + 1 < min(3, len(ordered)):
                adb.tap_precise(*info_pos)
                adb.wait_random(0.8, 1.4)
            continue

        safe_cost, cost_reason = _confirm_pays_resources_only(frame)
        if not safe_cost:
            log.error(f"suggest[{kind}]: REFUSING confirm — {cost_reason}")
            x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
            if x:
                adb.tap_precise(x[0], x[1])
            _dismiss(adb)
            if attempt + 1 < min(3, len(ordered)):
                adb.tap_precise(*info_pos)
                adb.wait_random(0.8, 1.4)
            continue

        # If the caller wanted a specific cost type (e.g. dark when dark
        # is maxed), and this dialog's cost doesn't match, abort and try
        # the next row. Better than spending the wrong resource.
        if prefer_cost:
            actual_cost = _confirm_cost_kind(frame)
            if actual_cost != prefer_cost and actual_cost in ("gold", "elixir", "dark"):
                log.info(
                    f"suggest[{kind}]: row {attempt + 1} cost={actual_cost}, "
                    f"want={prefer_cost} — closing and trying next"
                )
                x = find_red_close_x(frame, region=(1080, 0, 1280, 200))
                if x:
                    adb.tap_precise(x[0], x[1])
                _dismiss(adb)
                if attempt + 1 < min(3, len(ordered)):
                    adb.tap_precise(*info_pos)
                    adb.wait_random(0.8, 1.4)
                continue

        log.info(f"suggest[{kind}]: confirm cost is resources ({cost_reason}) — proceeding")
        adb.tap_precise(*CONFIRM_BTN)
        adb.wait_random(1.5, 2.5)
        _dismiss(adb)
        log.info(f"suggest[{kind}]: upgrade started (row {attempt + 1})")
        return True

    log.info(f"suggest[{kind}]: exhausted {min(3, len(ordered))} rows without an upgrade")
    return False


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
