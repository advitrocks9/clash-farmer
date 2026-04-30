"""One attack to capture btn_confirm_surrender."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import easyocr
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from input.adb import ADB, ADBConfig
from capture_v2 import Driver, expand, find_text


def main():
    d = Driver()
    f = d.go_home()

    # Attack → army edit
    d.tap(70, 660, wait=2.5)
    f = d.grab("army")
    # Tap ATTACK on army edit
    d.tap(1153, 641, wait=11.0)
    f = d.grab("opponent_for_surrender")

    # Deploy lots of sneaky goblins so battle doesn't auto-end
    d.tap(70, 670, wait=0.4)  # select goblin
    # Deploy 12 around the perimeter
    edges = [(x, 200) for x in range(200, 1080, 70)] + \
            [(x, 540) for x in range(200, 1080, 70)] + \
            [(200, y) for y in range(220, 540, 50)] + \
            [(1080, y) for y in range(220, 540, 50)]
    for x, y in edges[:30]:
        d.adb._run(f"input tap {x} {y}")
        time.sleep(0.05)
    time.sleep(2.0)

    f = d.grab("battle_with_goblins")

    # Find End Battle / Surrender (white flag bottom-left)
    sur = find_text(f, ["End Battle", "Surrender", "nd Battle", "urrender"], roi=(0, 480, 250, 580))
    if sur:
        cx, cy = (sur[0] + sur[2]) // 2, (sur[1] + sur[3]) // 2
        d.tap(cx, cy, wait=2.0)
        f = d.grab("surrender_dialog")
        # Find confirm button
        cs = find_text(f, ["Surrender", "OK", "Yes", "Confirm"])
        if cs:
            d.save_template(f, "btn_confirm_surrender", expand(cs, 12, f.shape))
            cx, cy = (cs[0] + cs[2]) // 2, (cs[1] + cs[3]) // 2
            d.tap(cx, cy, wait=5.0)
            f = d.grab("post_surrender_result")
        else:
            print("WARN: no confirm button on dialog")
            # Maybe it's a different layout — capture full frame for inspection
            cv2.imwrite("/tmp/surrender_dialog_full.png", f)
            d.adb._run("input keyevent 4")  # back out
    else:
        print("WARN: End Battle button not found in battle")
        cv2.imwrite("/tmp/battle_no_surrender.png", f)

    # Try to return home
    f = d.grab("post_battle")
    rh = find_text(f, ["Return Home", "Return"])
    if rh:
        cx, cy = (rh[0] + rh[2]) // 2, (rh[1] + rh[3]) // 2
        d.tap(cx, cy, wait=5.0)
    else:
        # Force-restart as last resort to get back home
        d.restart_coc(wait=20)


if __name__ == "__main__":
    main()
