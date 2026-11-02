/* Drawing of the screen UI: icons, widgets, the eight pages and the overlays.  The visible window of the panel is a
 * circle of about 216 px inside the 240x240 raster, so every layout below keeps its content inside that circle. */
#ifndef U30PRO_UI_PAGES_H
#define U30PRO_UI_PAGES_H
#include "common.h"
#include "qr.h"
#include "plugins.h"

#define FN12 (&ui_font_F12)
#define FN14 (&ui_font_F14)
#define FN16 (&ui_font_F16)
#define FB22 (&ui_font_B22)
#define FB34 (&ui_font_B34)
#define FB44 (&ui_font_B44)
#define FB56 (&ui_font_B56)

/* Layout of the UI: four pages side by side (swipe left/right), the control centre pulls down from the top edge, the SMS list pulls up from the
 * bottom edge, and the function page is a menu of second-level pages (built-in ones and the plugins of plugins.h). */
enum { PG_HOME, PG_SIGNAL, PG_TRAFFIC, PG_FUNC, NPAGES };
static const char *const page_names[NPAGES] = { "主页", "信号", "流量", "功能" };

enum { FN_WIFI, FN_POWER, FN_BATLOG, FN_SYSTEM, FN_DISPLAY, FN_ABOUT, FN_LOCK, NFN_BUILTIN };
enum { SH_NONE, SH_CTRL, SH_SMS };

enum { OV_NONE, OV_QR, OV_SMS };
enum { H_NONE, H_WIFI_TOGGLE, H_QR, H_RECONNECT, H_SLEEP, H_THEME, H_REBOOT, H_CLOSE,
       H_SMS_REFRESH, H_QR_ADMIN, H_BACK, H_SHEET_CLOSE, H_OPEN_SMS, H_OPEN_POWER, H_WAKE, H_LOCK_TOGGLE, H_PIN_SET, H_RELAY_TOGGLE, H_BYPASS,
       H_SIG_RAT, H_OPEN_BATLOG, H_GLASS, H_GLASS_BLUR, H_GLASS_TINT, H_WP_DIM, H_WP_LIVE,
       H_POWER_OFF, H_POWER_RESTART, H_POWER_CANCEL, H_POWER_CONFIRM,
       H_SMS_ROW = 100, H_GOTO = 200, H_FN = 300, H_PIN = 500, H_BH_RANGE = 600 };
/* keys of the PIN pad: H_PIN + 0..9 digits, then delete, confirm and cancel (setting a new PIN), back (unlock pad -> lock screen) */
enum { PIN_DEL = 10, PIN_OK, PIN_CANCEL, PIN_BACK };
/* what covers the screen: nothing, the unlock pad, the pad for a new PIN (first entry, then the repetition), the lock screen */
enum { PIN_NONE, PIN_UNLOCK, PIN_NEW, PIN_AGAIN, PIN_LOCKSCR };

/* A hit is the touch box of a control; how it lights up is a separate matter (the visual geometry vx..vr and the style):
 * FB_BG - the widget draws its own pressed state (tile, card, pill, round button: background layer, text stays sharp)
 * FB_RIPPLE - borderless hotspot (text/icon rows): a soft glow grows from the finger's own touch point
 * FB_DOT - a small round glow at (vx, vy) with radius vr (back chevron, mail icon)
 * FB_NONE - no feedback (full-screen close areas, sliders) */
enum { FB_NONE, FB_BG, FB_RIPPLE, FB_DOT };
typedef struct { int x, y, w, h, id, round, hold; short vx, vy, vw, vh, vr; unsigned char style; } Hit;

/* ------------------------------------------------------------------ UI state shared with the main loop */
static Hit hits[MAXHITS];
static int nhits, g_reg;
static long g_ms;
static int theme_idx, ov, ov_arg, sms_scroll, sms_lines_n;
static int power_menu, power_choice;
#define POWER_HOLD_MS 1500
static int sheet = SH_NONE, fn_sub = -1;
static float sheet_pos;
static int bright_max = 1024, off_idx = 2;	/* bright_max: the "on" level of the backlight (it cannot dim) */
static const int OFF_SECS[] = { 30, 60, 120, 300, 0 };
static const char *const OFF_NAMES[] = { "30秒", "1分钟", "2分钟", "5分钟", "常亮" };
/* what a touch does to the dark screen: wake it on a single tap, on a double tap, or nothing (the keys still wake it; the default, so that
 * the screen does not light up in a pocket or a bag) */
enum { WAKE_TAP, WAKE_DOUBLE, WAKE_KEY, NWAKE };
static const char *const WAKE_NAMES[] = { "单击", "双击", "关闭" };
static const char *const WAKE_KEYS[] = { "single", "double", "key" };
static int wake_idx = WAKE_KEY;
/* the Wi-Fi key (KEY_HOME): short and long press actions and the long-press time (u30pro_native_control.settings home_short_action,
 * home_long_action, home_long_press_ms, set in the web pages); the actions other than "page-next" run native-control.uc key-action */
enum { KA_PAGE, KA_NONE, KA_WIFI, KA_CELL, KA_REFRESH, NKA };
static const char *const KEY_ACTIONS[] = { "page-next", "none", "toggle-wifi", "reconnect-cellular", "refresh-status" };
static const char *const KEY_ACTION_NAMES[] = { "", "", "切换 Wi-Fi", "重新拨号", "刷新状态" };
static int key_short = KA_PAGE, key_long = KA_NONE, key_long_ms = 1800;
static int key_action_idx(const char *v, int fallback)
{
	for (int i = 0; v && i < NKA; i++) if (!strcmp(v, KEY_ACTIONS[i])) return i;
	return fallback;
}
static int key_long_ms_ok(int ms) { return ms == 1000 || ms == 1500 || ms == 1800 || ms == 2000 || ms == 2500 || ms == 3000 || ms == 4000 || ms == 5000; }
/* screen lock: lock_on with a PIN (pin_hash = "<16 hex salt>:<64 hex sha256(salt + PIN)>") locks the screen whenever it goes dark */
static int lock_on, locked, pin_mode, pin_len, pin_fails, pin_after_lock;
/* without a PIN the screen still locks (lock_soft): a long press of SOFT_HOLD_MS opens it, so that stray touches -- a pocket, a phone
 * held to the NFC tag behind the glass -- cannot work the controls; it locks when it goes dark and, when it never does (sleep 0),
 * after RELOCK_MS without a touch */
static int lock_soft;
#define SOFT_HOLD_MS 600
#define RELOCK_MS 60000L
static int pin_digits;		/* length of the stored PIN (0: not known, unlocking then needs confirm) */
static char pin_hash[96], pin_buf[8], pin_first[8];
static const char *pin_msg;
static long pin_msg_until, pin_wait_until, pin_err_until;
#define NOFF ((int)(sizeof OFF_SECS / sizeof *OFF_SECS))
static struct { char msg[96]; uint32_t col; long until, start; } toast;
static int hold_id, hold_pct;
/* press feedback: an envelope 0..1 (PRESS_DELAY of nothing, an 80 ms ease-out attack while held, a 180 ms ease-out release);
 * press_env is evaluated once per composed frame and read by the widgets through press_amt(id) */
#define PRESS_DELAY 35
#define PRESS_ATT 80
#define PRESS_REL 180
static Hit press_hit;
static int press_on, press_x, press_y;
static long press_t0, press_rel;
static float press_peak, press_env;
/* eased copies of the values drawn as arcs (the numbers next to them stay exact) */
static float sm_lv, sm_cpu, sm_mem, sm_snr;
static long wifi_busy_until, recon_busy_until, relay_busy_until;
static long bypass_busy_until;
static int wifi_want, relay_want, bypass_want;	/* where a switch is going while the change is under way (busy) */
static int sms_line_start[48], sms_line_len[48];
/* Vertical scrolling: slot 0 is the function list, slot 1 the second-level page that is open (it starts at the top whenever another one
 * opens).  A view that scrolls draws its content shifted by scroll_pos and clipped below its title (scroll_begin/scroll_end); scroll_live is
 * the slot of the view on screen in the last frame (-1: it does not scroll) and scroll_max how far its content reaches past the window. */
static float scroll_pos[2], scroll_max[2];
static int scroll_live = -1, scroll_sub = -1, scroll_moving;
static long view_t0;			/* when the open second-level page was opened: its tickers start from there */
static int hit_y0 = 0, hit_y1 = 240;	/* touch boxes are cut to this band (the visible part of a scrolling view) */
static int marquee_on, marquee_moving;	/* a ticker is on screen / running in the last frame */
static char notice[128];		/* a notice over everything ("first line|second line"), set by the "notice" command (firmware update) */

static inline const Theme *TH(void) { return &themes[theme_idx % NTHEMES]; }

#include "wall.h"

static Hit *last_hit;

/* touch box; the feedback style defaults to the finger-point glow, hit_fb() changes it for the hit just added */
static void hit_add(int x, int y, int w, int h, int id, int round, int hold)
{
	last_hit = NULL;
	if (!g_reg || nhits >= MAXHITS) return;
	if (y < hit_y0) { h -= hit_y0 - y; y = hit_y0; }
	if (y + h > hit_y1) h = hit_y1 - y;
	if (h < 12) return;
	hits[nhits] = (Hit){ x, y, w, h, id, round, hold, 0, 0, 0, 0, 0, FB_RIPPLE };
	last_hit = &hits[nhits++];
}

static void hit_fb(int style, int vx, int vy, int vw, int vh, int vr)
{
	if (!last_hit) return;
	last_hit->style = (unsigned char)style;
	last_hit->vx = (short)vx; last_hit->vy = (short)vy; last_hit->vw = (short)vw; last_hit->vh = (short)vh; last_hit->vr = (short)vr;
}

static float ease_out(float u) { u = u < 0.f ? 0.f : (u > 1.f ? 1.f : u); return 1.f - (1.f - u) * (1.f - u); }

static void press_begin(const Hit *h, int x, int y, long now)
{
	press_hit = *h; press_on = 1; press_x = x; press_y = y; press_t0 = now; press_rel = 0; press_peak = 0.f;
}

static float press_now(long now)
{
	if (!press_hit.id) return 0.f;
	if (press_on) return ease_out((float)(now - press_t0 - PRESS_DELAY) / (float)PRESS_ATT);
	if (press_rel && now < press_rel + PRESS_REL) return press_peak * (1.f - ease_out((float)(now - press_rel) / (float)PRESS_REL));
	return 0.f;
}

/* the finger lifted (a tap gets at least a visible 70 % pulse) or moved away / the press was taken over */
static void press_end(long now, int tap)
{
	if (!press_on) return;
	press_peak = press_now(now);
	if (tap && press_peak < .7f) press_peak = .7f;
	press_on = 0;
	press_rel = now;
}

static void press_kill(void) { press_on = 0; press_rel = 0; press_env = 0.f; }
static int press_busy(void) { return press_hit.id && (press_on || (press_rel && g_ms < press_rel + PRESS_REL)); }
static float press_amt(int id) { return id && press_hit.id == id ? press_env : 0.f; }

/* the list of the function menu: built-in entries first, then the plugins */
typedef struct { char title[32]; int plugin; int fn; } FnEntry;
static FnEntry fn_entries[NFN_BUILTIN + MAXPLUG];
static int nfn_entries;

static void fn_build(void)
{
	static const char *const names[NFN_BUILTIN] = { "Wi-Fi", "电源", "电池记录", "系统", "显示", "关于", "锁屏" };

	nfn_entries = 0;
	for (int i = 0; i < NFN_BUILTIN; i++) {
		snprintf(fn_entries[nfn_entries].title, sizeof fn_entries[0].title, "%s", names[i]);
		fn_entries[nfn_entries].plugin = -1;
		fn_entries[nfn_entries++].fn = i;
	}
	for (int i = 0; i < nplug && nfn_entries < NFN_BUILTIN + MAXPLUG; i++) {
		snprintf(fn_entries[nfn_entries].title, sizeof fn_entries[0].title, "%s", plugs[i].title);
		fn_entries[nfn_entries].plugin = i;
		fn_entries[nfn_entries++].fn = -1;
	}
}

static void toast_show(const char *msg, uint32_t col, int ms)
{
	snprintf(toast.msg, sizeof toast.msg, "%s", msg);
	toast.col = col;
	if (g_ms >= toast.until) toast.start = g_ms;	/* a toast replaced while still showing keeps its place instead of popping in again */
	toast.until = g_ms + ms;
}

/* ------------------------------------------------------------------ helpers */
static void TXT(Cv *c, const UiFont *f, int x, int base, const char *s, uint32_t col) { gx_text(c, f, x, base, s, col, 255); }
static void TXTC(Cv *c, const UiFont *f, int cx, int base, const char *s, uint32_t col) { gx_text_c(c, f, cx, base, s, col, 255); }
static void TXTR(Cv *c, const UiFont *f, int rx, int base, const char *s, uint32_t col) { gx_text_r(c, f, rx, base, s, col, 255); }
/* text centred on a row: the baseline sits 0.36 em below the row's centre line */
static void TXTM(Cv *c, const UiFont *f, int cx, int cy, const char *s, uint32_t col) { gx_text_c(c, f, cx, cy + (int)((float)f->size * .36f + .5f), s, col, 255); }

/* a card: translucent white over the gradient, glass over a wallpaper */
static void card(Cv *c, float x, float y, float w, float h, float r)
{
	if (wp_on) { gx_glass(c, x, y, w, h, r, 0, 0.f, 255); return; }
	gx_rrect(c, x, y, w, h, r, C_CARD, 17);
	gx_rrect_line(c, x, y, w, h, r, 1.f, C_CARD, 20);
}

/* a card whose control id is pressed: the surface brightens, the rim takes the accent colour and the card shrinks by a pixel */
static void card_p(Cv *c, float x, float y, float w, float h, float r, int id)
{
	float p = press_amt(id), k = p * 1.f;

	if (p <= 0.f) { card(c, x, y, w, h, r); return; }
	if (wp_on) {
		gx_glass(c, x + k, y + k, w - 2.f * k, h - 2.f * k, r - k, 0, p, 255);
		gx_rrect_line(c, x + k, y + k, w - 2.f * k, h - 2.f * k, r - k, 1.f, TH()->a, (int)(110.f * p));
		return;
	}
	gx_rrect(c, x + k, y + k, w - 2.f * k, h - 2.f * k, r - k, C_CARD, 17 + (int)(26.f * p));
	gx_rrect_line(c, x + k, y + k, w - 2.f * k, h - 2.f * k, r - k, 1.f, gx_mix(C_CARD, TH()->a, (unsigned)(150.f * p)), 20 + (int)(80.f * p));
}

static void pill(Cv *c, float x, float y, float w, float h, uint32_t col, int alpha) { gx_rrect(c, x, y, w, h, h * .5f, col, alpha); }

/* a pill that lightens while its control is pressed */
static void pill_p(Cv *c, float x, float y, float w, float h, uint32_t col, int alpha, int id)
{
	float p = press_amt(id), k = p * .8f;

	if (p > 0.f) col = gx_mix(col, 0xFFFFFF, (unsigned)(70.f * p));
	gx_rrect(c, x + k, y + k, w - 2.f * k, h - 2.f * k, (h - 2.f * k) * .5f, col, alpha);
}

/* Sizes follow the watch guidelines scaled to this panel (216 px round window, about 6.3 px per mm): body text 16 px, nothing smaller
 * than 14 px, touch targets at least 44 px high and as wide as the row. */
static void title_s(Cv *c, const char *name, int extra_dot)
{
	char t[48];
	int w;

	gx_fit(FN16, name, 130, t, sizeof t);
	w = gx_text_w(FN16, t);
	TXTC(c, FN16, 120, 36, t, C_DIM);
	if (extra_dot) gx_circle(c, (float)(120 + w / 2 + 8), 30.f, 3.5f, TH()->b, 255);
}

static void title(Cv *c, int p, int extra_dot) { title_s(c, page_names[p], extra_dot); }

/* title of a second-level page: a chevron before it; the whole top band (44 px high) goes back */
static void title_sub(Cv *c, const char *name)
{
	char t[48];
	int w;

	gx_fit(FN16, name, 116, t, sizeof t);
	w = gx_text_w(FN16, t);
	TXTC(c, FN16, 124, 37, t, C_TXT);
	gx_line(c, (float)(124 - w / 2 - 11), 26.f, (float)(124 - w / 2 - 17), 32.f, 2.f, C_DIM, 255);
	gx_line(c, (float)(124 - w / 2 - 17), 32.f, (float)(124 - w / 2 - 11), 38.f, 2.f, C_DIM, 255);
	/* the box covers chevron and title, the glow is only a dot on the chevron */
	hit_add(124 - w / 2 - 36, 10, w + 64, 44, H_BACK, 0, 0);
	hit_fb(FB_DOT, 124 - w / 2 - 14, 32, 0, 0, 14);
}

/* rows and cards of the second-level pages and the function list: 168 px wide (the round window is wide enough for that from y 50 to 200) */
#define ROW_X 36
#define ROW_W 168

/* a settings row (watch style): a full-width platter 44 px high, the name on the left, the value on the right in the accent colour (or the
 * name alone, centred, for an action) */
static void setting_row(Cv *c, int y, int id, const char *name, const char *value)
{
	const int x = ROW_X, w = ROW_W, h = 44;

	card_p(c, (float)x, (float)y, (float)w, (float)h, 22.f, id);
	hit_add(x, y, w, h, id, 0, 0);
	hit_fb(FB_BG, 0, 0, 0, 0, 0);
	if (value && value[0]) {
		TXT(c, FN16, x + 18, y + 28, name, C_TXT);
		TXTR(c, FN16, x + w - 18, y + 28, value, TH()->a);
	} else TXTM(c, FN16, x + w / 2, y + 22, name, C_TXT);
}

/* eased on/off state of a tile or switch (time constant 70 ms), so a toggle slides between its two looks instead of jumping; a control seen
 * for the first time starts settled */
static int tile_moving;

static float tile_ease(int id, int on)
{
	static struct { int id; float v; long t; } e[16];
	float target = on ? 1.f : 0.f;
	int i, spare = -1;

	for (i = 0; i < 16; i++) {
		if (e[i].id == id) break;
		if (!e[i].id && spare < 0) spare = i;
	}
	if (i == 16) {
		i = spare >= 0 ? spare : (int)(g_ms / 16 % 16);
		e[i].id = id; e[i].v = target; e[i].t = g_ms;
	}
	if (g_ms != e[i].t) {
		long dt = g_ms - e[i].t;

		e[i].v += (target - e[i].v) * (dt >= 500 ? 1.f : 1.f - expf(-(float)dt / 70.f));
		e[i].t = g_ms;
	}
	if (fabsf(target - e[i].v) < .02f) e[i].v = target;
	else tile_moving = 1;
	return e[i].v;
}

/* an on/off switch like iOS: a 44 x 26 capsule, green when on and grey when off, its white knob slides across and stretches a little while
 * the row is pressed; busy (the change is under way) puts a spinner on the knob */
static void toggle_sw(Cv *c, float x, float y, int on, int id, int busy)
{
	const float w = 44.f, h = 26.f, r = 11.f;
	float v = tile_ease(id, on), kw = 2.f * r + 6.f * press_amt(id), kx = x + 2.f + v * (w - 4.f - kw);

	pill(c, x, y, w, h, gx_mix(0x3B4555, 0x34C759, (unsigned)(v * 255.f)), 255);
	gx_rrect(c, kx, y + 3.f, kw, 2.f * r, r, 0x000000, 60);
	gx_rrect(c, kx, y + 2.f, kw, 2.f * r, r, 0xFFFFFF, 255);
	if (busy) {
		float a = (float)(g_ms % 900) * .4f;

		gx_arc(c, kx + kw * .5f, y + h * .5f, 5.f, 2.f, a, a + 240.f, 0x8A94A6, 255, 1);
	}
}

/* a settings row with a switch on the right; the whole row toggles */
static void setting_toggle(Cv *c, int y, int id, const char *name, int on, int busy)
{
	const int x = ROW_X, w = ROW_W, h = 44;

	card_p(c, (float)x, (float)y, (float)w, (float)h, 22.f, id);
	hit_add(x, y, w, h, id, 0, 0);
	hit_fb(FB_BG, 0, 0, 0, 0, 0);
	TXT(c, FN16, x + 18, y + 28, name, C_TXT);
	toggle_sw(c, (float)(x + w - 54), (float)y + 9.f, on, id, busy);
}

/* the "opens a page" mark at the right end of a row */
static void chevron_r(Cv *c, float x, float cy)
{
	gx_line(c, x - 3.f, cy - 6.f, x + 3.f, cy, 2.f, C_FAINT, 255);
	gx_line(c, x + 3.f, cy, x - 3.f, cy + 6.f, 2.f, C_FAINT, 255);
}

/* Text in the box x0..x1: centred when it fits, otherwise a ticker (watch style) that rests 1.5 s at its start, runs left at 30 px/s and
 * comes round again after a gap; while it runs both ends fade out at the sides of the box (at rest only the cut end does). */
static void marquee(Cv *c, const UiFont *f, int x0, int x1, int base, const char *s, uint32_t col)
{
	const int gap = 40, rest = 1500, speed = 30;
	int w = gx_text_w(f, s);

	if (w <= x1 - x0) { TXTC(c, f, (x0 + x1) / 2, base, s, col); return; }
	long cycle = rest + (long)(w + gap) * 1000 / speed, t = (g_ms - view_t0) % cycle;
	int off = t < rest ? 0 : (int)((t - rest) * speed / 1000);
	GxClip old = gx_clip(x0 - 12 + (off < 12 ? off : 12), -32768, x1, 32767, 12);

	TXT(c, f, x0 - off, base, s, col);
	if (off) TXT(c, f, x0 - off + w + gap, base, s, col);
	gx_unclip(old);
	marquee_on = 1;
	if (t >= rest) marquee_moving = 1;
}

/* A scrolling view runs from under its title to the bottom of the round window; content fades out over 14 px at both ends. */
#define VIEW_TOP 46
#define VIEW_BOT 226
static GxClip scroll_clip_old;

/* start drawing a scrolling view: returns the offset to take off the content's y coordinates */
static int scroll_begin(int slot)
{
	if (slot == 1 && fn_sub != scroll_sub) { scroll_sub = fn_sub; scroll_pos[1] = 0.f; view_t0 = g_ms; }
	if (g_reg) scroll_live = slot;
	scroll_clip_old = gx_clip(-32768, VIEW_TOP, 32767, VIEW_BOT, 14);
	hit_y0 = VIEW_TOP;
	hit_y1 = VIEW_BOT;
	return (int)floorf(scroll_pos[slot] + .5f);
}

/* end of a scrolling view whose content ends at bottom (content coordinates): the scroll range, and a scroll bar on the right edge (an arc
 * from 60 to 120 degrees, its thumb as long as the visible share) that is brighter while the view moves */
static void scroll_end(Cv *c, int slot, int bottom)
{
	float max = (float)(bottom - (VIEW_BOT - 14)), span = (float)(VIEW_BOT - VIEW_TOP), len, f, a0;

	gx_unclip(scroll_clip_old);
	hit_y0 = 0;
	hit_y1 = 240;
	scroll_max[slot] = max > 0.f ? max : 0.f;
	if (max <= 0.f) return;
	len = 60.f * span / (span + max);
	if (len < 14.f) len = 14.f;
	f = gx_clamp(scroll_pos[slot] / max, 0.f, 1.f);
	a0 = 60.f + (60.f - len) * f;
	gx_arc(c, 120, 120, 104, 3.f, 60.f, 120.f, C_TXT, 22, 1);
	gx_arc(c, 120, 120, 104, 3.f, a0, a0 + len, C_TXT, scroll_moving && g_reg ? 210 : 90, 1);
}

/* page dots: the current dot stretches into a pill, and while swiping the pill flows over to the page coming in (a = 0..1 of the way) */
static void dots(Cv *c, int cur, int next, float a)
{
	float x0 = 120.f - (NPAGES - 1) * 4.5f;

	for (int i = 0; i < NPAGES; i++) {
		float x = x0 + (float)i * 9.f, wt = (i == cur ? 1.f - a : 0.f) + (i == next ? a : 0.f), w = 4.f + 4.f * wt;
		uint32_t col = gx_mix(C_TXT, TH()->a, (unsigned)(wt * 255.f));

		pill(c, x - w * .5f, 216.f, w, 4.f, col, 70 + (int)(185.f * wt));
	}
}

/* ------------------------------------------------------------------ icons (all vector, cx/cy centre, s = half size) */
static void ic_signal(Cv *c, float x, float y, float w, float h, int level, uint32_t on)
{
	float bw = w / 7.f;

	for (int i = 0; i < 4; i++) {
		float bh = h * (.35f + .65f * (float)i / 3.f);

		gx_rrect(c, x + (float)i * bw * 2.f, y + h - bh, bw, bh, bw * .4f, i < level ? on : C_TXT, i < level ? 255 : 55);
	}
}

/* the fan of arcs is centred on (cx, cy): its top (outer arc) and bottom (dot) lie 0.67 s from it */
static void ic_wifi(Cv *c, float cx, float cy, float s, uint32_t col, int alpha)
{
	for (int i = 1; i <= 3; i++) gx_arc(c, cx, cy + s * .6125f, s * .42f * (float)i, s * .17f, -45.f, 45.f, col, alpha, 1);
	gx_circle(c, cx, cy + s * .6125f, s * .12f, col, alpha);
}

/* body and terminal together fill x .. x + w, y .. y + h */
static void ic_battery(Cv *c, float x, float y, float w, float h, int pct, uint32_t col)
{
	gx_rrect_line(c, x, y, w - 3.f, h, 3.f, 1.5f, C_TXT, 190);
	gx_rrect(c, x + w - 3.f, y + h * .3f, 2.5f, h * .4f, 1.f, C_TXT, 190);
	if (pct > 0) gx_rrect(c, x + 2.f, y + 2.f, (w - 7.f) * (float)(pct > 100 ? 100 : pct) / 100.f, h - 4.f, 1.5f, col, 255);
}

static void ic_bolt(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_tri(c, cx + s * .15f, cy - s, cx - s * .55f, cy + s * .1f, cx + s * .1f, cy + s * .1f, col, 255);
	gx_tri(c, cx - s * .15f, cy + s, cx + s * .55f, cy - s * .1f, cx - s * .1f, cy - s * .1f, col, 255);
}

static void ic_arrow(Cv *c, float cx, float cy, float s, int down, uint32_t col)
{
	if (down) gx_tri(c, cx - s, cy - s * .6f, cx + s, cy - s * .6f, cx, cy + s * .8f, col, 255);
	else gx_tri(c, cx - s, cy + s * .6f, cx + s, cy + s * .6f, cx, cy - s * .8f, col, 255);
}

static void ic_refresh(Cv *c, float cx, float cy, float s, uint32_t col)
{
	/* gx_arc angles start at 12 o'clock and run clockwise. The arrow
	 * follows the tangent at the actual end, rather than pointing backward. */
	const float a = 330.f * GX_PI / 180.f;
	float ex = cx + sinf(a) * s, ey = cy - cosf(a) * s;
	float tx = cosf(a), ty = sinf(a), nx = -ty, ny = tx;
	gx_arc(c, cx, cy, s, s * .26f, 45.f, 330.f, col, 255, 0);
	gx_tri(c, ex + tx * s * .2f, ey + ty * s * .2f,
	       ex - tx * s * .45f + nx * s * .36f, ey - ty * s * .45f + ny * s * .36f,
	       ex - tx * s * .45f - nx * s * .36f, ey - ty * s * .45f - ny * s * .36f, col, 255);
}

static void ic_sun(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_circle(c, cx, cy, s * .42f, col, 255);
	for (int i = 0; i < 8; i++) {
		float a = (float)i * GX_PI / 4.f, ca = cosf(a), sa = sinf(a);

		gx_line(c, cx + ca * s * .68f, cy + sa * s * .68f, cx + ca * s * .98f, cy + sa * s * .98f, s * .15f, col, 255);
	}
}

static void ic_moon(Cv *c, float cx, float cy, float s, uint32_t col, uint32_t under)
{
	gx_circle(c, cx, cy, s * .8f, col, 255);
	gx_circle(c, cx + s * .42f, cy - s * .3f, s * .7f, under, 255);
}

static void ic_palette(Cv *c, float cx, float cy, float s)
{
	gx_circle(c, cx - s * .45f, cy + s * .3f, s * .3f, themes[0].a, 255);
	gx_circle(c, cx + s * .45f, cy + s * .3f, s * .3f, themes[1].a, 255);
	gx_circle(c, cx, cy - s * .5f, s * .3f, themes[2].a, 255);
}

static void ic_power(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_arc(c, cx, cy + s * .05f, s * .78f, s * .22f, 40.f, 320.f, col, 255, 1);
	gx_line(c, cx, cy - s * .95f, cx, cy - s * .1f, s * .24f, col, 255);
}

static void ic_mail(Cv *c, float cx, float cy, float s, uint32_t col, int alpha)
{
	gx_rrect_line(c, cx - s, cy - s * .7f, s * 2.f, s * 1.4f, s * .2f, 1.8f, col, alpha);
	gx_line(c, cx - s * .9f, cy - s * .55f, cx, cy + s * .15f, 1.8f, col, alpha);
	gx_line(c, cx + s * .9f, cy - s * .55f, cx, cy + s * .15f, 1.8f, col, alpha);
}

/* ------------------------------------------------------------------ page chrome */
static void battery_halo(Cv *c)
{
	if (!D.bat_ok || D.bat_pct < 0) return;
	float f = (float)D.bat_pct / 100.f, sweep = 260.f * f;
	uint32_t col = D.bat_pct <= 15 ? C_BAD : (D.bat_pct <= 30 ? C_WARN : C_OK);

	gx_arc(c, 120, 120, 104, 5, -130, 130, C_TXT, 24, 1);
	if (sweep > 2.f) {
		gx_arc(c, 120, 120, 104, 5, -130, -130 + sweep, col, 255, 1);
		if (D.charging) {
			float a = -130.f + fmodf((float)(g_ms % 2400) / 2400.f * (sweep + 40.f), sweep + 40.f) - 20.f;
			float a0 = a < -130.f ? -130.f : a, a1 = a + 22.f > -130.f + sweep ? -130.f + sweep : a + 22.f;

			if (a1 > a0 + 1.f) gx_arc(c, 120, 120, 104, 5, a0, a1, C_TXT, 150, 1);
		}
	}
}

/* ------------------------------------------------------------------ HOME */
static const char *cell_text(char *buf, size_t cap)
{
	if (D.cell_connected) { snprintf(buf, cap, "%s", D.op[0] ? D.op : "已连接"); return buf; }
	if (!strcmp(D.cell_state, "waiting-sim")) return "无SIM卡";
	if (!strcmp(D.cell_state, "data-unavailable")) return "无数据";
	if (!D.feed_ok) return "启动中";
	return "拨号中…";
}

static void page_home(Cv *c)
{
	const Theme *th = TH();
	char b[64], num[24];
	const char *unit;

	/* rows (baselines): time 97, date 120, network 131..150, rates 175, footer 199, page dots 216 - about 10 px of air between them; over a
	 * wallpaper they sit on a disc of thin glass inside the battery ring */
	if (wp_on) gx_glass(c, 22.f, 22.f, 196.f, 196.f, 98.f, 1, 0.f, 255);
	battery_halo(c);
	if (D.clock_ok) {
		struct tm tm;
		time_t t = D.now;

		localtime_r(&t, &tm);
		snprintf(b, sizeof b, "%02d:%02d", tm.tm_hour, tm.tm_min);
		TXTC(c, FB56, 120, 97, b, C_TXT);
		snprintf(b, sizeof b, "%d月%d日 %s", tm.tm_mon + 1, tm.tm_mday, wday_name(tm.tm_wday));
		TXTC(c, FN14, 120, 120, b, C_DIM);
	} else {
		snprintf(b, sizeof b, "%ld:%02ld", D.uptime / 3600, (D.uptime % 3600) / 60);
		TXTC(c, FB56, 120, 97, b, C_TXT);
		TXTC(c, FN14, 120, 120, "已运行 · 等待授时", C_DIM);
	}
	/* network row */
	{
		const char *ct = cell_text(b, sizeof b);
		int rat5 = !strcmp(D.rat, "5G"), tw = gx_text_w(FN14, ct);
		/* the pill names the access technology only when it is known (the reader keeps the last reported one); never a guessed "4G" */
		int pw = D.cell_connected && D.rat[0] ? gx_text_w(FN14, D.rat) + 14 : 0, total = pw + (pw ? 6 : 0) + 20 + 6 + tw;
		float x = 120.f - (float)total / 2.f;

		if (pw) {
			pill(c, x, 131.f, (float)pw, 19.f, rat5 ? th->b : th->a, 255);
			TXTM(c, FN14, (int)x + pw / 2, 140, D.rat, 0x10151F);
			x += (float)pw + 6.f;
		}
		ic_signal(c, x, 132.f, 20.f, 17.f, D.cell_connected ? signal_bars_from_rsrp(D.rsrp, D.have_sig) : 0, D.cell_connected ? th->a : C_FAINT);
		TXT(c, FN14, (int)x + 26, 146, ct, D.cell_connected ? C_TXT : C_DIM);
		hit_add(36, 126, 168, 30, H_GOTO + PG_SIGNAL, 0, 0);
	}
	hit_add(40, 158, 160, 26, H_GOTO + PG_TRAFFIC, 0, 0);
	hit_add(52, 184, 136, 26, H_OPEN_POWER, 0, 0);
	/* speeds: arrow, number (16 px) and unit (14 px) twice, the pair centred by its real width */
	{
		char n2[24];
		const char *u2;
		int w1, w2, x;

		fmt_rate(D.rx_bps, num, sizeof num, &unit);
		fmt_rate(D.tx_bps, n2, sizeof n2, &u2);
		w1 = 12 + gx_text_w(FN16, num) + 2 + gx_text_w(FN14, unit);
		w2 = 12 + gx_text_w(FN16, n2) + 2 + gx_text_w(FN14, u2);
		x = 120 - (w1 + 16 + w2) / 2;
		ic_arrow(c, (float)x + 5.f, 169, 5.f, 1, th->a);
		x += 12;
		x += gx_text(c, FN16, x, 175, num, C_TXT, 255) + 2;
		x += gx_text(c, FN14, x, 175, unit, C_DIM, 255) + 16;
		ic_arrow(c, (float)x + 5.f, 169, 5.f, 0, th->b);
		x += 12;
		x += gx_text(c, FN16, x, 175, n2, C_TXT, 255) + 2;
		gx_text(c, FN14, x, 175, u2, C_DIM, 255);
	}
	/* footer facts: the icons centred on the digits (14 px digits stand from 189 to 199 on baseline 199) */
	ic_wifi(c, 80, 194, 8.f, D.wifi_active ? th->a : C_FAINT, 255);
	snprintf(b, sizeof b, "%d", D.nclients);
	TXT(c, FN14, 91, 199, b, D.wifi_active ? C_TXT : C_FAINT);
	if (D.bat_pct >= 0) snprintf(b, sizeof b, "%d%%", D.bat_pct); else snprintf(b, sizeof b, "--");
	ic_battery(c, 109.f, 189.f, 16.f, 10.f, D.bat_pct, D.bat_pct < 0 ? C_FAINT : (D.bat_pct <= 15 ? C_BAD : (D.bat_pct <= 30 ? C_WARN : C_OK)));
	TXT(c, FN14, 128, 199, b, C_TXT);
	if (D.charging) ic_bolt(c, 168, 194, 6.f, C_OK);
	else if (D.sms_unread > 0) { ic_mail(c, 174, 194, 6.f, th->b, 255); hit_add(160, 182, 28, 24, H_OPEN_SMS, 0, 0); hit_fb(FB_DOT, 174, 194, 0, 0, 14); }
}

/* ------------------------------------------------------------------ SIGNAL */
/* SINR on a gauge: -5 dB empty .. 25 dB full */
static float sinr_level(float db) { return gx_clamp((db + 5.f) / 30.f, 0.f, 1.f); }

/* one of the two gauges: a 270 degree arc open at the bottom, the value in the middle, the name under the opening */
static void sig_gauge(Cv *c, float cx, float cy, float lv, int ok, const char *val, const char *name, uint32_t col)
{
	gx_arc(c, cx, cy, 33, 6, -135, 135, C_TXT, 24, 1);
	if (ok && lv > .01f) gx_arc(c, cx, cy, 33, 6, -135, -135 + 270.f * lv, col, 255, 1);
	TXTC(c, FB22, (int)cx, (int)cy + 8, ok ? val : "--", ok ? C_TXT : C_FAINT);
	TXTC(c, FN14, (int)cx, (int)cy + 40, name, C_DIM);
}

/* the cell the signal page shows: with EN-DC the NR leg (unless LTE was picked) -- the one up now, or on an idle link the last one seen --,
 * otherwise the LTE cell */
static int sig_lte;
static int sig_nr(void) { return D.have_sig && (D.nr || D.nr_idle) && !sig_lte; }
static int sig_rsrp_ok(void) { return D.have_sig && (!sig_nr() || D.nr_rsrp_ok); }
static float sig_rsrp(void) { return sig_nr() ? D.nr_rsrp : D.rsrp; }
static int sig_sinr_ok(void) { return D.have_sig && (sig_nr() ? D.nr_sinr_ok : D.sinr_ok); }
static float sig_sinr(void) { return sig_nr() ? D.nr_sinr : D.sinr; }

/* in place of the title on an EN-DC anchor: "LTE | NR", the shown one filled (NR in the 5G colour of the home page); a tap swaps them.
 * On an idle link (the network adds the NR leg only while data flows) the NR side says so and shows the last NR cell; with no NR cell
 * seen at all it is greyed out and a tap explains why. */
static void sig_tabs(Cv *c, int nr)
{
	const Theme *th = TH();
	const float w = 120.f, h = 22.f, x = 120.f - w / 2.f, y = 22.f, lw = 48.f;
	const char *nrt = D.nr_idle ? "NR 空闲" : "NR";

	pill(c, x, y, w, h, C_TXT, 24);
	if (nr) pill(c, x + lw, y, w - lw, h, D.nr_idle ? gx_mix(C_CARD, th->b, 150) : th->b, 255);
	else pill(c, x, y, lw, h, th->a, 255);
	TXTM(c, FN14, (int)(x + lw / 2.f), (int)(y + h / 2.f), "LTE", nr ? C_DIM : 0x10151F);
	TXTM(c, FN14, (int)(x + lw + (w - lw) / 2.f), (int)(y + h / 2.f), nrt, nr ? 0x10151F : (D.nr || D.nr_idle ? C_DIM : C_FAINT));
	hit_add((int)x - 12, 12, (int)w + 24, 40, H_SIG_RAT, 0, 0);
	hit_fb(FB_DOT, nr ? (int)(x + lw / 2.f) : (int)(x + lw + (w - lw) / 2.f), (int)(y + h / 2.f), 0, 0, 14);
}

/* RSRP and SINR as two gauges side by side, under them the serving cell (band, PCI, channel, bandwidth) as a small table; with EN-DC and
 * its NR leg up, the NR cell or (tapped) the LTE anchor */
static void page_signal(Cv *c)
{
	static const char *const bw[] = { "1.4 MHz", "3 MHz", "5 MHz", "10 MHz", "15 MHz", "20 MHz" };
	char b[32], v[4][24];
	const char *labs[4] = { "频段", "PCI", "频点", "频宽" };
	int nr = sig_nr(), rok = sig_rsrp_ok(), sok = sig_sinr_ok();
	float rsrp = sig_rsrp(), sinr = sig_sinr(), lv = rok ? rsrp_level(rsrp) : 0.f;

	if (D.have_sig && (D.nr || D.endc)) sig_tabs(c, nr);
	else title(c, PG_SIGNAL, 0);
	snprintf(b, sizeof b, "%.0f", rsrp);
	/* the last NR cell of an idle link: its readings in grey, they are not current */
	sig_gauge(c, 78, 84, sm_lv, rok, b, "RSRP", nr && D.nr_idle ? C_DIM : (lv > .6f ? C_OK : (lv > .35f ? C_WARN : C_BAD)));
	snprintf(b, sizeof b, "%.1f", sinr);
	sig_gauge(c, 162, 84, sm_snr, sok, b, "SINR", nr && D.nr_idle ? C_DIM : (sinr > 13.f ? C_OK : (sinr > 0.f ? C_WARN : C_BAD)));
	if (nr) {
		snprintf(v[0], sizeof v[0], "n%d", D.nr_band);
		snprintf(v[1], sizeof v[1], "%d", D.nr_pci);
		snprintf(v[2], sizeof v[2], "%d", D.nr_arfcn);
		if (D.nr_bw > 0) snprintf(v[3], sizeof v[3], "%d MHz", D.nr_bw); else snprintf(v[3], sizeof v[3], "--");
	} else {
		snprintf(v[0], sizeof v[0], "B%d", D.band);
		snprintf(v[1], sizeof v[1], "%d", D.pci);
		snprintf(v[2], sizeof v[2], "%d", D.earfcn);
		snprintf(v[3], sizeof v[3], "%s", D.lte_bw[0] ? D.lte_bw : (D.bw_idx >= 0 && D.bw_idx <= 5 ? bw[D.bw_idx] : "--"));
	}
	for (int i = 0; i < 4; i++) {
		int y = 147 + i * 19;

		TXT(c, FN14, 66, y, labs[i], C_DIM);
		TXTR(c, FN16, 174, y, D.have_sig ? v[i] : "--", D.have_sig ? C_TXT : C_FAINT);
	}
}

/* ------------------------------------------------------------------ TRAFFIC */
static float nice_scale(float peak)
{
	static const float steps[] = { 20e3f, 50e3f, 100e3f, 250e3f, 500e3f, 1e6f, 2.5e6f, 5e6f, 10e6f, 25e6f, 50e6f, 100e6f, 250e6f };

	for (unsigned i = 0; i < sizeof steps / sizeof *steps; i++)
		if (peak <= steps[i]) return steps[i];
	return 1e9f;
}

static void chart(Cv *c, float x, float y, float w, float h)
{
	const Theme *th = TH();
	float peak = 0;

	for (int i = 0; i < 60; i++) {
		if (D.hist_rx[i] > peak) peak = D.hist_rx[i];
		if (D.hist_tx[i] > peak) peak = D.hist_tx[i];
	}
	float scale = nice_scale(peak);

	card(c, x, y, w, h, 10);
	for (int g = 1; g < 3; g++) gx_rect(c, (int)x + 6, (int)(y + h * (float)g / 3.f), (int)w - 12, 1, C_TXT, 18);
	float ix = x + 8, iw = w - 16, iy = y + 6, ih = h - 12;

	for (int pass = 0; pass < 2; pass++) {
		const float *hs = pass == 0 ? D.hist_rx : D.hist_tx;
		uint32_t col = pass == 0 ? th->a : th->b;
		float px = 0, py = 0;

		for (int i = 0; i < 60; i++) {
			float vx = ix + iw * (float)i / 59.f, v = hs[i] / scale;
			float vy = iy + ih * (1.f - (v > 1.f ? 1.f : v));

			if (pass == 0) gx_rect(c, (int)vx, (int)vy, 2, (int)(iy + ih - vy) + 1, col, 34);
			if (i) gx_line(c, px, py, vx, vy, 1.8f, col, 255);
			px = vx; py = vy;
		}
	}
}

static void page_traffic(Cv *c)
{
	const Theme *th = TH();
	char num[24], b[64], b2[32], b3[32];
	const char *unit;

	title(c, PG_TRAFFIC, 0);
	chart(c, 38, 46, 164, 66);
	for (int i = 0; i < 2; i++) {
		int x = i ? 164 : 76;
		double bps = i ? D.tx_bps : D.rx_bps;

		fmt_rate(bps, num, sizeof num, &unit);
		ic_arrow(c, (float)x - 18, 132, 5.f, !i, i ? th->b : th->a);
		TXTC(c, FN14, x + 6, 138, i ? "上行" : "下行", C_DIM);
		{
			int wn = gx_text_w(FB22, num), wu = gx_text_w(FN14, unit), x0 = x - (wn + 3 + wu) / 2;

			TXT(c, FB22, x0, 168, num, C_TXT);
			TXT(c, FN14, x0 + wn + 3, 168, unit, C_DIM);
		}
	}
	fmt_bytes(D.rx_total, b2, sizeof b2);
	fmt_bytes(D.tx_total, b3, sizeof b3);
	snprintf(b, sizeof b, "↓%s  ↑%s", b2, b3);
	{
		char fit[64];

		gx_fit(FN14, b, 146, fit, sizeof fit);
		TXTC(c, FN14, 120, 196, fit, C_DIM);
	}
}

/* ------------------------------------------------------------------ WI-FI */
/* The hotspot (its name as a ticker on a card that shows the QR code, a switch, band/channel/clients) and below it the Wi-Fi relay (a switch and
 * its state; the network to join and its password are set in the web interface, typing them on this screen is not practical).  It scrolls. */
static void page_wifi(Cv *c)
{
	const Theme *th = TH();
	char b[96], fit[96];
	int off, y;

	title_sub(c, "Wi-Fi");
	off = scroll_begin(1);
	y = 50 - off;
	card_p(c, ROW_X, (float)y, ROW_W, 62, 20, H_QR);
	hit_add(ROW_X, y, ROW_W, 62, H_QR, 0, 0);
	hit_fb(FB_BG, 0, 0, 0, 0, 0);
	if (D.ssid[0]) {
		const UiFont *sf = gx_has_all(FB22, D.ssid) ? FB22 : FN16;

		marquee(c, sf, ROW_X + 14, ROW_X + ROW_W - 14, y + 29, D.ssid, D.wifi_active ? C_TXT : C_DIM);
		TXTC(c, FN14, 120, y + 50, D.wifi_active ? "点按显示二维码" : "热点未开启", C_DIM);
	} else TXTC(c, FN16, 120, y + 36, "尚无热点配置", C_DIM);
	y += 72;
	setting_toggle(c, y, H_WIFI_TOGGLE, "热点", g_ms < wifi_busy_until ? wifi_want : D.wifi_active, g_ms < wifi_busy_until);
	y += 44 + 26;
	/* "5 GHz" -> "5G" keeps the line short */
	if (D.channel) snprintf(b, sizeof b, "%.*s%s%sCH%d · %d 台设备", (int)strcspn(D.wifi_band, " "), D.wifi_band, D.wifi_band[0] ? "G" : "", D.wifi_band[0] ? " · " : "", D.channel, D.nclients);
	else snprintf(b, sizeof b, "%d 台设备", D.nclients);
	gx_fit(FN14, b, 150, fit, sizeof fit);
	TXTC(c, FN14, 120, y, fit, C_DIM);
	for (int i = 0; i < D.nclients && i < 6; i++) {
		const char *ip = D.cl[i].ip, *dot = strrchr(ip, '.');
		char sip[24];

		y += 24;
		/* hosts of the router's own subnet show as ".101" */
		if (dot && !strncmp(ip, "192.168.25.", 11)) snprintf(sip, sizeof sip, "%s", dot); else snprintf(sip, sizeof sip, "%.23s", ip);
		gx_fit(FN14, D.cl[i].name[0] ? D.cl[i].name : "未知设备", 112 - gx_text_w(FN14, sip), fit, sizeof fit);
		gx_circle(c, 58.f, (float)y - 5.f, 3.f, th->a, 255);
		TXT(c, FN14, 66, y, fit, C_TXT);
		TXTR(c, FN14, 184, y, sip, C_DIM);
	}
	/* the relay */
	y += 34;
	TXT(c, FN14, ROW_X + 18, y, "中继", C_DIM);
	y += 10;
	setting_toggle(c, y, H_RELAY_TOGGLE, "Wi-Fi 中继", g_ms < relay_busy_until ? relay_want : D.relay_on, g_ms < relay_busy_until);
	y += 44 + 24;
	if (!D.relay_cfg[0]) {
		TXTC(c, FN14, 120, y, "尚未设置上级网络", C_DIM);
		y += 20;
		TXTC(c, FN14, 120, y, "请在网页中设置中继网络", C_FAINT);
	} else {
		int on = g_ms < relay_busy_until ? relay_want : D.relay_on;
		const char *state = D.relay_state == 2 && on ? "已连接" : (on ? "正在连接" : "上级网络");

		snprintf(b, sizeof b, "%s %s", state, D.relay_state == 2 && on && D.relay_ssid[0] ? D.relay_ssid : D.relay_cfg);
		gx_fit(FN14, b, 150, fit, sizeof fit);
		TXTC(c, FN14, 120, y, fit, D.relay_state == 2 && on ? C_OK : C_DIM);
	}
	scroll_end(c, 1, y + off + 10);
}

/* ------------------------------------------------------------------ POWER */
/* The charge as a ring along the lower edge of the window (the home screen's ring turned over: 260 degrees through the bottom, filled from
 * the bottom outwards to both sides so that the page stays symmetric), the percentage and the state at the top, the charger and the battery
 * as two centred columns (voltage, current, power; every value carries its unit) and the switch of direct supply (the charger runs the
 * device, the battery is not charged: "inhibit-charge").  The charger measures no current, so its column shows the measured USB voltage and
 * what it may deliver (the input current limit, marked "≤"). */
static void fmt_signed(char *b, size_t cap, float v, const char *unit)
{
	snprintf(b, cap, "%s%.2f%s", v > .005f ? "+" : (v < -.005f ? "-" : ""), fabsf(v), unit);
}

static void page_power(Cv *c)
{
	char b[64], est[48] = "", cv[3][16], bv[3][16];
	int pct = D.bat_pct, bypass = g_ms < bypass_busy_until ? bypass_want : D.chg_bypass;
	uint32_t col = pct < 0 ? C_FAINT : (pct <= 15 ? C_BAD : (pct <= 30 ? C_WARN : C_OK));
	float span = pct > 0 ? 260.f * (float)pct / 100.f : 0.f;
	const char *state;

	if (g_reg) want_charger = 1;
	title_sub(c, "电源");
	gx_arc(c, 120, 120, 104, 6, 50, 310, C_TXT, 24, 1);
	if (span > 1.f) {
		gx_arc(c, 120, 120, 104, 6, 180.f - span * .5f, 180.f + span * .5f, col, 255, 1);
		if (D.charging) {
			/* while charging two glints run from the bottom outwards */
			float h = span * .5f, a = fmodf((float)(g_ms % 2400) / 2400.f * (h + 30.f), h + 30.f) - 15.f;
			float a0 = a < 0.f ? 0.f : a, a1 = a + 16.f > h ? h : a + 16.f;

			if (a1 > a0 + 1.f) {
				gx_arc(c, 120, 120, 104, 6, 180.f + a0, 180.f + a1, C_TXT, 150, 1);
				gx_arc(c, 120, 120, 104, 6, 180.f - a1, 180.f - a0, C_TXT, 150, 1);
			}
		}
	}
	/* the percentage, a bolt before it while charging */
	if (pct >= 0) {
		snprintf(b, sizeof b, "%d", pct);
		int w = gx_text_w(FB34, b), wp = gx_text_w(FN16, "%"), bolt = D.charging ? 14 : 0, x = 120 - (bolt + w + 2 + wp) / 2;

		if (bolt) ic_bolt(c, (float)x + 5.f, 62.f, 7.f, C_OK);
		TXT(c, FB34, x + bolt, 74, b, C_TXT);
		TXT(c, FN16, x + bolt + w + 2, 74, "%", C_DIM);
	} else TXTC(c, FB34, 120, 74, "--", C_FAINT);
	/* the percentage opens the battery history */
	hit_add(70, 44, 100, 56, H_OPEN_BATLOG, 0, 0);
	hit_fb(FB_RIPPLE, 0, 0, 0, 0, 0);
	/* time estimate from charge counter and current */
	if (pct >= 0 && D.bat_a > .05f && D.bat_full_mah > 100.f && (D.charging || !D.chg_online)) {
		float mah = D.bat_full_mah * (D.charging ? (float)(100 - pct) : (float)pct) / 100.f, hours = mah / (D.bat_a * 1000.f);

		if (hours > 0.f && hours < 99.f) {
			int mins = (int)(hours * 60.f);

			snprintf(est, sizeof est, D.charging ? " · 约%d时%02d分充满" : " · 可用约%d时%02d分", mins / 60, mins % 60);
		}
	}
	if (D.chg_paused) state = "温度过高 · 已暂停充电";
	else if (!D.bat_ok) state = "无电池数据";
	else if (D.chg_online && bypass) state = "直供电 · 电池不充电";
	else if (D.full) state = "已充满";
	else state = D.charging ? "充电中" : (D.chg_online ? "已接电源" : "电池供电");
	snprintf(b, sizeof b, "%s%s", state, D.chg_paused || bypass ? "" : est);
	TXTC(c, FN14, 120, 94, b, D.chg_paused ? C_WARN : C_DIM);
	/* the table: row names on the left, the charger's and the battery's column right-aligned */
	if (D.chg_online) {
		snprintf(cv[0], sizeof cv[0], "%.2fV", D.chg_vbus);
		snprintf(cv[1], sizeof cv[1], "≤%.1fA", D.chg_ilim);
		snprintf(cv[2], sizeof cv[2], "≤%.0fW", D.chg_vbus * D.chg_ilim);
	} else for (int i = 0; i < 3; i++) snprintf(cv[i], sizeof cv[i], "--");
	snprintf(bv[0], sizeof bv[0], "%.2fV", D.bat_v);
	fmt_signed(bv[1], sizeof bv[1], D.bat_i, "A");
	fmt_signed(bv[2], sizeof bv[2], D.bat_i * D.bat_v, "W");
	TXTC(c, FN14, 84, 118, "充电器", C_DIM);
	TXTC(c, FN14, 156, 118, "电池", C_DIM);
	gx_rect(c, 120, 106, 1, 70, C_TXT, 30);
	for (int i = 0; i < 3; i++) {
		int y = 138 + i * 18;

		TXTC(c, FN16, 84, y, cv[i], D.chg_online ? C_TXT : C_FAINT);
		TXTC(c, FN16, 156, y, bv[i], C_TXT);
	}
	/* direct supply */
	TXTR(c, FN16, 116, 199, "直供电", C_TXT);
	toggle_sw(c, 124.f, 180.f, bypass, H_BYPASS, g_ms < bypass_busy_until);
	hit_add(60, 176, 120, 34, H_BYPASS, 0, 0);
	hit_fb(FB_NONE, 0, 0, 0, 0, 0);
}

/* ------------------------------------------------------------------ BATTERY HISTORY */
/* The records of u30pro-battlog (data.h batlog_load): four ranges as a segmented switch (since the last full charge, since the last charge,
 * 24 h, 7 days), the charge over the range (a line over a soft fill; the steps on the charger marked green underneath), the power drawn
 * from the battery as bars with its average, and two lines of text: where the range starts, then the time on battery and the points used. */
static int bh_range = BH_FULL;

static void fmt_hm(char *b, size_t cap, long s)
{
	if (s >= 86400) snprintf(b, cap, "%ld天%ld时", s / 86400, s % 86400 / 3600);
	else snprintf(b, cap, "%ld时%02ld分", s / 3600, s % 3600 / 60);
}

static void page_batlog(Cv *c)
{
	static const char *const segs[NBH] = { "充满", "充电", "24h", "7d" };
	const Theme *th = TH();
	const float sx = 36.f, sw = 168.f, sh = 22.f, sy = 48.f, gw = sw / NBH;
	const BattRange *r = &bh[bh_range];
	char b[96], b2[32], fit[96];
	int peak = 0;

	batlog_load();
	title_sub(c, "电池记录");
	pill(c, sx, sy, sw, sh, C_TXT, 24);
	pill(c, sx + gw * (float)bh_range, sy, gw, sh, th->a, 255);
	for (int i = 0; i < NBH; i++) {
		TXTM(c, FN14, (int)(sx + gw * ((float)i + .5f)), (int)(sy + sh / 2.f), segs[i], i == bh_range ? 0x10151F : C_DIM);
		hit_add((int)(sx + gw * (float)i), (int)sy - 6, (int)gw, (int)sh + 12, H_BH_RANGE + i, 0, 0);
		hit_fb(FB_DOT, (int)(sx + gw * ((float)i + .5f)), (int)(sy + sh / 2.f), 0, 0, 14);
	}
	if (!bh_ok || r->n == 0) {
		TXTC(c, FN16, 120, 124, "暂无记录", C_DIM);
		TXTC(c, FN14, 120, 148, "每分钟记录一次电量", C_FAINT);
		return;
	}
	/* the charge: 0..100 %, a line over a soft fill, the steps on the charger marked green along the bottom; its name and the percentage
	   now on the left */
	{
		const float x = 30.f, y = 74.f, w = 180.f, h = 50.f, ix = 78.f, iw = 124.f, iy = y + 6.f, ih = h - 12.f;
		float px = 0.f, py = 0.f;
		int have = 0;

		card(c, x, y, w, h, 10);
		TXT(c, FN14, (int)x + 10, (int)y + 21, "电量", C_DIM);
		if (r->pct_now >= 0) {
			snprintf(b, sizeof b, "%d%%", r->pct_now);
			TXT(c, FN14, (int)x + 10, (int)y + 40, b, C_TXT);
		}
		gx_rect(c, (int)ix, (int)(iy + ih / 2.f), (int)iw, 1, C_TXT, 18);
		for (int i = 0; i < r->n; i++) {
			float vx = ix + iw * (float)i / (float)(r->n > 1 ? r->n - 1 : 1), sw1 = iw / (float)(r->n > 1 ? r->n - 1 : 1);
			char st = r->st[i];

			if (st == 'C' || st == 'F' || st == 'P')
				gx_rect(c, (int)(vx - sw1 / 2.f), (int)(iy + ih + 1.f), (int)sw1 + 1, 3, st == 'P' ? C_DIM : C_OK, 220);
			if (r->pct[i] < 0) { have = 0; continue; }
			float vy = iy + ih * (1.f - (float)r->pct[i] / 100.f);

			if (have) {
				/* the fill under the segment, one pixel column at a time */
				for (int xx = (int)px; xx < (int)vx; xx++) {
					float yy = py + (vy - py) * ((float)xx - px) / (vx - px);

					gx_rect(c, xx, (int)yy, 1, (int)(iy + ih - yy) + 1, th->a, 40);
				}
				gx_line(c, px, py, vx, vy, 1.8f, th->a, 255);
			}
			px = vx; py = vy; have = 1;
		}
	}
	/* the power drawn from the battery: bars scaled to a round peak; its name and the average on the left */
	{
		const float x = 30.f, y = 130.f, w = 180.f, h = 36.f, ix = 78.f, iw = 124.f, iy = y + 5.f, ih = h - 10.f;
		float scale, bw = iw / (float)r->n;

		for (int i = 0; i < r->n; i++) if (r->mw[i] > peak) peak = r->mw[i];
		scale = peak <= 1000 ? 1000.f : (peak <= 2000 ? 2000.f : (peak <= 3000 ? 3000.f : (peak <= 5000 ? 5000.f : 10000.f)));
		card(c, x, y, w, h, 10);
		TXT(c, FN14, (int)x + 10, (int)y + 17, "耗电", C_DIM);
		if (r->avg_mw >= 0) {
			snprintf(b, sizeof b, "%.1fW", (float)r->avg_mw / 1000.f);
			TXT(c, FN14, (int)x + 10, (int)y + 32, b, C_TXT);
		}
		gx_rect(c, (int)ix, (int)iy, (int)iw, 1, C_TXT, 18);
		for (int i = 0; i < r->n; i++) {
			float v = (float)r->mw[i] / scale;

			if (r->mw[i] <= 0) continue;
			if (v > 1.f) v = 1.f;
			gx_rect(c, (int)(ix + bw * (float)i), (int)(iy + ih * (1.f - v)), bw > 2.f ? (int)bw - 1 : 1, (int)(ih * v) + 1, th->b, 210);
		}
	}
	/* where the range starts */
	if (bh_range == BH_DAY) snprintf(b, sizeof b, "最近 24 小时");
	else if (bh_range == BH_WEEK) snprintf(b, sizeof b, "最近 7 天");
	else if (!r->found) snprintf(b, sizeof b, "%s", bh_range == BH_FULL ? "未充满过 · 全部记录" : "未充过电 · 全部记录");
	else {
		struct tm tm;
		time_t t = r->t0;

		localtime_r(&t, &tm);
		snprintf(b, sizeof b, "%d/%d %02d:%02d %s", tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, bh_range == BH_FULL ? "充满后" : "开始充电");
	}
	gx_fit(FN14, b, 180, fit, sizeof fit);
	TXTC(c, FN14, 120, 185, fit, C_DIM);
	/* the use on battery */
	if (r->onbat_s >= 60) {
		fmt_hm(b2, sizeof b2, r->onbat_s);
		snprintf(b, sizeof b, "电池 %s · 掉%d%%", b2, r->used);
	} else snprintf(b, sizeof b, "一直接着电源");
	gx_fit(FN14, b, 160, fit, sizeof fit);
	TXTC(c, FN14, 120, 204, fit, C_TXT);
}

/* ------------------------------------------------------------------ SMS */
static void page_sms(Cv *c)
{
	const Theme *th = TH();
	char fit[128], b[64];

	title_s(c, "短信", D.sms_unread > 0);
	if (D.nsms == 0) {
		ic_mail(c, 120, 100, 20.f, C_FAINT, 200);
		TXTC(c, FN14, 120, 150, D.feed_ok ? "暂无短信" : "读取中…", C_DIM);
		hit_add(40, 60, 160, 120, H_SMS_REFRESH, 0, 0);
		hit_fb(FB_NONE, 0, 0, 0, 0, 0);
		return;
	}
	int first = sms_scroll < 0 ? 0 : sms_scroll;

	if (first > D.nsms - 3) first = D.nsms > 3 ? D.nsms - 3 : 0;
	for (int r = 0; r < 3 && first + r < D.nsms; r++) {
		const Sms *s = &D.sms[first + r];
		int y = 48 + r * 58;

		card_p(c, 42, (float)y, 156, 50, 14, H_SMS_ROW + first + r);
		hit_add(42, y, 156, 50, H_SMS_ROW + first + r, 0, 0);
		hit_fb(FB_BG, 0, 0, 0, 0, 0);
		if (s->unread) gx_circle(c, 53, (float)y + 16, 3.5f, th->b, 255);
		gx_fit(FN16, s->addr[0] ? s->addr : "未知号码", 92, fit, sizeof fit);
		TXT(c, FN16, s->unread ? 60 : 54, y + 22, fit, C_TXT);
		snprintf(b, sizeof b, "%s", s->ts + (strlen(s->ts) > 11 ? 5 : 0));
		if (strlen(b) > 5) b[5] = 0;
		TXTR(c, FN14, 188, y + 21, b, C_DIM);
		gx_fit(FN14, s->text, 134, fit, sizeof fit);
		TXT(c, FN14, 54, y + 41, fit, C_DIM);
	}
}

/* ------------------------------------------------------------------ SYSTEM */
static void ring_gauge(Cv *c, float cx, float cy, float r, float pct, float arc_pct, const char *label, uint32_t col)
{
	char b[16];

	gx_arc(c, cx, cy, r, 7, -135, 135, C_TXT, 24, 1);
	if (arc_pct > 1.f) gx_arc(c, cx, cy, r, 7, -135, -135 + 270.f * (arc_pct > 100.f ? 1.f : arc_pct / 100.f), col, 255, 1);
	snprintf(b, sizeof b, "%.0f%%", pct);
	TXTM(c, FN16, (int)cx, (int)cy, b, C_TXT);
	TXTC(c, FN14, (int)cx, (int)(cy + r + 26), label, C_DIM);
}

static void page_system(Cv *c)
{
	char b[96], d[48];
	const Theme *th = TH();

	title_sub(c, "系统");
	ring_gauge(c, 82, 88, 28, D.cpu_pct, sm_cpu, "处理器", th->a);
	ring_gauge(c, 158, 88, 28, D.mem_pct, sm_mem, "内存", th->b);
	fmt_duration(D.uptime, d, sizeof d);
	snprintf(b, sizeof b, "运行 %s", d);
	TXTC(c, FN16, 120, 168, b, C_TXT);
	{
		char ver[40];
		size_t n = strcspn(D.kernel, "-");	/* 6.12.94-u30pro-p1 -> 6.12.94 */

		snprintf(ver, sizeof ver, "%.*s", (int)n, D.kernel);
		snprintf(b, sizeof b, "Linux %s%s%s", ver, D.slot[0] ? " · 槽位 " : "", D.slot[0] ? D.slot : "");
	}
	{
		char fit[96];

		gx_fit(FN14, b, 156, fit, sizeof fit);
		TXTC(c, FN14, 120, 185, fit, C_DIM);
	}
	if (D.temp_c > -50.f) snprintf(b, sizeof b, "负载 %.2f · %.0f°C", D.load1, D.temp_c);
	else snprintf(b, sizeof b, "负载 %.2f · 内存 %d MB", D.load1, D.mem_total_mb);
	TXTC(c, FN14, 120, 207, b, C_DIM);
}

#include "menu.h"

/* ------------------------------------------------------------------ overlays */
static void scrim(Cv *c, int alpha)
{
	for (int i = 0; i < c->w * c->h; i++) c->p[i] = gx_mix(c->p[i], 0x05080E, (unsigned)alpha);
}

static void ov_qr(Cv *c)
{
	static QrCode q;
	char payload[200], esc_s[100], esc_p[160];
	const char *s;
	int n = 0;

	scrim(c, 255);
	/* WIFI:T:WPA;S:<ssid>;P:<psk>;; with the reserved characters escaped */
	for (s = D.ssid; *s && n < (int)sizeof esc_s - 3; s++) {
		if (strchr("\\;,:\"", *s)) esc_s[n++] = '\\';
		esc_s[n++] = *s;
	}
	esc_s[n] = 0;
	n = 0;
	for (s = D.psk; *s && n < (int)sizeof esc_p - 3; s++) {
		if (strchr("\\;,:\"", *s)) esc_p[n++] = '\\';
		esc_p[n++] = *s;
	}
	esc_p[n] = 0;
	if (ov_arg >= 2) {
		const Plugin *pl = &plugs[(ov_arg - 2) % (nplug > 0 ? nplug : 1)];

		snprintf(payload, sizeof payload, "%s", pl->text[0] ? pl->text : pl->msg);
		if (!payload[0]) snprintf(payload, sizeof payload, " ");
	} else if (ov_arg == 1) snprintf(payload, sizeof payload, "http://192.168.25.1/");
	else if (D.psk[0]) snprintf(payload, sizeof payload, "WIFI:T:WPA;S:%s;P:%s;;", esc_s, esc_p);
	else snprintf(payload, sizeof payload, "WIFI:T:nopass;S:%s;;", esc_s);
	int ver = qr_encode(&q, payload, (int)strlen(payload));

	if (!ver) {
		TXTC(c, FN14, 120, 124, "名称或密码过长", C_DIM);
		hit_add(0, 0, 240, 240, H_CLOSE, 0, 0);
		hit_fb(FB_NONE, 0, 0, 0, 0, 0);
		return;
	}
	int mod = 146 / (q.size + 2);

	if (mod < 2) mod = 2;
	int side = (q.size + 2) * mod, x0 = 120 - side / 2, y0 = 105 - side / 2;

	gx_rrect(c, (float)x0 - 4, (float)y0 - 4, (float)side + 8, (float)side + 8, 8, 0xFFFFFF, 255);
	for (int y = 0; y < q.size; y++)
		for (int x = 0; x < q.size; x++)
			if (q.dark[y * q.size + x]) gx_rect(c, x0 + (x + 1) * mod, y0 + (y + 1) * mod, mod, mod, 0x0B1220, 255);
	{
		char fit[96];

		gx_fit(FN14, ov_arg >= 2 ? plugs[(ov_arg - 2) % (nplug > 0 ? nplug : 1)].title : (ov_arg == 1 ? "管理页面 192.168.25.1" : D.ssid), 150, fit, sizeof fit);
		TXTC(c, FN14, 120, 198, fit, C_TXT);
		TXTC(c, FN14, 120, 215, "点按关闭", C_DIM);
	}
	hit_add(0, 0, 240, 240, H_CLOSE, 0, 0);
	hit_fb(FB_NONE, 0, 0, 0, 0, 0);
}

/* line breaks at spaces for Latin text, between any two characters for CJK */
static int wrap_text(const UiFont *f, const char *s, int maxw)
{
	int n = 0, i = 0, w = 0, start = 0, last_space = -1, w_at_space = 0;
	size_t len = strlen(s);

	while ((size_t)i < len && n < 47) {
		uint32_t cp;
		int step = gx_utf8(s + i, &cp);

		if (step <= 0) break;
		const UiGlyph *g = gx_glyph(f, cp);
		int gw = g ? g->adv : 8;

		if (cp == '\n') {
			sms_line_start[n] = start; sms_line_len[n] = i - start; n++;
			start = i + step; w = 0; last_space = -1; i += step;
			continue;
		}
		if (w + gw > maxw && i > start) {
			if (last_space > start) {
				sms_line_start[n] = start; sms_line_len[n] = last_space - start; n++;
				start = last_space + 1;
				w -= w_at_space;
			} else {
				sms_line_start[n] = start; sms_line_len[n] = i - start; n++;
				start = i; w = 0;
			}
			last_space = -1;
		}
		if (cp == ' ') { last_space = i; w_at_space = w + gw; }
		w += gw;
		i += step;
	}
	if (start < (int)len && n < 48) { sms_line_start[n] = start; sms_line_len[n] = (int)len - start; n++; }
	return n;
}

static void ov_sms(Cv *c)
{
	const Sms *s = &D.sms[ov_arg < D.nsms ? ov_arg : 0];
	char fit[96], line[128];

	scrim(c, 255);
	gx_fit(FN16, s->addr, 130, fit, sizeof fit);
	TXTC(c, FN16, 120, 40, fit, C_TXT);
	gx_fit(FN14, s->ts, 150, fit, sizeof fit);
	TXTC(c, FN14, 120, 58, fit, C_DIM);
	sms_lines_n = wrap_text(FN16, s->text, 160);
	int vis = 5, first = sms_scroll;

	if (first > sms_lines_n - vis) first = sms_lines_n - vis;
	if (first < 0) first = 0;
	for (int i = 0; i < vis && first + i < sms_lines_n; i++) {
		int len = sms_line_len[first + i];

		if (len > (int)sizeof line - 1) len = (int)sizeof line - 1;
		memcpy(line, s->text + sms_line_start[first + i], (size_t)len);
		line[len] = 0;
		TXT(c, FN16, 40, 86 + i * 24, line, C_TXT);
	}
	if (sms_lines_n > vis) {
		float th = 112.f * (float)vis / (float)sms_lines_n, y = 70.f + (112.f - th) * (float)first / (float)(sms_lines_n - vis);

		gx_rrect(c, 204, y, 3, th, 1.5f, TH()->a, 200);
	}
}

/* a toast slides down from y=24 to y=40 while fading in (180 ms) and drifts up while fading out (last 250 ms) */
static void draw_toast(Cv *c)
{
	if (g_ms >= toast.until || !toast.msg[0]) return;
	char fit[96];
	long left = toast.until - g_ms, age = g_ms - toast.start;
	float in = ease_out((float)age / 180.f), out = left < 250 ? ease_out((float)(250 - left) / 250.f) : 0.f;
	float y = 24.f + 16.f * in - 8.f * out;
	int alpha = (int)(255.f * in * (1.f - out));

	if (alpha <= 0) return;
	gx_fit(FN14, toast.msg, 140, fit, sizeof fit);
	int w = gx_text_w(FN14, fit) + 28;
	float x = 120.f - (float)w / 2.f;

	pill(c, x, y, (float)w, 26.f, 0x1B2638, alpha);
	gx_rrect_line(c, x, y, (float)w, 26.f, 13.f, 1.2f, toast.col, alpha);
	gx_circle(c, x + 12.f, y + 13.f, 3.5f, toast.col, alpha);
	gx_text(c, FN14, 120 - w / 2 + 21, (int)(y + 18.f + .5f), fit, C_TXT, alpha);
}

static void draw_page(Cv *c, int p)
{
	switch (p) {
	case PG_HOME: page_home(c); break;
	case PG_SIGNAL: page_signal(c); break;
	case PG_TRAFFIC: page_traffic(c); break;
	default:
		if (fn_sub >= 0) { draw_sub(c); return; }
		page_func(c);
		break;
	}
}

/* the notice screen: a spinner and one or two lines; nothing on it can be touched */
static void draw_notice(Cv *c)
{
	char a[128], *b;
	float t = (float)(g_ms % 1100) / 1100.f * 360.f;

	snprintf(a, sizeof a, "%s", notice);
	b = strchr(a, '|');
	if (b) *b++ = 0;
	gx_arc(c, 120, 94, 22, 4, 0, 360, C_TXT, 30, 0);
	gx_arc(c, 120, 94, 22, 4, t, t + 100.f, TH()->a, 255, 1);
	TXTC(c, FN16, 120, 148, a, C_TXT);
	if (b) TXTC(c, FN14, 120, 170, b, C_DIM);
}

/* Uses the same frosted background, rounded cards and press feedback as the
 * rest of the native UI; stays inside the circular panel's safe area. */
static void power_button(Cv *c, int y, int id, const char *label, uint32_t colour)
{
	card_p(c, 50, (float)y, 140, 42, 16, id);
	hit_add(50, y, 140, 42, id, 0, 0);
	hit_fb(FB_BG, 0, 0, 0, 0, 0);
	TXTC(c, FN16, 120, y + 27, label, colour);
}

static void draw_power_menu(Cv *c)
{
	TXTC(c, FB22, 120, 53, "电源", C_TXT);
	if (power_choice) {
		TXTC(c, FN16, 120, 87, power_choice == 1 ? "确认关闭设备？" : "确认重启设备？", C_DIM);
		power_button(c, 105, H_POWER_CONFIRM, power_choice == 1 ? "确认关机" : "确认重启", power_choice == 1 ? C_BAD : TH()->a);
		power_button(c, 157, H_POWER_CANCEL, "返回", C_TXT);
	} else {
		power_button(c, 73, H_POWER_OFF, "关机", C_BAD);
		power_button(c, 125, H_POWER_RESTART, "重启", TH()->a);
		hit_add(77, 181, 86, 32, H_POWER_CANCEL, 0, 0);
		TXTC(c, FN14, 120, 202, "取消", C_DIM);
	}
}

/* the two sheets: the control centre (from the top) and the SMS list (from the bottom) */
static void draw_sheet(Cv *c, int which)
{
	if (which == SH_CTRL) page_control(c);
	else {
		page_sms(c);
		pill(c, 108.f, 14.f, 24.f, 4.f, C_TXT, 80);
	}
}

static void draw_overlay(Cv *c)
{
	if (ov == OV_QR) ov_qr(c);
	else if (ov == OV_SMS) ov_sms(c);
}
#endif
