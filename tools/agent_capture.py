"""Agent-driven template capture.

I (the agent) drive BlueStacks via ADB and crop named templates from screencaps.
Each call: takes a fresh frame, crops one region by name+box, saves to templates/.
Run with --tap to also issue a tap before capturing (for state navigation).

Usage examples:
  uv run python tools/agent_capture.py --name btn_settings_gear --box 1200 500 1255 555
  uv run python tools/agent_capture.py --tap 1227 525 --wait 1 --capture-frame /tmp/post_tap.png
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.adb import ADB, ADBConfig

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"


def capture_bgr(adb: ADB) -> np.ndarray:
    img = adb.screencap()
    rgb = np.array(img.convert("RGB"))
    return rgb[:, :, ::-1].copy()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", help="template name (without .png)")
    p.add_argument("--box", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    p.add_argument("--tap", nargs=2, type=int, metavar=("X", "Y"), help="tap before capture")
    p.add_argument("--wait", type=float, default=0.5, help="seconds to sleep after tap")
    p.add_argument("--back", action="store_true", help="press BACK key before capture")
    p.add_argument("--capture-frame", help="save full screencap to this path too")
    p.add_argument("--annotate", help="save full frame with [box] outlined to this path")
    args = p.parse_args()

    adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
    adb.connect()

    if args.back:
        adb._run("input keyevent 4")
        time.sleep(args.wait)

    if args.tap:
        x, y = args.tap
        adb._run(f"input tap {x} {y}")
        time.sleep(args.wait)

    frame = capture_bgr(adb)
    print(f"frame: {frame.shape[1]}x{frame.shape[0]}")

    if args.capture_frame:
        cv2.imwrite(args.capture_frame, frame)
        print(f"full frame: {args.capture_frame}")

    if args.box and args.name:
        x1, y1, x2, y2 = args.box
        crop = frame[y1:y2, x1:x2]
        TEMPLATES.mkdir(exist_ok=True)
        out = TEMPLATES / f"{args.name}.png"
        cv2.imwrite(str(out), crop)
        print(f"saved: {out.relative_to(REPO)} ({x1},{y1})-({x2},{y2}) = {x2-x1}x{y2-y1}")

    if args.annotate and args.box:
        ann = frame.copy()
        x1, y1, x2, y2 = args.box
        cv2.rectangle(ann, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.imwrite(args.annotate, ann)
        print(f"annotated: {args.annotate}")


if __name__ == "__main__":
    main()
