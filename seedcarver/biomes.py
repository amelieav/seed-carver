"""Biome groups the user paints with, and how far apart they are."""
from . import core

GROUPS = {
    "Ocean": "ocean cold_ocean lukewarm_ocean warm_ocean frozen_ocean",
    "Deep ocean": "deep_ocean deep_cold_ocean deep_lukewarm_ocean deep_frozen_ocean",
    "River": "river frozen_river",
    "Plains": "plains sunflower_plains meadow",
    "Forest": "forest flower_forest birch_forest old_growth_birch_forest dark_forest pale_garden",
    "Taiga": "taiga old_growth_pine_taiga old_growth_spruce_taiga",
    "Snowy": "snowy_plains ice_spikes snowy_taiga grove",
    "Mountain": "windswept_hills windswept_forest windswept_gravelly_hills snowy_slopes stony_peaks jagged_peaks frozen_peaks",
    "Desert": "desert",
    "Savanna": "savanna savanna_plateau windswept_savanna",
    "Badlands": "badlands wooded_badlands eroded_badlands",
    "Jungle": "jungle sparse_jungle bamboo_jungle",
    "Swamp": "swamp mangrove_swamp",
    "Cherry": "cherry_grove",
    "Mushroom": "mushroom_fields",
}
# Thin features a cell's single sample lands on by chance: half cost whatever was painted.
EDGES = "river frozen_river beach snowy_beach stony_shore".split()
SIMILAR = [
    ("Ocean", "Deep ocean"), ("Plains", "Forest"), ("Plains", "Savanna"), ("Forest", "Taiga"), ("Forest", "Swamp"),
    ("Forest", "Jungle"), ("Taiga", "Snowy"), ("Snowy", "Mountain"), ("Desert", "Badlands"), ("Desert", "Savanna"),
]
MISS, NEAR, HIT = 2, 1, 0

NAMES = list(GROUPS)
_ids = {core.biome_name(i): i for i in range(256)}
MEMBERS = [[_ids[b] for b in GROUPS[g].split()] for g in NAMES]
_near = {g: {b for a, b in SIMILAR if a == g} | {a for a, b in SIMILAR if b == g} for g in NAMES}


def _cost_row(name):
    row = bytearray([MISS]) * 256
    for b in EDGES:
        row[_ids[b]] = NEAR
    for other in _near[name]:
        for i in MEMBERS[NAMES.index(other)]:
            row[i] = NEAR
    for i in MEMBERS[NAMES.index(name)]:
        row[i] = HIT
    return bytes(row)


COST = [_cost_row(g) for g in NAMES]
_palette = core.palette()
COLORS = ["#%02x%02x%02x" % _palette[m[0]] for m in MEMBERS]
