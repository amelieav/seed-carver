"""Desktop window: pywebview hosts ui.html and exposes Api to its JavaScript."""
import base64
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
        }

    def status(self):
        try:
            seeds = bank.count(BANK)
        except ValueError as e:
            return {"seeds": 0, "build": None, "error": str(e)}
        return {"seeds": seeds, "build": self._build, "error": self._error}

    def search(self, drawing, mode="area"):
        """mode: area, brushes or fit. Fit hits also give how the drawing was turned and stretched, and the drawing
        stretched (not turned), to lay over the preview turned back by `degrees`."""
        start = time.perf_counter()
        if mode == "fit":
            hits = [{"seed": s, "match": m, "degrees": d, "sx": sx, "sy": sy,
                     "drawing": bank.transform(drawing, 0, sx, sy)}
                    for s, m, d, sx, sy in bank.search_fit(BANK, drawing)]
        else:
            hits = [{"seed": s, "match": m} for s, m in bank.search(BANK, drawing, all_brushes=mode == "brushes")]
        return {
            "hits": hits,
            "seconds": time.perf_counter() - start,
            "searched": bank.count(BANK),
        }

    def preview(self, seed):
        """Biome ids for the bank's square around spawn, as base64, rendered in parallel bands."""
        band = max(MAP_SIZE // bank.THREADS, 1)
        rows = range(0, MAP_SIZE, band)

        def render(row):
            return core.biome_map(seed, -core.AREA // 2, -core.AREA // 2 + row * MAP_STEP, MAP_STEP, MAP_SIZE,
                                  min(band, MAP_SIZE - row))

        with ThreadPoolExecutor(bank.THREADS) as pool:
            ids = b"".join(pool.map(render, rows))
        return {"size": MAP_SIZE, "ids": base64.b64encode(ids).decode()}

    def start_build(self, target):
        if self._build:
            return
        target = int(target)
        try:
            have = bank.count(BANK)
        except ValueError as e:
            self._error = str(e)
            return
        needed = max(target - have, 0) * core.CELLS
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
