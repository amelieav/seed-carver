// Thin single-threaded core over cubiomes. Python drives threads; ctypes releases the GIL.
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "biomenoise.h"
#include "biomes.h"
#include "util.h"

#define MC MC_NEWEST
#define GRID 64
#define CELLS (GRID * GRID)
#define SPAN 16 // blocks per bank cell: one chunk

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
               SAMPLE_NO_DEPTH, out);
    }
}

API void sc_map(uint64_t seed, int x0, int z0, int step, int w, int h, uint8_t *out)
{
    BiomeNoise bn;
    initBiomeNoise(&bn, MC);
    setBiomeSeed(&bn, seed, 0);
    sample(&bn, x0, z0, step, w, h, SAMPLE_NO_DEPTH, out);
}

// Lowest-score records in a block. cost is 256 bytes per group; cells, groups and weights list the n painted cells.
// A record must also contain every bit of need, where id_mask maps each biome id to its bit (need 0 skips this).
// Returns hits found (<= k); idx/score sorted best first.
API int sc_scan(const uint8_t *data, int count, const uint8_t *cost, const uint16_t *cells,
                const uint16_t *groups, const uint32_t *weights, int n, uint32_t need,
                const uint32_t *id_mask, int k, uint32_t *idx, uint32_t *score)
{
    int found = 0;
    for (int i = 0; i < count; i++, data += CELLS) {
        uint32_t limit = found == k ? score[k - 1] : UINT32_MAX, s = 0;
        for (int j = 0; j < n && s < limit; j++)
            s += cost[groups[j] * 256 + data[cells[j]]] * weights[j];
        if (s >= limit)
            continue;
        uint32_t seen = 0;
        for (int c = 0; need && c < CELLS && (seen & need) != need; c++)
            seen |= id_mask[data[c]];
        if ((seen & need) != need)
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

#define TILE 4
#define TILES ((GRID / TILE) * (GRID / TILE))

// Per record: TILES class masks (the OR of class_bit over each 4x4 tile), then TILES bytes giving each tile's
// commonest class (low 4 bits) and how many of its 16 cells hold it, minus one (high 4 bits).
API void sc_tiles(const uint8_t *data, int count, const uint16_t *class_bit, uint8_t *out)
{
    const int side = GRID / TILE;
    for (int i = 0; i < count; i++, data += CELLS, out += TILES * 3) {
        uint16_t *mask = (uint16_t *)out;
        uint8_t *dom = out + TILES * 2;
        for (int t = 0; t < TILES; t++) {
            int n[16] = {0}, best = 0;
            uint16_t m = 0;
            for (int c = 0; c < TILE * TILE; c++) {
                uint16_t bit = class_bit[data[(t / side * TILE + c / TILE) * GRID + t % side * TILE + c % TILE]];
                m |= bit;
                if (bit)
                    n[__builtin_ctz(bit)]++;
            }
            for (int k = 1; k < 16; k++)
                if (n[k] > n[best])
                    best = k;
            mask[t] = m;
            dom[t] = (uint8_t)(best | (n[best] ? n[best] - 1 : 0) << 4);
        }
    }
}

static int cmp_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

// Lower bound of each record's score from its tiles. Each of the n entries is one painted group in one tile, with
// p painted cells of weight w each. level holds 16 bytes per entry, the cost of each class for its group; hit and
// near are the classes costing lv_hit and lv_near. In a tile whose commonest class covers c cells, at least
// p - (16 - c) painted cells fall on that class, and the rest cost no less than the best class present.
// Writes records whose bound <= limit and that hold every class of need as keys bound << 32 | index, sorted.
API int sc_bound(const uint8_t *tiles, int count, const uint16_t *tile, const uint16_t *hit, const uint16_t *near,
                 const uint8_t *level, const uint8_t *p, const uint32_t *w, int n, uint32_t lv_hit, uint32_t lv_near,
                 uint32_t lv_miss, uint32_t need, uint32_t limit, uint64_t *keys)
{
    int found = 0;
    for (int i = 0; i < count; i++, tiles += TILES * 3) {
        const uint16_t *mask = (const uint16_t *)tiles;
        const uint8_t *dom = tiles + TILES * 2;
        if (need) {
            uint32_t seen = 0;
            for (int t = 0; t < TILES; t++)
                seen |= mask[t];
            if ((seen & need) != need)
                continue;
        }
        uint32_t s = 0;
        for (int j = 0; j < n && s <= limit; j++) {
            uint16_t m = mask[tile[j]];
            uint32_t best = m & hit[j] ? lv_hit : m & near[j] ? lv_near : lv_miss;
            if (!m) {
                s += lv_miss * p[j] * w[j];
                continue;
            }
            int d = dom[tile[j]] & 15, c = (dom[tile[j]] >> 4) + 1, x = p[j] - (TILE * TILE - c);
            uint32_t ld = level[j * 16 + d];
            if (x > 0)
                s += (x * ld + (p[j] - x) * best) * w[j];
            else
                s += p[j] * best * w[j];
        }
        if (s <= limit)
            keys[found++] = (uint64_t)s << 32 | (uint32_t)i;
    }
    qsort(keys, found, sizeof *keys, cmp_u64);
    return found;
}

API void sc_palette(uint8_t *rgb)
{
    unsigned char c[256][3];
    initBiomeColors(c);
    memcpy(rgb, c, sizeof c);
}

API const char *sc_biome_name(int id) { return biome2str(MC, id); }
