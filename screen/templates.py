from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


class Template:
    __slots__ = ("name", "image", "gray", "mask", "w", "h")

    def __init__(self, name: str, image: np.ndarray, mask: np.ndarray | None) -> None:
        self.name = name
        self.image = image
        self.gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        self.mask = mask
        self.h, self.w = image.shape[:2]


def load_template(path: Path) -> Template:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Template not found: {path}")
    mask = None
    if img.shape[2] == 4:
        _, mask = cv2.threshold(img[:, :, 3], 128, 255, cv2.THRESH_BINARY)
        img = img[:, :, :3]
    return Template(path.stem, img, mask)


def load_all(directory: Path | None = None) -> dict[str, Template]:
    d = directory or TEMPLATES_DIR
    templates: dict[str, Template] = {}
    for p in sorted(d.glob("*.png")):
        templates[p.stem] = load_template(p)
    return templates


ROI = tuple[int, int, int, int]  # x1, y1, x2, y2


def find(
    frame: np.ndarray,
    template: Template,
    threshold: float = 0.85,
    roi: ROI | None = None,
) -> tuple[int, int] | None:
    search = frame
    ox, oy = 0, 0
    if roi:
        x1, y1, x2, y2 = roi
        search = frame[y1:y2, x1:x2]
        ox, oy = x1, y1

    if len(search.shape) == 3:
        search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

    result = cv2.matchTemplate(search, template.gray, cv2.TM_CCOEFF_NORMED, mask=template.mask)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val < threshold:
        return None
    return max_loc[0] + template.w // 2 + ox, max_loc[1] + template.h // 2 + oy


def find_all(
    frame: np.ndarray,
    template: Template,
    threshold: float = 0.85,
    roi: ROI | None = None,
    min_distance: int = 20,
) -> list[tuple[int, int]]:
    search = frame
    ox, oy = 0, 0
    if roi:
        x1, y1, x2, y2 = roi
        search = frame[y1:y2, x1:x2]
        ox, oy = x1, y1

    if len(search.shape) == 3:
        search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY)

    result = cv2.matchTemplate(search, template.gray, cv2.TM_CCOEFF_NORMED, mask=template.mask)
    locations = np.where(result >= threshold)

    hits: list[tuple[int, int]] = []
    for pt in zip(locations[1], locations[0]):
        cx = pt[0] + template.w // 2 + ox
        cy = pt[1] + template.h // 2 + oy
        if all(abs(cx - hx) > min_distance or abs(cy - hy) > min_distance for hx, hy in hits):
            hits.append((cx, cy))
    return hits


def find_any(
    frame: np.ndarray,
    templates: list[Template],
    threshold: float = 0.85,
    roi: ROI | None = None,
) -> tuple[str, int, int] | None:
    for t in templates:
        pos = find(frame, t, threshold, roi)
        if pos:
            return t.name, pos[0], pos[1]
    return None


def exists(
    frame: np.ndarray,
    template: Template,
    threshold: float = 0.85,
    roi: ROI | None = None,
) -> bool:
    return find(frame, template, threshold, roi) is not None
