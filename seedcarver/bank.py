"""The seed bank: flat file of raw biome ids, one 256-byte record per seed, seed = record index."""
import heapq
import os
import threading
from array import array
from concurrent.futures import ThreadPoolExecutor

from . import biomes, core

MAGIC = b"SEEDCARVER1\n\0\0\0\0"  # bump when the sampling or file layout changes
HEADER = len(MAGIC)
BUILD_BLOCK = 4096
SCAN_BLOCK = 16384
THREADS = os.cpu_count() or 4


def count(path):
    """Seeds stored in the bank; 0 if absent. Raises ValueError for a file that isn't a bank."""
    try:
        with open(path, "rb") as f:
            if f.read(HEADER) != MAGIC:
                raise ValueError(f"{path} is not a seed bank (or is from another version)")
    except FileNotFoundError:
        return 0
    return (os.path.getsize(path) - HEADER) // core.CELLS


def build(path, target, progress=None, stop=None, threads=THREADS):
    """Grow the bank to `target` seeds. Safe to interrupt: resumes from the last whole record."""
    done = count(path)

    def gen(start):
        n = min(BUILD_BLOCK, target - start)
        buf = bytearray(n * core.CELLS)
        core.gen_block(start, n, buf)
        return buf

    with open(path, "r+b" if os.path.exists(path) else "wb") as f, ThreadPoolExecutor(threads) as pool:
        if done == 0:
            f.seek(0)
            f.write(MAGIC)
        f.seek(HEADER + done * core.CELLS)
        f.truncate()
        while done < target and not (stop and stop.is_set()):
            starts = range(done, min(done + threads * BUILD_BLOCK, target), BUILD_BLOCK)
            for buf in pool.map(gen, starts):
                f.write(buf)
                done += len(buf) // core.CELLS
            f.flush()
            if progress:
                progress(done, target)
    return done


def search(path, drawing, k=10, threads=THREADS):
    """Best k seeds for a drawing: CELLS group indexes, -1 = unpainted. Returns [(seed, match 0-1)]."""
    if len(drawing) != core.CELLS:
        raise ValueError(f"drawing must have {core.CELLS} cells")
    painted = [i for i, g in enumerate(drawing) if g >= 0]
    if not painted:
        raise ValueError("drawing is empty")
    total = count(path)
    cells = array("H", painted)
    cost = b"".join(biomes.COST[drawing[i]] for i in painted)
    local = threading.local()
    files = []

    def scan(start):
        if not hasattr(local, "f"):
            local.f = open(path, "rb")
            files.append(local.f)
            local.buf = bytearray(SCAN_BLOCK * core.CELLS)
        n = min(SCAN_BLOCK, total - start)
        local.f.seek(HEADER + start * core.CELLS)
        local.f.readinto(local.buf)
        return [(s, start + i) for s, i in core.scan(local.buf, n, cost, cells, k)]

    with ThreadPoolExecutor(threads) as pool:
        hits = heapq.nsmallest(k, (h for block in pool.map(scan, range(0, total, SCAN_BLOCK)) for h in block))
    for f in files:
        f.close()
    worst = biomes.MISS * len(painted)
    return [(seed, 1 - score / worst) for score, seed in hits]
