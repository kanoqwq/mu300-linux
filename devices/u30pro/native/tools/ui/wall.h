/* Wallpaper and liquid glass of the screen UI, after the url(wallpaper) and url(glass) of zwrt-devui (the U60 Pro screen UI), redone for the
 * 240x240 canvas.
 *
 * The wallpaper is a picture or a live clip that the web interface (System > Screen > Wallpaper) makes from the user's own file:
 *   WP_DIR/wallpaper.jpg    the picture, a JPEG (any size: it is cover-scaled to the screen)
 *   WP_DIR/wallpaper.live   a live clip ("U3LW", below); it wins over the picture, which is then its first frame for the web pages
 * It is pinned to the screen: pages, sheets and swipes move over it.  A canvas drawn to be shown shifted (a page in a swipe, a sheet being
 * pulled) says so in g_bgx/g_bgy, and its background and glass are taken from where they will land.
 *
 * Glass (gx_glass) is a rounded box that shows the wallpaper behind it blurred, bent inward near the rim like a lens, washed towards a tinted
 * pane by the transparency setting, with a rim lit from above.  The blurred backdrop is toned so that light text on the glass stays legible.
 * Without a wallpaper nothing here draws: the UI keeps its gradient and its translucent cards.
 *
 * Settings (u30pro_native_control.display): wp_dim 0..90 (percent darker, 30), glass 0..100 (transparency, 50), glass_blur 0..100 (50),
 * glass_tint 0..3 (night blue, graphite, white fog, theme colour), wp_live 0..2 (where a clip plays: nowhere, the lock screen, the lock
 * screen and the home page; 2).  A clip plays while one of those is on screen and still; anywhere else its current frame is the wallpaper.
 *
 * wallpaper.live: a 16-byte header ("U3LW", version 1, fps 1..30, width and height 240 as u16, a u16 0, the frame count as u32; little
 * endian), then each frame as a u32 length and a baseline JPEG of 240x240.  The clip loops. */
#ifndef U30PRO_UI_WALL_H
#define U30PRO_UI_WALL_H
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

#define STB_IMAGE_STATIC
#define STB_IMAGE_IMPLEMENTATION
#define STBI_ONLY_JPEG
#define STBI_NO_STDIO
#define STBI_NO_LINEAR
#define STBI_NO_HDR
#define STBI_NO_FAILURE_STRINGS
#if defined(__aarch64__)
#define STBI_NEON
#endif
#pragma GCC diagnostic push
#ifdef __clang__
#pragma clang diagnostic ignored "-Weverything"
#else
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#pragma GCC diagnostic ignored "-Wimplicit-fallthrough"
#pragma GCC diagnostic ignored "-Wtype-limits"
#pragma GCC diagnostic ignored "-Wmisleading-indentation"
#endif
#include "stb_image.h"
#pragma GCC diagnostic pop

#ifdef UI_HOST
#define WP_DIR UI_RUN
#else
#define WP_DIR "/etc/u30pro/wallpaper"
#endif
#define WP_N (SCREEN * SCREEN)

static int wp_dim = 30, glass_clear = 50, glass_blur = 50, glass_tint, wp_live = 2;
static const char *const TINT_NAMES[] = { "夜蓝", "石墨", "白雾", "主题色" };
static const uint32_t TINT_RGB[] = { 0x161E2C, 0x1C1C1E, 0xE1E8F0, 0 };
#define NTINT 4
static const char *const LIVE_NAMES[] = { "关闭", "锁屏", "锁屏和主页" };

static int wp_on;			/* a wallpaper is in use */
static int g_bgx, g_bgy;		/* where the canvas being drawn will be shown, relative to the screen */
static uint8_t wp_rgb[WP_N * 3];	/* the picture or the clip's current frame, as decoded */
static uint32_t wp_bg[WP_N];		/* dimmed: the background of the pages */
static uint8_t wp_soft[WP_N * 3];	/* dimmed and blurred */
static uint8_t wp_glass[2][WP_N * 3];	/* wp_soft as glass shows it: [0] glass, [1] thin glass (the home dial) */
static uint32_t wp_frost[WP_N];		/* wp_glass[0] under a plain pane: the background of the sheets and the text pages */
static int wp_ready;			/* which of wp_glass[0], wp_glass[1], wp_frost (bits 1, 2, 4) are up to date: they are made when first used,
					 * so a playing clip only works out what the home page shows */
static int tint_r, tint_g, tint_b;

static inline int wp_luma(int r, int g, int b) { return (r * 299 + g * 587 + b * 114) / 1000; }

static uint32_t tint_rgb(void)
{
	uint32_t a = TH()->a;

	if (TINT_RGB[glass_tint % NTINT]) return TINT_RGB[glass_tint % NTINT];
	return ((a >> 16 & 255) * 2 / 5) << 16 | ((a >> 8 & 255) * 2 / 5) << 8 | (a & 255) * 2 / 5;
}

/* ------------------------------------------------------------------ blur (three box passes each way, like a Gaussian) */
/* one running-sum box pass of radius r along each of the n rows of img (w RGB pixels each; the edge pixels repeat) */
static void wp_box_rows(uint8_t *img, int w, int n, int r)
{
	const uint32_t win = 2u * (uint32_t)r + 1u, inv = (65536u + win - 1u) / win;
	uint8_t tmp[SCREEN * 3];

	for (int y = 0; y < n; y++) {
		uint8_t *p = img + (size_t)y * (size_t)w * 3;
		uint32_t s0 = 0, s1 = 0, s2 = 0;

		for (int i = -r; i <= r; i++) {
			const uint8_t *q = p + (i < 0 ? 0 : (i > w - 1 ? w - 1 : i)) * 3;

			s0 += q[0]; s1 += q[1]; s2 += q[2];
		}
		for (int i = 0; i < w; i++) {
			const uint8_t *in = p + (i + r + 1 > w - 1 ? w - 1 : i + r + 1) * 3, *out = p + (i - r < 0 ? 0 : i - r) * 3;

			tmp[i * 3] = (uint8_t)((s0 * inv + 32768u) >> 16);
			tmp[i * 3 + 1] = (uint8_t)((s1 * inv + 32768u) >> 16);
			tmp[i * 3 + 2] = (uint8_t)((s2 * inv + 32768u) >> 16);
			s0 += in[0] - out[0]; s1 += in[1] - out[1]; s2 += in[2] - out[2];
		}
		memcpy(p, tmp, (size_t)w * 3);
	}
}

/* the same pass down each column, the n rows of src into dst */
static void wp_box_cols(const uint8_t *src, uint8_t *dst, int w, int n, int r)
{
	const int N = w * 3;
	const uint32_t win = 2u * (uint32_t)r + 1u, inv = (65536u + win - 1u) / win;
	uint32_t acc[SCREEN * 3];

	memset(acc, 0, sizeof acc);
	for (int i = -r; i <= r; i++) {
		const uint8_t *q = src + (size_t)(i < 0 ? 0 : (i > n - 1 ? n - 1 : i)) * (size_t)N;

		for (int c = 0; c < N; c++) acc[c] += q[c];
	}
	for (int y = 0; y < n; y++) {
		uint8_t *o = dst + (size_t)y * (size_t)N;
		const uint8_t *in = src + (size_t)(y + r + 1 > n - 1 ? n - 1 : y + r + 1) * (size_t)N, *out = src + (size_t)(y - r < 0 ? 0 : y - r) * (size_t)N;

		for (int c = 0; c < N; c++) o[c] = (uint8_t)((acc[c] * inv + 32768u) >> 16);
		for (int c = 0; c < N; c++) acc[c] += in[c] - out[c];
	}
}

/* the blur setting spreads 0..30 px of radius over the three passes (50: 5 px a pass, about 9 px).  From 4 px on the picture is blurred
 * at half size with half the radius and scaled back up (bilinear): the result is smooth anyway, and a playing clip blurs every frame. */
static void wp_blur(const uint8_t *src, uint8_t *dst)
{
	enum { H = SCREEN / 2 };
	static uint8_t t1[WP_N * 3], t2[WP_N * 3];
	int total = glass_blur * 30 / 100, half = total >= 4, w = half ? H : SCREEN;
	uint8_t *a = t1, *b = t2, *t;

	if (half) {
		for (int y = 0; y < H; y++)
			for (int x = 0; x < H; x++)
				for (int c = 0; c < 3; c++) {
					const uint8_t *p = src + ((size_t)(2 * y) * SCREEN + (size_t)(2 * x)) * 3 + c;

					a[(y * H + x) * 3 + c] = (uint8_t)((p[0] + p[3] + p[SCREEN * 3] + p[SCREEN * 3 + 3] + 2) / 4);
				}
		total = (total + 1) / 2;
	} else memcpy(a, src, sizeof t1);
	for (int pass = 0; pass < 3; pass++) {
		int r = total / 3 + (pass < total % 3 ? 1 : 0);

		if (r <= 0) continue;
		wp_box_rows(a, w, w, r);
		wp_box_cols(a, b, w, w, r);
		t = a; a = b; b = t;
	}
	if (!half) { memcpy(dst, a, sizeof t1); return; }
	/* back to full size: each output pixel between two half-size pixels on each axis, at 1/4 or 3/4 (the same table for x and y) */
	{
		static int i0[SCREEN], i1[SCREEN], wt[SCREEN], ready;

		if (!ready) {
			for (int k = 0; k < SCREEN; k++) {
				int lo = k == 0 ? 0 : (k - 1) / 2, hi = lo + 1 < H ? lo + 1 : H - 1;

				i0[k] = lo; i1[k] = hi;
				wt[k] = k == 0 ? 0 : ((k & 1) ? 64 : 192);	/* weight of hi, of 256 */
			}
			ready = 1;
		}
		for (int y = 0; y < SCREEN; y++) {
			const uint8_t *r0 = a + i0[y] * H * 3, *r1 = a + i1[y] * H * 3;
			int wy = wt[y];
			uint8_t *o = dst + (size_t)y * SCREEN * 3;

			for (int x = 0; x < SCREEN; x++) {
				const uint8_t *p00 = r0 + i0[x] * 3, *p01 = r0 + i1[x] * 3, *p10 = r1 + i0[x] * 3, *p11 = r1 + i1[x] * 3;
				int wx = wt[x];

				for (int c = 0; c < 3; c++) {
					int top = p00[c] * 256 + (p01[c] - p00[c]) * wx, bot = p10[c] * 256 + (p11[c] - p10[c]) * wx;

					o[x * 3 + c] = (uint8_t)((top * 256 + (bot - top) * wy + 32768) >> 16);
				}
			}
		}
	}
}

/* ------------------------------------------------------------------ glass */
/* the most (dark pane) or least (light pane) the glass may show of the wallpaper's brightness, as luma of the glass itself */
enum { GLASS_LUMA_MAX = 112, GLASS_THIN_LUMA_MAX = 135, GLASS_LUMA_MIN = 168, GLASS_THIN = 35 };

static int glass_is_light(void) { return wp_luma(tint_r, tint_g, tint_b) >= 128; }

/* tone the blurred wallpaper so that the glass over it stays within those limits: a soft knee, so nothing clips flat */
static void wp_tone(const uint8_t *src, uint8_t *dst, float strength, int lmax, int lmin)
{
	const float pane = (float)(100 - glass_clear) / 100.f * strength, keep = (1.f - pane) * .94f;
	const int light = glass_is_light();
	const float base = (float)wp_luma(tint_r, tint_g, tint_b) * pane * .94f + 255.f * .06f;
	float lim;
	int map[256];

	memcpy(dst, src, WP_N * 3);
	if (keep < .02f) return;
	lim = ((float)(light ? lmin : lmax) - base) / keep;
	if (light ? lim <= 0.f : lim >= 255.f) return;
	for (int l = 0; l < 256; l++) {
		float v = (float)l;

		if (!light) {
			float knee = lim * .6f;

			if (v > knee) { float t = (v - knee) / (lim - knee); v = knee + (lim - knee) * t / (1.f + t); }
		} else {
			float knee = lim + (255.f - lim) * .4f;

			if (v < knee) { float t = (knee - v) / (knee - lim); v = knee - (knee - lim) * t / (1.f + t); }
		}
		map[l] = v < 0.f ? 0 : (v > 255.f ? 255 : (int)lrintf(v));
	}
	for (int i = 0; i < WP_N * 3; i += 3) {
		uint8_t *p = &dst[i];
		int l = wp_luma(p[0], p[1], p[2]), m = map[l];

		if (m == l) continue;
		for (int c = 0; c < 3; c++) {
			int v = !light ? (l ? p[c] * m / l : 0) : p[c] + (255 - p[c]) * (m - l) / (255 - l > 1 ? 255 - l : 1);

			p[c] = (uint8_t)(v < 0 ? 0 : (v > 255 ? 255 : v));
		}
	}
}

/* everything about a glass pixel that does not depend on what lies behind it: coverage, the lens offset of the backdrop sample, and the
 * shading as out = backdrop * keep + c (per channel, 8.8 fixed point) */
typedef struct { float cov; int lx, ly, keep, cr, cg, cb; } GlassPix;

static int glass_at(float px, float py, float bx, float by, float bw, float bh, float r, int thin, float lift, GlassPix *o)
{
	float hw = bw * .5f, hh = bh * .5f, x = px - (bx + hw), y = py - (by + hh), nx, ny, d;
	float qx, qy, sx = x < 0.f ? -1.f : 1.f, sy = y < 0.f ? -1.f : 1.f;

	if (r > hw) r = hw;
	if (r > hh) r = hh;
	qx = fabsf(x) - (hw - r);
	qy = fabsf(y) - (hh - r);
	if (qx > 0.f && qy > 0.f) {
		float l = sqrtf(qx * qx + qy * qy);

		nx = sx * qx / l; ny = sy * qy / l; d = r - l;
	} else if (qx > qy) { nx = sx; ny = 0.f; d = r - qx; }
	else { nx = 0.f; ny = sy; d = r - qy; }
	o->cov = gx_clamp(d + .5f, 0.f, 1.f);
	if (o->cov <= 0.f) return 0;
	{
		const float lens = fminf(9.f, fminf(bw, bh) * .35f);
		const float pane = (float)(100 - glass_clear) / 100.f * (float)(100 - (thin ? GLASS_THIN : 0)) / 100.f;
		float k = d < lens ? 1.f - d / lens : 0.f;
		float rim = gx_clamp(1.5f - d, 0.f, 1.f) * (.10f + .32f * fmaxf(0.f, -ny) + .06f * fmaxf(0.f, -nx));
		float top = (py - by) / (bh * .34f), frost = 255.f * (.06f + .10f * lift);

		if (top < 1.f) rim += .05f * (1.f - top);
		o->lx = (int)lrintf(nx * 6.f * k * k);
		o->ly = (int)lrintf(ny * 6.f * k * k);
		o->keep = (int)lrintf((1.f - pane) * .94f * (1.f - rim) * 256.f);
		o->cr = (int)lrintf((((float)tint_r * pane * .94f + frost) * (1.f - rim) + 255.f * rim) * 256.f);
		o->cg = (int)lrintf((((float)tint_g * pane * .94f + frost) * (1.f - rim) + 255.f * rim) * 256.f);
		o->cb = (int)lrintf((((float)tint_b * pane * .94f + frost) * (1.f - rim) + 255.f * rim) * 256.f);
	}
	return 1;
}

static inline int glass_ch(int v, int keep, int c) { v = (v * keep + c) >> 8; return v > 255 ? 255 : v; }

/* What glass_at says about the pixels of a box does not change while the box stays where it is, so it is kept for the boxes drawn lately:
 * per row the middle span (one shading) and the pixels near the edge one by one.  A frame of a live wallpaper then only samples and blends.
 * glass_gen changes with the glass settings, which the shading depends on. */
typedef struct { int16_t x, y; int8_t lx, ly; uint16_t keep, cr, cg, cb; float cov; } GlassEdge;
typedef struct { int16_t a, b; uint16_t keep, cr, cg, cb; } GlassRow;
typedef struct { float x, y, w, h, r, lift; int thin, y0, nrows, nedge; unsigned gen, used; GlassRow *rows; GlassEdge *edge; } GlassMask;
static GlassMask glass_masks[8];
static unsigned glass_gen = 1, glass_tick;

static GlassMask *glass_mask(float x, float y, float w, float h, float r, int thin, float lift)
{
	GlassMask *m = &glass_masks[0];
	int x0 = (int)floorf(x), y0 = (int)floorf(y), x1 = (int)ceilf(x + w), y1 = (int)ceilf(y + h), cap;
	GlassPix g;

	for (int i = 0; i < (int)(sizeof glass_masks / sizeof *glass_masks); i++) {
		GlassMask *e = &glass_masks[i];

		if (e->rows && e->gen == glass_gen && e->x == x && e->y == y && e->w == w && e->h == h && e->r == r && e->thin == thin && e->lift == lift) {
			e->used = ++glass_tick;
			return e;
		}
		if (e->used < m->used) m = e;
	}
	free(m->rows);
	free(m->edge);
	memset(m, 0, sizeof *m);
	cap = (x1 - x0) * (y1 - y0);
	m->rows = calloc((size_t)(y1 - y0 > 0 ? y1 - y0 : 1), sizeof *m->rows);
	m->edge = malloc((size_t)(cap > 0 ? cap : 1) * sizeof *m->edge);
	if (!m->rows || !m->edge) { free(m->rows); free(m->edge); memset(m, 0, sizeof *m); return NULL; }
	m->x = x; m->y = y; m->w = w; m->h = h; m->r = r; m->thin = thin; m->lift = lift; m->gen = glass_gen; m->used = ++glass_tick;
	m->y0 = y0; m->nrows = y1 - y0;
	for (int py = y0; py < y1; py++) {
		/* the middle of the row, more than the lens and the rim away from the edge, shades alike */
		const float L = fminf(9.f, fminf(w, h) * .35f) + 2.f, rr = r - L, cy = (float)py + .5f - (y + h * .5f);
		float qy = fabsf(cy) - (h * .5f - r), half = -1.f;
		GlassRow *row = &m->rows[py - y0];
		GlassPix mid;

		if (rr > 0.f && fabsf(cy) <= h * .5f - L) half = qy <= 0.f ? w * .5f - L : (qy < rr ? w * .5f - r + sqrtf(rr * rr - qy * qy) : -1.f);
		if (half > 1.f && glass_at(x + w * .5f, (float)py + .5f, x, y, w, h, r, thin, lift, &mid)) {
			row->a = (int16_t)ceilf(x + w * .5f - half);
			row->b = (int16_t)floorf(x + w * .5f + half);
			row->keep = (uint16_t)mid.keep; row->cr = (uint16_t)mid.cr; row->cg = (uint16_t)mid.cg; row->cb = (uint16_t)mid.cb;
		}
		for (int px = x0; px < x1; px++) {
			if (px >= row->a && px < row->b) continue;
			if (!glass_at((float)px + .5f, (float)py + .5f, x, y, w, h, r, thin, lift, &g)) continue;
			m->edge[m->nedge++] = (GlassEdge){ (int16_t)px, (int16_t)py, (int8_t)g.lx, (int8_t)g.ly, (uint16_t)g.keep, (uint16_t)g.cr, (uint16_t)g.cg,
							   (uint16_t)g.cb, g.cov };
		}
	}
	return m;
}

static inline uint32_t glass_px(const uint8_t *back, int px, int py, int lx, int ly, int keep, int cr, int cg, int cb)
{
	int sx = px + g_bgx - lx, sy = py + g_bgy - ly;
	const uint8_t *q;

	sx = sx < 0 ? 0 : (sx > SCREEN - 1 ? SCREEN - 1 : sx);
	sy = sy < 0 ? 0 : (sy > SCREEN - 1 ? SCREEN - 1 : sy);
	q = &back[(sy * SCREEN + sx) * 3];
	return gx_rgb(glass_ch(q[0], keep, cr), glass_ch(q[1], keep, cg), glass_ch(q[2], keep, cb));
}

/* a glass box x, y, w, h with corner radius r (a disc: w = h = 2r); thin: the lighter glass of the home dial; lift 0..1: the pressed look
 * (more frost); alpha scales the whole box (a sheet fading in) */
static void wp_need(int what);

static void gx_glass(Cv *c, float x, float y, float w, float h, float r, int thin, float lift, int alpha)
{
	const uint8_t *back = wp_glass[thin ? 1 : 0];
	GlassMask *m;

	if (!wp_on || w <= 0.f || h <= 0.f) return;
	wp_need(thin ? 2 : 1);
	if (r > w * .5f) r = w * .5f;
	if (r > h * .5f) r = h * .5f;
	if (!(m = glass_mask(x, y, w, h, r, thin, lift))) return;
	for (int i = 0; i < m->nrows; i++) {
		const GlassRow *row = &m->rows[i];
		int py = m->y0 + i;

		for (int px = row->a; px < row->b; px++) gx_put(c, px, py, glass_px(back, px, py, 0, 0, row->keep, row->cr, row->cg, row->cb), alpha);
	}
	for (int i = 0; i < m->nedge; i++) {
		const GlassEdge *e = &m->edge[i];

		gx_cov(c, e->x, e->y, glass_px(back, e->x, e->y, e->lx, e->ly, e->keep, e->cr, e->cg, e->cb), e->cov, alpha);
	}
}

/* the background of a canvas: the wallpaper (wp_bg) or the frosted backdrop of a sheet (wp_frost), from where the canvas will be shown */
static void wp_paint(Cv *dst, const uint32_t *src)
{
	if (src == wp_frost) wp_need(4);
	for (int y = 0; y < SCREEN; y++) {
		int sy = y + g_bgy;

		sy = sy < 0 ? 0 : (sy > SCREEN - 1 ? SCREEN - 1 : sy);
		for (int x = 0; x < SCREEN; x++) {
			int sx = x + g_bgx;

			dst->p[y * SCREEN + x] = src[sy * SCREEN + (sx < 0 ? 0 : (sx > SCREEN - 1 ? SCREEN - 1 : sx))];
		}
	}
}

/* ------------------------------------------------------------------ the wallpaper from wp_rgb */
static void wp_derive(void)
{
	int keep = 100 - (wp_dim < 0 ? 0 : (wp_dim > 90 ? 90 : wp_dim));
	uint32_t t = tint_rgb();
	static uint8_t dimmed[WP_N * 3];

	tint_r = (int)(t >> 16 & 255); tint_g = (int)(t >> 8 & 255); tint_b = (int)(t & 255);
	for (int i = 0; i < WP_N; i++) {
		int r = wp_rgb[i * 3] * keep / 100, g = wp_rgb[i * 3 + 1] * keep / 100, b = wp_rgb[i * 3 + 2] * keep / 100;

		dimmed[i * 3] = (uint8_t)r; dimmed[i * 3 + 1] = (uint8_t)g; dimmed[i * 3 + 2] = (uint8_t)b;
		wp_bg[i] = gx_rgb(r, g, b);
	}
	wp_blur(dimmed, wp_soft);
	wp_ready = 0;
}

static void wp_need(int what)
{
	if ((what & 1) && !(wp_ready & 1)) { wp_tone(wp_soft, wp_glass[0], 1.f, GLASS_LUMA_MAX, GLASS_LUMA_MIN); wp_ready |= 1; }
	if ((what & 2) && !(wp_ready & 2)) { wp_tone(wp_soft, wp_glass[1], 1.f - GLASS_THIN / 100.f, GLASS_THIN_LUMA_MAX, GLASS_LUMA_MIN); wp_ready |= 2; }
	if ((what & 4) && !(wp_ready & 4)) {
		/* a sheet's backdrop: the glass with no rim, a little darker, so the page under it falls back */
		const float pane = (float)(100 - glass_clear) / 100.f;
		int k = (int)lrintf((1.f - pane) * .86f * 256.f), cr = (int)lrintf(((float)tint_r * pane * .94f + 10.f) * 256.f),
		    cg = (int)lrintf(((float)tint_g * pane * .94f + 10.f) * 256.f), cb = (int)lrintf(((float)tint_b * pane * .94f + 10.f) * 256.f);

		wp_need(1);
		for (int i = 0; i < WP_N; i++) {
			const uint8_t *q = &wp_glass[0][i * 3];

			wp_frost[i] = gx_rgb(glass_ch(q[0], k, cr), glass_ch(q[1], k, cg), glass_ch(q[2], k, cb));
		}
		wp_ready |= 4;
	}
}

/* decode a JPEG into wp_rgb, cover-scaled (box filter when shrinking); 0 on success */
static int wp_decode(const uint8_t *buf, size_t len)
{
	int w, h, n;
	uint8_t *img;

	if (!len || len > 0x7fffffff) return -1;
	img = stbi_load_from_memory(buf, (int)len, &w, &h, &n, 3);
	if (!img) return -1;
	{
		float s = (float)(w < h ? w : h) / (float)SCREEN, ox = ((float)w - s * SCREEN) * .5f, oy = ((float)h - s * SCREEN) * .5f;
		int k = s > 1.f ? (int)ceilf(s) : 1;

		for (int y = 0; y < SCREEN; y++)
			for (int x = 0; x < SCREEN; x++) {
				int x0 = (int)(ox + (float)x * s), y0 = (int)(oy + (float)y * s), cnt = 0;
				unsigned acc[3] = { 0, 0, 0 };

				for (int j = 0; j < k; j++)
					for (int i = 0; i < k; i++) {
						int sx = x0 + i, sy = y0 + j;

						if (sx >= w || sy >= h) continue;
						for (int c = 0; c < 3; c++) acc[c] += img[((size_t)sy * (size_t)w + (size_t)sx) * 3 + (size_t)c];
						cnt++;
					}
				for (int c = 0; c < 3; c++) wp_rgb[(y * SCREEN + x) * 3 + c] = (uint8_t)(cnt ? acc[c] / (unsigned)cnt : 0);
			}
	}
	stbi_image_free(img);
	return 0;
}

/* ------------------------------------------------------------------ the live clip */
static struct { uint8_t *map; size_t size; size_t *off; uint32_t *len; int n, fps, cur, want, playing; long t0; } lw;

static uint32_t wp_rd32(const uint8_t *p) { return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24; }
static unsigned wp_rd16(const uint8_t *p) { return (unsigned)p[0] | (unsigned)p[1] << 8; }

static void lw_close(void)
{
	if (lw.map) munmap(lw.map, lw.size);
	free(lw.off);
	free(lw.len);
	memset(&lw, 0, sizeof lw);
}

static int lw_open(const char *path)
{
	struct stat st;
	int fd = open(path, O_RDONLY | O_CLOEXEC), n;
	size_t pos = 16;
	void *m;

	lw_close();
	if (fd < 0) return -1;
	if (fstat(fd, &st) || st.st_size < 16) { close(fd); return -1; }
	m = mmap(NULL, (size_t)st.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
	close(fd);		/* the mapping keeps the file, even once the web interface replaces it */
	if (m == MAP_FAILED) return -1;
	lw.map = m;
	lw.size = (size_t)st.st_size;
	n = (int)wp_rd32(lw.map + 12);
	if (memcmp(lw.map, "U3LW", 4) || lw.map[4] != 1 || lw.map[5] < 1 || lw.map[5] > 30 || wp_rd16(lw.map + 6) != SCREEN ||
	    wp_rd16(lw.map + 8) != SCREEN || n < 1 || n > 30 * 60) { lw_close(); return -1; }
	lw.fps = lw.map[5];
	lw.off = malloc((size_t)n * sizeof *lw.off);
	lw.len = malloc((size_t)n * sizeof *lw.len);
	if (!lw.off || !lw.len) { lw_close(); return -1; }
	for (int i = 0; i < n; i++) {
		uint32_t len;

		if (lw.size - pos < 4) { lw_close(); return -1; }
		len = wp_rd32(lw.map + pos);
		pos += 4;
		if (!len || len > lw.size - pos) { lw_close(); return -1; }
		lw.off[i] = pos;
		lw.len[i] = len;
		pos += len;
	}
	lw.n = n;
	return 0;
}

/* show frame i of the clip; 0 when it is on screen */
static int lw_show(int i)
{
	if (!lw.n || i < 0 || i >= lw.n) return -1;
	if (wp_decode(lw.map + lw.off[i], lw.len[i])) return -1;
	lw.cur = lw.want = i;
	wp_derive();
	return 0;
}

/* ------------------------------------------------------------------ loading */
static struct { time_t m_live, m_jpg; off_t s_live, s_jpg; long t_live, t_jpg; int dim, clear, blur, tint, theme, done; } wp_seen;

static int wp_read_file(const char *path, uint8_t **buf, size_t *len)
{
	struct stat st;
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	ssize_t got;

	*buf = NULL;
	if (fd < 0) return -1;
	if (fstat(fd, &st) || st.st_size <= 0 || st.st_size > 8 << 20 || !(*buf = malloc((size_t)st.st_size))) { close(fd); return -1; }
	got = read(fd, *buf, (size_t)st.st_size);
	close(fd);
	if (got != st.st_size) { free(*buf); *buf = NULL; return -1; }
	*len = (size_t)got;
	return 0;
}

/* look at the files and the settings (once a second and after a settings reload); 1 when the background changed */
static int wp_check(void)
{
	struct stat sl, sj;
	int has_l = !stat(WP_DIR "/wallpaper.live", &sl), has_j = !stat(WP_DIR "/wallpaper.jpg", &sj), was = wp_on;
	int files = !wp_seen.done || (has_l ? sl.st_mtime != wp_seen.m_live || sl.st_size != wp_seen.s_live || sl.st_mtim.tv_nsec != wp_seen.t_live : wp_seen.s_live >= 0) ||
		    (has_j ? sj.st_mtime != wp_seen.m_jpg || sj.st_size != wp_seen.s_jpg || sj.st_mtim.tv_nsec != wp_seen.t_jpg : wp_seen.s_jpg >= 0);
	int look = wp_seen.dim != wp_dim || wp_seen.clear != glass_clear || wp_seen.blur != glass_blur || wp_seen.tint != glass_tint ||
		   (glass_tint == 3 && wp_seen.theme != theme_idx);

	if (!files && !look) return 0;
	glass_gen++;
	wp_seen.dim = wp_dim; wp_seen.clear = glass_clear; wp_seen.blur = glass_blur; wp_seen.tint = glass_tint; wp_seen.theme = theme_idx;
	if (files) {
		wp_seen.done = 1;
		wp_seen.m_live = has_l ? sl.st_mtime : 0; wp_seen.s_live = has_l ? sl.st_size : -1; wp_seen.t_live = has_l ? sl.st_mtim.tv_nsec : 0;
		wp_seen.m_jpg = has_j ? sj.st_mtime : 0; wp_seen.s_jpg = has_j ? sj.st_size : -1; wp_seen.t_jpg = has_j ? sj.st_mtim.tv_nsec : 0;
		lw_close();
		wp_on = 0;
		if (has_l && !lw_open(WP_DIR "/wallpaper.live") && !lw_show(0)) wp_on = 1;
		else if (has_j) {
			uint8_t *buf;
			size_t len;

			lw_close();
			if (!wp_read_file(WP_DIR "/wallpaper.jpg", &buf, &len)) {
				if (!wp_decode(buf, len)) { wp_derive(); wp_on = 1; }
				free(buf);
			}
		}
		return 1;
	}
	if (wp_on) wp_derive();
	return was || wp_on;
}

/* the clip plays while `here` (a page it plays on is on screen and still); 1 when another frame is due.  The frame is decoded only when the
 * next picture is composed (lw_sync): the panel takes fewer frames a second than a clip may have, and the ones it skips are never decoded. */
static int lw_tick(long now, int here)
{
	int f;

	if (!wp_on || lw.n < 2) return 0;
	if (here != lw.playing) {
		lw.playing = here;
		lw.t0 = now - (long)lw.cur * 1000L / lw.fps;	/* go on from the frame it stopped at */
	}
	if (!here) return 0;
	f = (int)(((now - lw.t0) * lw.fps / 1000L) % lw.n);
	if (f == lw.want) return 0;
	lw.want = f;
	return 1;
}

static void lw_sync(void)
{
	if (wp_on && lw.n > 1 && lw.want != lw.cur && lw_show(lw.want)) lw.want = lw.cur;
}

static long lw_interval(void) { return lw.playing && lw.n > 1 ? 1000L / lw.fps : 1000L; }
#endif
