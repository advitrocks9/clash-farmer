"""Single-shot driver that walks every CoC state and captures all templates.

Run from repo root after BlueStacks Tiramisu64 is at the home village.
Saves checkpoints to /tmp/cap_NN.png so misfires can be debugged.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.adb import ADB, ADBConfig

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "templates"
LOG = REPO / ".autonomy" / "LOG" / "capture_run.txt"

_OCR = None


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(msg + "\n")


def ocr():
    global _OCR
    if _OCR is None:
        import easyocr
        log("[ocr] loading easyocr...")
        _OCR = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _OCR


def find_text(
    frame: np.ndarray,
    targets: list[str],
    roi: Optional[tuple[int, int, int, int]] = None,
    min_conf: float = 0.3,
) -> Optional[tuple[int, int, int, int]]:
    if roi:
        x1, y1, x2, y2 = roi
        crop = frame[y1:y2, x1:x2]
        ox, oy = x1, y1
    else:
        crop = frame
        ox, oy = 0, 0
    results = ocr().readtext(crop)
    for target in targets:
        for bbox, text, conf in results:
            if target.lower() in text.lower() and conf > min_conf:
                pts = np.array(bbox)
                bx1 = int(pts[:, 0].min()) + ox
                by1 = int(pts[:, 1].min()) + oy
                bx2 = int(pts[:, 0].max()) + ox
                by2 = int(pts[:, 1].max()) + oy
                log(f"  [ocr] '{text}' (~{target!r}) ({bx1},{by1})-({bx2},{by2}) conf={conf:.2f}")
                return (bx1, by1, bx2, by2)
    return None


def find_red_x(frame: np.ndarray) -> Optional[tuple[int, int, int, int]]:
    """Red circular X in the top-right region (CoC modal close)."""
    h, w = frame.shape[:2]
    roi = frame[0 : int(h * 0.35), int(w * 0.65) :]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 130, 100]), np.array([10, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([170, 130, 100]), np.array([179, 255, 255]))
    mask = cv2.bitwise_or(m1, m2)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if 25 <= bw <= 80 and 25 <= bh <= 80 and 0.7 <= bw / bh <= 1.4:
            area = cv2.contourArea(c)
            if area < 200:
                continue
            fx, fy = x + int(w * 0.65), y
            if best is None or area > best[0]:
                best = (area, (fx, fy, fx + bw, fy + bh))
    if best:
        log(f"  [redx] {best[1]} area={int(best[0])}")
        return best[1]
    return None


def expand(box, pad, shape):
    h, w = shape[:2]
    x1, y1, x2, y2 = box
    return (max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad))


class D:
    def __init__(self):
        self.adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
        self.adb.connect()
        log(f"[adb] connected: {self.adb._addr}")
        TEMPLATES.mkdir(exist_ok=True)
        self.step = 0

    def grab(self, label: str) -> np.ndarray:
        img = self.adb.screencap()
        rgb = np.array(img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        self.step += 1
        path = f"/tmp/cap_{self.step:02d}_{label}.png"
        cv2.imwrite(path, bgr)
        log(f"[grab] step {self.step} → {path}")
        return bgr

    def tap(self, x: int, y: int, wait: float = 1.5) -> None:
        log(f"[tap] ({x}, {y}) wait={wait}")
        self.adb._run(f"input tap {x} {y}")
        time.sleep(wait)

    def back(self, wait: float = 1.0) -> None:
        log(f"[back] wait={wait}")
        self.adb._run("input keyevent 4")
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, dur=500, wait=1.0):
        self.adb._run(f"input swipe {x1} {y1} {x2} {y2} {dur}")
        time.sleep(wait)

    def save(self, frame: np.ndarray, name: str, box: tuple[int, int, int, int]) -> None:
        x1, y1, x2, y2 = box
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame[y1:y2, x1:x2]
        out = TEMPLATES / f"{name}.png"
        cv2.imwrite(str(out), crop)
        # self-match for sanity
        gframe = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gtmpl = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(gframe, gtmpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, _ = cv2.minMaxLoc(res)
        log(f"  [save] {name}.png ({x2-x1}x{y2-y1}) match={mv:.3f}")


def ensure_home(d: D) -> np.ndarray:
    """Press back + tap empty until home village is shown (no popups, no info card)."""
    for attempt in range(4):
        frame = d.grab(f"ensure_home_{attempt}")
        # Heuristic: home village has the ATTACK button at (10-130, 615-705) — check
        # if the bottom-left has a yellow Attack badge (high red+green, low blue).
        roi = frame[640:700, 30:120]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        yellow_mask = cv2.inRange(hsv, np.array([15, 100, 150]), np.array([35, 255, 255]))
        ratio = yellow_mask.mean() / 255
        log(f"  attack-yellow ratio: {ratio:.3f}")
        if ratio > 0.10:
            log("  home village confirmed")
            return frame
        d.back(wait=1.0)
    raise RuntimeError("Could not return to home village after 4 backs")


def main() -> None:
    LOG.unlink(missing_ok=True)
    d = D()

    # === STATE: home village ===
    log("\n=== HOME ===")
    frame = ensure_home(d)
    d.save(frame, "btn_attack", (10, 615, 130, 705))
    d.save(frame, "btn_settings_gear", (1200, 520, 1255, 570))

    # === STATE: settings panel ===
    log("\n=== SETTINGS PANEL ===")
    d.tap(1227, 545, wait=2.0)
    frame = d.grab("settings")
    # btn_close_modal — red X in top-right of settings panel
    x_box = find_red_x(frame)
    if x_box:
        d.save(frame, "btn_close_modal", expand(x_box, 4, frame.shape))
    # btn_more_settings — OCR for "More 5ettings"
    ms = find_text(frame, ["More Settings", "More 5ettings", "ore Sett", "ore 5ett"])
    if ms:
        d.save(frame, "btn_more_settings", expand(ms, 10, frame.shape))
        cx, cy = (ms[0] + ms[2]) // 2, (ms[1] + ms[3]) // 2
        d.tap(cx, cy, wait=2.0)

        # === STATE: more settings panel ===
        log("\n=== MORE SETTINGS ===")
        frame = d.grab("more_settings")
        # Scroll within panel until "Copy" appears
        copy_box = find_text(frame, ["Copy", "C0py", "Cop"])
        scrolls = 0
        while copy_box is None and scrolls < 8:
            d.swipe(640, 580, 640, 180, dur=600, wait=1.2)
            scrolls += 1
            frame = d.grab(f"more_settings_scroll{scrolls}")
            copy_box = find_text(frame, ["Copy", "C0py", "Cop"])
        log(f"  scrolled {scrolls}x")
        if copy_box:
            d.save(frame, "btn_copy_data", expand(copy_box, 8, frame.shape))
        # Close panels with single back (More Settings → Settings → Home is 2 backs)
        d.back(wait=1.0)
        d.back(wait=1.0)
    else:
        d.back(wait=1.0)  # close settings

    # === STATE: building info (tap a defense to get upgrade button) ===
    log("\n=== BUILDING INFO ===")
    frame = ensure_home(d)
    # Tap a defense building. Cannons are typical — let's try tapping bottom-row
    # of base where defenses cluster. Will need to confirm visually.
    d.tap(950, 380, wait=1.5)
    frame = d.grab("building_info")
    # Look for "Upgrade" text via OCR
    up = find_text(frame, ["Upgrade", "Upqrade", "pgrade"])
    if up:
        d.save(frame, "btn_upgrade", expand(up, 12, frame.shape))
        cx, cy = (up[0] + up[2]) // 2, (up[1] + up[3]) // 2
        # Tap upgrade — popup may appear OR insufficient resources may appear
        d.tap(cx, cy, wait=2.0)
        frame = d.grab("upgrade_dialog")
        # Look for "Insufficient" or "Confirm"
        ir = find_text(frame, ["Insufficient", "nsufficient", "Not Enough", "ot Enough"])
        if ir:
            d.save(frame, "btn_insufficient_resources", expand(ir, 10, frame.shape))
        cu = find_text(frame, ["Upgrade", "Confirm"])
        if cu:
            d.save(frame, "btn_confirm_upgrade", expand(cu, 12, frame.shape))
        # Cancel — tap elsewhere or back
        d.back(wait=1.0)
    d.back(wait=1.0)

    # === STATE: attack chooser ===
    log("\n=== ATTACK CHOOSER ===")
    frame = ensure_home(d)
    d.tap(70, 660, wait=1.8)  # ATTACK button
    frame = d.grab("attack_chooser")
    fm = find_text(frame, ["Find a Match", "Find Match", "ind a Match", "Find"])
    if fm:
        d.save(frame, "btn_find_match", expand(fm, 12, frame.shape))
        cx, cy = (fm[0] + fm[2]) // 2, (fm[1] + fm[3]) // 2
        d.tap(cx, cy, wait=8.0)  # matchmaking takes a few seconds

        # === STATE: opponent base ===
        log("\n=== OPPONENT BASE ===")
        frame = d.grab("opponent_base")
        # btn_next — typically right edge mid-screen
        nx = find_text(frame, ["Next", "Skip"])
        if nx:
            d.save(frame, "btn_next", expand(nx, 8, frame.shape))
        else:
            # Fallback fixed location
            d.save(frame, "btn_next", (1130, 540, 1255, 620))

    # === END (don't actually attack — just back out) ===
    log("\n=== Phase 1 main captures done ===")


if __name__ == "__main__":
    main()
