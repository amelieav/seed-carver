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

#define RECORD (TILES * 3 + 32)

// Per record: TILES class masks (the OR of class_bit over each 4x4 tile); TILES bytes giving each tile's commonest
// class (low 4 bits) and how many of its 16 cells hold it, minus one (high 4 bits); then per class a uint16 upper
// bound on its cells in the whole map: the commonest class's count where it is commonest, else 16 minus that.
API void sc_tiles(const uint8_t *data, int count, const uint16_t *class_bit, uint8_t *out)
{
    const int side = GRID / TILE;
    for (int i = 0; i < count; i++, data += CELLS, out += RECORD) {
        uint16_t *mask = (uint16_t *)out, *most = (uint16_t *)(out + TILES * 3);
        uint8_t *dom = out + TILES * 2;
        memset(most, 0, 32);
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
            for (int k = 0; k < 16; k++)
                if (m >> k & 1)
                    most[k] += k == best ? n[best] : TILE * TILE - n[best];
        }
    }
}

// Per record and group: upper bounds on the map's cells that could hit, and that could hit or be near (uint16 pair).
API void sc_whole(const uint8_t *tiles, int count, int ng, const uint16_t *hit, const uint16_t *near, uint16_t *out)
{
    for (int i = 0; i < count; i++, tiles += RECORD) {
        const uint16_t *most = (const uint16_t *)(tiles + TILES * 3);
        for (int g = 0; g < ng; g++, out += 2) {
            uint32_t a = 0, b = 0;
            for (int k = 0; k < 16; k++) {
                a += hit[g] >> k & 1 ? most[k] : 0;
                b += (hit[g] | near[g]) >> k & 1 ? most[k] : 0;
            }
            out[0] = (uint16_t)(a < CELLS ? a : CELLS);
            out[1] = (uint16_t)(b < CELLS ? b : CELLS);
        }
    }
}

static uint8_t cap(uint16_t m, uint16_t d, int c, uint16_t want)
{
    uint16_t mw = m & want;
    return !mw ? 0 : !(d & want) ? (uint8_t)(TILE * TILE - c) : mw & ~d ? TILE * TILE : (uint8_t)c;
}

// One record's per group and tile upper bounds on cells that could hit, and that could hit or be near (2 bytes).
static void tile_caps(const uint8_t *rec, int ng, const uint16_t *hit, const uint16_t *near, uint8_t *cp)
{
    const uint16_t *mask = (const uint16_t *)rec;
    const uint8_t *dom = rec + TILES * 2;
    for (int g = 0; g < ng; g++, cp += TILES * 2)
        for (int t = 0; t < TILES; t++) {
            uint16_t d = (uint16_t)(1 << (dom[t] & 15));
            int c = (dom[t] >> 4) + 1;
            cp[t * 2] = cap(mask[t], d, c, hit[g]);
            cp[t * 2 + 1] = cap(mask[t], d, c, hit[g] | near[g]);
        }
}

// One record's per group upper bounds over 16 regions of 4x4 tiles (uint16 pairs), from per class bounds: a tile
// holds at most c cells of its commonest class and 16 - c of any other.
static void region_caps(const uint8_t *rec, int ng, const uint16_t *hit, const uint16_t *near, uint16_t *rp)
{
    const uint16_t *mask = (const uint16_t *)rec;
    const uint8_t *dom = rec + TILES * 2;
    uint16_t most[16][16] = {{0}};
    for (int t = 0; t < TILES; t++) {
        int r = (t >> 6) * 4 + ((t & 15) >> 2), best = dom[t] & 15, c = (dom[t] >> 4) + 1;
        for (uint32_t m = mask[t]; m; m &= m - 1) {
            int k = __builtin_ctz(m);
            most[r][k] += (uint16_t)(k == best ? c : TILE * TILE - c);
        }
    }
    for (int g = 0; g < ng; g++, rp += 32) {
        uint16_t good = hit[g] | near[g];
        for (int r = 0; r < 16; r++) {
            uint32_t a = 0, b = 0;
            for (int k = 0; k < 16; k++) {
                a += hit[g] >> k & 1 ? most[r][k] : 0;
                b += good >> k & 1 ? most[r][k] : 0;
            }
            rp[r * 2] = (uint16_t)(a < 256 ? a : 256);
            rp[r * 2 + 1] = (uint16_t)(b < 256 ? b : 256);
        }
    }
}

// For the records listed in in (index in the low 32 bits): per group, tile caps (2 bytes per tile) and region caps
// (uint16 pairs per region). Outputs are laid out by record index, so records not listed are left as they were.
API void sc_caps(const uint8_t *tiles, const uint64_t *in, int count, int ng, const uint16_t *hit,
                 const uint16_t *near, uint8_t *caps, uint16_t *regions)
{
    for (int k = 0; k < count; k++) {
        uint32_t i = (uint32_t)in[k];
        tile_caps(tiles + (size_t)i * RECORD, ng, hit, near, caps + (size_t)i * ng * TILES * 2);
        region_caps(tiles + (size_t)i * RECORD, ng, hit, near, regions + (size_t)i * ng * 32);
    }
}

static int cmp_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

// Lower bound of each record's score from caps over nreg regions (uint8 caps when narrow, else uint16).
// Each of the n entries puts p painted cells of weight w, of group index gi, in one region: they fill the cells
// that could hit first, then those that could be near, and the rest miss. Records come from in (count of them) or,
// when in is NULL, are 0..count-1. Records whose bound <= limit, and whose caps show every group index in need,
// are written as keys bound << 32 | index, sorted if asked. Returns how many.
API int sc_bound(const void *caps, int narrow, int nreg, int ng, const uint64_t *in, int count, const uint16_t *reg,
                 const uint8_t *gi, const uint16_t *p, const uint32_t *w, int n, uint32_t lv_hit, uint32_t lv_near,
                 uint32_t lv_miss, uint32_t need, uint32_t limit, uint64_t *keys, int sort)
{
    int found = 0;
    const int stride = ng * nreg * 2;
    for (int k = 0; k < count; k++) {
        uint32_t i = in ? (uint32_t)in[k] : (uint32_t)k;
        const uint8_t *c8 = (const uint8_t *)caps + (size_t)i * stride;
        const uint16_t *c16 = (const uint16_t *)caps + (size_t)i * stride;
        int ok = 1;
        for (int g = 0; need && g < ng && ok; g++) {
            if (!(need >> g & 1))
                continue;
            uint32_t any = 0;
            for (int r = 0; r < nreg; r++)
                any += narrow ? c8[(g * nreg + r) * 2] : c16[(g * nreg + r) * 2];
            ok = any > 0;
        }
        if (!ok)
            continue;
        uint32_t s = 0;
        for (int j = 0; j < n && s <= limit; j++) {
            int at = (gi[j] * nreg + reg[j]) * 2;
            uint32_t a = narrow ? c8[at] : c16[at], b = narrow ? c8[at + 1] : c16[at + 1];
            uint32_t h = p[j] < a ? p[j] : a, nn = p[j] - h < b - a ? p[j] - h : b - a;
            s += (h * lv_hit + nn * lv_near + (p[j] - h - nn) * lv_miss) * w[j];
        }
        if (s <= limit)
            keys[found++] = (uint64_t)s << 32 | i;
    }
    if (sort)
        qsort(keys, found, sizeof *keys, cmp_u64);
    return found;
}

typedef struct {
    int n;
    const uint16_t *reg;
    const uint8_t *gi;
    const uint16_t *p;
    const uint32_t *w;
} Level;

typedef struct {
    Level level[3]; // whole map, 16 regions, tiles
    uint32_t need, limit;
} Variant;

// For a block of count index records and nv variants of a drawing: the records each variant's bound keeps, coarse
// to fine, as sorted keys (count per variant in keys) with how many in found. Each level's caps are only computed for
// records some variant keeps at the level before. whole, regions, caps and mark are scratch sized for count.
API void sc_filter(const uint8_t *tiles, int count, int ng, const uint16_t *hit, const uint16_t *near,
                   const uint32_t *lv, uint16_t *whole, uint16_t *regions, uint8_t *caps, uint8_t *mark, int nv, const Variant *vs, uint64_t *keys, int *found)
{
    if (count <= 0)
        return;
    sc_whole(tiles, count, ng, hit, near, whole);
    memset(mark, 0, (size_t)count);
    for (int v = 0; v < nv; v++) {
        const Level *l = &vs[v].level[0];
        uint64_t *out = keys + (size_t)v * count;
        found[v] = sc_bound(whole, 0, 1, ng, NULL, count, l->reg, l->gi, l->p, l->w, l->n, lv[0], lv[1], lv[2],
                            vs[v].need, vs[v].limit, out, 0);
        for (int k = 0; k < found[v]; k++)
            mark[(uint32_t)out[k]] = 1;
    }
    for (int i = 0; i < count; i++)
        if (mark[i])
            region_caps(tiles + (size_t)i * RECORD, ng, hit, near, regions + (size_t)i * ng * 32);
    memset(mark, 0, (size_t)count);
    for (int v = 0; v < nv; v++) {
        uint64_t *out = keys + (size_t)v * count;
        const Level *l = &vs[v].level[1];
        found[v] = sc_bound(regions, 0, 16, ng, out, found[v], l->reg, l->gi, l->p, l->w, l->n, lv[0], lv[1],
                            lv[2], 0, vs[v].limit, out, 0);
        for (int k = 0; k < found[v]; k++)
            mark[(uint32_t)out[k]] = 1;
    }
    for (int i = 0; i < count; i++)
        if (mark[i])
            tile_caps(tiles + (size_t)i * RECORD, ng, hit, near, caps + (size_t)i * ng * TILES * 2);
    for (int v = 0; v < nv; v++) {
        uint64_t *out = keys + (size_t)v * count;
        const Level *l = &vs[v].level[2];
        found[v] = sc_bound(caps, 1, TILES, ng, out, found[v], l->reg, l->gi, l->p, l->w, l->n, lv[0], lv[1],
                            lv[2], 0, vs[v].limit, out, 1);
    }
}

API void sc_palette(uint8_t *rgb)
{
    unsigned char c[256][3];
    initBiomeColors(c);
    memcpy(rgb, c, sizeof c);
}

API const char *sc_biome_name(int id) { return biome2str(MC, id); }
