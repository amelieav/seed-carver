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

## Matching

A cell scores full for the same group, half for a similar group (forest and plains) or a river or beach, and nothing otherwise. Unpainted cells are ignored. Each bank cell is one sample per 256 blocks, so use the preview to judge small features.

Seeds are sequential from 0. Neighbouring seeds share no more biomes than random pairs (5.1% of cells either way).

## License

MIT. cubiomes is MIT, by Cubitect.
