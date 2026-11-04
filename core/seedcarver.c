// Thin single-threaded core over cubiomes. Python drives threads; ctypes releases the GIL.
#include <stdint.h>
#include <string.h>
#include "biomenoise.h"
#include "biomes.h"
#include "util.h"

#define MC MC_NEWEST
#define GRID 16
#define CELLS (GRID * GRID)
#define SPAN 256 // blocks per bank cell

#ifdef _WIN32
#define API __declspec(dllexport)
#else
#define API
#endif

// Depth 0 means surface biome: y=63 would report cave biomes under every mountain.
static void sample(const BiomeNoise *bn, int x0, int z0, int step, int w, int h,
                   uint32_t flags, uint8_t *out)
{
    uint64_t dat = 0;
    for (int z = 0; z < h; z++)
        for (int x = 0; x < w; x++)
            *out++ = (uint8_t)sampleBiomeNoise(bn, NULL, (x0 + x * step + step / 2) >> 2, 0,
                                               (z0 + z * step + step / 2) >> 2, &dat, flags);
}

API void sc_gen_block(uint64_t start, int count, uint8_t *out)
{
    BiomeNoise bn;
    initBiomeNoise(&bn, MC);
    for (int i = 0; i < count; i++, out += CELLS) {
        setBiomeSeed(&bn, start + i, 0);
        sample(&bn, -GRID * SPAN / 2, -GRID * SPAN / 2, SPAN, GRID, GRID,
               SAMPLE_NO_DEPTH | SAMPLE_NO_SHIFT, out);
    }
}

API void sc_map(uint64_t seed, int x0, int z0, int step, int w, int h, uint8_t *out)
{
    BiomeNoise bn;
    initBiomeNoise(&bn, MC);
    setBiomeSeed(&bn, seed, 0);
    sample(&bn, x0, z0, step, w, h, SAMPLE_NO_DEPTH, out);
}

// Lowest-score records in a block. cost is n*256 bytes, cells holds the n painted indexes.
// Returns hits found (<= k); idx/score sorted best first.
API int sc_scan(const uint8_t *data, int count, const uint8_t *cost, const uint16_t *cells, int n,
                int k, uint32_t *idx, uint32_t *score)
{
    int found = 0;
    for (int i = 0; i < count; i++, data += CELLS) {
        uint32_t limit = found == k ? score[k - 1] : UINT32_MAX, s = 0;
        const uint8_t *c = cost;
        for (int j = 0; j < n && s < limit; j++, c += 256)
            s += c[data[cells[j]]];
        if (s >= limit)
            continue;
        int p = found < k ? found++ : k - 1;
        for (; p > 0 && score[p - 1] > s; p--) {
            score[p] = score[p - 1];
            idx[p] = idx[p - 1];
        }
        score[p] = s;
        idx[p] = i;
    }
    return found;
}

API void sc_palette(uint8_t *rgb)
{
    unsigned char c[256][3];
    initBiomeColors(c);
    memcpy(rgb, c, sizeof c);
}

API const char *sc_biome_name(int id) { return biome2str(MC, id); }
