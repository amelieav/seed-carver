<img width="1409" height="836" alt="Screenshot 2026-11-04 151150" src="https://github.com/user-attachments/assets/cac55c80-885e-4132-b3aa-618bf364ba04" />

# Seed Carver

Paint your ideal biome layout, and get matched with the seed closest to it.

Biomes come from [cubiomes](https://github.com/Cubitect/cubiomes), using its newest model (1.21.4). The app searches a local bank of seeds, each stored as a surface biome map of the 1 km square around 0,0, with one 64×64 grid cell per chunk.

## Download

From the Releases page, get `SeedCarver-windows.zip` (exe plus 1M seeds), unzip it, run `SeedCarver.exe`. Needs the WebView2 runtime, which Windows 11 includes. The exe is unsigned, so SmartScreen may warn.

The app keeps `seeds.bank` beside the exe. Grow it with **Build** in the sidebar - the mode seeds in the bank the higher the chance of a good match with your custom drawing.

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
python -m seedcarver bench          # time 10k seeds on your PC
python -m seedcarver build 1000000  # build or grow the bank; safe to interrupt
python -m seedcarver                # open the app
python build.py --exe               # standalone exe in dist/ (close the app first)
```

## Bank size

Each seed takes 4,096 bytes. On an i5-12600K (16 threads), about 1,000 seeds/s:

| Seeds | Build | Disk | Search |
|---|---|---|---|
| 100k | 2 min | 0.4 GB | 0.2 s (estimate) |
| 1M | 17 min | 4.1 GB | 0.6-1.7 s |
| 10M | 2.8 h (estimate) | 41 GB | 6-17 s (estimate) |

Best match by bank size:

| Drawing | 10k | 100k | 1M |
|---|---|---|---|
| Island: ocean, plains, forest | 60% | 64% | 68% |
| Half desert, half snow | 54% | 54% | 55% |
| 500-block cherry patch, rest ignored | 41% | 54% | 66% |

Start with 1M. Rare biomes improve most with more seeds, and shapes the terrain can't make stay low at any size.

## FAQ

**Does building add to `seeds.bank`?**
Yes. Build appends after the last complete seed, so growing 1M to 5M generates only the 4M new seeds. Stopping or crashing loses at most the unfinished record, and the next build resumes there.

**What area does a match look at?**
A fixed square, x and z from -512 to +512. It is split into 64×64 cells lined up with chunks, and each cell is one surface biome sample at its centre. Your drawing is only compared at that position, never shifted or rotated.

World spawn is near 0,0 but not exactly on it, and may be some way off.

**How is a match scored?**
Each painted cell scores full for the same group, half for a similar group (forest and plains) or a river or beach, and nothing otherwise. Unpainted cells are ignored. The percentage is points earned out of points possible, and the top 10 seeds are shown.

**What do the match modes do?**
**Area** counts every painted cell equally, so a small brush barely affects the result. **All brushes** counts every brush equally however many cells it covers, and only shows seeds containing each brush somewhere in the area. Use it when a small patch, like cherry in a forest, must be present. **Best fit** scores like Area, but also tries the drawing turned by 90°, 180° and 270° and stretched to 0.8 or 1.25 of its width and height, each about the centre, and lists each seed at its best fit. Use it when the shape matters more than where it sits. The preview turns to line up with your drawing, and **Show drawing** lays your drawing over it stretched as it was matched.

**How well do painted rivers match?**
Closely, but rarely exactly. On a 1M bank, top results for a painted line had a river within 16 blocks of 70-79% of its cells, against about 20% for random seeds.

**Where does the app look for the bank?**
Only for a file named exactly `seeds.bank`, directly beside the exe. Subfolders and other names are ignored.

**What is `seeds.bank.idx`?**
A search index built from the bank: for each seed, which biome groups appear in each 4×4-cell tile and which is commonest, plus an upper bound on each group's cells (800 bytes per seed, under a fifth of the bank). A search first works out every seed's best possible match from the index, coarse to fine (whole map, 16 regions, tiles), then reads and scores exactly only the seeds that could still make the top 10, so most of the bank is never read. Results are identical to a full scan. It is created and kept up to date automatically, and rebuilt if the biome groups change. Deleting it is safe.

**Are new seeds searchable while the bank grows?**
Yes. Every search re-reads the bank size, so it includes all seeds written so far. Results refresh on your next brush stroke and when a build ends.

**Can banks be combined?**
No need. Every bank starts at seed 0 and generation is deterministic, so the largest bank already contains any smaller one of the same version. Banks from another version can't be used or converted.

**Are sequential seeds less varied than random ones?**
No. Neighbouring seeds share no more biomes than random pairs (5.1% of cells either way).

## License

MIT. cubiomes is MIT, by Cubitect.
