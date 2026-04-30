from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
RAID_LOG = Path(__file__).resolve().parent / "local" / "raid_log.jsonl"


def append_raid_log(entry: dict) -> None:
    RAID_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(RAID_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")

from attack.deploy import (
    deploy_heroes,
    deploy_sneaky_goblins,
    monitor_battle,
    return_home,
    wait_for_result_screen,
)
from attack.search import LootThresholds, end_battle_warmup, enter_matchmaking, search_loop
from input.adb import ADB, ADBConfig
from planner.export import parse_export, state_to_planner_json
from planner.gemini import plan
from screen import templates as tmpl
from screen.capture import grab_frame_bgr
from screen.ocr import read_resources
from screen.state import GameState, StateDetector, find_red_close_x
from upgrade.execute import execute_hero_upgrade, execute_upgrade

log = logging.getLogger("clash-farmer")

CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
LOCAL_CONFIG_PATH = Path(__file__).resolve().parent / "config.local.yaml"


def load_config() -> dict:
    path = LOCAL_CONFIG_PATH if LOCAL_CONFIG_PATH.exists() else CONFIG_PATH
    return yaml.safe_load(path.read_text())


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


def close_modal(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    frame = grab_frame_bgr(adb)
    btn = template_set.get("btn_close_modal")
    if btn:
        pos = tmpl.find(frame, btn)
        if pos:
            adb.tap(pos[0], pos[1])
            adb.wait_random(0.5, 1.0)


def collect_resources(adb: ADB, template_set: dict[str, tmpl.Template]) -> None:
    collectors = ["collector_gold", "collector_elixir", "collector_dark_elixir"]
    for name in collectors:
        t = template_set.get(name)
        if t is None:
            continue
        frame = grab_frame_bgr(adb)
        hits = tmpl.find_all(frame, t, threshold=0.80)
        for x, y in hits:
            adb.tap(x, y)
            adb.wait_random(0.1, 0.3)


def check_resources_near_max(resources: dict[str, int | None], config: dict) -> bool:
    trigger = config["resources"]["planner_trigger_pct"]
    storage = config["resources"]["storage_max"]
    for key in ("gold", "elixir"):
        val = resources.get(key)
        if val is None:
            continue
        # Reject OCR garbage — values larger than 4× storage cap are misreads.
        if val > storage[key] * 4:
            continue
        if val >= storage[key] * trigger:
            return True
    return False


def run_planner(adb: ADB, template_set: dict[str, tmpl.Template], config: dict) -> None:
    log.info("Resources near max — running planner")

    # Navigate to settings and export base state
    frame = grab_frame_bgr(adb)
    btn_settings = template_set.get("btn_settings_gear")
    if not btn_settings:
        log.error("No settings gear template")
        return

    pos = tmpl.find(frame, btn_settings)
    if not pos:
        log.warning("Settings button not found")
        return

    adb.tap(pos[0], pos[1])
    adb.wait_random(1.0, 2.0)

    # Tap "More Settings"
    frame = grab_frame_bgr(adb)
    btn_more = template_set.get("btn_more_settings")
    if btn_more:
        pos = tmpl.find(frame, btn_more)
        if pos:
            adb.tap(pos[0], pos[1])
            adb.wait_random(1.0, 2.0)

    # Scroll down and tap "Copy" in Data Export section
    adb.swipe(640, 500, 640, 200, duration_ms=500)
    adb.wait_random(0.5, 1.0)

    frame = grab_frame_bgr(adb)
    btn_copy = template_set.get("btn_copy_data")
    if btn_copy:
        pos = tmpl.find(frame, btn_copy)
        if pos:
            adb.tap(pos[0], pos[1])
            adb.wait_random(1.0, 1.5)

    # Read clipboard via Clipper APK
    try:
        raw_json = adb.read_clipboard()
    except RuntimeError:
        log.error("Failed to read clipboard — skipping planner")
        adb.back()
        adb.wait_random(0.5, 1.0)
        adb.back()
        return

    # Close settings
    adb.back()
    adb.wait_random(0.5, 1.0)
    adb.back()
    adb.wait_random(0.5, 1.0)

    try:
        base_state = parse_export(raw_json)
    except Exception as e:
        log.error(f"Failed to parse base state: {e}")
        return

    if base_state.free_builders == 0:
        log.info("No free builders — skipping upgrades")
        return

    frame = grab_frame_bgr(adb)
    resources = read_resources(frame)
    clean_resources = {k: v or 0 for k, v in resources.items()}

    planner_json = state_to_planner_json(base_state, clean_resources)

    try:
        result = plan(planner_json, clean_resources)
    except Exception as e:
        log.error(f"Gemini planner failed: {e}")
        return

    for decision in result.decisions:
        if decision.action == "wait" or decision.action == "skip":
            log.info(f"Planner says {decision.action}: {decision.reasoning}")
            continue

        if decision.action == "upgrade_hero":
            execute_hero_upgrade(adb, decision, template_set)
        else:
            execute_upgrade(adb, decision, template_set)


def attack_cycle(
    adb: ADB,
    template_set: dict[str, tmpl.Template],
    config: dict,
    state_detector: StateDetector,
) -> tuple[bool, dict]:
    farming = config["farming"]
    thresholds = LootThresholds(
        min_gold=farming["min_gold"],
        min_elixir=farming["min_elixir"],
        min_dark_elixir=farming["min_dark_elixir"],
        max_skips=farming["max_skips"],
        decay=farming["skip_threshold_decay"],
    )

    res_before = read_resources(grab_frame_bgr(adb))
    info: dict = {"res_before": res_before}

    if not enter_matchmaking(adb, template_set):
        info["abort"] = "enter_matchmaking_failed"
        return False, info

    if not state_detector.wait_for(GameState.BATTLE, lambda: grab_frame_bgr(adb), timeout=10.0):
        log.warning("Did not reach BATTLE after enter_matchmaking — backing out")
        # Likely stuck on army view with the ATTACK tap missed. BACK three
        # times to walk home → matchmaking → army → home.
        for _ in range(3):
            adb.back()
            adb.wait_random(0.8, 1.2)
            if state_detector.detect(grab_frame_bgr(adb)) == GameState.HOME:
                break
        info["abort"] = "no_battle_state"
        return False, info

    loot = search_loop(adb, template_set, thresholds)
    if loot is None:
        log.warning("Search loop gave up — ending battle")
        end_battle_warmup(adb)
        info["abort"] = "no_loot_threshold"
        return False, info

    info["loot"] = {"gold": loot.gold, "elixir": loot.elixir, "dark": loot.dark_elixir}

    deploy_sneaky_goblins(adb, template_set)
    deploy_heroes(adb, template_set)

    # Confirm we're STILL in battle after deploy. If state flipped to HOME,
    # the deploy taps landed off-target (e.g., we never actually entered battle).
    cur = state_detector.detect(grab_frame_bgr(adb))
    if cur != GameState.BATTLE and cur != GameState.RESULT:
        log.warning(f"Not in BATTLE after deploy (state={cur.name}) — fake cycle")
        info["abort"] = "deploy_off_target"
        return False, info

    monitor_battle(adb, template_set)

    # Wait for RESULT screen (must reach within 30s; battle naturally ends or surrender resolves).
    if not state_detector.wait_for(GameState.RESULT, lambda: grab_frame_bgr(adb), timeout=30.0):
        log.warning("RESULT screen not reached — pressing back")
        adb.back()
        adb.wait_random(1.5, 2.5)

    return_home(adb, template_set)
    close_modal(adb, template_set)

    # Post-attack reward animations chain 3-5 screens (chest closed → opens
    # → reveal item → "Continue" → next chest). Use raw classify (not the
    # smoothed StateDetector) to break out as soon as home is visible —
    # otherwise the loop keeps tapping after we've returned and accidentally
    # opens the Shop (1230, 700 = Shop icon on home).
    from screen.state import classify, detect_signals
    for _ in range(20):
        frame = grab_frame_bgr(adb)
        sig = detect_signals(frame, template_set, threshold=0.70)
        if classify(sig) == GameState.HOME:
            break
        adb.tap(640, 400)        # tap chest/item to advance the open animation
        time.sleep(0.4)
        adb.tap(640, 595)        # tap Continue button for reveal screens
        time.sleep(0.5)

    if not state_detector.wait_for(GameState.HOME, lambda: grab_frame_bgr(adb), timeout=25.0):
        log.error("Could not return to HOME after attack — needs CoC restart")
        info["abort"] = "no_home_after_battle"
        return False, info

    res_after = read_resources(grab_frame_bgr(adb))
    info["res_after"] = res_after

    # Compute delta defensively — OCR can return None or wildly wrong values.
    def _delta(key: str) -> int | None:
        b, a = res_before.get(key), res_after.get(key)
        if b is None or a is None:
            return None
        if abs(b) > 10_000_000_000 or abs(a) > 10_000_000_000:
            return None  # OCR garbage
        return a - b
    info["delta"] = {k: _delta(k) for k in ("gold", "elixir", "dark_elixir")}

    return True, info


def should_take_break(session_start: float, config: dict) -> bool:
    interval = config["session"]["break_interval_hours"] * 3600
    jitter = random.uniform(-600, 600)  # ±10 min
    return time.time() - session_start >= interval + jitter


def take_break(adb: ADB, config: dict) -> None:
    lo, hi = config["session"]["break_duration_min"]
    duration = random.uniform(lo, hi) * 60
    log.info(f"Taking break for {duration / 60:.0f} minutes")
    adb.kill_coc()
    time.sleep(duration)
    adb.launch_coc()
    time.sleep(15)  # loading screen


def main() -> None:
    setup_logging()
    config = load_config()

    adb_config = ADBConfig(
        port_range=tuple(config["emulator"]["adb_port_range"]),
        tap_jitter_px=config["delays"]["tap_jitter_px"],
        delay_range_ms=tuple(config["delays"]["between_actions_ms"]),
    )
    adb = ADB(config=adb_config)

    log.info("Connecting to BlueStacks...")
    addr = adb.connect()
    log.info(f"Connected: {addr}")

    res = adb.get_resolution()
    log.info(f"Resolution: {res[0]}x{res[1]}")

    template_set = tmpl.load_all()
    log.info(f"Loaded {len(template_set)} templates")

    state_detector = StateDetector(template_set)

    if not adb.is_coc_running():
        log.info("Launching CoC...")
        adb.launch_coc()
        adb.wait(15)

    session_start = time.time()
    attack_count = 0
    consecutive_failures = 0
    consecutive_unknown = 0
    home_zoomed_out = False

    log.info("Starting farming loop")

    while True:
        frame = grab_frame_bgr(adb)
        state = state_detector.detect(frame)

        if state == GameState.MODAL:
            close_modal(adb, template_set)
            consecutive_unknown = 0
            continue

        if state == GameState.UNKNOWN:
            consecutive_unknown += 1
            # Two CoC restarts didn't recover — game is likely in a
            # maintenance break, login flow, or network outage. Sleep then
            # retry rather than spinning every 1.5s.
            if consecutive_unknown >= 12:
                log.warning("UNKNOWN x12 — long sleep (5 min) then retry")
                time.sleep(300)
                state_detector.reset()
                consecutive_unknown = 0
                continue
            if consecutive_unknown >= 4 and consecutive_unknown % 4 == 0:
                log.warning(f"UNKNOWN x{consecutive_unknown} — force-restarting CoC")
                adb.kill_coc()
                time.sleep(2)
                adb.launch_coc()
                time.sleep(20)
                state_detector.reset()
                continue
            # Recovery ladder for unknown screens:
            # 1st: tap chest/center to advance reward animations
            # 2nd: tap red close-X if a modal
            # 3rd: BACK as last resort before CoC restart
            if consecutive_unknown == 1:
                log.info("UNKNOWN x1 — tap (640, 400) to advance reward / dismiss overlay")
                adb.tap(640, 400)
            elif consecutive_unknown == 2:
                close_pos = find_red_close_x(frame)
                if close_pos is not None:
                    log.info(f"UNKNOWN x2 — tap red close-X at {close_pos}")
                    adb.tap(close_pos[0], close_pos[1])
                else:
                    adb.back()
            elif consecutive_unknown == 3:
                adb.back()
            adb.wait_random(1.0, 2.0)
            continue

        if state != GameState.HOME:
            adb.back()
            adb.wait_random(1.0, 2.0)
            continue

        consecutive_unknown = 0

        # Pinch home village out once per session so collect_resources sees
        # all collectors at default scroll. CoC keeps the zoom level until
        # something else (battle, modal) resets it.
        if not home_zoomed_out:
            log.info("Zooming out home village")
            adb.bluestacks_zoom_out(taps=6)
            adb.wait_random(0.3, 0.6)
            home_zoomed_out = True

        if should_take_break(session_start, config):
            take_break(adb, config)
            session_start = time.time()
            continue

        collect_resources(adb, template_set)

        frame = grab_frame_bgr(adb)
        resources = read_resources(frame)
        log.info(f"Resources: {resources}")

        # Planner trigger disabled until ca.zgrs.clipper is sideloaded.
        # The org.rojekti.clipper variant doesn't expose `clipper.get`, so
        # run_planner currently fails on read_clipboard and disrupts the
        # state machine (settings panel left open).
        if False and check_resources_near_max(resources, config):
            run_planner(adb, template_set, config)

        cycle_started_at = time.time()
        result, info = attack_cycle(adb, template_set, config, state_detector)
        cycle_duration = time.time() - cycle_started_at
        if result:
            attack_count += 1
            consecutive_failures = 0
            delta = info.get("delta", {})
            log.info(
                f"Attack #{attack_count} complete ({cycle_duration:.1f}s) Δ "
                f"gold={delta.get('gold')} elixir={delta.get('elixir')} dark={delta.get('dark_elixir')}"
            )
            append_raid_log({
                "cycle": attack_count,
                "duration_s": round(cycle_duration, 1),
                "result": "completed",
                "loot_seen": info.get("loot"),
                "delta": delta,
                "res_before": info.get("res_before"),
                "res_after": info.get("res_after"),
            })
        else:
            consecutive_failures += 1
            abort_reason = info.get("abort", "unknown")
            log.warning(
                f"Attack cycle failed [{abort_reason}] ({consecutive_failures} in a row) — retrying in 5s"
            )
            append_raid_log({
                "cycle": attack_count + 1,
                "duration_s": round(cycle_duration, 1),
                "result": "failed",
                "abort_reason": abort_reason,
            })
            if consecutive_failures >= 5:
                log.warning("Too many failures — force-restarting CoC")
                adb.kill_coc()
                time.sleep(2)
                adb.launch_coc()
                time.sleep(20)
                state_detector.reset()
                consecutive_failures = 0
            adb.wait(5)


if __name__ == "__main__":
    main()
