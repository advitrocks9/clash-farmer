"""Phase 1 capture v2 — state-aware, idempotent, self-healing.

Workflow:
1. Detect current state (HOME / SETTINGS / etc.) via lightweight CV heuristics.
2. If not at home, recover via back+force-restart.
3. Execute capture sequence: home → settings → more_settings → copy → back ×2
   → tap building → upgrade → cancel → attack → find_match → opponent → next.
4. Each capture verified with self-match; failures logged for vision-fallback.
"""

from __future__ import annotations

import json
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
LOG_DIR = REPO / ".autonomy" / "LOG"
CAP_DIR = LOG_DIR / "captures"
CAPTURES_TXT = LOG_DIR / "capture_v2.txt"

_OCR = None


def log(msg: str) -> None:
    print(msg, flush=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CAPTURES_TXT, "a") as f:
        f.write(msg + "\n")


def ocr():
    global _OCR
    if _OCR is None:
        import easyocr
        log("[ocr] loading...")
        _OCR = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _OCR


# === STATE DETECTORS ===


def is_home(frame: np.ndarray) -> bool:
    """Strict home state: btn_attack visible AND no popup card in center.

    Checks both the saved btn_attack template AND that the center vertical band
    has the green grass / base look (high green ratio) — otherwise a popup card
    is in the middle.
    """
    tmpl_path = TEMPLATES / "btn_attack.png"
    if not tmpl_path.exists():
        return False
    tmpl = cv2.imread(str(tmpl_path), cv2.IMREAD_COLOR)
    if tmpl is None:
        return False
    h, w = frame.shape[:2]
    roi = frame[max(0, h - 200) : h, 0 : 250]
    if roi.shape[0] < tmpl.shape[0] or roi.shape[1] < tmpl.shape[1]:
        return False
    g_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    g_tmpl = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(g_roi, g_tmpl, cv2.TM_CCOEFF_NORMED)
    _, mv, _, _ = cv2.minMaxLoc(res)
    if mv <= 0.75:
        return False
    # Reject if a popup is present: the bottom-center has the white info card
    # background. Sample a strip at y=560-595, x=200-1080. If mostly white/light,
    # a card is open.
    strip = frame[560:595, 200:1080]
    light_mask = cv2.inRange(strip, np.array([180, 180, 180]), np.array([255, 255, 255]))
    light_ratio = light_mask.mean() / 255
    return light_ratio < 0.25


def is_loading(frame: np.ndarray) -> bool:
    """Mostly-black screen with Supercell logo or similar."""
    return frame.mean() < 30


def is_settings_panel(frame: np.ndarray) -> bool:
    """Detect 'Settings' title + sliders."""
    res = ocr().readtext(frame[:200, 400:900])
    return any("settings" in t.lower() for _, t, _ in res if "more" not in t.lower())


def is_more_settings_panel(frame: np.ndarray) -> bool:
    res = ocr().readtext(frame[:200, 400:900])
    return any("more" in t.lower() and ("settings" in t.lower() or "ettings" in t.lower())
               for _, t, _ in res)


def is_attack_chooser(frame: np.ndarray) -> bool:
    """Large green 'Find a Match!' button visible."""
    res = ocr().readtext(frame)
    return any("find" in t.lower() and "match" in t.lower() for _, t, _ in res)


def is_opponent_base(frame: np.ndarray) -> bool:
    """Has 'Next' button right side + opponent loot at top-left."""
    res = ocr().readtext(frame[400:700, 1080:])
    return any(t.lower() in ("next", "skip") for _, t, _ in res)


def detect_state(frame: np.ndarray) -> str:
    if is_home(frame):
        return "HOME"
    if is_loading(frame):
        return "LOADING"
    if is_more_settings_panel(frame):
        return "MORE_SETTINGS"
    if is_settings_panel(frame):
        return "SETTINGS"
    if is_attack_chooser(frame):
        return "ATTACK_CHOOSER"
    if is_opponent_base(frame):
        return "OPPONENT_BASE"
    return "UNKNOWN"


# === HELPERS ===


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
                log(f"  [ocr] '{text}' (~{target}) {bx1},{by1}-{bx2},{by2} c={conf:.2f}")
                return (bx1, by1, bx2, by2)
    return None


def find_red_x(frame: np.ndarray) -> Optional[tuple[int, int, int, int]]:
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
    return best[1] if best else None


def expand(box, pad, shape):
    h, w = shape[:2]
    x1, y1, x2, y2 = box
    return (max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad))


# === DRIVER ===


class Driver:
    def __init__(self):
        self.adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
        self.adb.connect()
        log(f"[adb] {self.adb._addr}")
        TEMPLATES.mkdir(exist_ok=True)
        CAP_DIR.mkdir(parents=True, exist_ok=True)
        self.step = 0

    def grab(self, label: str) -> np.ndarray:
        img = self.adb.screencap()
        rgb = np.array(img.convert("RGB"))
        bgr = rgb[:, :, ::-1].copy()
        self.step += 1
        path = CAP_DIR / f"{self.step:02d}_{label}.png"
        cv2.imwrite(str(path), bgr)
        return bgr

    def tap(self, x, y, wait=1.5):
        log(f"[tap] {x},{y}")
        self.adb._run(f"input tap {x} {y}")
        time.sleep(wait)

    def back(self, wait=1.0):
        log("[back]")
        self.adb._run("input keyevent 4")
        time.sleep(wait)

    def swipe(self, x1, y1, x2, y2, dur=500, wait=1.0):
        log(f"[swipe] {x1},{y1}→{x2},{y2}")
        self.adb._run(f"input swipe {x1} {y1} {x2} {y2} {dur}")
        time.sleep(wait)

    def restart_coc(self, wait=15.0):
        log("[restart_coc]")
        self.adb._run("am force-stop com.supercell.clashofclans")
        time.sleep(1)
        self.adb._run("monkey -p com.supercell.clashofclans -c android.intent.category.LAUNCHER 1")
        time.sleep(wait)

    def go_home(self, max_attempts=8) -> np.ndarray:
        for i in range(max_attempts):
            f = self.grab(f"home_check_{i}")
            state = detect_state(f)
            log(f"  state[{i}]: {state}")
            if state == "HOME":
                return f
            if state in ("LOADING",):
                time.sleep(3)
                continue
            self.back(wait=1.2)
        log("  go_home failed → force restart")
        self.restart_coc(wait=20)
        f = self.grab("home_after_restart")
        if not is_home(f):
            raise RuntimeError("Cannot reach HOME state")
        return f

    def save_template(self, frame: np.ndarray, name: str, box: tuple[int, int, int, int]):
        x1, y1, x2, y2 = box
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        crop = frame[y1:y2, x1:x2]
        out = TEMPLATES / f"{name}.png"
        cv2.imwrite(str(out), crop)
        gframe = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gtmpl = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        res = cv2.matchTemplate(gframe, gtmpl, cv2.TM_CCOEFF_NORMED)
        _, mv, _, _ = cv2.minMaxLoc(res)
        log(f"  [save] {name}.png ({x2-x1}x{y2-y1}) self-match={mv:.3f}")
        return mv


# === CAPTURE SEQUENCE ===


def cap_home(d: Driver):
    log("\n=== HOME ===")
    f = d.go_home()
    d.save_template(f, "btn_attack", (10, 615, 130, 705))
    d.save_template(f, "btn_settings_gear", (1200, 520, 1255, 570))


def cap_settings(d: Driver):
    log("\n=== SETTINGS ===")
    f = d.go_home()
    d.tap(1227, 545, wait=2.0)
    f = d.grab("settings")
    if not is_settings_panel(f):
        log("  WARN: settings panel not detected; skipping")
        return
    x_box = find_red_x(f)
    if x_box:
        d.save_template(f, "btn_close_modal", expand(x_box, 4, f.shape))
    ms = find_text(f, ["More Settings", "More 5ettings", "ore Sett", "ore 5ett"])
    if ms:
        d.save_template(f, "btn_more_settings", expand(ms, 10, f.shape))
        cx, cy = (ms[0] + ms[2]) // 2, (ms[1] + ms[3]) // 2
        d.tap(cx, cy, wait=2.5)
        f = d.grab("more_settings")
        if not is_more_settings_panel(f):
            log("  WARN: more_settings not detected after tap")
        # Scroll for Copy
        copy_box = find_text(f, ["Copy", "C0py", "Cop"])
        scrolls = 0
        while copy_box is None and scrolls < 8:
            d.swipe(640, 580, 640, 180, dur=600, wait=1.2)
            scrolls += 1
            f = d.grab(f"more_settings_scroll{scrolls}")
            copy_box = find_text(f, ["Copy", "C0py", "Cop"])
        log(f"  scrolled {scrolls}x")
        if copy_box:
            d.save_template(f, "btn_copy_data", expand(copy_box, 8, f.shape))
        d.back(wait=1.0)  # close more settings
    d.back(wait=1.0)  # close settings


def cap_attack_flow(d: Driver):
    log("\n=== ATTACK CHOOSER ===")
    f = d.go_home()
    d.tap(70, 660, wait=2.0)
    f = d.grab("attack_chooser")
    if not is_attack_chooser(f):
        log("  WARN: attack chooser not detected")
        return
    fm = find_text(f, ["Find a Match", "Find Match", "ind a Match", "ind Match"])
    if fm:
        d.save_template(f, "btn_find_match", expand(fm, 12, f.shape))
        cx, cy = (fm[0] + fm[2]) // 2, (fm[1] + fm[3]) // 2
        d.tap(cx, cy, wait=10.0)  # matchmaking
        f = d.grab("opponent_base")
        if is_opponent_base(f):
            nx = find_text(f, ["Next"])
            if nx:
                d.save_template(f, "btn_next", expand(nx, 8, f.shape))
            else:
                d.save_template(f, "btn_next", (1130, 540, 1255, 620))
            # CRITICAL: do NOT actually attack — surrender out
            # Find return-home / end-battle / back-out path
            d.back(wait=1.5)
            f = d.grab("after_back_from_opponent")
            # If still in opponent_base, try tapping a home/exit icon
            if is_opponent_base(f):
                # The "End Battle" button typically appears bottom-left
                d.tap(40, 670, wait=2.0)
                f = d.grab("after_end_battle_tap")
        else:
            log("  WARN: opponent_base not detected after find_match")


def cap_building_upgrade(d: Driver):
    log("\n=== BUILDING UPGRADE ===")
    f = d.go_home()
    # Tap a defense building. Try several positions if first doesn't open the bar.
    candidates = [(950, 380), (450, 280), (760, 480), (300, 420)]
    for x, y in candidates:
        d.tap(x, y, wait=1.5)
        f = d.grab(f"building_tap_{x}_{y}")
        # Check for "Upgrade" text
        up = find_text(f, ["Upgrade", "Upqrade", "pgrade"])
        if up:
            d.save_template(f, "btn_upgrade", expand(up, 12, f.shape))
            cx, cy = (up[0] + up[2]) // 2, (up[1] + up[3]) // 2
            d.tap(cx, cy, wait=2.0)
            f = d.grab("upgrade_dialog")
            ir = find_text(f, ["Insufficient", "nsufficient", "Not Enough", "ot Enough"])
            if ir:
                d.save_template(f, "btn_insufficient_resources", expand(ir, 10, f.shape))
            cu = find_text(f, ["Confirm", "Upgrade", "Yes"])
            if cu:
                d.save_template(f, "btn_confirm_upgrade", expand(cu, 12, f.shape))
            d.back(wait=1.0)
            d.back(wait=1.0)  # close info bar too
            return
        d.back(wait=0.5)
    log("  WARN: no upgrade button found across all candidates")


# === MAIN ===


def main() -> None:
    CAPTURES_TXT.unlink(missing_ok=True)
    d = Driver()
    cap_home(d)
    cap_settings(d)
    cap_building_upgrade(d)
    cap_attack_flow(d)
    log("\n=== DONE ===")
    log(f"\n{len(list(TEMPLATES.glob('*.png')))} templates in templates/")


if __name__ == "__main__":
    main()
