from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import easyocr
import numpy as np

_reader: easyocr.Reader | None = None
ROI = tuple[int, int, int, int]  # x1, y1, x2, y2

ROIS_PATH = Path(__file__).resolve().parent / "rois.json"

# Defaults — overridden by rois.json if it exists (use calibrate tool to generate).
_DEFAULTS: dict[str, ROI] = {
    "res_gold": (70, 5, 205, 30),
    "res_elixir": (70, 33, 205, 58),
    "res_dark_elixir": (70, 61, 205, 86),
    "loot_gold": (50, 80, 220, 110),
    "loot_elixir": (50, 110, 220, 140),
    "loot_dark_elixir": (50, 140, 220, 170),
    "builders": (250, 5, 320, 30),
    "loot_gained": (550, 60, 730, 85),
}

_rois_cache: dict[str, ROI] | None = None


def _load_rois() -> dict[str, ROI]:
    global _rois_cache
    if _rois_cache is not None:
        return _rois_cache
    rois = dict(_DEFAULTS)
    if ROIS_PATH.exists():
        saved = json.loads(ROIS_PATH.read_text())
        for k, v in saved.items():
            rois[k] = tuple(v)
    _rois_cache = rois
    return rois


def get_roi(name: str) -> ROI:
    rois = _load_rois()
    if name not in rois:
        raise KeyError(f"ROI '{name}' not found. Run calibrate tool or add to rois.json.")
    return rois[name]


def _get_reader() -> easyocr.Reader:
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def _preprocess(crop: np.ndarray, scale: int = 3) -> np.ndarray:
    h, w = crop.shape[:2]
    upscaled = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(gray, -1, kernel)
    _, binary = cv2.threshold(sharpened, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def _parse_resource_string(text: str) -> int | None:
    text = text.strip().upper().replace(",", "").replace(" ", "").replace("O", "0")
    multiplier = 1
    if text.endswith("M"):
        multiplier = 1_000_000
        text = text[:-1]
    elif text.endswith("K"):
        multiplier = 1_000
        text = text[:-1]
    cleaned = re.sub(r"[^0-9.]", "", text)
    if not cleaned:
        return None
    try:
        return int(float(cleaned) * multiplier)
    except ValueError:
        return None


def read_number(frame: np.ndarray, roi: ROI, scale: int = 3) -> int | None:
    """Read a resource number from a frame ROI.

    Resource numbers in CoC are space-separated digit groups (e.g. "7 619 532").
    EasyOCR often returns these as multiple segments. We concatenate all
    segments left-to-right and parse the result, rather than picking only the
    top-confidence segment.
    """
    x1, y1, x2, y2 = roi
    crop = frame[y1:y2, x1:x2]
    binary = _preprocess(crop, scale)
    reader = _get_reader()
    results = reader.readtext(
        binary,
        allowlist="0123456789KkMm,.",
        detail=1,
        paragraph=False,
        text_threshold=0.6,
        low_text=0.3,
    )
    if not results:
        return None
    # Filter low-confidence segments and sort left-to-right for concatenation.
    good = [(bbox, text, conf) for bbox, text, conf in results if conf >= 0.4]
    if not good:
        return None
    good.sort(key=lambda r: min(p[0] for p in r[0]))
    concat = "".join(text for _, text, _ in good)
    return _parse_resource_string(concat)


def read_resources(frame: np.ndarray) -> dict[str, int | None]:
    return {
        "gold": read_number(frame, get_roi("res_gold")),
        "elixir": read_number(frame, get_roi("res_elixir")),
        "dark_elixir": read_number(frame, get_roi("res_dark_elixir")),
    }


def read_loot(frame: np.ndarray) -> dict[str, int | None]:
    return {
        "gold": read_number(frame, get_roi("loot_gold")),
        "elixir": read_number(frame, get_roi("loot_elixir")),
        "dark_elixir": read_number(frame, get_roi("loot_dark_elixir")),
    }


def read_builders(frame: np.ndarray) -> tuple[int, int] | None:
    x1, y1, x2, y2 = get_roi("builders")
    crop = frame[y1:y2, x1:x2]
    binary = _preprocess(crop, scale=3)
    reader = _get_reader()
    results = reader.readtext(
        binary,
        allowlist="0123456789/",
        detail=1,
        paragraph=False,
    )
    if not results:
        return None
    _, text, conf = max(results, key=lambda r: r[2])
    if conf < 0.4:
        return None
    text = text.strip().replace(" ", "")
    if "/" not in text:
        return None
    parts = text.split("/")
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None
