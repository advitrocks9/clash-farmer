from __future__ import annotations

import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")
RAID_LOG = Path(__file__).resolve().parent / "local" / "raid_log.jsonl"

# Dark elixir is rarer than gold/elixir. The combined-loot metric weights it
# at 12× — middle of the 10-15 range used by most farming guides — so the
# optimisation target reflects what's actually scarce.
DARK_WEIGHT = 12


def loot_score(gold: int = 0, elixir: int = 0, dark_elixir: int = 0) -> int:
    return gold + elixir + dark_elixir * DARK_WEIGHT


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
from bot import telegram
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


CRASH_DIR = Path(__file__).resolve().parent / "local" / "crashes"


def _save_crash_dump(adb: ADB, reason: str, info: dict) -> None:
    # Save the failing frame + cycle info so the failure is debuggable
    # post-mortem without having to reproduce live.
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        outdir = CRASH_DIR / f"{ts}_{reason}"
        outdir.mkdir(parents=True, exist_ok=True)
        frame_pil = adb.screencap()
        frame_pil.save(outdir / "frame.png")
        (outdir / "info.json").write_text(json.dumps(info, indent=2, default=str))
        log.info(f"crash dump saved: {outdir.relative_to(CRASH_DIR.parent.parent)}")
    except Exception as e:
        log.warning(f"crash dump failed: {e}")


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

    # Scroll down and tap the green Copy button next to "Export Village data".
    adb.swipe(640, 500, 640, 200, duration_ms=500)
    adb.wait_random(0.5, 1.0)

    frame = grab_frame_bgr(adb)
    btn_copy = template_set.get("btn_copy_data")
    pos = tmpl.find(frame, btn_copy) if btn_copy else None
    if pos is None:
        # Fallback: known location of the Copy button when Data Export is on screen.
        pos = (1130, 595)
    # Empty the macOS clipboard first so we can tell whether the Copy actually
    # landed (otherwise pbpaste returns whatever the user had copied earlier).
    adb.clear_clipboard()
    adb.tap(pos[0], pos[1])
    adb.wait_random(1.0, 1.5)

    try:
        raw_json = adb.read_clipboard()
    except RuntimeError as e:
        log.error(f"Clipboard read failed — skipping planner ({e})")
        adb.back(); adb.wait_random(0.5, 1.0)
        adb.back(); adb.wait_random(0.5, 1.0)
        return
    if not raw_json.lstrip().startswith("{"):
        log.error("Clipboard does not contain JSON — Copy Data tap probably missed")
        adb.back(); adb.wait_random(0.5, 1.0)
        adb.back(); adb.wait_random(0.5, 1.0)
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

    summary_lines: list[str] = []
    for decision in result.decisions:
        if decision.action in ("wait", "skip"):
            log.info(f"Planner says {decision.action}: {decision.reasoning}")
            summary_lines.append(f"{decision.action}: {decision.reasoning}")
            continue

        log.info(f"Planner: {decision.action} {decision.target} → lvl {decision.target_level}")
        summary_lines.append(f"{decision.action}: {decision.target} → lvl {decision.target_level}")
        if decision.action == "upgrade_hero":
            execute_hero_upgrade(adb, decision, template_set)
        else:
            execute_upgrade(adb, decision, template_set)

    if summary_lines:
        telegram.send("<b>planner</b>\n" + "\n".join(summary_lines), silent=True)


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
        _save_crash_dump(adb, "enter_matchmaking_failed", info)
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
        _save_crash_dump(adb, "no_battle_state", info)
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

    # State-aware return-home loop. The previous version blindly tapped
    # (640,400) and (640,595) until HOME was detected — those coords land on
    # Shop/Treasure/hotbar widgets once we're already on home, *creating*
    # modals the next iter sees and prolonging the loop. Instead, dispatch
    # by state: tap the actual return-home / close-X position when found,
    # use BACK as the safe primitive when we don't know where we are.
    from screen import templates as tmpl
    from screen.state import RESULT_ROI, classify, detect_signals
    btn_return_home = template_set.get("btn_return_home")
    deadline = time.time() + 30.0
    last_state = None
    last_state_change = time.time()
    unknown_taps = 0
    while time.time() < deadline:
        frame = grab_frame_bgr(adb)
        sig = detect_signals(frame, template_set, threshold=0.70)
        state = classify(sig)
        if state != last_state:
            log.info(f"  return-home: {last_state} → {state}")
            last_state = state
            last_state_change = time.time()
        if state == GameState.HOME:
            break
        if state == GameState.MODAL:
            pos = find_red_close_x(frame)
            if pos:
                adb.tap_precise(pos[0], pos[1])
            else:
                adb.back()
        elif state == GameState.RESULT and btn_return_home is not None:
            pos = tmpl.find(frame, btn_return_home, threshold=0.70, roi=RESULT_ROI)
            if pos:
                adb.tap_precise(pos[0], pos[1])
            else:
                adb.tap(640, 595)
        else:
            # UNKNOWN — likely chest reveal (no template for "Continue").
            # First try BACK (closes Shop/Treasure if we accidentally opened
            # one), then advance with a centre-upper tap that won't hit the
            # bottom hotbar. Escalate after stalling >12s.
            if unknown_taps % 2 == 0:
                adb.back()
            else:
                adb.tap(640, 360)
            unknown_taps += 1
            if time.time() - last_state_change > 12.0:
                log.warning(f"  return-home stalled in {state.name} for 12s — escalating BACK x3")
                for _ in range(3):
                    adb.back()
                    time.sleep(0.5)
                last_state_change = time.time()
        time.sleep(0.5)

    if state_detector.detect(grab_frame_bgr(adb)) != GameState.HOME and not state_detector.wait_for(
        GameState.HOME, lambda: grab_frame_bgr(adb), timeout=8.0
    ):
        log.error("Could not return to HOME after attack — needs CoC restart")
        _save_crash_dump(adb, "no_home_after_battle", info)
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
    failed_count = 0
    consecutive_failures = 0
    consecutive_unknown = 0
    home_zoomed_out = False

    log.info("Starting farming loop")
    digest_anchor = time.time()
    session_anchor = time.time()
    digest_loot = {"gold": 0, "elixir": 0, "dark_elixir": 0}
    session_loot = {"gold": 0, "elixir": 0, "dark_elixir": 0}
    last_planner_run = 0.0
    PLANNER_MIN_GAP_S = 1800  # at most one Gemini call every 30 min

    pause_event = threading.Event()
    pause_event.set()  # set = run; clear = pause

    def _fmt(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        if n >= 1_000:
            return f"{n / 1_000:.0f}K"
        return str(n)

    def _eta_to_full(current: int, cap: int, per_hour: int) -> str:
        if per_hour <= 0 or current >= cap:
            return "—"
        hours = (cap - current) / per_hour
        if hours < 1:
            return f"{int(hours * 60)}m"
        return f"{hours:.1f}h"

    def _status_text() -> str:
        secs = max(int(time.time() - session_anchor), 1)
        score = loot_score(**session_loot)
        rate = int(score * 3600 / secs)
        gold_rate = int(session_loot["gold"] * 3600 / secs)
        elixir_rate = int(session_loot["elixir"] * 3600 / secs)
        dark_rate = int(session_loot["dark_elixir"] * 3600 / secs)

        # Read current resources from the live screen (not session totals).
        cur = read_resources(grab_frame_bgr(adb))
        cur = {k: (v if isinstance(v, int) and v < 1_000_000_000 else 0) for k, v in cur.items()}

        cap_g = config["resources"]["storage_max"]["gold"]
        cap_e = config["resources"]["storage_max"]["elixir"]
        cap_d = config["resources"]["storage_max"]["dark_elixir"]

        running = "▶️ running" if pause_event.is_set() else "⏸ <b>paused</b>"
        return (
            f"🏰 <b>clash-farmer</b> · {running}\n"
            f"⏱ {secs // 3600}h{(secs % 3600) // 60:02d}m  ·  "
            f"🥊 {attack_count} ok / {failed_count} fail\n"
            f"\n"
            f"📊 <b>session score</b>: {_fmt(score)} ({_fmt(rate)}/hr)\n"
            f"🪙 gold   gained {_fmt(session_loot['gold'])} ({_fmt(gold_rate)}/hr) · "
            f"on hand {_fmt(cur.get('gold', 0))} (full in {_eta_to_full(cur.get('gold', 0), cap_g, gold_rate)})\n"
            f"💧 elixir gained {_fmt(session_loot['elixir'])} ({_fmt(elixir_rate)}/hr) · "
            f"on hand {_fmt(cur.get('elixir', 0))} (full in {_eta_to_full(cur.get('elixir', 0), cap_e, elixir_rate)})\n"
            f"🟣 dark   gained {_fmt(session_loot['dark_elixir'])} ({_fmt(dark_rate)}/hr) · "
            f"on hand {_fmt(cur.get('dark_elixir', 0))} (full in {_eta_to_full(cur.get('dark_elixir', 0), cap_d, dark_rate)})\n"
            f"\n"
            f"🎯 state: <code>{state_detector.current.name}</code>"
        )

    def _send_screenshot(_: str = "") -> str | None:
        import subprocess
        path = Path("/tmp/clash_farmer_screenshot.png")
        with open(path, "wb") as f:
            subprocess.run(
                ["adb", "-s", adb._addr, "exec-out", "screencap", "-p"],
                stdout=f, timeout=10, check=False,
            )
        telegram.send_photo(path, caption=_status_text())
        return None

    poller = telegram.CommandPoller()
    poller.on("/start", lambda _: _status_text())
    poller.on("/status", lambda _: _status_text())
    poller.on("/score", lambda _: _status_text())
    poller.on("/screenshot", _send_screenshot)
    poller.on("/photo", _send_screenshot)
    poller.on("/pause", lambda _: (pause_event.clear(), "paused — call /resume to continue")[1])
    poller.on("/resume", lambda _: (pause_event.set(), "resumed")[1])
    poller.on("/restart", lambda _: (
        adb.kill_coc(), time.sleep(2), adb.launch_coc(),
        "restarted CoC; bot will resume when home detected"
    )[3])
    poller.on("/help", lambda _: (
        "🤖 <b>commands</b>\n"
        "/status — uptime · cycles · loot rates · ETAs\n"
        "/screenshot — current BlueStacks frame\n"
        "/pause — stop the loop (won't touch CoC)\n"
        "/resume — start the loop again\n"
        "/restart — kill + launch CoC"
    ))
    poller.start()
    telegram.send("🚀 clash-farmer started — /help for commands", silent=True)

    while True:
        pause_event.wait()
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
            if consecutive_unknown >= 8 and consecutive_unknown % 4 == 0:
                log.warning(f"UNKNOWN x{consecutive_unknown} — force-restarting CoC")
                adb.kill_coc()
                time.sleep(2)
                adb.launch_coc()
                time.sleep(20)
                state_detector.reset()
                continue
            # Recovery ladder for unknown screens:
            # 1st: any "Return Home" button on screen (defense replay,
            #      visit-village, post-attack result outside the RESULT ROI).
            # 2nd-3rd: tap chest/item center + Continue button — most
            #      "stuck" situations are chained reward animations.
            # 4th-5th: red close-X / BACK for modals.
            # 8+: CoC restart (handled below).
            ret = template_set.get("btn_return_home")
            ret_pos = tmpl.find(frame, ret, threshold=0.6) if ret is not None else None
            if consecutive_unknown == 1 and ret_pos is not None:
                log.info(f"UNKNOWN x1 — tap Return Home at {ret_pos}")
                adb.tap(ret_pos[0], ret_pos[1])
            elif consecutive_unknown in (1, 2, 3):
                # Chest sequences chain 3-5 screens; tap chest then Continue
                # in alternating attempts.
                if consecutive_unknown % 2 == 1:
                    adb.tap(640, 400)
                else:
                    adb.tap(640, 595)
            elif consecutive_unknown in (4, 5):
                close_pos = find_red_close_x(frame)
                if close_pos is not None:
                    log.info(f"UNKNOWN x{consecutive_unknown} — red close-X")
                    adb.tap(close_pos[0], close_pos[1])
                else:
                    adb.back()
            elif consecutive_unknown in (6, 7):
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

        # Planner reads CoC's JSON export via Settings → More → Copy Data,
        # then `pbpaste` (BlueStacks mirrors Android clipboard to the host).
        # Resource OCR is noisy enough that the storages-near-max gate is
        # unreliable, so we also trigger on a 30-min timer.
        time_for_planner = time.time() - last_planner_run > PLANNER_MIN_GAP_S
        if time_for_planner or check_resources_near_max(resources, config):
            try:
                run_planner(adb, template_set, config)
                last_planner_run = time.time()
            except Exception as e:
                log.error(f"planner failed, continuing farm: {e}")

        cycle_started_at = time.time()
        result, info = attack_cycle(adb, template_set, config, state_detector)
        cycle_duration = time.time() - cycle_started_at
        if result:
            attack_count += 1
            consecutive_failures = 0
            delta = info.get("delta", {})
            seen = info.get("loot") or {}
            log.info(
                f"Attack #{attack_count} complete ({cycle_duration:.1f}s) Δ "
                f"gold={delta.get('gold')} elixir={delta.get('elixir')} dark={delta.get('dark_elixir')}"
            )
            append_raid_log({
                "cycle": attack_count,
                "duration_s": round(cycle_duration, 1),
                "result": "completed",
                "loot_seen": seen,
                "delta": delta,
                "res_before": info.get("res_before"),
                "res_after": info.get("res_after"),
            })
            for k in ("gold", "elixir", "dark_elixir"):
                v = seen.get("dark" if k == "dark_elixir" else k)
                if isinstance(v, int):
                    digest_loot[k] += v
                    session_loot[k] += v
            cycle_score = loot_score(
                seen.get("gold", 0), seen.get("elixir", 0), seen.get("dark", 0) or 0
            )
            session_secs = max(time.time() - session_anchor, 1)
            session_score = loot_score(**session_loot)
            score_per_hr = int(session_score * 3600 / session_secs)
            log.info(
                f"  cycle score {cycle_score:,} | session {score_per_hr:,}/hr "
                f"({session_score:,} over {session_secs/60:.0f} min)"
            )
            if time.time() - digest_anchor >= 3600:
                telegram.send(_status_text(), silent=True)
                digest_anchor = time.time()
                digest_loot = {"gold": 0, "elixir": 0, "dark_elixir": 0}
        else:
            consecutive_failures += 1
            failed_count += 1
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
                telegram.send(
                    f"clash-farmer: 5 cycles failed in a row "
                    f"(last reason: {abort_reason}). Restarting CoC."
                )
                adb.kill_coc()
                time.sleep(2)
                adb.launch_coc()
                time.sleep(20)
                state_detector.reset()
                consecutive_failures = 0
            adb.wait(5)


if __name__ == "__main__":
    main()
