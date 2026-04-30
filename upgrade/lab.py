"""Laboratory research automation.

Skeleton — needs templates + a layout entry for the Laboratory before it
can run live.

Flow:
1. Tap Laboratory on home village (`base/layout.json["Laboratory"]`).
2. Lab UI shows research grid. Read first available unfinished research
   that matches `memory/lab_priority.md` order.
3. Tap the troop/spell tile, then the Research button.
4. Confirm if a "Use X gold/elixir/dark elixir" dialog appears.
5. Back home.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from input.adb import ADB
from screen import templates as tmpl
from screen.capture import grab_frame_bgr

log = logging.getLogger(__name__)

LAYOUT_PATH = Path(__file__).resolve().parent.parent / "base" / "layout.json"
PRIORITY_PATH = Path(__file__).resolve().parent.parent / "memory" / "lab_priority.md"


def _layout() -> dict[str, tuple[int, int]]:
    if not LAYOUT_PATH.exists():
        return {}
    raw = json.loads(LAYOUT_PATH.read_text())
    return {k: (v[0], v[1]) for k, v in raw.items()}


def _priorities() -> list[str]:
    if not PRIORITY_PATH.exists():
        return []
    return [
        line.strip(" -*\t").lstrip("0123456789. ")
        for line in PRIORITY_PATH.read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]


def start_next_research(adb: ADB, template_set: dict[str, tmpl.Template]) -> bool:
    """Open lab and start the next priority research. Returns True on kick-off."""
    coords = _layout().get("Laboratory")
    if coords is None:
        log.warning("Laboratory coords missing from base/layout.json — skipping lab")
        return False

    adb.tap(coords[0], coords[1])
    adb.wait_random(1.5, 2.5)

    btn_research = template_set.get("btn_lab_research")
    if btn_research is None:
        log.warning("btn_lab_research template missing — capture it from the lab UI")
        adb.back()
        return False

    # The research grid scrolls; we tap the first highlighted (researchable)
    # item in priority order. Without per-troop templates, we rely on the
    # "Research" button being enabled after a tap on a troop tile. The
    # priority list informs which order to try.
    priorities = _priorities()
    if not priorities:
        log.info("No lab priorities set — skipping")
        adb.back()
        return False

    # TODO: per-troop tile detection. For now, just tap the layout-stored
    # position of the first priority and check if the research button activates.
    troop = priorities[0]
    tile_coords = _layout().get(f"lab_tile_{troop}")
    if tile_coords is None:
        log.warning(f"Lab tile coords missing for {troop}")
        adb.back()
        return False
    adb.tap(tile_coords[0], tile_coords[1])
    adb.wait_random(0.5, 1.0)

    frame = grab_frame_bgr(adb)
    pos = tmpl.find(frame, btn_research)
    if pos is None:
        log.info(f"Research button not active for {troop} — already maxed or insufficient")
        adb.back(); adb.back()
        return False

    adb.tap(pos[0], pos[1])
    adb.wait_random(1.0, 2.0)

    # Confirm dialog if present.
    btn_confirm = template_set.get("btn_confirm_upgrade")
    if btn_confirm:
        frame = grab_frame_bgr(adb)
        pos_confirm = tmpl.find(frame, btn_confirm)
        if pos_confirm:
            adb.tap(pos_confirm[0], pos_confirm[1])
            adb.wait_random(1.0, 2.0)

    log.info(f"Lab research started: {troop}")
    adb.back()
    return True
