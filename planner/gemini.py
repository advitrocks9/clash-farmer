from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from google import genai
from google.genai import types

from planner.schemas import PlannerOutput

MEMORY_DIR = Path(__file__).resolve().parent.parent / "memory"

SYSTEM_PROMPT = """\
You are a Clash of Clans upgrade planner for a TH15 account.

Given the current base state (buildings, heroes, traps with levels and upgrade status),
available resources, and the number of free builders, decide what to upgrade next.

Rules:
- Only suggest upgrades the player can afford right now.
- Only suggest as many upgrades as there are free builders.
- If no builder is free or nothing is affordable, return action="wait".
- Prioritize based on the priority list provided, but use judgment — don't blindly follow
  it if a critical upgrade is available (e.g., a hero at a very low level).
- Heroes and lab upgrades are generally higher priority than defenses.
- Never suggest upgrading walls (handled separately).
- The updated_priorities list should reflect your current thinking on what to upgrade next,
  ordered by importance. Keep it to 10-15 items max.
- Keep reasoning concise — one sentence per decision.
"""


def _read_memory_file(name: str) -> str:
    path = MEMORY_DIR / name
    if path.exists():
        return path.read_text().strip()
    return ""


def _write_memory_file(name: str, content: str) -> None:
    MEMORY_DIR.mkdir(exist_ok=True)
    (MEMORY_DIR / name).write_text(content)


def _append_thought(decision_summary: str) -> None:
    path = MEMORY_DIR / "thoughts.md"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n## {ts}\n{decision_summary}\n"
    if path.exists():
        with open(path, "a") as f:
            f.write(entry)
    else:
        path.write_text(f"# Planner Thought Log\n{entry}")


def plan(base_state_json: dict, resources: dict[str, int]) -> PlannerOutput:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    priorities = _read_memory_file("priority.md")
    goals = _read_memory_file("goals.md")

    prompt = f"""\
## Current Base State
```json
{json.dumps(base_state_json, indent=2)}
```

## Available Resources
Gold: {resources.get('gold', 0):,}
Elixir: {resources.get('elixir', 0):,}
Dark Elixir: {resources.get('dark_elixir', 0):,}

## Current Priority List
{priorities if priorities else "No priorities set yet — create an initial priority list."}

## Goals
{goals if goals else "No specific goals set. Default: max heroes, then offense, then defense."}

Decide what to upgrade. Return one decision per free builder.\
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=PlannerOutput,
        ),
    )

    result = PlannerOutput.model_validate_json(response.text)

    if result.updated_priorities:
        _write_memory_file(
            "priority.md",
            "# Upgrade Priorities\n\n"
            + "\n".join(f"{i+1}. {p}" for i, p in enumerate(result.updated_priorities)),
        )

    summary_lines = []
    for d in result.decisions:
        summary_lines.append(f"- {d.action}: {d.target} → lvl {d.target_level} ({d.reasoning})")
    if result.notes:
        summary_lines.append(f"\nNotes: {result.notes}")
    _append_thought("\n".join(summary_lines))

    return result
