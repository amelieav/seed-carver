"""ctypes binding for the compiled C core. Each call releases the GIL, so Python threads scale."""
import ctypes as C
import sys
from pathlib import Path

GRID = 64
CELLS = GRID * GRID
AREA = GRID * 16  # blocks per side of the bank's sampled square; one cell per chunk
TILE = 4
TILES = (GRID // TILE) ** 2
INDEX_RECORD = TILES * 3

_ext = {"win32": "dll", "darwin": "dylib"}.get(sys.platform, "so")
_lib = C.CDLL(str(Path(__file__).with_name(f"libcore.{_ext}")))
_lib.sc_gen_block.argtypes = [C.c_uint64, C.c_int, C.c_void_p]
_lib.sc_gen_block.restype = None
_lib.sc_map.argtypes = [C.c_uint64, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p]
_lib.sc_map.restype = None
_lib.sc_scan.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint32, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
_lib.sc_tiles.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
_lib.sc_tiles.restype = None
_lib.sc_bound.argtypes = [C.c_void_p, C.c_int] + [C.c_void_p] * 6 + [C.c_int] + [C.c_uint32] * 5 + [C.c_void_p]
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


class Bound:
    """Lower bound of a drawing's score from tile summaries.
    entries: [(tile, hit mask, near mask, 16 class costs, painted cells, weight per cell)]; levels: (hit, near, miss)."""

    def __init__(self, entries, levels, need):
        self.n = len(entries)
        tile, hit, near, level, p, w = zip(*entries) if entries else [()] * 6
        self.args = [(C.c_uint16 * self.n)(*tile), (C.c_uint16 * self.n)(*hit), (C.c_uint16 * self.n)(*near),
                     (C.c_uint8 * (16 * self.n))(*(c for row in level for c in row)), (C.c_uint8 * self.n)(*p),
                     (C.c_uint32 * self.n)(*w)]
        self.tail = [*levels, need]

    def __call__(self, buf, count, limit, keys):
        """Fill keys (c_uint64 array) with bound << 32 | index for records with bound <= limit, sorted. Returns how many."""
        return _lib.sc_bound(_ptr(buf), count, *self.args, self.n, *self.tail, limit, keys)


def palette():
    """256 (r, g, b) tuples indexed by biome id."""
    rgb = bytearray(768)
    _lib.sc_palette(_ptr(rgb))
    return [tuple(rgb[i:i + 3]) for i in range(0, 768, 3)]


def biome_name(biome_id):
    name = _lib.sc_biome_name(biome_id)
    return name.decode() if name else None
