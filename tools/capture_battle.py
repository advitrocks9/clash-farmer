"""Drive through one full attack cycle to capture remaining templates.

Flow: home → tap Attack → army edit → tap ATTACK → opponent base → deploy
goblin → battle → surrender → confirm → result → return home.

Captures: btn_next (proper), btn_confirm_surrender, verifies others.
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
from capture_v2 import Driver, expand, find_text, ocr


def find_and_tap(d, frame, targets, label="", roi=None):
    """OCR-find a button by text and tap its center."""
    box = find_text(frame, targets, roi=roi)
    if box is None:
        d.log if False else None  # noqa
        print(f"  [find_and_tap] could not find {targets}")
        return None
    cx = (box[0] + box[2]) // 2
    cy = (box[1] + box[3]) // 2
    print(f"  [find_and_tap] {label or targets[0]} center=({cx},{cy})")
    d.tap(cx, cy, wait=2.0)
    return box


def main():
    d = Driver()

    # === STATE: HOME ===
    f = d.go_home()

    # === Tap Attack (opens Army Edit) ===
    print("\n=== ATTACK button → Army Edit ===")
    # btn_attack found at (10, 615). Center (70, 660).
    d.tap(70, 660, wait=2.5)
    f = d.grab("after_attack_tap")

    # On army edit, look for ATTACK button. Resilient via OCR.
    print("\n=== ATTACK on Army Edit ===")
    res = ocr().readtext(f)
    attack_box = None
    for bbox, text, conf in res:
        if text.strip().lower() in ("attack", "attack!", "attac") and conf > 0.4:
            pts = np.array(bbox)
            attack_box = (
                int(pts[:, 0].min()),
                int(pts[:, 1].min()),
                int(pts[:, 0].max()),
                int(pts[:, 1].max()),
            )
            break
    if attack_box is None:
        # Fixed fallback at (1153, 641) per earlier
        d.tap(1153, 641, wait=10.0)
    else:
        cx = (attack_box[0] + attack_box[2]) // 2
        cy = (attack_box[1] + attack_box[3]) // 2
        d.tap(cx, cy, wait=10.0)

    # === STATE: OPPONENT_BASE ===
    print("\n=== OPPONENT_BASE ===")
    f = d.grab("opponent")
    # btn_next properly
    nx_box = find_text(f, ["Next"], roi=(1080, 460, 1280, 600))
    if nx_box:
        d.save_template(f, "btn_next", expand(nx_box, 12, f.shape))
    else:
        # Fallback to known approximate coords
        d.save_template(f, "btn_next", (1100, 470, 1260, 570))

    # === Deploy a single sneaky goblin (slot 1 in army bar) ===
    # Tap slot 1 to select sneaky goblins
    print("\n=== DEPLOY 1 goblin to start battle ===")
    d.tap(70, 670, wait=0.5)
    # Tap on the base at center to deploy
    d.tap(640, 360, wait=2.5)

    # === STATE: BATTLE (after deploy) ===
    f = d.grab("battle")
    # Now try to find Surrender button (bottom-left, white flag)
    sur_box = find_text(f, ["Surrender", "End Battle"], roi=(0, 480, 220, 720))
    if sur_box:
        d.save_template(f, "btn_surrender", expand(sur_box, 10, f.shape))
        d.save_template(f, "btn_end_battle", expand(sur_box, 10, f.shape))
        cx = (sur_box[0] + sur_box[2]) // 2
        cy = (sur_box[1] + sur_box[3]) // 2
        d.tap(cx, cy, wait=2.0)

        # === STATE: SURRENDER_DIALOG ===
        f = d.grab("surrender_dialog")
        # Look for confirm button — usually "Surrender" or "OK" or "Yes"
        cs_box = find_text(f, ["Surrender", "OK", "Yes", "Confirm"])
        if cs_box:
            d.save_template(f, "btn_confirm_surrender", expand(cs_box, 12, f.shape))
            cx = (cs_box[0] + cs_box[2]) // 2
            cy = (cs_box[1] + cs_box[3]) // 2
            d.tap(cx, cy, wait=5.0)

            # === STATE: RESULT ===
            f = d.grab("result")
            rh_box = find_text(f, ["Return Home", "eturn Home", "Return"])
            if rh_box:
                d.save_template(f, "btn_return_home", expand(rh_box, 12, f.shape))
                cx = (rh_box[0] + rh_box[2]) // 2
                cy = (rh_box[1] + rh_box[3]) // 2
                d.tap(cx, cy, wait=5.0)
        else:
            print("  WARN: no confirm button on surrender dialog")
    else:
        print("  WARN: no surrender button")

    print("\n=== BATTLE FLOW DONE ===")


if __name__ == "__main__":
    main()
