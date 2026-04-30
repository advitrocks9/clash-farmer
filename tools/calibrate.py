"""Live-feed calibration tool for capturing templates and defining ROIs."""

from __future__ import annotations

import json
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from input.adb import ADB, ADBConfig

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
ROIS_PATH = Path(__file__).resolve().parent.parent / "screen" / "rois.json"

FEED_INTERVAL_MS = 500
CANVAS_SCALE = 1.0  # 1:1 at 1280x720


class CapturedItem:
    def __init__(self, name: str, kind: str, rect: tuple[int, int, int, int]) -> None:
        self.name = name
        self.kind = kind  # "template" or "roi"
        self.rect = rect  # x1, y1, x2, y2 in game coordinates


class App:
    def __init__(self, root: tk.Tk) -> None:
        print("calibrate: App.__init__ entered", flush=True)
        self.root = root
        self.root.title("Clash Farmer — Calibrate")

        self.adb = ADB(config=ADBConfig(delay_range_ms=(0, 0)))
        self.frame_np: np.ndarray | None = None
        self.photo: ImageTk.PhotoImage | None = None
        self.items: list[CapturedItem] = []
        self.paused = False

        # Selection state
        self.sel_start: tuple[int, int] | None = None
        self.sel_rect: tuple[int, int, int, int] | None = None
        self.sel_rect_id: int | None = None

        # Frame queue — worker thread puts (frame_bgr, pil_for_display); main thread drains
        self._frame_q: queue.Queue[tuple[np.ndarray, Image.Image]] = queue.Queue(maxsize=2)
        self._stop = False

        print("calibrate: load_existing", flush=True)
        self._load_existing()
        print("calibrate: build_ui", flush=True)
        self._build_ui()
        print("calibrate: connect (synchronous)", flush=True)
        self._connect_sync()
        print("calibrate: starting feed worker", flush=True)
        threading.Thread(target=self._feed_worker, daemon=True).start()
        self.root.after(50, self._drain_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        print("calibrate: __init__ done — entering mainloop", flush=True)

    # --- UI ---

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=5)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")

        self.pause_btn = ttk.Button(toolbar, text="⏸ Pause", command=self._toggle_pause)
        self.pause_btn.pack(side="left", padx=2)

        self.status_label = ttk.Label(toolbar, text="Connecting...")
        self.status_label.pack(side="left", padx=10)

        # Canvas for screenshot
        self.canvas = tk.Canvas(self.root, width=1280, height=720, bg="#1e1e1e", cursor="crosshair")
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        # Sidebar
        sidebar = ttk.Frame(self.root, padding=10, width=250)
        sidebar.grid(row=1, column=1, sticky="ns", padx=(0, 5), pady=5)
        sidebar.grid_propagate(False)

        ttk.Label(sidebar, text="Name:").pack(anchor="w")
        self.name_var = tk.StringVar()
        self.name_entry = ttk.Entry(sidebar, textvariable=self.name_var, width=25)
        self.name_entry.pack(anchor="w", pady=(0, 8))

        ttk.Label(sidebar, text="Type:").pack(anchor="w")
        self.kind_var = tk.StringVar(value="template")
        ttk.Radiobutton(sidebar, text="Template (PNG)", variable=self.kind_var, value="template").pack(anchor="w")
        ttk.Radiobutton(sidebar, text="ROI (coordinates)", variable=self.kind_var, value="roi").pack(anchor="w", pady=(0, 8))

        self.coord_label = ttk.Label(sidebar, text="Selection: (none)")
        self.coord_label.pack(anchor="w", pady=(0, 8))

        ttk.Button(sidebar, text="Save Region", command=self._save).pack(anchor="w", pady=(0, 16))

        ttk.Separator(sidebar, orient="horizontal").pack(fill="x", pady=5)
        ttk.Label(sidebar, text="Saved Items:", font=("", 11, "bold")).pack(anchor="w", pady=(5, 5))

        list_frame = ttk.Frame(sidebar)
        list_frame.pack(fill="both", expand=True)

        self.items_listbox = tk.Listbox(list_frame, width=25, font=("Menlo", 10))
        self.items_listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.items_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.items_listbox.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(sidebar)
        btn_frame.pack(fill="x", pady=(5, 0))
        ttk.Button(btn_frame, text="Delete Selected", command=self._delete_selected).pack(side="left")

        self._refresh_list()

    # --- ADB connection ---

    def _connect_sync(self) -> None:
        try:
            addr = self.adb.connect()
            print(f"calibrate: connected to {addr}", flush=True)
            self.status_label.configure(text=f"Connected: {addr}")
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"
            print(f"calibrate: connect failed — {msg}", flush=True)
            self.status_label.configure(text=f"Error: {msg}")

    def _on_close(self) -> None:
        self._stop = True
        self.root.destroy()

    # --- Live feed (queue-based; macOS Tk doesn't process after() reliably from threads) ---

    def _feed_worker(self) -> None:
        import time as _time
        while not self._stop:
            if self.paused or not self.adb._addr:
                _time.sleep(FEED_INTERVAL_MS / 1000)
                continue
            try:
                img = self.adb.screencap()
                rgb = np.array(img.convert("RGB"))
                bgr = rgb[:, :, ::-1].copy()
                display = rgb.copy()
                self._draw_saved_rects(display)
                pil = Image.fromarray(display)
                # Drop oldest frame if queue is full — never block the worker
                try:
                    self._frame_q.put_nowait((bgr, pil))
                except queue.Full:
                    try:
                        self._frame_q.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        self._frame_q.put_nowait((bgr, pil))
                    except queue.Full:
                        pass
            except Exception as e:
                print(f"calibrate: feed error — {type(e).__name__}: {e}", flush=True)
            _time.sleep(FEED_INTERVAL_MS / 1000)

    def _drain_queue(self) -> None:
        try:
            while True:
                bgr, pil = self._frame_q.get_nowait()
                self.frame_np = bgr
                self._update_canvas(pil)
        except queue.Empty:
            pass
        if not self._stop:
            self.root.after(50, self._drain_queue)

    def _update_canvas(self, pil_img: Image.Image) -> None:
        self.photo = ImageTk.PhotoImage(pil_img)
        self.canvas.delete("bg")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="bg")
        self.canvas.tag_lower("bg")

    def _draw_saved_rects(self, display: np.ndarray) -> None:
        for item in self.items:
            x1, y1, x2, y2 = item.rect
            color = (80, 80, 255) if item.kind == "roi" else (255, 180, 50)
            cv2.rectangle(display, (x1, y1), (x2, y2), color, 2)
            cv2.putText(display, item.name, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    # --- Mouse selection ---

    def _on_press(self, event: tk.Event) -> None:
        self.paused = True
        self.pause_btn.configure(text="▶ Resume")
        self.sel_start = (event.x, event.y)
        if self.sel_rect_id:
            self.canvas.delete(self.sel_rect_id)

    def _on_drag(self, event: tk.Event) -> None:
        if self.sel_start is None:
            return
        if self.sel_rect_id:
            self.canvas.delete(self.sel_rect_id)
        x1, y1 = self.sel_start
        x2, y2 = event.x, event.y
        self.sel_rect_id = self.canvas.create_rectangle(x1, y1, x2, y2, outline="#00ff00", width=2)
        self.sel_rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.coord_label.configure(text=f"Selection: {self.sel_rect}")

    def _on_release(self, event: tk.Event) -> None:
        if self.sel_start is None:
            return
        x1, y1 = self.sel_start
        x2, y2 = event.x, event.y
        self.sel_rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        self.coord_label.configure(text=f"Selection: {self.sel_rect}")
        self.name_entry.focus_set()

    def _toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_btn.configure(text="▶ Resume" if self.paused else "⏸ Pause")

    # --- Save / Delete ---

    def _save(self) -> None:
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Enter a name for this region.")
            return
        if self.sel_rect is None or self.frame_np is None:
            messagebox.showwarning("No selection", "Draw a rectangle on the screenshot first.")
            return

        x1, y1, x2, y2 = self.sel_rect
        kind = self.kind_var.get()

        # Clamp to frame bounds
        h, w = self.frame_np.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)

        if x2 - x1 < 5 or y2 - y1 < 5:
            messagebox.showwarning("Too small", "Selection is too small.")
            return

        # Remove existing item with same name
        self.items = [i for i in self.items if i.name != name]

        item = CapturedItem(name, kind, (x1, y1, x2, y2))
        self.items.append(item)

        if kind == "template":
            TEMPLATES_DIR.mkdir(exist_ok=True)
            crop = self.frame_np[y1:y2, x1:x2]
            cv2.imwrite(str(TEMPLATES_DIR / f"{name}.png"), crop)

        self._save_rois()
        self._refresh_list()

        # Reset
        self.name_var.set("")
        if self.sel_rect_id:
            self.canvas.delete(self.sel_rect_id)
            self.sel_rect_id = None
        self.sel_rect = None
        self.sel_start = None
        self.coord_label.configure(text="Selection: (none)")

        self.paused = False
        self.pause_btn.configure(text="⏸ Pause")

    def _delete_selected(self) -> None:
        sel = self.items_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        item = self.items[idx]

        if item.kind == "template":
            path = TEMPLATES_DIR / f"{item.name}.png"
            if path.exists():
                path.unlink()

        self.items.pop(idx)
        self._save_rois()
        self._refresh_list()

    def _refresh_list(self) -> None:
        self.items_listbox.delete(0, tk.END)
        for item in sorted(self.items, key=lambda i: (i.kind, i.name)):
            prefix = "📸" if item.kind == "template" else "📐"
            self.items_listbox.insert(tk.END, f"{prefix} {item.name}")

    # --- Persistence ---

    def _save_rois(self) -> None:
        rois = {}
        for item in self.items:
            if item.kind == "roi":
                rois[item.name] = list(item.rect)
        ROIS_PATH.parent.mkdir(exist_ok=True)
        ROIS_PATH.write_text(json.dumps(rois, indent=2) + "\n")

    def _load_existing(self) -> None:
        # Load existing templates
        if TEMPLATES_DIR.exists():
            for p in TEMPLATES_DIR.glob("*.png"):
                img = cv2.imread(str(p))
                if img is not None:
                    h, w = img.shape[:2]
                    self.items.append(CapturedItem(p.stem, "template", (0, 0, w, h)))

        # Load existing ROIs
        if ROIS_PATH.exists():
            rois = json.loads(ROIS_PATH.read_text())
            for name, coords in rois.items():
                self.items.append(CapturedItem(name, "roi", tuple(coords)))


def main() -> None:
    root = tk.Tk()
    root.geometry("1560x740")
    root.minsize(1000, 600)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
