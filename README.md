# Seed Carver

Paint biomes on a grid, get the Minecraft Java seeds that match best, preview them. Fully offline.

Biomes come from [cubiomes](https://github.com/Cubitect/cubiomes), using its newest model (1.21.4). The app searches a local bank of seeds, each stored as a 16×16 surface biome map of the 4 km square around 0,0.

## Download

From the Releases page, get `SeedCarver-windows.zip` (exe plus 1M seeds), unzip it, run `SeedCarver.exe`. Needs the WebView2 runtime, which Windows 11 includes. The exe is unsigned, so SmartScreen may warn.

The app keeps `seeds.bank` beside the exe. Grow it with **Build** in the sidebar, or download `seeds-10M.zip` and put its `seeds.bank` there instead.

## Build from source

Needs Python 3.12 and `gcc` on PATH (on Windows: [MSYS2](https://www.msys2.org) with `mingw-w64-ucrt-x86_64-gcc`). Only Windows has been tested.

```bash
git clone --recursive https://github.com/amelieav/seed-carver
cd seed-carver
python -m venv .venv
source .venv/Scripts/activate
pip install -r requirements.txt
python build.py
python -m unittest
```

```bash
python -m seedcarver bench          # time 100k seeds on your PC
python -m seedcarver build 10000000 # build or grow the bank; safe to interrupt
python -m seedcarver                # open the app
python build.py --exe               # standalone exe in dist/
```

## Bank size

Building and searching on an i5-12600K (16 threads, 11k seeds/s):

| Seeds | Build | Disk | Search |
|---|---|---|---|
| 1M | 1.5 min | 256 MB | 0.05 s |
| 10M | 15 min | 2.6 GB | 0.4 s |
| 100M | 2.5 h (estimate) | 26 GB | 4 s (estimate) |

Best match by bank size:

| Drawing | 100k | 1M | 10M |
|---|---|---|---|
| Island: ocean, plains, forest | 57% | 60% | 61% |
| Half desert, half snow | 50% | 51% | 60% |
| 2 km cherry patch, rest ignored | 20% | 23% | 26% |

More seeds help slowly, so 10M is a good stopping point. Rare biomes and shapes the terrain can't make stay low at any size.

## FAQ

**Does building add to `seeds.bank`?**
Yes. Build appends after the last complete seed, so growing 1M to 10M generates only the 9M new seeds. Stopping or crashing loses at most the unfinished record, and the next build resumes there.

**What format is the bank?**
Raw binary: a 16-byte header, then 256 bytes per seed. Seeds run sequentially from 0, so the seed is the record's position and isn't stored. Each byte is a cubiomes biome ID, in rows north to south, each west to east:

```
offset = 16 + seed * 256 + row * 16 + column
```

Raw IDs are kept rather than groups, so groups and scoring can change without rebuilding.

**What area does a match look at?**
A fixed square, x and z from -2048 to +2048, not a radius. It is split into 16×16 cells of 256 blocks, and each cell is one surface biome sample at its centre. Your drawing is only compared at that position, never shifted or rotated. Features smaller than a cell can be missed, so judge them in the preview.

World spawn is near 0,0 but not exactly on it, and may be some way off.

**How is a match scored?**
Each painted cell scores full for the same group, half for a similar group (forest and plains) or a river or beach, and nothing otherwise. Unpainted cells are ignored. The percentage is points earned out of points possible, and the top 10 seeds are shown.

**Are sequential seeds less varied than random ones?**
No. Neighbouring seeds share no more biomes than random pairs (5.1% of cells either way).

## License

MIT. cubiomes is MIT, by Cubitect.
