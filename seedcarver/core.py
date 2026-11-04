"""ctypes binding for the compiled C core. Each call releases the GIL, so Python threads scale."""
import ctypes as C
import sys
from pathlib import Path

GRID = 64
CELLS = GRID * GRID
AREA = GRID * 16  # blocks per side of the bank's sampled square; one cell per chunk
TILE = 4
TILES = (GRID // TILE) ** 2
INDEX_RECORD = TILES * 3 + 32

_ext = {"win32": "dll", "darwin": "dylib"}.get(sys.platform, "so")
_lib = C.CDLL(str(Path(__file__).with_name(f"libcore.{_ext}")))
_lib.sc_gen_block.argtypes = [C.c_uint64, C.c_int, C.c_void_p]
_lib.sc_gen_block.restype = None
_lib.sc_map.argtypes = [C.c_uint64, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p]
_lib.sc_map.restype = None
_lib.sc_scan.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint32, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
_lib.sc_tiles.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
_lib.sc_tiles.restype = None
_lib.sc_whole.argtypes = [C.c_void_p, C.c_int, C.c_int] + [C.c_void_p] * 3
_lib.sc_whole.restype = None
_lib.sc_caps.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_int] + [C.c_void_p] * 4
_lib.sc_caps.restype = None
_lib.sc_bound.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_int] + [C.c_void_p] * 4 + \
    [C.c_int] + [C.c_uint32] * 5 + [C.c_void_p, C.c_int]
_lib.sc_filter.argtypes = [C.c_void_p, C.c_int, C.c_int] + [C.c_void_p] * 7 + [C.c_int] + [C.c_void_p] * 3
_lib.sc_filter.restype = None
_lib.sc_palette.argtypes = [C.c_void_p]
_lib.sc_palette.restype = None
_lib.sc_biome_name.argtypes = [C.c_int]
_lib.sc_biome_name.restype = C.c_char_p


def _ptr(buf):
    return (C.c_char * len(buf)).from_buffer(buf)


def gen_block(start, count, buf):
    """Fill buf (bytearray of count*CELLS) with raw biome ids for seeds start..start+count."""
    _lib.sc_gen_block(start, count, _ptr(buf))


def biome_map(seed, x0, z0, step, w, h):
    """Surface biome ids for a w*h window with `step` blocks per pixel."""
    out = bytearray(w * h)
    _lib.sc_map(seed, x0, z0, step, w, h, _ptr(out))
    return out


class Scorer:
    """Exact scores of records. cost: 256 bytes per group; cells, groups: array('H'); weights, id_mask: array('I').
    Records must contain every bit of need, with id_mask giving each biome id's bit."""

    def __init__(self, cost, cells, groups, weights, need, id_mask):
        self.n, self.need, self.cost = len(cells), need, cost
        self.cells = (C.c_uint16 * self.n)(*cells)
        self.groups = (C.c_uint16 * self.n)(*groups)
        self.weights = (C.c_uint32 * self.n)(*weights)
        self.id_mask = (C.c_uint32 * 256)(*id_mask)

    def __call__(self, buf, count, k):
        """Best k records of a block, as [(score, index)]."""
        index = (C.c_uint32 * k)()
        score = (C.c_uint32 * k)()
        n = _lib.sc_scan(_ptr(buf), count, self.cost, self.cells, self.groups, self.weights, self.n, self.need,
                         self.id_mask, k, index, score)
        return [(score[i], index[i]) for i in range(n)]


def scan(buf, count, cost, cells, groups, weights, need, id_mask, k):
    return Scorer(cost, cells, groups, weights, need, id_mask)(buf, count, k)


def tiles(buf, count, class_bit, out):
    """Fill out (bytearray of count*INDEX_RECORD) with each record's tile summary. class_bit: array('H') of 256."""
    _lib.sc_tiles(_ptr(buf), count, (C.c_uint16 * 256).from_buffer(class_bit), _ptr(out))


class Caps:
    """Per record and group, upper bounds on cells that could hit and that could hit or be near: over the whole map,
    over 16 regions of 4x4 tiles, and per tile. hit, near: one class mask per group."""

    def __init__(self, count, hit, near):
        self.ng = len(hit)
        self.hit, self.near = (C.c_uint16 * self.ng)(*hit), (C.c_uint16 * self.ng)(*near)
        self.whole = bytearray(count * self.ng * 4)
        self.regions = bytearray(count * self.ng * 16 * 4)
        self.tiles = bytearray(count * self.ng * TILES * 2)

    def fill_whole(self, buf, count):
        _lib.sc_whole(_ptr(buf), count, self.ng, self.hit, self.near, _ptr(self.whole))

    def fill(self, buf, keys, count):
        """Regions and tiles for the records listed in keys (c_uint64 array, index in the low 32 bits)."""
        _lib.sc_caps(_ptr(buf), keys, count, self.ng, self.hit, self.near, _ptr(self.tiles), _ptr(self.regions))


class Bound:
    """Lower bound of a drawing's score from caps over nreg regions. entries: [(region, group index, cells, weight)]."""

    def __init__(self, entries, nreg, narrow, levels):
        self.n, self.nreg, self.narrow, self.levels = len(entries), nreg, narrow, levels
        reg, gi, p, w = zip(*entries)
        self.args = [(C.c_uint16 * self.n)(*reg), (C.c_uint8 * self.n)(*gi), (C.c_uint16 * self.n)(*p),
                     (C.c_uint32 * self.n)(*w)]

    def __call__(self, caps_buf, ng, keys_in, count, need, limit, keys, sort):
        """Write keys bound << 32 | index for records (from keys_in, or 0..count-1 if None) with bound <= limit."""
        return _lib.sc_bound(_ptr(caps_buf), self.narrow, self.nreg, ng, keys_in, count, *self.args, self.n,
                             *self.levels, need, limit, keys, sort)


class _Level(C.Structure):
    _fields_ = [("n", C.c_int), ("reg", C.c_void_p), ("gi", C.c_void_p), ("p", C.c_void_p), ("w", C.c_void_p)]


class _Variant(C.Structure):
    _fields_ = [("level", _Level * 3), ("need", C.c_uint32), ("limit", C.c_uint32)]


class Filter:
    """Records of a block that each variant's bounds keep, whole map to tiles, in one call.
    bounds: per variant, a Bound for each of the whole map, 16 regions and tiles; needs: per variant."""

    def __init__(self, count, hit, near, bounds, needs):
        self.count, self.nv = count, len(bounds)
        self.caps = Caps(count, hit, near)
        self.bounds = bounds
        self.vs = (_Variant * self.nv)()
        for v, (levels, need) in enumerate(zip(bounds, needs)):
            self.vs[v].need = need
            for i, b in enumerate(levels):
                self.vs[v].level[i] = _Level(b.n, *(C.cast(a, C.c_void_p) for a in b.args))
        self.lv = (C.c_uint32 * 3)(*bounds[0][0].levels)
        self.mark = bytearray(count)
        self.keys = (C.c_uint64 * (self.nv * count))()
        self.found = (C.c_int * self.nv)()

    def __call__(self, buf, count, limits):
        """Returns per variant the kept keys, bound << 32 | index, sorted."""
        for v, limit in enumerate(limits):
            self.vs[v].limit = limit
        c = self.caps
        _lib.sc_filter(_ptr(buf), count, c.ng, c.hit, c.near, self.lv, _ptr(c.whole), _ptr(c.regions), _ptr(c.tiles),
                       _ptr(self.mark), self.nv, self.vs, self.keys, self.found)
        return [self.keys[v * count:v * count + self.found[v]] for v in range(self.nv)]


def palette():
    """256 (r, g, b) tuples indexed by biome id."""
    rgb = bytearray(768)
    _lib.sc_palette(_ptr(rgb))
    return [tuple(rgb[i:i + 3]) for i in range(0, 768, 3)]


def biome_name(biome_id):
    name = _lib.sc_biome_name(biome_id)
    return name.decode() if name else None
