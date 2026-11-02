/* Software renderer of the screen UI: anti-aliased shapes (signed distance fields), 4-bit alpha glyph text, blending.
 * Everything draws into a malloc'ed XRGB canvas; the DRM dumb buffer is only ever written with whole-frame copies. */
#ifndef U30PRO_UI_GFX_H
#define U30PRO_UI_GFX_H
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef struct { int w, h; uint32_t *p; } Cv;

#define GX_PI 3.14159265f

static Cv cv_new(int w, int h)
{
	Cv c = { w, h, calloc((size_t)w * (size_t)h, 4) };

	return c;
}

static inline float gx_clamp(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }

static inline uint32_t gx_mix(uint32_t bg, uint32_t fg, unsigned a)
{
	if (a >= 255) return fg;
	if (!a) return bg;
	unsigned ia = 255 - a;
	unsigned r = ((fg >> 16 & 255) * a + (bg >> 16 & 255) * ia + 127) / 255;
	unsigned g = ((fg >> 8 & 255) * a + (bg >> 8 & 255) * ia + 127) / 255;
	unsigned b = ((fg & 255) * a + (bg & 255) * ia + 127) / 255;

	return r << 16 | g << 8 | b;
}

static inline uint32_t gx_rgb(int r, int g, int b) { return (uint32_t)(r & 255) << 16 | (uint32_t)(g & 255) << 8 | (uint32_t)(b & 255); }

/* Clip box of all drawing (gx_put and gx_rect), with an optional soft edge: within gx_cf pixels of a side of the box the drawing fades out
 * (the top and bottom of a scrolling list, the ends of a ticker).  Scrolling views and tickers set it with gx_clip() and put the old one back. */
typedef struct { int x0, y0, x1, y1, fade; } GxClip;
static GxClip gx_cl = { -32768, -32768, 32767, 32767, 0 };

/* intersect the clip box with x0..x1 x y0..y1 (fade: soft edge of the new box) and return the previous one for gx_unclip() */
static GxClip gx_clip(int x0, int y0, int x1, int y1, int fade)
{
	GxClip old = gx_cl;

	gx_cl.x0 = x0 > old.x0 ? x0 : old.x0; gx_cl.y0 = y0 > old.y0 ? y0 : old.y0;
	gx_cl.x1 = x1 < old.x1 ? x1 : old.x1; gx_cl.y1 = y1 < old.y1 ? y1 : old.y1;
	gx_cl.fade = fade > old.fade ? fade : old.fade;
	return old;
}

static void gx_unclip(GxClip old) { gx_cl = old; }

/* a: 0..255 coverage already multiplied by the caller's alpha */
static inline void gx_put(Cv *c, int x, int y, uint32_t col, int a)
{
	if (a <= 0 || x < 0 || y < 0 || x >= c->w || y >= c->h) return;
	if (x < gx_cl.x0 || y < gx_cl.y0 || x >= gx_cl.x1 || y >= gx_cl.y1) return;
	if (gx_cl.fade) {
		int d = x - gx_cl.x0, e;

		if ((e = gx_cl.x1 - 1 - x) < d) d = e;
		if ((e = y - gx_cl.y0) < d) d = e;
		if ((e = gx_cl.y1 - 1 - y) < d) d = e;
		if (d < gx_cl.fade) a = a * (d + 1) / (gx_cl.fade + 1);
		if (a <= 0) return;
	}
	uint32_t *d = &c->p[y * c->w + x];

	*d = gx_mix(*d, col, (unsigned)(a > 255 ? 255 : a));
}

static inline void gx_cov(Cv *c, int x, int y, uint32_t col, float cov, int alpha)
{
	if (cov <= 0.f) return;
	gx_put(c, x, y, col, (int)(cov * (float)alpha + 0.5f));
}

static void gx_fill(Cv *c, uint32_t col)
{
	for (int i = 0; i < c->w * c->h; i++) c->p[i] = col;
}

static void gx_rect(Cv *c, int x, int y, int w, int h, uint32_t col, int alpha)
{
	int x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y, x1 = x + w > c->w ? c->w : x + w, y1 = y + h > c->h ? c->h : y + h;

	if (x0 < gx_cl.x0) x0 = gx_cl.x0;
	if (y0 < gx_cl.y0) y0 = gx_cl.y0;
	if (x1 > gx_cl.x1) x1 = gx_cl.x1;
	if (y1 > gx_cl.y1) y1 = gx_cl.y1;

	for (int j = y0; j < y1; j++)
		for (int i = x0; i < x1; i++) {
			uint32_t *d = &c->p[j * c->w + i];

			*d = gx_mix(*d, col, (unsigned)alpha);
		}
}

static void gx_circle(Cv *c, float cx, float cy, float r, uint32_t col, int alpha)
{
	int x0 = (int)floorf(cx - r - 1), x1 = (int)ceilf(cx + r + 1), y0 = (int)floorf(cy - r - 1), y1 = (int)ceilf(cy + r + 1);

	for (int y = y0; y <= y1; y++)
		for (int x = x0; x <= x1; x++) {
			float dx = (float)x + .5f - cx, dy = (float)y + .5f - cy;

			gx_cov(c, x, y, col, gx_clamp(r - sqrtf(dx * dx + dy * dy) + .5f, 0.f, 1.f), alpha);
		}
}

/* annulus between radii r0 and r1 (full circle) */
static void gx_ring(Cv *c, float cx, float cy, float r0, float r1, uint32_t col, int alpha)
{
	int x0 = (int)floorf(cx - r1 - 1), x1 = (int)ceilf(cx + r1 + 1), y0 = (int)floorf(cy - r1 - 1), y1 = (int)ceilf(cy + r1 + 1);

	for (int y = y0; y <= y1; y++)
		for (int x = x0; x <= x1; x++) {
			float dx = (float)x + .5f - cx, dy = (float)y + .5f - cy, d = sqrtf(dx * dx + dy * dy);

			gx_cov(c, x, y, col, gx_clamp(d - r0 + .5f, 0.f, 1.f) * gx_clamp(r1 - d + .5f, 0.f, 1.f), alpha);
		}
}

/* The coverage of the arcs drawn lately, so that an arc drawn again with the same geometry (the battery ring, the gauges) is only blended:
 * a frame of a live wallpaper redraws the whole page, and atan2 for every pixel of a 200 px ring is slow on the small cores. */
typedef struct { int16_t x, y; float cov; } GxCovPx;
typedef struct { float cx, cy, r, th, a0, a1; int caps, n; unsigned used; GxCovPx *px; } GxArcMask;
static GxArcMask gx_arcs[12];
static unsigned gx_arc_tick;

/* arc of thickness `th` around radius r; angles in degrees, 0 = 12 o'clock, clockwise; a1 > a0 (span <= 360) */
static void gx_arc(Cv *c, float cx, float cy, float r, float th, float a0, float a1, uint32_t col, int alpha, int round_caps)
{
	float half = th * .5f, r0 = r - half, r1 = r + half;
	int x0 = (int)floorf(cx - r1 - 1), x1 = (int)ceilf(cx + r1 + 1), y0 = (int)floorf(cy - r1 - 1), y1 = (int)ceilf(cy + r1 + 1);
	GxArcMask *m = NULL, *old = &gx_arcs[0];

	if (a1 - a0 >= 359.9f) { gx_ring(c, cx, cy, r0, r1, col, alpha); return; }
	if (a1 <= a0) return;
	for (int i = 0; i < (int)(sizeof gx_arcs / sizeof *gx_arcs); i++) {
		GxArcMask *e = &gx_arcs[i];

		if (e->px && e->cx == cx && e->cy == cy && e->r == r && e->th == th && e->a0 == a0 && e->a1 == a1 && e->caps == round_caps) { m = e; break; }
		if (e->used < old->used) old = e;
	}
	if (m) {
		m->used = ++gx_arc_tick;
		for (int i = 0; i < m->n; i++) gx_cov(c, m->px[i].x, m->px[i].y, col, m->px[i].cov, alpha);
	} else {
		/* a new arc: the pixels with coverage go into the oldest slot as they are drawn */
		size_t cap = (size_t)(x1 - x0 + 1) * 8 + (size_t)((r1 + 1) * (a1 - a0) * GX_PI / 180.f + 8) * (size_t)(th + 4);

		free(old->px);
		memset(old, 0, sizeof *old);
		if ((old->px = malloc(cap * sizeof *old->px))) {
			old->cx = cx; old->cy = cy; old->r = r; old->th = th; old->a0 = a0; old->a1 = a1; old->caps = round_caps; old->used = ++gx_arc_tick;
		}
		for (int y = y0; y <= y1; y++)
			for (int x = x0; x <= x1; x++) {
				float dx = (float)x + .5f - cx, dy = (float)y + .5f - cy, d2 = dx * dx + dy * dy;

				if (d2 > (r1 + 1) * (r1 + 1) || d2 < (r0 - 1) * (r0 - 1)) continue;
				float d = sqrtf(d2), th_deg = atan2f(dx, -dy) * 180.f / GX_PI;

				while (th_deg < a0) th_deg += 360.f;
				while (th_deg >= a0 + 360.f) th_deg -= 360.f;
				float radial = gx_clamp(d - r0 + .5f, 0.f, 1.f) * gx_clamp(r1 - d + .5f, 0.f, 1.f);
				float edge = round_caps ? 1.f : gx_clamp(fminf(th_deg - a0, a1 - th_deg) * GX_PI / 180.f * d + .5f, 0.f, 1.f);

				if (th_deg > a1) edge = 0.f;
				if (radial * edge <= 0.f) continue;
				gx_cov(c, x, y, col, radial * edge, alpha);
				if (old->px) {
					if ((size_t)old->n == cap) { free(old->px); memset(old, 0, sizeof *old); }
					else old->px[old->n++] = (GxCovPx){ (int16_t)x, (int16_t)y, radial * edge };
				}
			}
	}
	if (round_caps) {
		float ra0 = a0 * GX_PI / 180.f, ra1 = a1 * GX_PI / 180.f;

		gx_circle(c, cx + r * sinf(ra0), cy - r * cosf(ra0), half, col, alpha);
		gx_circle(c, cx + r * sinf(ra1), cy - r * cosf(ra1), half, col, alpha);
	}
}

static inline float gx_rr_dist(float px, float py, float x, float y, float w, float h, float r)
{
	float qx = fabsf(px - (x + w * .5f)) - (w * .5f - r), qy = fabsf(py - (y + h * .5f)) - (h * .5f - r);
	float ox = qx > 0 ? qx : 0, oy = qy > 0 ? qy : 0;

	return sqrtf(ox * ox + oy * oy) + fminf(fmaxf(qx, qy), 0.f) - r;
}

static void gx_rrect(Cv *c, float x, float y, float w, float h, float r, uint32_t col, int alpha)
{
	if (r > h * .5f) r = h * .5f;
	if (r > w * .5f) r = w * .5f;
	for (int j = (int)floorf(y - 1); j <= (int)ceilf(y + h + 1); j++)
		for (int i = (int)floorf(x - 1); i <= (int)ceilf(x + w + 1); i++)
			gx_cov(c, i, j, col, gx_clamp(.5f - gx_rr_dist((float)i + .5f, (float)j + .5f, x, y, w, h, r), 0.f, 1.f), alpha);
}

static void gx_rrect_line(Cv *c, float x, float y, float w, float h, float r, float th, uint32_t col, int alpha)
{
	if (r > h * .5f) r = h * .5f;
	if (r > w * .5f) r = w * .5f;
	for (int j = (int)floorf(y - th - 1); j <= (int)ceilf(y + h + th + 1); j++)
		for (int i = (int)floorf(x - th - 1); i <= (int)ceilf(x + w + th + 1); i++) {
			float d = gx_rr_dist((float)i + .5f, (float)j + .5f, x, y, w, h, r);

			gx_cov(c, i, j, col, gx_clamp(.5f - (fabsf(d + th * .5f) - th * .5f), 0.f, 1.f), alpha);
		}
}

static void gx_line(Cv *c, float x0, float y0, float x1, float y1, float th, uint32_t col, int alpha)
{
	float half = th * .5f, vx = x1 - x0, vy = y1 - y0, len2 = vx * vx + vy * vy;
	int bx0 = (int)floorf(fminf(x0, x1) - half - 1), bx1 = (int)ceilf(fmaxf(x0, x1) + half + 1);
	int by0 = (int)floorf(fminf(y0, y1) - half - 1), by1 = (int)ceilf(fmaxf(y0, y1) + half + 1);

	for (int y = by0; y <= by1; y++)
		for (int x = bx0; x <= bx1; x++) {
			float px = (float)x + .5f - x0, py = (float)y + .5f - y0;
			float t = len2 > 0 ? gx_clamp((px * vx + py * vy) / len2, 0.f, 1.f) : 0.f;
			float dx = px - t * vx, dy = py - t * vy;

			gx_cov(c, x, y, col, gx_clamp(half - sqrtf(dx * dx + dy * dy) + .5f, 0.f, 1.f), alpha);
		}
}

static void gx_tri(Cv *c, float x0, float y0, float x1, float y1, float x2, float y2, uint32_t col, int alpha)
{
	float xs[3] = { x0, x1, x2 }, ys[3] = { y0, y1, y2 };
	float area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0), sgn = area < 0 ? -1.f : 1.f;
	int bx0 = (int)floorf(fminf(x0, fminf(x1, x2)) - 1), bx1 = (int)ceilf(fmaxf(x0, fmaxf(x1, x2)) + 1);
	int by0 = (int)floorf(fminf(y0, fminf(y1, y2)) - 1), by1 = (int)ceilf(fmaxf(y0, fmaxf(y1, y2)) + 1);

	for (int y = by0; y <= by1; y++)
		for (int x = bx0; x <= bx1; x++) {
			float px = (float)x + .5f, py = (float)y + .5f, dmin = 1e9f;

			for (int i = 0; i < 3; i++) {
				int j = (i + 1) % 3;
				float ex = xs[j] - xs[i], ey = ys[j] - ys[i], el = sqrtf(ex * ex + ey * ey);
				/* positive inside the edge, in pixels */
				float side = -((px - xs[i]) * ey - (py - ys[i]) * ex) * sgn / (el > 0 ? el : 1.f);

				if (side < dmin) dmin = side;
			}
			gx_cov(c, x, y, col, gx_clamp(dmin + .5f, 0.f, 1.f), alpha);
		}
}

/* ------------------------------------------------------------------ text */

static const UiGlyph *gx_find(const UiFont *f, uint32_t cp)
{
	unsigned lo = 0, hi = f->n;

	while (lo < hi) {
		unsigned mid = (lo + hi) / 2;

		if (f->g[mid].cp == cp) return &f->g[mid];
		if (f->g[mid].cp < cp) lo = mid + 1; else hi = mid;
	}
	return NULL;
}

/* decode one UTF-8 sequence; returns the number of bytes consumed (1 on invalid input) */
static int gx_utf8(const char *s, uint32_t *cp)
{
	const unsigned char *u = (const unsigned char *)s;

	if (u[0] < 0x80) { *cp = u[0]; return u[0] ? 1 : 0; }
	if (u[0] >= 0xC2 && u[0] < 0xE0 && (u[1] & 0xC0) == 0x80) { *cp = (u[0] & 0x1Fu) << 6 | (u[1] & 0x3Fu); return 2; }
	if (u[0] >= 0xE0 && u[0] < 0xF0 && (u[1] & 0xC0) == 0x80 && (u[2] & 0xC0) == 0x80) {
		*cp = (u[0] & 0x0Fu) << 12 | (u[1] & 0x3Fu) << 6 | (u[2] & 0x3Fu);
		return 3;
	}
	if (u[0] >= 0xF0 && u[0] < 0xF5 && (u[1] & 0xC0) == 0x80 && (u[2] & 0xC0) == 0x80 && (u[3] & 0xC0) == 0x80) {
		*cp = (u[0] & 0x07u) << 18 | (u[1] & 0x3Fu) << 12 | (u[2] & 0x3Fu) << 6 | (u[3] & 0x3Fu);
		return 4;
	}
	*cp = '?';
	return 1;
}

/* a traditional character missing from the font: its simplified form (t2s.h), or 0 */
static uint32_t gx_t2s(uint32_t cp)
{
	unsigned lo = 0, hi = sizeof ui_t2s / sizeof *ui_t2s;

	while (lo < hi) {
		unsigned mid = (lo + hi) / 2;

		if (ui_t2s[mid][0] == cp) return ui_t2s[mid][1];
		if (ui_t2s[mid][0] < cp) lo = mid + 1; else hi = mid;
	}
	return 0;
}

/* the glyph of cp; a traditional character the font lacks is drawn in its simplified form, anything else missing as a box */
static const UiGlyph *gx_glyph(const UiFont *f, uint32_t cp)
{
	const UiGlyph *g = gx_find(f, cp);
	uint32_t s;

	if (!g && (s = gx_t2s(cp))) g = gx_find(f, s);
	if (!g) g = gx_find(f, 0x25A1);
	if (!g) g = gx_find(f, '?');
	return g;
}

/* every character of s has its own glyph in f (no placeholder box) */
static int gx_has_all(const UiFont *f, const char *s)
{
	uint32_t cp;

	for (int n; (n = gx_utf8(s, &cp)) > 0; s += n)
		if (!gx_find(f, cp) && !(gx_t2s(cp) && gx_find(f, gx_t2s(cp)))) return 0;
	return 1;
}

static int gx_text_w(const UiFont *f, const char *s)
{
	int w = 0;
	uint32_t cp;

	for (int n; (n = gx_utf8(s, &cp)) > 0; s += n) {
		const UiGlyph *g = gx_glyph(f, cp);

		if (g) w += g->adv;
	}
	return w;
}

/* baseline-positioned text; returns the advance width */
/* strength 0..255 of a one-pixel shadow under light text (set while a wallpaper is behind the pages) */
static int gx_shadow;

static int gx_text(Cv *c, const UiFont *f, int x, int base, const char *s, uint32_t col, int alpha)
{
	int x0 = x, sh = gx_shadow && ((col >> 16 & 255) * 299 + (col >> 8 & 255) * 587 + (col & 255) * 114) / 1000 > 128 ? gx_shadow : 0;
	uint32_t cp;

	for (int n; (n = gx_utf8(s, &cp)) > 0; s += n) {
		const UiGlyph *g = gx_glyph(f, cp);

		if (!g) continue;
		const uint8_t *bits = ui_font_bits + g->off;

		for (int j = 0; sh && j < g->h; j++)
			for (int i = 0; i < g->w; i++) {
				unsigned idx = (unsigned)(j * g->w + i);
				unsigned v = (idx & 1) ? (bits[idx >> 1] & 15u) : (bits[idx >> 1] >> 4);

				if (v) gx_put(c, x + g->bx + i, base + g->by + j + 1, 0, (int)(v * 17u * (unsigned)alpha / 255u * (unsigned)sh / 255u));
			}
		for (int j = 0; j < g->h; j++)
			for (int i = 0; i < g->w; i++) {
				unsigned idx = (unsigned)(j * g->w + i);
				unsigned v = (idx & 1) ? (bits[idx >> 1] & 15u) : (bits[idx >> 1] >> 4);

				if (v) gx_put(c, x + g->bx + i, base + g->by + j, col, (int)(v * 17u * (unsigned)alpha / 255u));
			}
		x += g->adv;
	}
	return x - x0;
}

static int gx_text_c(Cv *c, const UiFont *f, int cx, int base, const char *s, uint32_t col, int alpha)
{
	int w = gx_text_w(f, s);

	gx_text(c, f, cx - w / 2, base, s, col, alpha);
	return w;
}

static int gx_text_r(Cv *c, const UiFont *f, int rx, int base, const char *s, uint32_t col, int alpha)
{
	int w = gx_text_w(f, s);

	gx_text(c, f, rx - w, base, s, col, alpha);
	return w;
}

/* copy of s shortened with an ellipsis so that it fits max_w pixels */
static void gx_fit(const UiFont *f, const char *s, int max_w, char *out, size_t cap)
{
	size_t len = strlen(s);

	if (len >= cap) len = cap - 1;
	memcpy(out, s, len);
	out[len] = 0;
	if (gx_text_w(f, out) <= max_w) return;
	for (;;) {
		/* drop one UTF-8 character from the end */
		size_t n = strlen(out);

		if (!n) return;
		do { n--; } while (n && (out[n] & 0xC0) == 0x80);
		out[n] = 0;
		char tmp[200];

		snprintf(tmp, sizeof tmp, "%s…", out);
		if (gx_text_w(f, tmp) <= max_w || !n) { snprintf(out, cap, "%s", tmp); return; }
	}
}
#endif
