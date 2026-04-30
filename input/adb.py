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

    def scroll(self, dx: int = 0, dy: int = -5) -> None:
        self._run(f"input roll {dx} {dy}")
        self._random_delay()

    def zoom_out(self, steps: int = 5) -> None:
        # Trackball-roll fallback (works on physical devices, not BlueStacks).
        for _ in range(steps):
            self.scroll(dy=-3)
            time.sleep(0.1)

    def bluestacks_zoom_out(self, taps: int = 8) -> None:
        """Send UP-arrow keystrokes to BlueStacks via macOS osascript.

        BlueStacks' default Clash of Clans keymap binds UP arrow to in-game
        zoom-out. ADB key events bypass BlueStacks' keymap layer, so we send
        the keystroke from the host. This stelas focus briefly while the
        keys are sent — there is no way to drive BlueStacks' keymap headless
        without Input Monitoring permission for the Python process.
        """
        script = f'''
tell application "BlueStacks" to activate
delay 0.4
tell application "System Events"
  tell process "BlueStacks"
    repeat {taps} times
      key code 126
      delay 0.05
    end repeat
  end tell
end tell
'''
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=10)

    def pinch_zoom_out(
        self,
        steps: int = 6,
        spread_start: int = 300,
        spread_end: int = 80,
        center: tuple[int, int] = (640, 360),
    ) -> None:
        """Two-finger pinch via raw multitouch on /dev/input/event2.

        BlueStacks ignores `input roll` and KEYCODE_ZOOM_OUT, so we drive the
        virtual touchscreen directly. Coordinates are scaled to ABS range
        (0-32767) which the BlueStacks Virtual Touch device expects.
        """
        x_factor = 32767 / 1280
        y_factor = 32767 / 720
        cx, cy = center

        def to_abs(x: int, y: int) -> tuple[int, int]:
            return int(x * x_factor), int(y * y_factor)

        cmds: list[str] = []

        def emit(typ: int, code: int, value: int) -> None:
            cmds.append(f"sendevent /dev/input/event2 {typ} {code} {value}")

        def syn() -> None:
            emit(0, 0, 0)

        # Press both fingers at the outer positions (horizontal pinch).
        x0, y0 = to_abs(cx - spread_start, cy)
        x1, y1 = to_abs(cx + spread_start, cy)
        emit(3, 47, 0)
        emit(3, 57, 100)
        emit(3, 53, x0)
        emit(3, 54, y0)
        emit(3, 47, 1)
        emit(3, 57, 101)
        emit(3, 53, x1)
        emit(3, 54, y1)
        syn()

        # Move both fingers inward over `steps` increments.
        for step in range(1, steps + 1):
            t = step / steps
            spread = spread_start + (spread_end - spread_start) * t
            x0, _ = to_abs(int(cx - spread), cy)
            x1, _ = to_abs(int(cx + spread), cy)
            emit(3, 47, 0)
            emit(3, 53, x0)
            emit(3, 47, 1)
            emit(3, 53, x1)
            syn()

        # Release both fingers.
        emit(3, 47, 0)
        emit(3, 57, -1)
        emit(3, 47, 1)
        emit(3, 57, -1)
        syn()

        script = "\n".join(cmds)
        subprocess.run(
            ["adb", "-s", self._addr, "shell", script],
            capture_output=True,
            timeout=10,
        )

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

    # --- Clipboard (Clipper APK) ---
    # Android 11+ restricts clipboard to foreground apps.
    # Workaround: bring Clipper to foreground, broadcast, then kill it.

    def read_clipboard(self) -> str:
        self._run("monkey -p org.rojekti.clipper -c android.intent.category.LAUNCHER 1")
        time.sleep(0.5)
        result = self._run("am broadcast -a clipper.get")
        self._run("am force-stop org.rojekti.clipper")
        return self._parse_clipper_result(result)

    def _parse_clipper_result(self, result: str) -> str:
        for line in result.splitlines():
            line = line.strip()
            if "data=" in line:
                start = line.index('data="') + 6
                end = line.rindex('"')
                return line[start:end]
        raise RuntimeError(f"Clipper returned unexpected output: {result}")

    def read_clipboard_file(self, path: str = "/sdcard/clipboard.txt") -> str:
        return self._run(f"cat {path}")

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
