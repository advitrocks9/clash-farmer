"""Programmatic template extraction from a live screencap.

Captures a screenshot via ADB and saves named crops to templates/ at known
coordinates for stable home-village UI elements. Reduces the manual work
calibrate.py has to do.

Usage:
  uv run python tools/auto_extract.py --state home_village
  uv run python tools/auto_extract.py --state battle  # not implemented yet

Coordinates are in 1280x720 frame. Crops are tight around the UI element.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.adb import ADB, ADBConfig

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
ROIS_PATH = REPO / "screen" / "rois.json"


HOME_VILLAGE_TEMPLATES: dict[str, tuple[int, int, int, int]] = {
    # name: (x1, y1, x2, y2) in 1280x720
    "btn_attack": (10, 615, 130, 705),
    "btn_settings_gear": (1180, 545, 1255, 605),
}

HOME_VILLAGE_ROIS: dict[str, tuple[int, int, int, int]] = {
    # Resource counters top-right corner (numbers only).
    "res_gold":        (1135, 27,  1255, 56),
    "res_elixir":      (1135, 60,  1255, 89),
    "res_dark_elixir": (1135, 93,  1255, 122),
    # Builder count badge top-center is variable; leave defaults.
    "builders": (250, 5, 320, 30),
    # Loot bar mid-attack (placeholder; recapture during a real attack).
    "loot_gained": (550, 60, 730, 85),
}


def capture(adb: ADB) -> np.ndarray:
    img = adb.screencap()
    rgb = np.array(img.convert("RGB"))
    return rgb[:, :, ::-1].copy()  # BGR for OpenCV


def crop_save(frame: np.ndarray, name: str, box: tuple[int, int, int, int]) -> Path:
    x1, y1, x2, y2 = box
    crop = frame[y1:y2, x1:x2]
    TEMPLATES.mkdir(exist_ok=True)
    out = TEMPLATES / f"{name}.png"
    cv2.imwrite(str(out), crop)
    return out


def merge_rois(new: dict[str, tuple[int, int, int, int]]) -> dict:
    existing: dict[str, list[int]] = {}
    if ROIS_PATH.exists():
        existing = json.loads(ROIS_PATH.read_text())
    for k, v in new.items():
        existing[k] = list(v)
    ROIS_PATH.parent.mkdir(exist_ok=True)
    ROIS_PATH.write_text(json.dumps(existing, indent=2) + "\n")
    return existing


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        choices=["home_village"],
        default="home_village",
        help="Which game screen the emulator is currently on.",
    )
    args = parser.parse_args()

    adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
    addr = adb.connect()
    print(f"connected: {addr}")

    frame = capture(adb)
    print(f"frame: {frame.shape[1]}x{frame.shape[0]}")

    if args.state == "home_village":
        for name, box in HOME_VILLAGE_TEMPLATES.items():
            out = crop_save(frame, name, box)
            print(f"saved template: {out.relative_to(REPO)} ({box})")
        rois = merge_rois(HOME_VILLAGE_ROIS)
        print(f"saved {len(HOME_VILLAGE_ROIS)} rois → {ROIS_PATH.relative_to(REPO)}")
        print(f"total rois in file: {len(rois)}")


if __name__ == "__main__":
    main()
