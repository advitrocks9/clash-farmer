from __future__ import annotations

import io
import random
import socket
import subprocess
import time
from dataclasses import dataclass, field

from PIL import Image


@dataclass
class ADBConfig:
    host: str = "127.0.0.1"
    port_range: tuple[int, int] = (5554, 5600)
    tap_jitter_px: int = 5
    delay_range_ms: tuple[int, int] = (200, 800)


@dataclass
class ADB:
    config: ADBConfig = field(default_factory=ADBConfig)
    _addr: str = field(init=False, default="")

    def connect(self) -> str:
        port = self._scan_port()
        if port is None:
            raise ConnectionError(
                f"No ADB device found on {self.config.host}:{self.config.port_range}"
            )
        self._addr = f"{self.config.host}:{port}"
        result = self._run_raw(["adb", "connect", self._addr])
        if "connected" not in result and "already" not in result:
            raise ConnectionError(f"ADB connect failed: {result}")
        return self._addr

    @property
    def connected(self) -> bool:
        if not self._addr:
            return False
        result = self._run_raw(["adb", "devices"])
        return self._addr in result and "device" in result

    def _scan_port(self) -> int | None:
        lo, hi = self.config.port_range
        for port in range(lo, hi):
            try:
                s = socket.create_connection((self.config.host, port), timeout=0.05)
                s.close()
                return port
            except OSError:
                continue
        return None

    def _run(self, shell_cmd: str) -> str:
        return self._run_raw(["adb", "-s", self._addr, "shell", shell_cmd])

    def _run_raw(self, cmd: list[str]) -> str:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()

    # --- Input ---

    def tap(self, x: int, y: int) -> None:
        jx = x + random.randint(-self.config.tap_jitter_px, self.config.tap_jitter_px)
        jy = y + random.randint(-self.config.tap_jitter_px, self.config.tap_jitter_px)
        self._run(f"input tap {jx} {jy}")
        self._random_delay()

    def tap_precise(self, x: int, y: int) -> None:
        self._run(f"input tap {x} {y}")
        self._random_delay()

    def tap_fast(self, x: int, y: int) -> None:
        # Skip the per-action random delay. Use only inside tight deploy loops
        # where 200-800ms between every tap would exceed the warmup timer.
        self._run(f"input tap {x} {y}")

    def tap_burst(self, points: list[tuple[int, int]], gap_ms: int = 30) -> None:
        # Fire many taps in a single adb shell session. Each `input tap`
        # invocation has ~50ms overhead, so 192 separate adb calls would take
        # ~10s. Batching collapses that to one ADB roundtrip plus the on-device
        # input-tap latency.
        cmds = []
        for x, y in points:
            cmds.append(f"input tap {x} {y}")
            if gap_ms > 0:
                cmds.append(f"sleep 0.{gap_ms:03d}")
        script = "; ".join(cmds)
        subprocess.run(
            ["adb", "-s", self._addr, "shell", script],
            capture_output=True,
            timeout=60,
        )

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._run(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
        self._random_delay()

    def bluestacks_zoom_out(self, taps: int = 6) -> None:
        """Send UP-arrow keystrokes to BlueStacks (default CoC keymap binds it
        to in-game pinch-out). ADB key events bypass BlueStacks' keymap layer,
        so the keystroke has to come from the host. We save the user's
        currently-focused app, activate BlueStacks, send the keys, then
        restore the previous app — total disturbance ~400 ms.
        """
        script = f'''
set prev to ""
try
  tell application "System Events" to set prev to name of (first process whose frontmost is true)
end try
tell application "BlueStacks" to activate
delay 0.2
tell application "System Events"
  tell process "BlueStacks"
    repeat {taps} times
      key code 126
      delay 0.04
    end repeat
  end tell
end tell
delay 0.05
if prev is not "" and prev is not "BlueStacks" then
  try
    tell application prev to activate
  end try
end if
'''
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)

    def back(self) -> None:
        self._run("input keyevent 4")
        self._random_delay()

    # --- Screen ---

    def screencap(self) -> Image.Image:
        result = subprocess.run(
            ["adb", "-s", self._addr, "exec-out", "screencap", "-p"],
            capture_output=True,
            timeout=10,
        )
        if not result.stdout:
            raise RuntimeError("ADB screencap returned empty data")
        return Image.open(io.BytesIO(result.stdout))

    # --- Clipboard ---
    # Android 11+ blocks background apps from reading the clipboard, so
    # broadcast-based tools like ca.zgrs.clipper return result=0 on Android 13.
    # BlueStacks Air mirrors Android's clipboard to the macOS clipboard, so we
    # just read it from the host via `pbpaste` after CoC's "Copy Data" tap.

    def read_clipboard(self) -> str:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=2
        )
        text = result.stdout
        if not text:
            raise RuntimeError("Mac clipboard empty (BlueStacks→host sync may be off)")
        return text

    def clear_clipboard(self) -> None:
        # Stash an empty string into the macOS clipboard so we can detect
        # whether a subsequent CoC "Copy" actually succeeded (vs. picking up
        # whatever the user had previously copied).
        subprocess.run(["pbcopy"], input="", text=True, timeout=2)

    # --- App lifecycle ---

    def launch_coc(self) -> None:
        self._run("monkey -p com.supercell.clashofclans -c android.intent.category.LAUNCHER 1")

    def kill_coc(self) -> None:
        self._run("am force-stop com.supercell.clashofclans")

    def is_coc_running(self) -> bool:
        result = self._run("pidof com.supercell.clashofclans")
        return bool(result)

    # --- Display ---

    def get_resolution(self) -> tuple[int, int]:
        result = self._run("wm size")
        # "Physical size: 1280x720" or "Override size: 1280x720"
        parts = result.strip().splitlines()[-1]
        size = parts.split(":")[-1].strip()
        w, h = size.split("x")
        return int(w), int(h)

    def set_resolution(self, width: int, height: int) -> None:
        self._run(f"wm size {width}x{height}")

    def set_dpi(self, dpi: int) -> None:
        self._run(f"wm density {dpi}")

    # --- Timing ---

    def _random_delay(self) -> None:
        lo, hi = self.config.delay_range_ms
        time.sleep(random.randint(lo, hi) / 1000)

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait_random(self, lo: float, hi: float) -> None:
        time.sleep(random.uniform(lo, hi))
