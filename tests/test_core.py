"""Run with `python -m unittest` after `python build.py`."""
import os
import tempfile
import threading
import unittest

from seedcarver import bank, biomes, core


class Core(unittest.TestCase):
    def test_known_seed(self):
        # cubiomes' documented example: seed 262 has mushroom fields at (0, 0)
        self.assertEqual(core.biome_name(core.biome_map(262, 0, 0, 4, 1, 1)[0]), "mushroom_fields")

    def test_every_generated_biome_has_a_group(self):
        buf = bytearray(4096 * core.CELLS)
        core.gen_block(0, 4096, buf)
        known = {i for m in biomes.MEMBERS for i in m}
        known |= {i for i in range(256) if core.biome_name(i) in biomes.EDGES}
        self.assertEqual({core.biome_name(b) for b in set(buf) - known}, set())

    def test_block_matches_map(self):
        buf = bytearray(core.CELLS)
        core.gen_block(7, 1, buf)
        step = 256
        full = core.biome_map(7, -core.AREA // 2, -core.AREA // 2, step, 16, 16)
        # the bank skips the +-16 block shift noise, so only near-total agreement is expected
        same = sum(a == b for a, b in zip(buf, full))
        self.assertGreater(same, 230)


class Bank(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.bank")
        self.addCleanup(self.dir.cleanup)

    def test_build_resumes_identically(self):
        stop = threading.Event()
        bank.build(self.path, 10000, stop=stop, progress=lambda d, t: stop.set(), threads=1)
        self.assertLess(bank.count(self.path), 10000)
        with open(self.path, "ab") as f:
            f.write(b"x" * 100)  # torn write from a crash
        self.assertEqual(bank.build(self.path, 10000, threads=2), 10000)
        fresh = self.path + "2"
        bank.build(fresh, 10000, threads=4)
        with open(self.path, "rb") as a, open(fresh, "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_rejects_foreign_file(self):
        with open(self.path, "wb") as f:
            f.write(b"not a bank at all")
        with self.assertRaises(ValueError):
            bank.count(self.path)

    def test_search_finds_a_seed_from_its_own_map(self):
        bank.build(self.path, 20000)
        buf = bytearray(core.CELLS)
        seed = 12345
        core.gen_block(seed, 1, buf)
        to_group = {i: g for g, m in enumerate(biomes.MEMBERS) for i in m}
        drawing = [to_group.get(b, -1) for b in buf]
        hits = bank.search(self.path, drawing, k=5)
        self.assertEqual(hits[0], (seed, 1.0))
        self.assertEqual(len(hits), 5)
        self.assertEqual(hits, sorted(hits, key=lambda h: -h[1]))

    def test_search_rejects_empty_drawing(self):
        bank.build(self.path, 10)
        with self.assertRaises(ValueError):
            bank.search(self.path, [-1] * core.CELLS)


if __name__ == "__main__":
    unittest.main()
