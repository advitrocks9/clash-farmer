from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

import cv2
import numpy as np

from screen import templates as tmpl


class GameState(Enum):
    HOME = auto()
    SEARCH = auto()
    BATTLE = auto()
    RESULT = auto()
    MODAL = auto()
    UNKNOWN = auto()


def find_red_close_x(
    frame: np.ndarray,
    region: tuple[int, int, int, int] = (1080, 0, 1280, 120),
) -> tuple[int, int] | None:
    # Most CoC popups (Army, Treasure shop, info cards, event widgets) put a
    # round red close-X in the top-right corner. HSV-mask the saturated reds
    # in that region and return the centroid of the largest blob.
    x1, y1, x2, y2 = region
    crop = frame[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 150, 100]), np.array([10, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([170, 150, 100]), np.array([180, 255, 255]))
    mask = m1 | m2
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < 80:
        return None
    M = cv2.moments(largest)
    if M["m00"] == 0:
        return None
    cx = int(M["m10"] / M["m00"]) + x1
    cy = int(M["m01"] / M["m00"]) + y1
    return cx, cy


# Defining templates per state — found at tight ROI. Each state requires its
# own template AND requires the OTHER states' templates to be absent.
HOME_ROI: tmpl.ROI = (0, 600, 220, 720)
# Surrender / End Battle button is at bottom-left of the play field, around y=510-560.
BATTLE_ROI: tmpl.ROI = (0, 500, 200, 580)
SEARCH_ROI: tmpl.ROI = (1100, 460, 1280, 620)
RESULT_ROI: tmpl.ROI = (480, 540, 800, 700)


@dataclass
class StateSignals:
    home_attack: bool
    battle_surrender: bool
    battle_end: bool
    search_next: bool
    result_return: bool
    modal_close_x: bool


def _has(frame: np.ndarray, t: tmpl.Template | None, roi: tmpl.ROI, threshold: float) -> bool:
    if t is None:
        return False
    return tmpl.exists(frame, t, threshold=threshold, roi=roi)


def detect_signals(
    frame: np.ndarray,
    template_set: dict[str, tmpl.Template],
    threshold: float = 0.70,
) -> StateSignals:
    return StateSignals(
        home_attack=_has(frame, template_set.get("btn_attack"), HOME_ROI, threshold),
        battle_surrender=_has(frame, template_set.get("btn_surrender"), BATTLE_ROI, threshold),
        battle_end=_has(frame, template_set.get("btn_end_battle"), BATTLE_ROI, threshold),
        search_next=_has(frame, template_set.get("btn_next"), SEARCH_ROI, threshold),
        result_return=_has(frame, template_set.get("btn_return_home"), RESULT_ROI, threshold),
        modal_close_x=find_red_close_x(frame) is not None,
    )


def classify(signals: StateSignals) -> GameState:
    in_battle = signals.battle_surrender or signals.battle_end

    if signals.result_return and not in_battle:
        return GameState.RESULT
    if in_battle and not signals.home_attack and not signals.result_return:
        return GameState.BATTLE
    if signals.search_next and not signals.home_attack and not in_battle:
        return GameState.SEARCH
    if signals.home_attack and not in_battle and not signals.result_return:
        return GameState.HOME
    if signals.modal_close_x:
        return GameState.MODAL
    return GameState.UNKNOWN


class StateDetector:
    def __init__(
        self,
        template_set: dict[str, tmpl.Template],
        window: int = 3,
        required_stable: int = 2,
        threshold: float = 0.70,
    ) -> None:
        self._templates = template_set
        self._history: deque[GameState] = deque(maxlen=window)
        self._required = required_stable
        self._threshold = threshold
        self._committed = GameState.UNKNOWN

    def detect(self, frame: np.ndarray) -> GameState:
        signals = detect_signals(frame, self._templates, self._threshold)
        raw = classify(signals)
        self._history.append(raw)
        recent = list(self._history)[-self._required :]
        if (
            len(recent) == self._required
            and len(set(recent)) == 1
            and recent[0] != GameState.UNKNOWN
        ):
            self._committed = recent[0]
        return self._committed

    @property
    def current(self) -> GameState:
        return self._committed

    def reset(self) -> None:
        self._history.clear()
        self._committed = GameState.UNKNOWN

    def wait_for(
        self,
        target: GameState,
        grab_frame: Callable[[], np.ndarray],
        timeout: float = 10.0,
        poll_interval: float = 0.5,
    ) -> bool:
        # Block until detector commits to `target` or timeout. Returns True on success.
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.detect(grab_frame())
            if self._committed == target:
                return True
            time.sleep(poll_interval)
        return False
