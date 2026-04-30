# clash-farmer

Home-base farming bot for Clash of Clans on BlueStacks (macOS). Drives the standard farm loop via ADB and template matching; optional Gemini hook for upgrade decisions when storages fill.

```bash
uv sync
uv run python main.py
```

## Setup

- BlueStacks Air, instance Tiramisu64 (Android 13), 6 GB RAM
- ADB enabled in BlueStacks Settings → Advanced
- 1280×720 @ 240 DPI
- Logged in to Clash of Clans on the home village
- `.env` with `GEMINI_API_KEY` (only needed if you enable the planner)

## Loop

```
home → attack → find a match → army view → ATTACK
              → battle warmup (read loot, skip / commit)
              → deploy goblins + heroes → battle → result
              → return home → dismiss reward chests
```

Zoom-out is driven from the host via `osascript` (BlueStacks' default CoC keymap binds the up-arrow to in-game pinch-out — ADB `input` can't do real multi-touch on the Virtual Touch device).

## Modules

| Path | Notes |
|------|-------|
| `screen/` | screencap, template match, OCR, state classifier |
| `input/` | ADB driver (tap, swipe, multitouch attempts, host-side keystroke) |
| `attack/` | matchmaking flow, search-loop, deploy, battle monitor |
| `planner/` | Gemini structured output, JSON export parser |
| `upgrade/` | building nav + upgrade tap helpers |
| `tools/` | calibrate, capture, test_planner |

## Known limitations

- The bot relies on BlueStacks' default CoC keymap for zoom; pinch can't be driven from ADB alone.
- The org.rojekti.clipper variant doesn't expose a broadcast API. Live JSON-export → planner needs `ca.zgrs.clipper`. Planner is exercised against a synthetic state in `tools/test_planner.py`.
- Top-right resource OCR is noisy. Loot OCR (top-left of warmup screen) is reliable enough to gate the attack/skip decision; net loot per cycle is checked from the resource bar at the start/end of each cycle.
