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
    # Zoomed-out battle view — base shrinks, exposing a wide green band on
    # all four edges. Sweep multiple rows/columns densely so most positions
    # land in the deploy zone. Only ~16% of single-row taps were valid; this
    # version doubles the coverage on each edge.
    "top": [(x, y) for y in (15, 35, 55, 75) for x in range(80, 1200, 30)],
    "bottom": [(x, y) for y in (565, 590, 605) for x in range(80, 1200, 30)],
    "left": [(x, y) for x in (15, 35, 55, 75) for y in range(95, 575, 25)],
    "right": [(x, y) for x in (1205, 1225, 1245, 1265) for y in range(95, 575, 25)],
}


def select_troop(adb: ADB, template_set: dict[str, tmpl.Template], troop_name: str) -> bool:
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
    adb.tap_burst(all_points, gap_ms=20)

    log.info(f"Issued {len(all_points)} sneaky goblin deploy taps across all edges")


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
    max_battle_seconds: float = 180.0,
) -> None:
    last_loot: int | None = None
    plateau_count = 0
    max_plateau = 3
    check_interval = 2.0
    started_at = time.time()

    while True:
        if time.time() - started_at > max_battle_seconds:
            log.warning(f"Battle timeout after {max_battle_seconds:.0f}s — surrendering")
            _surrender(adb, template_set)
            return

        frame = grab_frame_bgr(adb)

        btn_return = template_set.get("btn_return_home")
        if btn_return and tmpl.exists(frame, btn_return, roi=(500, 550, 780, 700)):
            log.info("Battle ended naturally")
            return

        current_loot = read_number(frame, get_roi("loot_gained"))

        if current_loot is not None and last_loot is not None:
            if current_loot <= last_loot:
                plateau_count += 1
                log.info(f"Loot plateaued ({plateau_count}/{max_plateau}): {current_loot:,}")
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
