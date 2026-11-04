"""The seed bank: flat file of raw biome ids, one record of core.CELLS bytes per seed, seed = record index."""
import ctypes
import os
import threading
from array import array
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from . import biomes, core

MAGIC = b"SEEDCARVER2\n\0\0\0\0"  # bump when the sampling or file layout changes
HEADER = len(MAGIC)
BUILD_BLOCK = 256
SCAN_BLOCK = 1024
BATCH = 16
DENSE = SCAN_BLOCK // 16  # more candidates than this in a block: one sequential read beats many small ones
THREADS = os.cpu_count() or 4


def count(path):
    """Seeds stored in the bank; 0 if absent. Raises ValueError for a file that isn't a bank."""
    try:
        with open(path, "rb") as f:
            if f.read(HEADER) != MAGIC:
                raise ValueError(f"{path} is from another version. Delete or move it to build a new bank.")
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
    index(path, done, threads)
    return done


INDEX_MAGIC = b"SEEDCARVERIDX2\n\0"
INDEX_RECORD = core.INDEX_RECORD
_index_lock = threading.Lock()


def _index_header():
    return INDEX_MAGIC + array("H", biomes.CLASS_BIT).tobytes()


def index(path, total, threads=THREADS):
    """Bring `path`.idx up to `total` seeds: per seed, the classes in each tile. Rebuilt when the classes change."""
    header = _index_header()
    class_bit = array("H", biomes.CLASS_BIT)
    idx_path = f"{path}.idx"
    with _index_lock, open(idx_path, "r+b" if os.path.exists(idx_path) else "w+b") as f:
        if f.read(len(header)) != header:
            f.seek(0)
            f.truncate()
            f.write(header)
        have = min((f.seek(0, os.SEEK_END) - len(header)) // INDEX_RECORD, total)
        f.seek(len(header) + have * INDEX_RECORD)
        f.truncate()
        local = threading.local()
        files = []

        def tiles(start):
            if not hasattr(local, "f"):
                local.f = open(path, "rb")
                files.append(local.f)
                local.buf = bytearray(SCAN_BLOCK * core.CELLS)
            n = min(SCAN_BLOCK, total - start)
            local.f.seek(HEADER + start * core.CELLS)
            local.f.readinto(local.buf)
            out = bytearray(n * INDEX_RECORD)
            core.tiles(local.buf, n, class_bit, out)
            return out

        try:
            with ThreadPoolExecutor(threads) as pool:
                for out in pool.map(tiles, range(have, total, SCAN_BLOCK)):
                    f.write(out)
        finally:
            for g in files:
                g.close()


def _bound(cells, groups, weights, need):
    rep = {}
    for i, bit in enumerate(biomes.CLASS_BIT):
        rep.setdefault(bit, i)
    rep.pop(0, None)
    per_tile = Counter()
    weight = {}
    for cell, g, w in zip(cells, groups, weights):
        row, col = divmod(cell, core.GRID)
        per_tile[(row // core.TILE) * (core.GRID // core.TILE) + col // core.TILE, g] += 1
        weight[g] = w
    entries = []
    for (tile, g), p in per_tile.items():
        cost = biomes.COST[g]
        hit = sum(bit for bit, i in rep.items() if cost[i] == biomes.HIT)
        near = sum(bit for bit, i in rep.items() if cost[i] == biomes.NEAR)
        level = [cost[rep[1 << c]] if 1 << c in rep else biomes.MISS for c in range(16)]
        entries.append((tile, hit, near, level, p, weight[g]))
    return core.Bound(entries, (biomes.HIT, biomes.NEAR, biomes.MISS), need)


def search(path, drawing, k=10, threads=THREADS, all_brushes=False):
    """Best k seeds for a drawing: CELLS group indexes, -1 = unpainted. Returns [(seed, match 0-1)].

    By default every painted cell counts equally. With all_brushes every brush counts equally, however
    many cells it covers, and a seed must contain each brush somewhere in the area.

    A per-tile index gives each seed a lower bound on its score; only seeds whose bound can still beat
    the current top k are read from the bank and scored exactly.
    """
    if len(drawing) != core.CELLS:
        raise ValueError(f"drawing must have {core.CELLS} cells")
    painted = [i for i, g in enumerate(drawing) if g >= 0]
    if not painted:
        raise ValueError("drawing is empty")
    total = count(path)
    index(path, total, threads)
    cells = array("H", painted)
    groups = array("H", (drawing[i] for i in painted))
    cost = b"".join(biomes.COST)
    used = Counter(groups)
    # 2**24 per brush keeps the total inside uint32: MISS * 2**24 * 15 brushes < 2**32
    weights = array("I", (2**24 // used[g] if all_brushes else 1 for g in groups))
    id_mask = array("I", [0]) * 256
    for g in used:
        for i in biomes.MEMBERS[g]:
            id_mask[i] |= 1 << g
    need = sum(1 << g for g in used) if all_brushes else 0
    bound = _bound(cells, groups, weights, need)
    scorer = core.Scorer(cost, cells, groups, weights, need, id_mask)
    idx_header = len(_index_header())
    best = []
    limit = [2**32 - 1]
    cutoff = [(2**32, 0)]
    lock = threading.Lock()

    def merge(hits):
        with lock:
            best.extend(hits)
            best.sort()
            del best[k:]
            if len(best) == k:
                limit[0], cutoff[0] = best[-1][0], best[-1]

    local = threading.local()
    files = []

    def scan(start):
        if not hasattr(local, "f"):
            local.f = open(path, "rb")
            local.idx = open(f"{path}.idx", "rb")
            files.extend((local.f, local.idx))
            local.tiles = bytearray(SCAN_BLOCK * INDEX_RECORD)
            local.keys = (ctypes.c_uint64 * SCAN_BLOCK)()
            local.recs = bytearray(BATCH * core.CELLS)
            local.chunk = bytearray(SCAN_BLOCK * core.CELLS)
        n = min(SCAN_BLOCK, total - start)
        local.idx.seek(idx_header + start * INDEX_RECORD)
        local.idx.readinto(local.tiles)
        found = bound(local.tiles, n, limit[0], local.keys)
        if found > DENSE:
            local.f.seek(HEADER + start * core.CELLS)
            local.f.readinto(local.chunk)
            merge([(s, start + i) for s, i in scorer(local.chunk, n, k)])
            return
        view = memoryview(local.recs)
        for first in range(0, found, BATCH):
            seeds = []
            for key in local.keys[first:min(first + BATCH, found)]:
                seed = start + (key & 0xFFFFFFFF)
                if (key >> 32, seed) > cutoff[0]:
                    break
                local.f.seek(HEADER + seed * core.CELLS)
                local.f.readinto(view[len(seeds) * core.CELLS:(len(seeds) + 1) * core.CELLS])
                seeds.append(seed)
            if not seeds:
                break
            merge([(s, seeds[i]) for s, i in scorer(local.recs, len(seeds), k)])

    try:
        with ThreadPoolExecutor(threads) as pool:
            list(pool.map(scan, range(0, total, SCAN_BLOCK)))
    finally:
        for f in files:
            f.close()
    worst = biomes.MISS * sum(weights)
    return [(seed, 1 - score / worst) for score, seed in best]
