"""The seed bank: raw biome ids, core.CELLS bytes per seed, seeds in order from 0, zlib-compressed in blocks.

A block is a uint16 seed count n (at most BUILD_BLOCK), n uint16 compressed lengths, then the n compressed records.
"""
import bisect
import ctypes
import os
import struct
import threading
import zlib
from array import array
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from itertools import compress

from . import biomes, core

MAGIC = b"SEEDCARVER3\n\0\0\0\0"  # bump when the sampling or file layout changes
OLD_MAGIC = b"SEEDCARVER2\n\0\0\0\0"  # same sampling, uncompressed: `convert` turns it into this format
HEADER = len(MAGIC)
BUILD_BLOCK = 256
SCAN_BLOCK = 1024
BATCH = 16
DENSE = SCAN_BLOCK // 16  # more candidates than this in a block: one sequential read beats many small ones
THREADS = os.cpu_count() or 4
SEED_BYTES = 1500  # disk per seed, bank plus index, rounded up


class _Blocks:
    """Where each block of a bank starts, read once and extended as the file grows."""

    def __init__(self):
        self.pos, self.first, self.lengths = [], [], []
        self.end = HEADER
        self.seeds = 0

    def scan(self, f, size):
        while self.end + 2 <= size:
            f.seek(self.end)
            head = f.read(2 + 2 * BUILD_BLOCK)
            n = struct.unpack_from("<H", head)[0]
            if not 0 < n <= BUILD_BLOCK or len(head) < 2 + 2 * n:
                break
            lengths = array("H", head[2:2 + 2 * n])
            end = self.end + 2 + 2 * n + sum(lengths)
            if end > size:
                break
            self.pos.append(self.end + 2 + 2 * n)
            self.first.append(self.seeds)
            self.lengths.append(lengths)
            self.seeds += n
            self.end = end

    def locate(self, seed):
        """(file position, compressed length) of a seed's record."""
        b = bisect.bisect_right(self.first, seed) - 1
        j = seed - self.first[b]
        lengths = self.lengths[b]
        return self.pos[b] + sum(lengths[:j]), lengths[j]


_blocks = {}
_blocks_lock = threading.Lock()


def _layout(path):
    """The bank's blocks, rescanned from the start if the file shrank or was replaced."""
    path = os.fspath(path)
    with open(path, "rb") as f:
        head = f.read(HEADER)
        if head == OLD_MAGIC:
            raise ValueError(f"{path} is from the previous version. Run `python -m seedcarver convert` to shrink it "
                             "to the new format, about a seventh of the size.")
        if head != MAGIC:
            raise ValueError(f"{path} is from another version. Delete or move it to build a new bank.")
        stat = os.fstat(f.fileno())
        with _blocks_lock:
            blocks = _blocks.get(path)
            if not blocks or blocks.end > stat.st_size or blocks.ino != (stat.st_ino, stat.st_dev):
                blocks = _blocks[path] = _Blocks()
                blocks.ino = (stat.st_ino, stat.st_dev)
            blocks.scan(f, stat.st_size)
            return blocks


def count(path):
    """Seeds stored in the bank; 0 if absent. Raises ValueError for a file that isn't a bank."""
    try:
        return _layout(path).seeds
    except FileNotFoundError:
        return 0


def pack(raw, level=6):
    """One block from up to BUILD_BLOCK raw records (bytes, a multiple of core.CELLS)."""
    parts = [zlib.compress(raw[i:i + core.CELLS], level) for i in range(0, len(raw), core.CELLS)]
    return struct.pack(f"<H{len(parts)}H", len(parts), *map(len, parts)) + b"".join(parts)


class Reader:
    """Reads raw records from a bank. Not thread safe: one per thread."""

    def __init__(self, path):
        self.f = open(path, "rb")
        self.blocks = _layout(path)

    def close(self):
        self.f.close()

    def one(self, seed, out):
        """Decompress one seed's record into out (a writable buffer of core.CELLS bytes)."""
        pos, n = self.blocks.locate(seed)
        self.f.seek(pos)
        out[:] = zlib.decompress(self.f.read(n))

    def range(self, start, count, out):
        """Decompress seeds start..start+count into out (count * core.CELLS bytes)."""
        blocks, view, done = self.blocks, memoryview(out), 0
        b = bisect.bisect_right(blocks.first, start) - 1
        while done < count:
            j = start + done - blocks.first[b]
            lengths = blocks.lengths[b]
            take = min(len(lengths) - j, count - done)
            pos = blocks.pos[b] + sum(lengths[:j])
            self.f.seek(pos)
            data = memoryview(self.f.read(sum(lengths[j:j + take])))
            at = 0
            for n in lengths[j:j + take]:
                view[done * core.CELLS:(done + 1) * core.CELLS] = zlib.decompress(data[at:at + n])
                at += n
                done += 1
            b += 1


def read(path, start, count):
    """Raw records of seeds start..start+count as one bytearray."""
    out = bytearray(count * core.CELLS)
    r = Reader(path)
    try:
        r.range(start, count, out)
    finally:
        r.close()
    return out


def build(path, target, progress=None, stop=None, threads=THREADS):
    """Grow the bank to `target` seeds. Safe to interrupt: resumes after the last whole block."""
    done = count(path)
    end = _layout(path).end if done else HEADER

    def gen(start):
        n = min(BUILD_BLOCK, target - start)
        buf = bytearray(n * core.CELLS)
        core.gen_block(start, n, buf)
        return n, pack(buf)

    with open(path, "r+b" if os.path.exists(path) else "wb") as f, ThreadPoolExecutor(threads) as pool:
        f.seek(0)
        f.write(MAGIC)
        f.seek(end)
        f.truncate()
        while done < target and not (stop and stop.is_set()):
            starts = range(done, min(done + threads * BUILD_BLOCK, target), BUILD_BLOCK)
            for n, block in pool.map(gen, starts):
                f.write(block)
                done += n
            f.flush()
            if progress:
                progress(done, target)
    index(path, done, threads)
    return done


def convert(path, progress=None, threads=THREADS):
    """Rewrite a previous-version (uncompressed) bank in this format, in place. The index stays valid."""
    with open(path, "rb") as f:
        if f.read(HEADER) != OLD_MAGIC:
            raise ValueError(f"{path} is not a previous-version bank")
    total = (os.path.getsize(path) - HEADER) // core.CELLS
    tmp = f"{path}.converting"
    local = threading.local()
    files = []

    def block(start):
        if not hasattr(local, "f"):
            local.f = open(path, "rb")
            files.append(local.f)
        n = min(BUILD_BLOCK, total - start)
        local.f.seek(HEADER + start * core.CELLS)
        return n, pack(local.f.read(n * core.CELLS))

    done = 0
    try:
        with open(tmp, "wb") as out, ThreadPoolExecutor(threads) as pool:
            out.write(MAGIC)
            for n, data in pool.map(block, range(0, total, BUILD_BLOCK)):
                out.write(data)
                done += n
                if progress and (done % (BUILD_BLOCK * 64) < BUILD_BLOCK or done == total):
                    progress(done, total)
    finally:
        for g in files:
            g.close()
    os.replace(tmp, path)
    return total


INDEX_MAGIC = b"SEEDCARVERIDX4\n\0"
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
                local.f = Reader(path)
                files.append(local.f)
                local.buf = bytearray(SCAN_BLOCK * core.CELLS)
            n = min(SCAN_BLOCK, total - start)
            local.f.range(start, n, local.buf)
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




def _hit_near(g):
    rep = {}
    for i, bit in enumerate(biomes.CLASS_BIT):
        rep.setdefault(bit, i)
    rep.pop(0, None)
    hit = sum(bit for bit, i in rep.items() if biomes.COST[g][i] == biomes.HIT)
    near = sum(bit for bit, i in rep.items() if biomes.COST[g][i] == biomes.NEAR)
    return hit, near


_ACROSS = core.GRID // core.TILE
_TILE_OF = [(cell // core.GRID // core.TILE) * _ACROSS + cell % core.GRID // core.TILE for cell in range(core.CELLS)]
_REGION_OF = [(t // _ACROSS // 4) * 4 + t % _ACROSS // 4 for t in range(core.TILES)]
_OUTSIDE_CENTRE = [int(not (4 <= t // _ACROSS < 12 and 4 <= t % _ACROSS < 12)) for t in range(core.TILES)]
_COST = b"".join(biomes.COST)


def _bounds(cells, groups, weights, gi):
    weight = dict(zip(groups, weights))
    tiles = Counter(zip(map(_TILE_OF.__getitem__, cells), groups))
    regions, whole = Counter(), Counter()
    for (t, g), n in tiles.items():
        regions[_REGION_OF[t], g] += n
        whole[_OUTSIDE_CENTRE[t], g] += n
    levels = (biomes.HIT, biomes.NEAR, biomes.MISS)
    # Heaviest entries first, so a seed's bound passes the limit, and stops being summed, sooner.
    return [core.Bound(sorted(((r, gi[g], n, weight[g]) for (r, g), n in counts.items()), key=lambda e: -e[2] * e[3]),
                       nreg, nreg == core.TILES, levels)
            for counts, nreg in ((whole, 2), (regions, 16), (tiles, core.TILES))]


class _Variant:
    def __init__(self, drawing, all_brushes, info=None):
        cells = array("H", compress(range(core.CELLS), map((-1).__lt__, drawing)))
        self.drawing, self.info = drawing, info
        groups = array("H", map(drawing.__getitem__, cells))
        used = Counter(groups)
        # 2**24 per brush keeps the total inside uint32: MISS * 2**24 * 15 brushes < 2**32
        weight = {g: 2**24 // n if all_brushes else 1 for g, n in used.items()}
        weights = array("I", map(weight.__getitem__, groups))
        id_mask = array("I", [0]) * 256
        for g in used:
            for i in biomes.MEMBERS[g]:
                id_mask[i] |= 1 << g
        need = sum(1 << g for g in used) if all_brushes else 0
        self.used = sorted(used)
        self.cells, self.groups, self.weights, self.all_brushes = cells, groups, weights, all_brushes
        self.scorer = core.Scorer(_COST, cells, groups, weights, need, id_mask)
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


ANGLES = range(0, 360, 15)
ZOOMS = (0.8, 1, 1.25, 1.6, 2)  # the preview zoomed in by this much: the drawing covers 1 / zoom of the area
STRETCHES = ((1, 1), (1.2, 1), (1, 1.2))  # the drawing stretched across, down


def transform(drawing, degrees=0, sx=1, sy=1):
    """The drawing resized by sx across and sy down, then turned clockwise by degrees, both about the grid's centre."""
    return core.transform(drawing, degrees, sx, sy)


def search_fit(path, drawing, k=10, threads=THREADS):
    """Best k seeds for the drawing turned by any of ANGLES, zoomed by any of ZOOMS and stretched by any of
    STRETCHES, each seed once at its best fit. Scored as with all_brushes: each brush counts equally and a seed must
    contain every brush. A fit that pushes a brush out of the area is skipped.
    Returns [(seed, match 0-1, degrees, zoom, stretch across, stretch down)]."""
    _check(drawing)
    brushes = {g for g in drawing if g >= 0}
    variants, seen = [], set()
    for zoom in ZOOMS:
        for kx, ky in STRETCHES:
            for degrees in ANGLES:
                moved = transform(drawing, degrees, kx / zoom, ky / zoom)
                if tuple(moved) not in seen and {g for g in moved if g >= 0} == brushes:
                    seen.add(tuple(moved))
                    variants.append(_Variant(moved, True, (degrees, zoom, kx, ky)))
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
            local.f = Reader(path)
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
                    local.f.range(start, n, local.chunk)
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
                        local.f.one(seed, view[len(seeds) * core.CELLS:(len(seeds) + 1) * core.CELLS])
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
