"""End-to-end test: synthetic base state → Gemini planner → decisions.

Run: PYTHONPATH=. uv run python tools/test_planner.py

The clipboard path (Settings → Copy Data → Clipper read) requires the
ca.zgrs.clipper variant which isn't installed in our BlueStacks. This test
validates the Gemini side of the pipeline against a hand-crafted base state
representative of the live TH15 account.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from planner.gemini import plan  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("test_planner")


SYNTHETIC_STATE = {
    "tag": "#TEST",
    "th_level": 15,
    "free_builders": 1,
    "resources": {"gold": 7_616_732, "elixir": 6_692_204, "dark_elixir": 107_804},
    "buildings": [
        {"name": "Cannon", "level": 19, "count": 7, "upgrading": False, "max_level": 21},
        {"name": "Archer_Tower", "level": 20, "count": 8, "upgrading": False, "max_level": 21},
        {"name": "Mortar", "level": 14, "count": 4, "upgrading": False, "max_level": 15},
        {"name": "Wizard_Tower", "level": 14, "count": 5, "upgrading": False, "max_level": 15},
        {"name": "Air_Defense", "level": 12, "count": 4, "upgrading": False, "max_level": 13},
        {"name": "X-Bow", "level": 9, "count": 4, "upgrading": False, "max_level": 10},
        {"name": "Inferno_Tower", "level": 8, "count": 3, "upgrading": False, "max_level": 9},
        {"name": "Eagle_Artillery", "level": 5, "count": 1, "upgrading": False, "max_level": 6},
        {"name": "Scattershot", "level": 3, "count": 2, "upgrading": False, "max_level": 4},
        {"name": "Lab", "level": 12, "count": 1, "upgrading": False, "max_level": 13},
        {"name": "Spell_Factory", "level": 7, "count": 1, "upgrading": False, "max_level": 7},
        {"name": "Storage_Gold", "level": 16, "count": 4, "upgrading": False, "max_level": 17},
        {"name": "Storage_Elixir", "level": 16, "count": 4, "upgrading": False, "max_level": 17},
        {"name": "Builders_Hut", "level": 1, "count": 6, "upgrading": False, "max_level": 1},
    ],
    "heroes": [
        {"name": "Barbarian_King", "level": 80, "upgrading": False},
        {"name": "Archer_Queen", "level": 80, "upgrading": False},
        {"name": "Grand_Warden", "level": 55, "upgrading": False},
        {"name": "Royal_Champion", "level": 30, "upgrading": False},
    ],
    "traps": [
        {"name": "Bomb", "level": 9, "count": 6, "upgrading": False},
        {"name": "Spring_Trap", "level": 5, "count": 6, "upgrading": False},
    ],
}


def main() -> int:
    resources = SYNTHETIC_STATE["resources"]
    log.info("Calling Gemini planner with synthetic TH15 state...")
    result = plan(SYNTHETIC_STATE, resources)

    print("\n=== Planner Decisions ===")
    if not result.decisions:
        print("  (no decisions returned)")
    for d in result.decisions:
        print(f"  {d.action}: {d.target} → lvl {d.target_level}")
        print(f"    {d.reasoning}")

    if result.notes:
        print(f"\nNotes: {result.notes}")

    print("\n=== Updated Priorities (top 10) ===")
    for i, p in enumerate(result.updated_priorities[:10], 1):
        print(f"  {i}. {p}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
