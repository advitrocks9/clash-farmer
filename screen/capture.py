from __future__ import annotations

import numpy as np
from PIL import Image

from input.adb import ADB


def grab_frame(adb: ADB) -> np.ndarray:
    img = adb.screencap()
    return np.array(img.convert("RGB"))


def grab_frame_bgr(adb: ADB) -> np.ndarray:
    img = adb.screencap()
    rgb = np.array(img.convert("RGB"))
    return rgb[:, :, ::-1].copy()


def pil_to_cv(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))[:, :, ::-1].copy()


def cv_to_pil(frame: np.ndarray) -> Image.Image:
    return Image.fromarray(frame[:, :, ::-1])
