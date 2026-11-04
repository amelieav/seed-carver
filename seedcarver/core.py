"""ctypes binding for the compiled C core. Each call releases the GIL, so Python threads scale."""
import ctypes as C
import sys
from pathlib import Path

GRID = 64
CELLS = GRID * GRID
AREA = GRID * 16  # blocks per side of the bank's sampled square; one cell per chunk

_ext = {"win32": "dll", "darwin": "dylib"}.get(sys.platform, "so")
_lib = C.CDLL(str(Path(__file__).with_name(f"core.{_ext}")))
_lib.sc_gen_block.argtypes = [C.c_uint64, C.c_int, C.c_void_p]
_lib.sc_gen_block.restype = None
_lib.sc_map.argtypes = [C.c_uint64, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p]
_lib.sc_map.restype = None
_lib.sc_scan.argtypes = [C.c_void_p, C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_uint32, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p]
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


def scan(buf, count, cost, cells, groups, weights, need, id_mask, k):
    """Best k records of a block. cost: 256 bytes per group; cells, groups: array('H'); weights: array('I').
    Records must contain every bit of need, with id_mask giving each biome id's bit. Returns [(score, index)]."""
    index = (C.c_uint32 * k)()
    score = (C.c_uint32 * k)()
    cell_ptr = (C.c_uint16 * len(cells)).from_buffer(cells)
    group_ptr = (C.c_uint16 * len(groups)).from_buffer(groups)
    weight_ptr = (C.c_uint32 * len(weights)).from_buffer(weights)
    mask_ptr = (C.c_uint32 * len(id_mask)).from_buffer(id_mask)
    n = _lib.sc_scan(_ptr(buf), count, cost, cell_ptr, group_ptr, weight_ptr, len(cells), need, mask_ptr, k, index, score)
    return [(score[i], index[i]) for i in range(n)]


def palette():
    """256 (r, g, b) tuples indexed by biome id."""
    rgb = bytearray(768)
    _lib.sc_palette(_ptr(rgb))
    return [tuple(rgb[i:i + 3]) for i in range(0, 768, 3)]


def biome_name(biome_id):
    name = _lib.sc_biome_name(biome_id)
    return name.decode() if name else None
