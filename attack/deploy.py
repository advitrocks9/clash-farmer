from __future__ import annotations

import logging
import time

import numpy as np

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.ocr import get_roi, read_number

log = logging.getLogger(__name__)

DEPLOY_EDGES = {
    # Zoomed-out battle view layout (verified 2026-04-30 1280x720):
    #   y 0-15       — top resource bar / coin display (skip)
    #   y 15-90      — top-edge green strip (deploy)
    #   y 90-455     — base + opponent buildings (red, no deploy)
    #   y 455-505    — bottom-edge green strip (deploy)
    #   y 510-580    — End Battle + Boost Army + Boost Heroes buttons
    #   y 580-720    — Next button (right) + army hotbar (NEVER tap)
    #   x 0-90       — extreme left edge (deploy)
    #   x 1190-1280  — extreme right edge (deploy) — but x>1080 y<180 is top resource bar
    "top":    [(x, y) for y in (15, 35, 55, 75) for x in range(120, 1080, 30)],
    "bottom": [(x, y) for y in (465, 485, 505) for x in range(220, 1060, 30)],
    "left":   [(x, y) for x in (15, 35, 55, 75) for y in range(110, 505, 25)],
    "right":  [(x, y) for x in (1205, 1225, 1245, 1265) for y in range(110, 505, 25)],
}

# Hard mask — never tap inside these rectangles. Defence in depth on top of
# the DEPLOY_EDGES layout so a future tweak can't accidentally hit the GUI.
DEPLOY_KILL_ZONES = [
    (0, 510, 1280, 720),    # ENTIRE bottom GUI band: End Battle / Boost / hotbar / Next
    (1080, 0, 1280, 200),   # top-right resource bar + close-X
    (0, 0, 200, 100),       # top-left player name + Available Loot
]


def _safe(x: int, y: int) -> bool:
    for kx1, ky1, kx2, ky2 in DEPLOY_KILL_ZONES:
        if kx1 <= x <= kx2 and ky1 <= y <= ky2:
            return False
    return True


# Sneaky goblin lives in the leftmost troop slot at a fixed pixel position
# on the rearranged base. Hardcoded — template-based detection was unreliable
# because the slot's "selected" green outline and damage indicators changed
# the cropped icon between frames.
SNEAKY_GOBLIN_SLOT = (45, 665)


def select_troop(adb: ADB, template_set: dict[str, tmpl.Template], troop_name: str) -> bool:
    if troop_name == "sneaky_goblin":
        adb.tap_precise(*SNEAKY_GOBLIN_SLOT)
        adb.wait_random(0.2, 0.4)
        return True
    frame = grab_frame_bgr(adb)
    t = template_set.get(f"troop_{troop_name}")
    if t is None:
        return False
    pos = tmpl.find(frame, t, threshold=0.75, roi=(0, 620, 1280, 720))
    if pos:
        adb.tap_precise(pos[0], pos[1])
        adb.wait_random(0.2, 0.4)
        return True
    return False


def deploy_sneaky_goblins(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    # Zoom out so DEPLOY_EDGES land on the green strip outside the base.
    adb.bluestacks_zoom_out(taps=6)
    adb.wait_random(0.3, 0.6)

    if not select_troop(adb, template_set, "sneaky_goblin"):
        log.warning("Could not find sneaky goblin in army bar — tapping first slot")
        # Slot 1 icon center is at (90, 670). x=40 hits the left screen edge
        # rather than the goblin sprite, so the slot never gets selected and
        # subsequent deploy taps fire with no troop chosen.
        adb.tap_precise(90, 670)
        adb.wait_random(0.3, 0.5)

    all_points: list[tuple[int, int]] = []
    for edge_name in ("top", "bottom", "left", "right"):
        all_points.extend(DEPLOY_EDGES[edge_name])
    safe_points = [(x, y) for x, y in all_points if _safe(x, y)]
    skipped = len(all_points) - len(safe_points)
    adb.tap_burst(safe_points, gap_ms=20)

    log.info(f"Issued {len(safe_points)} sneaky goblin deploy taps "
             f"({skipped} masked out of GUI kill-zones)")


def deploy_heroes(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    # Drop each hero on a different edge — center of screen is inside the
    # opponent base (red zone) and a deploy tap there does nothing.
    hero_drops = [
        ("hero_bk", (640, 605)),    # bottom center, green strip above troop bar
        ("hero_aq", (640, 25)),     # top center
        ("hero_gw", (40, 360)),     # left middle
        ("hero_rc", (1240, 360)),   # right middle
    ]
    for name, (dx, dy) in hero_drops:
        t = template_set.get(name)
        if t is None:
            continue
        frame = grab_frame_bgr(adb)
        pos = tmpl.find(frame, t, threshold=0.75, roi=(0, 620, 1280, 720))
        if pos:
            adb.tap_precise(pos[0], pos[1])
            adb.wait_random(0.2, 0.4)
            adb.tap_fast(dx, dy)
            adb.wait_random(0.3, 0.5)
            log.info(f"Deployed {name} at ({dx},{dy})")


def monitor_battle(
    adb: ADB,
    template_set: dict[str, tmpl.Template],
    max_battle_seconds: float = 90.0,
) -> None:
    """Wait until battle ends or loot plateaus, whichever comes first.

    Exit signals (polled every second):
    - btn_return_home anywhere on screen → battle ended naturally
    - both surrender + end-battle absent → battle ended naturally
    - loot_gained OCR plateaus for 2 reads → surrender to save time
    - max_battle_seconds elapsed → surrender (hard stop)
    """
    btn_return = template_set.get("btn_return_home")
    btn_surrender = template_set.get("btn_surrender")
    btn_end_battle = template_set.get("btn_end_battle")

    last_loot: int | None = None
    plateau_count = 0
    max_plateau = 2  # was 3 — faster surrender on stalled raids
    check_interval = 1.0
    started_at = time.time()
    battle_btn_roi = (0, 500, 200, 580)

    while True:
        if time.time() - started_at > max_battle_seconds:
            log.warning(f"Battle timeout after {max_battle_seconds:.0f}s — surrendering")
            _surrender(adb, template_set)
            return

        frame = grab_frame_bgr(adb)

        if btn_return is not None and tmpl.exists(frame, btn_return, threshold=0.6):
            log.info("Battle ended (return-home button visible)")
            return
        # Surrender button text swaps to "End Battle" once troops are deployed.
        # Battle has only really ended when NEITHER label is present at the
        # bottom-left button position.
        surrender_present = btn_surrender is not None and tmpl.exists(
            frame, btn_surrender, threshold=0.6, roi=battle_btn_roi
        )
        end_battle_present = btn_end_battle is not None and tmpl.exists(
            frame, btn_end_battle, threshold=0.6, roi=battle_btn_roi
        )
        if (
            (btn_surrender is not None or btn_end_battle is not None)
            and not surrender_present
            and not end_battle_present
            and time.time() - started_at > 6
        ):
            log.info("Battle ended (surrender/end-battle button gone)")
            return

        current_loot = read_number(frame, get_roi("loot_gained"))
        # Treat OCR misses (None) as plateau ticks too — when troops are
        # idle and not generating new loot the OCR often can't lock onto
        # the static loot bar either, so a None should still count toward
        # surrender rather than resetting the plateau counter.
        if last_loot is not None:
            if current_loot is None or current_loot <= last_loot:
                plateau_count += 1
                log.info(f"Loot plateaued ({plateau_count}/{max_plateau}): "
                         f"{current_loot if current_loot is not None else 'OCR-miss'}")
            else:
                plateau_count = 0
        if current_loot is not None:
            last_loot = current_loot
        if plateau_count >= max_plateau:
            log.info("Loot gain plateaued — surrendering")
            _surrender(adb, template_set)
            return

        time.sleep(check_interval)


def _surrender(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    # End Battle red button location (battle view bottom-left).
    adb.tap_precise(85, 540)
    adb.wait_random(0.5, 1.0)
    # The "Surrender?" confirm dialog has Okay (green) on the right.
    btn_confirm = template_set.get("btn_confirm_surrender")
    if btn_confirm:
        frame = grab_frame_bgr(adb)
        pos = tmpl.find(frame, btn_confirm)
        if pos:
            adb.tap(pos[0], pos[1])
            adb.wait_random(1.0, 2.0)
            return
    # Fallback: tap the known Okay position.
    adb.tap_precise(782, 412)
    adb.wait_random(1.0, 2.0)


def wait_for_result_screen(adb: ADB, template_set: dict[str, tmpl.Template], timeout: float = 15.0) -> bool:
    btn = template_set.get("btn_return_home")
    if btn is None:
        adb.wait(timeout)
        return False

    start = time.time()
    while time.time() - start < timeout:
        frame = grab_frame_bgr(adb)
        if tmpl.exists(frame, btn, roi=(500, 550, 780, 700)):
            return True
        time.sleep(1.0)
    return False


def return_home(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    btn = template_set.get("btn_return_home")
    if btn is None:
        adb.back()
        return

    frame = grab_frame_bgr(adb)
    pos = tmpl.find(frame, btn, roi=(500, 550, 780, 700))
    if pos:
        adb.tap(pos[0], pos[1])
    else:
        adb.back()
    adb.wait_random(2.0, 4.0)
