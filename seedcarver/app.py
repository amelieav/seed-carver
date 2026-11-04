"""Desktop window: pywebview hosts ui.html and exposes Api to its JavaScript."""
import base64
import math
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import webview

from . import bank, biomes, core
from .paths import BANK

MAP_STEP = 4  # blocks per preview pixel: the biome map's native resolution
MAP_SIZE = core.AREA // MAP_STEP


class Api:
    def __init__(self):
        self._stop = threading.Event()
        self._build = None
        self._error = None

    def init(self):
        return {
            "groups": [{"name": n, "color": c} for n, c in zip(biomes.NAMES, biomes.COLORS)],
            "palette": core.palette(),
            "names": [core.biome_name(i) or "" for i in range(256)],
            "grid": core.GRID,
            "area": core.AREA,
            "seed_bytes": bank.SEED_BYTES,
        }

    def status(self):
        try:
            seeds = bank.count(BANK)
        except ValueError as e:
            return {"seeds": 0, "build": None, "error": str(e)}
        return {"seeds": seeds, "build": self._build, "error": self._error}

    def search(self, drawing, mode="area"):
        """mode: area, brushes or fit. Fit hits also give how the preview is turned and zoomed and the drawing
        stretched to fit, and the stretched drawing to lay over that preview."""
        start = time.perf_counter()
        if mode == "fit":
            hits = [{"seed": s, "match": m, "degrees": d, "zoom": z, "kx": kx, "ky": ky,
                     "drawing": bank.transform(drawing, 0, kx, ky)}
                    for s, m, d, z, kx, ky in bank.search_fit(BANK, drawing)]
        else:
            hits = [{"seed": s, "match": m} for s, m in bank.search(BANK, drawing, all_brushes=mode == "brushes")]
        return {
            "hits": hits,
            "seconds": time.perf_counter() - start,
            "searched": bank.count(BANK),
        }

    def preview(self, seed, degrees=0, zoom=1):
        """Biome ids around spawn, as base64, rendered in parallel bands. The drawing's frame is AREA / zoom blocks
        across, turned by degrees; the square rendered (span blocks across, step blocks per pixel) covers it."""
        frame = core.AREA / zoom
        a = math.radians(degrees)
        step = max(1, round(MAP_STEP / zoom))
        size = math.ceil(frame * (abs(math.cos(a)) + abs(math.sin(a))) / step - 1e-9)
        span = size * step
        band = max(size // bank.THREADS, 1)

        def render(row):
            return core.biome_map(seed, -span // 2, -span // 2 + row * step, step, size, min(band, size - row))

        with ThreadPoolExecutor(bank.THREADS) as pool:
            ids = b"".join(pool.map(render, range(0, size, band)))
        return {"size": size, "step": step, "span": span, "frame": frame, "ids": base64.b64encode(ids).decode()}

    def start_build(self, target):
        if self._build:
            return
        target = int(target)
        try:
            have = bank.count(BANK)
        except ValueError as e:
            self._error = str(e)
            return
        needed = max(target - have, 0) * bank.SEED_BYTES
        free = shutil.disk_usage(BANK.parent).free
        if needed > free:
            self._error = f"Not enough disk space: needs {needed / 1e9:.1f} GB, {free / 1e9:.1f} GB free."
            return
        self._stop.clear()
        self._error = None
        self._build = {"done": have, "target": target, "rate": 0}
        threading.Thread(target=self._run_build, args=(target,), daemon=True).start()

    def stop_build(self):
        self._stop.set()

    def _run_build(self, target):
        started, first = time.perf_counter(), self._build["done"]

        def progress(done, total):
            self._build = {"done": done, "target": total, "rate": (done - first) / (time.perf_counter() - started)}

        try:
            bank.build(BANK, target, progress, self._stop)
        except (OSError, ValueError) as e:
            self._error = str(e)
        self._build = None


def run():
    html = Path(__file__).with_name("ui.html").read_text(encoding="utf-8")
    webview.create_window("Seed Carver", html=html, js_api=Api(), width=1180, height=860, min_size=(1000, 700))
    webview.start()
