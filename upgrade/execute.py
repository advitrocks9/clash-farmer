from __future__ import annotations

import json
import logging
from pathlib import Path

from input.adb import ADB
from planner.schemas import UpgradeDecision
from screen import templates as tmpl
from screen.capture import grab_frame_bgr

log = logging.getLogger(__name__)

LAYOUT_PATH = Path(__file__).resolve().parent.parent / "base" / "layout.json"


def load_layout() -> dict[str, tuple[int, int]]:
    if not LAYOUT_PATH.exists():
        return {}
    raw = json.loads(LAYOUT_PATH.read_text())
    return {k: (v[0], v[1]) for k, v in raw.items()}


def execute_upgrade(
    adb: ADB,
    decision: UpgradeDecision,
    template_set: dict[str, tmpl.Template],
) -> bool:
    layout = load_layout()

    coords = layout.get(decision.target)
    if coords is None:
        log.warning(f"No coordinates for {decision.target} in layout.json — skipping")
        return False

    log.info(f"Upgrading {decision.target} to level {decision.target_level}")

    adb.tap(coords[0], coords[1])
    adb.wait_random(0.8, 1.2)

    frame = grab_frame_bgr(adb)
    btn = template_set.get("btn_upgrade")
    if btn is None:
        log.error("btn_upgrade template not loaded")
        _dismiss(adb)
        return False

    pos = tmpl.find(frame, btn)
    if pos is None:
        log.warning(f"No upgrade button found for {decision.target} — may be max level or busy")
        _dismiss(adb)
        return False

    adb.tap(pos[0], pos[1])
    adb.wait_random(0.5, 1.0)

    frame = grab_frame_bgr(adb)

    btn_insufficient = template_set.get("btn_insufficient_resources")
    if btn_insufficient and tmpl.exists(frame, btn_insufficient):
        log.warning(f"Insufficient resources for {decision.target}")
        _dismiss(adb)
        return False

    btn_confirm = template_set.get("btn_confirm_upgrade")
    if btn_confirm:
        pos_confirm = tmpl.find(frame, btn_confirm)
        if pos_confirm:
            adb.tap(pos_confirm[0], pos_confirm[1])
            adb.wait_random(1.0, 2.0)
            log.info(f"Upgrade confirmed: {decision.target} → lvl {decision.target_level}")
            return True

    # Some upgrades don't have a confirm dialog — the tap on upgrade button is enough
    log.info(f"Upgrade initiated: {decision.target} → lvl {decision.target_level}")
    return True


def _dismiss(adb: ADB) -> None:
    adb.back()
    adb.wait_random(0.3, 0.5)


def execute_hero_upgrade(
    adb: ADB,
    decision: UpgradeDecision,
    template_set: dict[str, tmpl.Template],
) -> bool:
    layout = load_layout()
    coords = layout.get(decision.target)
    if coords is None:
        log.warning(f"No coordinates for hero {decision.target} in layout.json")
        return False

    log.info(f"Upgrading hero {decision.target} to level {decision.target_level}")

    adb.tap(coords[0], coords[1])
    adb.wait_random(0.8, 1.2)

    frame = grab_frame_bgr(adb)
    btn = template_set.get("btn_upgrade_hero")
    if btn is None:
        btn = template_set.get("btn_upgrade")
    if btn is None:
        log.error("No hero upgrade template loaded")
        _dismiss(adb)
        return False

    pos = tmpl.find(frame, btn)
    if pos is None:
        log.warning(f"No upgrade button for hero {decision.target}")
        _dismiss(adb)
        return False

    adb.tap(pos[0], pos[1])
    adb.wait_random(0.5, 1.0)

    frame = grab_frame_bgr(adb)
    btn_confirm = template_set.get("btn_confirm_upgrade")
    if btn_confirm:
        pos_confirm = tmpl.find(frame, btn_confirm)
        if pos_confirm:
            adb.tap(pos_confirm[0], pos_confirm[1])
            adb.wait_random(1.0, 2.0)

    log.info(f"Hero upgrade initiated: {decision.target} → lvl {decision.target_level}")
    return True
