"""Capture the attack/battle/result flow.

Drives Attack → Find Match → opponent → deploy troops → battle → surrender → home.
Captures all templates along the way. Costs: troops (instant/free) + small ad
of attack cooldown. Leaves the user at home village.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from input.adb import ADB, ADBConfig
from capture_v2 import (
    Driver,
    detect_state,
    expand,
    find_red_x,
    find_text,
    is_attack_chooser,
    is_home,
    is_opponent_base,
    log,
    ocr,
)

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


def cap_attack(d: Driver):
    log("\n=== ATTACK CHOOSER ===")
    f = d.go_home()
    # Tap Attack button — find via template match against saved btn_attack
    tmpl = cv2.imread(str(TEMPLATES / "btn_attack.png"))
    res = cv2.matchTemplate(
        cv2.cvtColor(f, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY),
        cv2.TM_CCOEFF_NORMED,
    )
    _, mv, _, ml = cv2.minMaxLoc(res)
    log(f"  btn_attack found at {ml} score={mv:.3f}")
    cx = ml[0] + tmpl.shape[1] // 2
    cy = ml[1] + tmpl.shape[0] // 2
    d.tap(cx, cy, wait=2.5)
    f = d.grab("attack_chooser")

    # btn_find_match
    fm = find_text(f, ["Find a Match", "Find Match", "ind a Match", "ind Match", "Match"])
    if fm:
        d.save_template(f, "btn_find_match", expand(fm, 12, f.shape))
        cx = (fm[0] + fm[2]) // 2
        cy = (fm[1] + fm[3]) // 2
        d.tap(cx, cy, wait=12.0)  # matchmaking ~5-10s
        f = d.grab("opponent_base")

        # btn_next at right edge
        nx = find_text(f, ["Next", "kip"], roi=(1080, 400, 1280, 700))
        if nx:
            d.save_template(f, "btn_next", expand(nx, 8, f.shape))
        else:
            d.save_template(f, "btn_next", (1130, 540, 1255, 620))

        # === Deploy a sneaky goblin to start battle ===
        # First the troop bar at the bottom (y=620-720). Tap first slot at y=670.
        # Actually we need the army bar — try to capture it first by zooming out
        # which the bot does before deploy.
        # Quick zoom-out via mouse roll
        for _ in range(8):
            d.adb._run("input roll 0 -3")
            time.sleep(0.1)
        time.sleep(1.0)

        f = d.grab("zoomed_opponent_base")

        # The army bar's first slot (sneaky goblin) is at left
        # Tap it
        d.tap(100, 670, wait=1.0)
        f = d.grab("troop_selected")

        # Capture troop_sneaky_goblin from the army bar (first slot)
        # Each slot is ~88x88 px. First slot starts around x=60.
        d.save_template(f, "troop_sneaky_goblin", (60, 625, 145, 715))

        # Capture hero portraits (4 heroes after troop slots typically)
        # Heroes appear right of troops. Try to find via OCR text labels (BK/AQ/etc)
        # or by fixed positions.
        # For TH15, the army bar order is typically: troops then heroes from left to right.
        # Each slot ~88x88. Heroes start around x=600 (approx).
        # Capture conservatively — may need adjustment.
        # We'll save speculative crops; user can verify.

        # Deploy goblins on perimeter to start battle (so we can capture battle UI)
        for x in range(200, 1080, 60):
            d.adb._run(f"input tap {x} 200")
            time.sleep(0.05)
        for x in range(200, 1080, 60):
            d.adb._run(f"input tap {x} 540")
            time.sleep(0.05)
        time.sleep(2.0)

        f = d.grab("battle_started")
        cv2.imwrite("/tmp/battle_full.png", f)

        # Try to find heroes by OCR label
        for hero_short, hero_targets in [
            ("hero_bk", ["BK", "Barb"]),
            ("hero_aq", ["AQ", "Arch"]),
            ("hero_gw", ["GW", "Wa", "ard"]),
            ("hero_rc", ["RC", "Roy"]),
        ]:
            box = find_text(f, hero_targets, roi=(150, 620, 1280, 720))
            if box:
                d.save_template(f, hero_short, expand(box, 12, f.shape))

        # btn_surrender — white flag bottom-left
        sur = find_text(f, ["Surrender", "End Battle", "urrender"], roi=(0, 600, 250, 720))
        if sur:
            d.save_template(f, "btn_surrender", expand(sur, 10, f.shape))
            cx = (sur[0] + sur[2]) // 2
            cy = (sur[1] + sur[3]) // 2
            d.tap(cx, cy, wait=1.5)
            f = d.grab("surrender_dialog")
            # btn_confirm_surrender
            cs = find_text(f, ["Surrender", "Yes", "OK"])
            if cs:
                d.save_template(f, "btn_confirm_surrender", expand(cs, 12, f.shape))
                cx = (cs[0] + cs[2]) // 2
                cy = (cs[1] + cs[3]) // 2
                d.tap(cx, cy, wait=4.0)
                f = d.grab("battle_result")
                # btn_return_home
                rh = find_text(f, ["Return Home", "Return", "eturn"])
                if rh:
                    d.save_template(f, "btn_return_home", expand(rh, 12, f.shape))
                    cx = (rh[0] + rh[2]) // 2
                    cy = (rh[1] + rh[3]) // 2
                    d.tap(cx, cy, wait=4.0)
        else:
            log("  WARN: no surrender button found")
    else:
        log("  WARN: no Find a Match button")


def main():
    d = Driver()
    cap_attack(d)
    log("\n=== ATTACK FLOW DONE ===")


if __name__ == "__main__":
    main()
