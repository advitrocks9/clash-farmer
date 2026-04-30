"""Verify all saved templates against the current frame.

Loads each template from templates/ and runs cv2.matchTemplate against the
current screencap. Reports score for each. Templates whose target screen isn't
visible right now get score 0 — that's expected; they should be tested when
their state is active.
"""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.adb import ADB, ADBConfig
from screen import templates as tmpl

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


def main():
    adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
    adb.connect()
    img = adb.screencap()
    rgb = np.array(img.convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()
    cv2.imwrite("/tmp/verify_frame.png", bgr)
    print(f"frame: {bgr.shape[1]}x{bgr.shape[0]}\n")

    results = []
    for path in sorted(TEMPLATES.glob("*.png")):
        t = tmpl.load_template(path)
        pos = tmpl.find(bgr, t, threshold=0.0)  # don't filter; we want raw score
        # cv2.matchTemplate score
        gframe = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        gtmpl = t.gray
        if t.mask is not None:
            res = cv2.matchTemplate(gframe, gtmpl, cv2.TM_CCOEFF_NORMED, mask=t.mask)
        else:
            res = cv2.matchTemplate(gframe, gtmpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        passes = "  ✓" if mv >= 0.85 else "  ?" if mv >= 0.70 else "  ✗"
        print(f"{passes} {path.name:30s} score={mv:.3f} at {ml} size={t.w}x{t.h}")
        results.append((path.name, mv, ml))

    on_screen = [r for r in results if r[1] >= 0.85]
    print(f"\nVisible on current frame: {len(on_screen)}/{len(results)}")
    print("(Templates not visible → captured for OTHER game states; that's fine)")


if __name__ == "__main__":
    main()
