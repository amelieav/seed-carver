"""Run with `python -m unittest` after `python build.py`."""
import base64
import ctypes
import os
import tempfile
import threading
import unittest
from array import array
from collections import Counter

from seedcarver import bank, biomes, core


def level_bounds(tiles, count, cells, groups, weights, used, all_brushes):
    """Each level's bound for every record, computed on its own."""
    order = sorted(used)
    gi = {g: i for i, g in enumerate(order)}
    hit, near = zip(*map(bank._hit_near, order))
    caps = core.Caps(count, hit, near)
    caps.fill_whole(tiles, count)
    caps.fill(tiles, (ctypes.c_uint64 * count)(*range(count)), count)
    need = (1 << len(order)) - 1 if all_brushes else 0
    out = []
    for buf, b in zip([caps.whole, caps.regions, caps.tiles], bank._bounds(cells, groups, weights, gi)):
        keys = (ctypes.c_uint64 * count)()
        found = b(buf, len(order), None, count, need, 2**32 - 1, keys, 0)
        out.append({key & 0xFFFFFFFF: key >> 32 for key in keys[:found]})
    return out


class Core(unittest.TestCase):
    def test_known_seed(self):
        # cubiomes' documented example: seed 262 has mushroom fields at (0, 0)
        self.assertEqual(core.biome_name(core.biome_map(262, 0, 0, 4, 1, 1)[0]), "mushroom_fields")

    def test_every_generated_biome_has_a_group(self):
        buf = bytearray(500 * core.CELLS)
        core.gen_block(0, 500, buf)
        known = {i for m in biomes.MEMBERS for i in m}
        known |= {i for i in range(256) if core.biome_name(i) in biomes.EDGES}
        self.assertEqual({core.biome_name(b) for b in set(buf) - known}, set())

    def test_block_matches_map(self):
        buf = bytearray(core.CELLS)
        core.gen_block(7, 1, buf)
        half = core.AREA // 2
        self.assertEqual(buf, core.biome_map(7, -half, -half, 16, core.GRID, core.GRID))


    def test_cost_depends_only_on_class(self):
        for row in biomes.COST:
            by_class = {}
            for i, bit in enumerate(biomes.CLASS_BIT):
                if bit:
                    self.assertEqual(by_class.setdefault(bit, row[i]), row[i])


class App(unittest.TestCase):
    def test_preview_bands_join_into_the_full_map(self):
        from seedcarver.app import MAP_SIZE, MAP_STEP, Api
        got = base64.b64decode(Api().preview(5)["ids"])
        half = core.AREA // 2
        self.assertEqual(got, core.biome_map(5, -half, -half, MAP_STEP, MAP_SIZE, MAP_SIZE))


class Bank(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.bank")
        self.addCleanup(self.dir.cleanup)

    def test_build_resumes_identically(self):
        stop = threading.Event()
        bank.build(self.path, 600, stop=stop, progress=lambda d, t: stop.set(), threads=1)
        self.assertLess(bank.count(self.path), 600)
        with open(self.path, "ab") as f:
            f.write(b"x" * 100)  # torn write from a crash
        self.assertEqual(bank.build(self.path, 600, threads=2), 600)
        fresh = self.path + "2"
        bank.build(fresh, 600, threads=4)
        with open(self.path, "rb") as a, open(fresh, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_rejects_foreign_file(self):
        with open(self.path, "wb") as f:
            f.write(b"not a bank at all")
        with self.assertRaises(ValueError):
            bank.count(self.path)

    def test_search_finds_a_seed_from_its_own_map(self):
        bank.build(self.path, 1500)
        buf = bytearray(core.CELLS)
        seed = 1234
        core.gen_block(seed, 1, buf)
        to_group = {i: g for g, m in enumerate(biomes.MEMBERS) for i in m}
        drawing = [to_group.get(b, -1) for b in buf]
        hits = bank.search(self.path, drawing, k=5)
        self.assertEqual(hits[0], (seed, 1.0))
        self.assertEqual(len(hits), 5)
        self.assertEqual(hits, sorted(hits, key=lambda h: -h[1]))

    def test_all_brushes_finds_a_small_brush_that_area_ignores(self):
        forest = biomes.MEMBERS[biomes.NAMES.index("Forest")][0]
        plains = biomes.MEMBERS[biomes.NAMES.index("Plains")][0]
        cherry = biomes.MEMBERS[biomes.NAMES.index("Cherry")][0]
        all_forest = bytearray([forest]) * core.CELLS
        cherry_elsewhere = bytearray(all_forest)
        cherry_elsewhere[0] = cherry
        half_forest_cherry_in_place = bytearray([forest, plains]) * (core.CELLS // 2)
        half_forest_cherry_in_place[100] = cherry
        with open(self.path, "wb") as f:
            f.write(bank.MAGIC + all_forest + cherry_elsewhere + half_forest_cherry_in_place)
        drawing = [biomes.NAMES.index("Forest")] * core.CELLS
        drawing[100] = biomes.NAMES.index("Cherry")

        self.assertEqual([s for s, _ in bank.search(self.path, drawing, k=3)], [0, 1, 2])
        ranked = bank.search(self.path, drawing, k=3, all_brushes=True)
        self.assertEqual([s for s, _ in ranked], [2, 1])  # seed 0 has no cherry at all

    def test_search_matches_a_full_scan(self):
        import random
        bank.build(self.path, 3000)
        rng = random.Random(1)
        buf = bytearray(3000 * core.CELLS)
        with open(self.path, "rb") as f:
            f.seek(bank.HEADER)
            f.readinto(buf)
        cost = b"".join(biomes.COST)
        with open(self.path + ".idx", "rb") as f:
            f.seek(len(bank._index_header()))
            tiles = bytearray(f.read())
        for all_brushes in (False, True):
            for _ in range(4):
                drawing = [-1] * core.CELLS
                for _ in range(rng.randint(1, 5)):
                    g, r, c, size = rng.randrange(15), rng.randrange(64), rng.randrange(64), rng.randint(1, 40)
                    for i in range(r, min(r + size, 64)):
                        for j in range(c, min(c + size, 64)):
                            drawing[i * 64 + j] = g
                painted = [i for i, g in enumerate(drawing) if g >= 0]
                cells, groups = array("H", painted), array("H", (drawing[i] for i in painted))
                used = Counter(groups)
                weights = array("I", (2**24 // used[g] if all_brushes else 1 for g in groups))
                id_mask = array("I", [0]) * 256
                for g in used:
                    for i in biomes.MEMBERS[g]:
                        id_mask[i] |= 1 << g
                need = sum(1 << g for g in used) if all_brushes else 0
                want = core.scan(buf, 3000, cost, cells, groups, weights, need, id_mask, 10)
                exact = dict((i, sc) for sc, i in core.scan(buf, 3000, cost, cells, groups, weights, need, id_mask, 3000))
                for level, b in zip(bank.LEVELS, level_bounds(tiles, 3000, cells, groups, weights, used, all_brushes)):
                    self.assertEqual(set(b), set(exact), level)
                    self.assertTrue(all(b[i] <= exact[i] for i in exact), level)
                worst = biomes.MISS * sum(weights)
                self.assertEqual(bank.search(self.path, drawing, all_brushes=all_brushes),
                                 [(i, 1 - s / worst) for s, i in want])

    def test_index_catches_up_and_rebuilds(self):
        bank.build(self.path, 300)
        bank.index(self.path, 100)
        bank.index(self.path, 300)
        with open(self.path + ".idx", "rb") as f:
            grown = f.read()
        os.remove(self.path + ".idx")
        bank.index(self.path, 300)
        with open(self.path + ".idx", "rb") as f:
            self.assertEqual(f.read(), grown)

    def test_transform(self):
        drawing = list(range(core.CELLS))
        self.assertEqual(bank.transform(drawing), drawing)
        turned = drawing
        for _ in range(4):
            turned = bank.transform(turned, 90)
        self.assertEqual(turned, drawing)
        self.assertEqual(bank.transform(drawing, 90)[core.GRID - 1], 0)
        self.assertEqual(bank.transform(drawing, 0, 2, 2)[0], drawing[16 * core.GRID + 16])
        self.assertEqual(bank.transform(drawing, 0, 2, 1)[0], drawing[16])
        self.assertEqual(bank.transform(drawing, 90, 2, 1), bank.transform(bank.transform(drawing, 0, 2, 1), 90))

    def test_fit_finds_a_turned_seed_and_matches_a_full_scan(self):
        bank.build(self.path, 1500)
        buf = bytearray(1500 * core.CELLS)
        with open(self.path, "rb") as f:
            f.seek(bank.HEADER)
            f.readinto(buf)
        to_group = {i: g for g, m in enumerate(biomes.MEMBERS) for i in m}
        seed = 321
        drawing = bank.transform([to_group.get(b, -1) for b in buf[seed * core.CELLS:(seed + 1) * core.CELLS]], 90)
        hits = bank.search_fit(self.path, drawing)
        self.assertEqual(hits[0], (seed, 1.0, 270, 1, 1))
        self.assertEqual(len({h[0] for h in hits}), 10)
        want = {}
        for sx in bank.SCALES:
            for sy in bank.SCALES:
                for degrees in bank.ROTATIONS:
                    for s, m in bank.search(self.path, bank.transform(drawing, degrees, sx, sy), k=1500):
                        want[s] = max(want.get(s, 0), m)
        top = sorted(want.items(), key=lambda e: (-e[1], e[0]))[:10]
        self.assertEqual([(h[0], h[1]) for h in hits], top)

    def test_search_rejects_empty_drawing(self):
        bank.build(self.path, 10)
        with self.assertRaises(ValueError):
            bank.search(self.path, [-1] * core.CELLS)


if __name__ == "__main__":
    unittest.main()
