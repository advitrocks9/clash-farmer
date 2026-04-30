"""Append-only event log for the farm.

Three streams in `local/`:
- `resources.jsonl` — every successful HOME-state resource read (one line
  per sample). Lets you plot gold/elixir/dark trajectories over hours.
- `events.jsonl`   — discrete events (cycle_start, cycle_end, wall_upgrade,
  popup_seen, recovery_run, planner_export, errors). One line per event.
- `state_trace.jsonl` — every committed state transition + timestamp.
  Off by default; flip ENABLE_STATE_TRACE on to debug.

`tools/stats.py` reads these to print rate-per-hour, success ratios, and
fill-ETAs. Telegram /stats and /trend pull from here too.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

LOCAL_DIR = Path(__file__).resolve().parent / "local"
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

RESOURCES_PATH = LOCAL_DIR / "resources.jsonl"
EVENTS_PATH = LOCAL_DIR / "events.jsonl"
STATE_TRACE_PATH = LOCAL_DIR / "state_trace.jsonl"

ENABLE_STATE_TRACE = bool(int(os.environ.get("CLASH_TRACE_STATE", "0")))

_lock = threading.Lock()


def _append(path: Path, payload: dict[str, Any]) -> None:
    payload["ts"] = time.time()
    line = json.dumps(payload, default=str)
    with _lock:
        with path.open("a") as f:
            f.write(line + "\n")


def log_resources(resources: dict[str, int | None], extra: dict | None = None) -> None:
    payload: dict[str, Any] = {
        "gold": resources.get("gold"),
        "elixir": resources.get("elixir"),
        "dark_elixir": resources.get("dark_elixir"),
    }
    if extra:
        payload.update(extra)
    _append(RESOURCES_PATH, payload)


def log_event(kind: str, **fields: Any) -> None:
    payload = {"kind": kind, **fields}
    _append(EVENTS_PATH, payload)


def log_state_transition(prev: str, current: str) -> None:
    if not ENABLE_STATE_TRACE:
        return
    _append(STATE_TRACE_PATH, {"prev": prev, "current": current})
