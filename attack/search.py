from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.ocr import read_loot
from screen.state import find_red_close_x

log = logging.getLogger(__name__)


@dataclass
class LootThresholds:
    min_gold: int = 400_000
    min_elixir: int = 400_000
    min_dark_elixir: int = 3_500
    max_skips: int = 30
    decay: float = 0.8


@dataclass
class LootResult:
    gold: int
    elixir: int
    dark_elixir: int

    @property
    def combined(self) -> int:
        return self.gold + self.elixir

    def meets_threshold(self, t: LootThresholds) -> bool:
        return (
            self.gold + self.elixir >= t.min_gold + t.min_elixir
            or self.dark_elixir >= t.min_dark_elixir
        )


def _find_with_retry(
    adb: ADB,
    template_set: dict[str, tmpl.Template],
    name: str,
    roi: tmpl.ROI | None = None,
    threshold: float = 0.7,
    attempts: int = 4,
    interval: float = 0.4,
) -> tuple[int, int] | None:
    """Re-grab the frame up to `attempts` times before giving up — UI
    transitions can hold a stale/empty frame for ~1 s after a tap.
    """
    t = template_set.get(name)
    if t is None:
        return None
    for _ in range(attempts):
        frame = grab_frame_bgr(adb)
        pos = tmpl.find(frame, t, threshold=threshold, roi=roi)
        if pos is not None:
            return pos
        adb.wait(interval)
    return None


def enter_matchmaking(adb: ADB, template_set: dict[str, tmpl.Template]) -> bool:
    """HOME → Attack → Find a Match → Army view → green ATTACK → battle warmup."""
    frame = grab_frame_bgr(adb)
    close_pos = find_red_close_x(frame)
    if close_pos is not None:
        adb.tap(close_pos[0], close_pos[1])
        adb.wait_random(0.6, 1.2)

    pos = _find_with_retry(adb, template_set, "btn_attack", roi=(0, 580, 250, 720))
    if pos is None:
        log.warning("home Attack button not found")
        return False
    adb.tap(pos[0], pos[1] + 5)
    adb.wait_random(1.5, 2.5)

    pos = _find_with_retry(adb, template_set, "btn_find_match", threshold=0.65)
    if pos is None:
        log.warning("Find a Match button not found")
        return False
    adb.tap(pos[0], pos[1])
    adb.wait_random(2.5, 4.0)

    pos = _find_with_retry(
        adb, template_set, "btn_search_attack",
        roi=(950, 580, 1280, 720), threshold=0.7, attempts=6, interval=0.5,
    )
    if pos is None:
        log.warning("Army-view ATTACK button not found")
        return False
    adb.tap(pos[0], pos[1])
    adb.wait_random(3.5, 5.5)
    return True


def read_current_loot(frame: np.ndarray) -> LootResult:
    loot = read_loot(frame)
    return LootResult(
        gold=loot.get("gold") or 0,
        elixir=loot.get("elixir") or 0,
        dark_elixir=loot.get("dark_elixir") or 0,
    )


def search_loop(
    adb: ADB,
    template_set: dict[str, tmpl.Template],
    thresholds: LootThresholds,
) -> LootResult | None:
    """In the BATTLE warmup, decide attack-or-skip based on opponent loot.

    Returns the loot snapshot when threshold met (caller deploys troops);
    returns None if max_skips reached without a hit (caller should End Battle).
    """
    btn_next = template_set.get("btn_next")
    if btn_next is None:
        log.error("btn_next template not loaded")
        return None

    active = LootThresholds(
        min_gold=thresholds.min_gold,
        min_elixir=thresholds.min_elixir,
        min_dark_elixir=thresholds.min_dark_elixir,
        max_skips=thresholds.max_skips,
        decay=thresholds.decay,
    )
    skips = 0

    while True:
        frame = grab_frame_bgr(adb)
        loot = read_current_loot(frame)
        log.info(f"Loot: {loot.gold:,}G / {loot.elixir:,}E / {loot.dark_elixir:,}DE")

        if loot.meets_threshold(active):
            log.info("Loot meets threshold — attacking")
            return loot

        skips += 1
        if skips >= active.max_skips:
            active.min_gold = int(active.min_gold * active.decay)
            active.min_elixir = int(active.min_elixir * active.decay)
            active.min_dark_elixir = int(active.min_dark_elixir * active.decay)
            log.info(
                f"Decayed thresholds after {skips} skips: "
                f"{active.min_gold:,}G / {active.min_elixir:,}E / {active.min_dark_elixir:,}DE"
            )
            skips = 0

        # Tap "Next" (orange button bottom-right of warmup) to find another opponent.
        pos = tmpl.find(frame, btn_next, threshold=0.65, roi=(1100, 480, 1280, 580))
        if pos:
            adb.tap(pos[0], pos[1])
        else:
            adb.tap(1190, 575)
        adb.wait_random(2.0, 3.0)


def end_battle_warmup(adb: ADB) -> None:
    """Tap End Battle (red, bottom-left) during warmup to surrender for free."""
    adb.tap(85, 540)
    adb.wait_random(1.0, 1.5)
