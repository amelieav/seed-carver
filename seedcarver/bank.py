"""The seed bank: flat file of raw biome ids, one record of core.CELLS bytes per seed, seed = record index."""
import ctypes
import os
import threading
from array import array
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

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


INDEX_MAGIC = b"SEEDCARVERIDX3\n\0"
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


LEVELS = (16, 4, 1)  # region sides in tiles, coarse to fine: whole map, 16 regions, tiles; as core.Caps sums them


def _hit_near(g):
    rep = {}
    for i, bit in enumerate(biomes.CLASS_BIT):
        rep.setdefault(bit, i)
    rep.pop(0, None)
    hit = sum(bit for bit, i in rep.items() if biomes.COST[g][i] == biomes.HIT)
    near = sum(bit for bit, i in rep.items() if biomes.COST[g][i] == biomes.NEAR)
    return hit, near


def _bounds(cells, groups, weights, gi):
    across = core.GRID // core.TILE
    weight = dict(zip(groups, weights))
    out = []
    for side in LEVELS:
        per = across // side
        p = Counter()
        for cell, g in zip(cells, groups):
            row, col = divmod(cell, core.GRID)
            p[(row // core.TILE // side) * per + col // core.TILE // side, g] += 1
        entries = [(r, gi[g], n, weight[g]) for (r, g), n in p.items()]
        out.append(core.Bound(entries, per * per, side == 1, (biomes.HIT, biomes.NEAR, biomes.MISS)))
    return out


class _Variant:
    def __init__(self, drawing, all_brushes, info=None):
        painted = [i for i, g in enumerate(drawing) if g >= 0]
        self.drawing, self.info = drawing, info
        cells = array("H", painted)
        groups = array("H", (drawing[i] for i in painted))
        used = Counter(groups)
        # 2**24 per brush keeps the total inside uint32: MISS * 2**24 * 15 brushes < 2**32
        weights = array("I", (2**24 // used[g] if all_brushes else 1 for g in groups))
        id_mask = array("I", [0]) * 256
        for g in used:
            for i in biomes.MEMBERS[g]:
                id_mask[i] |= 1 << g
        need = sum(1 << g for g in used) if all_brushes else 0
        self.used = sorted(used)
        self.cells, self.groups, self.weights, self.all_brushes = cells, groups, weights, all_brushes
        self.scorer = core.Scorer(b"".join(biomes.COST), cells, groups, weights, need, id_mask)
        self.worst = biomes.MISS * sum(weights)


def _check(drawing):
    if len(drawing) != core.CELLS:
        raise ValueError(f"drawing must have {core.CELLS} cells")
    if all(g < 0 for g in drawing):
        raise ValueError("drawing is empty")


def search(path, drawing, k=10, threads=THREADS, all_brushes=False):
    """Best k seeds for a drawing: CELLS group indexes, -1 = unpainted. Returns [(seed, match 0-1)].

    By default every painted cell counts equally. With all_brushes every brush counts equally, however
    many cells it covers, and a seed must contain each brush somewhere in the area.
    """
    _check(drawing)
    return [(seed, match) for seed, match, _ in _search(path, [_Variant(drawing, all_brushes)], k, threads)]


ROTATIONS = (0, 90, 180, 270)
SCALES = (0.8, 1, 1.25)


def transform(drawing, degrees=0, sx=1, sy=1):
    """The drawing resized by sx across and sy down, then turned clockwise by a multiple of 90 degrees,
    both about the grid's centre."""
    half = core.GRID / 2
    out = [-1] * core.CELLS
    for r in range(core.GRID):
        for c in range(core.GRID):
            x, y = c + 0.5 - half, r + 0.5 - half
            for _ in range(degrees // 90):
                x, y = y, -x
            sr, sc = int(y / sy + half + 1) - 1, int(x / sx + half + 1) - 1
            if 0 <= sr < core.GRID and 0 <= sc < core.GRID:
                out[r * core.GRID + c] = drawing[sr * core.GRID + sc]
    return out


def search_fit(path, drawing, k=10, threads=THREADS):
    """Best k seeds for the drawing at any of ROTATIONS, resized across and down by any of SCALES, each seed once
    at its best fit. Returns [(seed, match 0-1, degrees, sx, sy)]."""
    _check(drawing)
    variants, seen = [], set()
    for sx in SCALES:
        for sy in SCALES:
            for degrees in ROTATIONS:
                moved = transform(drawing, degrees, sx, sy)
                if tuple(moved) not in seen and any(g >= 0 for g in moved):
                    seen.add(tuple(moved))
                    variants.append(_Variant(moved, False, (degrees, sx, sy)))
    return [(seed, match, *v.info) for seed, match, v in _search(path, variants, k, threads)]


def _search(path, variants, k, threads):
    """Best k distinct seeds over all variants by match. A per-tile index gives each seed a lower bound on its
    score; only seeds whose bound can still beat the current top k are read from the bank and scored exactly."""
    total = count(path)
    index(path, total, threads)
    idx_header = len(_index_header())
    used = sorted({g for v in variants for g in v.used})
    gi = {g: i for i, g in enumerate(used)}
    ng = len(used)
    hit, near = zip(*map(_hit_near, used))
    for v in variants:
        v.bounds = _bounds(v.cells, v.groups, v.weights, gi)
        v.need = sum(1 << gi[g] for g in v.used) if v.all_brushes else 0
    best = {}
    cutoff = [(Fraction(2), 0)]
    lock = threading.Lock()

    def merge(v, hits):
        with lock:
            for score, seed in hits:
                key = (Fraction(score, v.worst), seed)
                if seed not in best or key < best[seed][0]:
                    best[seed] = (key, v)
            top = sorted(best.values(), key=lambda e: e[0])[:k]
            best.clear()
            best.update((e[0][1], e) for e in top)
            if len(top) == k:
                cutoff[0] = top[-1][0]

    local = threading.local()
    files = []

    def scan(start):
        if not hasattr(local, "f"):
            local.f = open(path, "rb")
            local.idx = open(f"{path}.idx", "rb")
            files.extend((local.f, local.idx))
            local.tiles = bytearray(SCAN_BLOCK * INDEX_RECORD)
            local.recs = bytearray(BATCH * core.CELLS)
            local.chunk = bytearray(SCAN_BLOCK * core.CELLS)
            local.filter = core.Filter(SCAN_BLOCK, hit, near, [v.bounds for v in variants], [v.need for v in variants])
        n = min(SCAN_BLOCK, total - start)
        local.idx.seek(idx_header + start * INDEX_RECORD)
        local.idx.readinto(local.tiles)
        ratio = cutoff[0][0]
        kept = local.filter(local.tiles, n, [min(int(ratio * v.worst), 2**32 - 1) for v in variants])
        chunk_read = False
        view = memoryview(local.recs)
        for v, keys in zip(variants, kept):
            found = len(keys)
            if found > DENSE:
                if not chunk_read:
                    local.f.seek(HEADER + start * core.CELLS)
                    local.f.readinto(local.chunk)
                    chunk_read = True
                merge(v, [(s, start + i) for s, i in v.scorer(local.chunk, n, k)])
                continue
            for first in range(0, found, BATCH):
                seeds = []
                for key in keys[first:first + BATCH]:
                    seed = start + (key & 0xFFFFFFFF)
                    if (Fraction(key >> 32, v.worst), seed) > cutoff[0]:
                        break
                    if chunk_read:
                        view[len(seeds) * core.CELLS:(len(seeds) + 1) * core.CELLS] = \
                            local.chunk[(seed - start) * core.CELLS:(seed - start + 1) * core.CELLS]
                    else:
                        local.f.seek(HEADER + seed * core.CELLS)
                        local.f.readinto(view[len(seeds) * core.CELLS:(len(seeds) + 1) * core.CELLS])
                    seeds.append(seed)
                if not seeds:
                    break
                merge(v, [(s, seeds[i]) for s, i in v.scorer(local.recs, len(seeds), k)])

    try:
        with ThreadPoolExecutor(threads) as pool:
            list(pool.map(scan, range(0, total, SCAN_BLOCK)))
    finally:
        for f in files:
            f.close()
    return [(seed, 1 - float(ratio), v) for (ratio, seed), v in sorted(best.values(), key=lambda e: e[0])]
