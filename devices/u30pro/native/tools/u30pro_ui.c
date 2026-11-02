/* SPDX-License-Identifier: MIT
 * u30pro-panel: the on-device touch UI of the U30 Pro (240x240 round window).
 * Native KMS output, no Android compositor, GPU or storage writes.  Pages: home, signal, traffic, Wi-Fi, power, SMS,
 * system, controls, with swipe navigation, QR code for the hotspot, SMS reader, sleep-timeout and theme settings.
 * The backlight of this panel can only be switched on or off (docs/SCREEN.md): there is no brightness setting and no dimming.
 *
 *   u30pro-panel probe|test|run          (device)
 *   u30pro-panel sim <dir> [--guide]     (host build with -DUI_HOST: scripted events on stdin, PPM screenshots)
 *
 * The UI reads kernel files itself; the cellular/Wi-Fi/SMS details come from the u30pro-ui-feed service.  On the device it is the ubus
 * object u30pro.panel (tools/ui/bus.h: status, screen, command, feed, reload) and keeps its settings in /etc/config/u30pro_native_control
 * (section display); the host simulation uses the files feed.kv, settings.kv and cmd in its directory.  The current frame is kept in
 * /run/u30pro-screen.ppm. */
#define _GNU_SOURCE
#pragma GCC diagnostic ignored "-Wunused-function"
#ifndef __clang__
#pragma GCC diagnostic ignored "-Wformat-truncation"
#endif
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "ui/font.h"
#include "ui/t2s.h"
#include "ui/gfx.h"
#include "ui/data.h"
#include "ui/pages.h"
#include "ui/sha256.h"

#ifdef UI_HOST
enum { T_DOWN, T_MOVE, T_UP };
static long vclock;
static long now_ms(void) { return vclock; }
static void run_async(const char *cmd) { printf("RUN %s\n", cmd); }
static void backlight_level(int level) { printf("BACKLIGHT %d\n", level); }
static void display_power(int on) { printf("DISPLAY %s\n", on ? "on" : "off"); }
static void present_off(const Cv *c) { (void)c; }
#else
#include "ui/platform.h"
#endif

static Cv scr, pa, pb, bgc, shc;
static int page, asleep, dirty = 1;
static long last_act, splash_until;
static float tx_off;
static int dragging, anim_on, anim_commit;
static long anim_t0;
static float anim_from, anim_to;
static unsigned gesture_count, key_count, last_key;
static const char *last_gesture = "none";
/* sdrag: 1/2 pulling a sheet open, 3/4 pushing it closed, 5 scrolling a view; caught: the touch stopped a moving list (it is no tap);
 * on_hit: the touch started on a control (a long press there is no "go home") */
static struct { int down, x0, y0, x, y, dir, swallow, hold_fired, base_scroll, zone, sdrag, caught, on_hit; long t0; } tc;
static float sheet_from, sheet_to;
static long sheet_t0;
static int sheet_anim;

#define SETTINGS_PATH UI_RUN "/settings.kv"

static int wrap_page(int p) { return ((p % NPAGES) + NPAGES) % NPAGES; }

/* ------------------------------------------------------------------ background */
/* the gradient behind the pages when there is no wallpaper (wall.h) */
static void bg_build(void)
{
	uint32_t centre = gx_mix(0x152238, TH()->a, 26), edge = 0x04070D;

	if (!bgc.p) bgc = cv_new(SCREEN, SCREEN);
	for (int y = 0; y < SCREEN; y++)
		for (int x = 0; x < SCREEN; x++) {
			float dx = (float)x + .5f - 120.f, dy = (float)y + .5f - 112.f, d = sqrtf(dx * dx + dy * dy) / 150.f;

			if (d > 1.f) d = 1.f;
			bgc.p[y * SCREEN + x] = gx_mix(centre, edge, (unsigned)(d * d * 255.f));
		}
}

/* ------------------------------------------------------------------ settings */
static int pin_hash_ok(const char *s)
{
	if (strlen(s) != 81 || s[16] != ':') return 0;
	for (int i = 0; i < 81; i++) if (i != 16 && !isxdigit((unsigned char)s[i])) return 0;
	return 1;
}

#ifdef UI_HOST
/* the host simulation keeps the settings in settings.kv of its directory; the device in UCI (tools/ui/bus.h) */
static struct timespec settings_mtime;

static void settings_stamp(void)
{
	struct stat st;

	if (!stat(SETTINGS_PATH, &st)) settings_mtime = st.st_mtim;
}

static void settings_save(void)
{
	FILE *f = fopen(SETTINGS_PATH ".new", "w");

	if (!f) return;
	fprintf(f, "theme=%d\noff=%d\nwake=%s\nlock=%d\npin=%s\npinlen=%d\n", theme_idx, off_idx, WAKE_KEYS[wake_idx % NWAKE], lock_on, pin_hash,
		pin_hash[0] ? pin_digits : 0);
	fprintf(f, "home_short=%s\nhome_long=%s\nhome_ms=%d\n", KEY_ACTIONS[key_short], KEY_ACTIONS[key_long], key_long_ms);
	fprintf(f, "wp_dim=%d\nglass=%d\nglass_blur=%d\nglass_tint=%d\nwp_live=%d\n", wp_dim, glass_clear, glass_blur, glass_tint, wp_live);
	fclose(f);
	rename(SETTINGS_PATH ".new", SETTINGS_PATH);
	settings_stamp();
}

static void settings_load(void)
{
	FILE *f = fopen(SETTINGS_PATH, "r");
	char line[160], s[100];

	settings_stamp();
	if (!f) return;
	wake_idx = WAKE_KEY;
	lock_on = 0;
	pin_hash[0] = 0;
	pin_digits = 0;
	while (fgets(line, sizeof line, f)) {
		int v;

		if (sscanf(line, "theme=%d", &v) == 1 && v >= 0 && v < NTHEMES) theme_idx = v;
		else if (sscanf(line, "off=%d", &v) == 1 && v >= 0 && v < NOFF) off_idx = v;
		else if (sscanf(line, "lock=%d", &v) == 1) lock_on = v == 1;
		else if (sscanf(line, "pinlen=%d", &v) == 1) pin_digits = v >= 4 && v <= 6 ? v : 0;
		else if (sscanf(line, "wake=%15s", s) == 1) { for (int i = 0; i < NWAKE; i++) if (!strcmp(s, WAKE_KEYS[i])) wake_idx = i; }
		else if (sscanf(line, "pin=%99s", s) == 1 && pin_hash_ok(s)) snprintf(pin_hash, sizeof pin_hash, "%s", s);
		else if (sscanf(line, "home_short=%31s", s) == 1) key_short = key_action_idx(s, KA_PAGE);
		else if (sscanf(line, "home_long=%31s", s) == 1) { key_long = key_action_idx(s, KA_NONE); if (key_long == KA_PAGE) key_long = KA_NONE; }
		else if (sscanf(line, "home_ms=%d", &v) == 1 && key_long_ms_ok(v)) key_long_ms = v;
		else if (sscanf(line, "wp_dim=%d", &v) == 1 && v >= 0 && v <= 90) wp_dim = v;
		else if (sscanf(line, "glass=%d", &v) == 1 && v >= 0 && v <= 100) glass_clear = v;
		else if (sscanf(line, "glass_blur=%d", &v) == 1 && v >= 0 && v <= 100) glass_blur = v;
		else if (sscanf(line, "glass_tint=%d", &v) == 1 && v >= 0 && v < NTINT) glass_tint = v;
		else if (sscanf(line, "wp_live=%d", &v) == 1 && v >= 0 && v <= 2) wp_live = v;
	}
	fclose(f);
}
#else
#include "ui/bus.h"
#endif

/* ------------------------------------------------------------------ screen lock */
static void pin_digest(const char *salt, const char *pin, char out[65])
{
	char m[40];

	snprintf(m, sizeof m, "%.16s%.6s", salt, pin);
	sha256_hex(m, out);
}

static int pin_matches(const char *pin)
{
	char salt[17], d[65];

	if (!pin_hash_ok(pin_hash)) return 0;
	memcpy(salt, pin_hash, 16);
	salt[16] = 0;
	pin_digest(salt, pin, d);
	return !strcmp(d, pin_hash + 17);
}

static void pin_store(const char *pin)
{
	unsigned char r[8] = {0};
	char salt[17], d[65];
	FILE *f = fopen("/dev/urandom", "rb");

	if (!f || fread(r, 1, sizeof r, f) != sizeof r) {
		long t = now_ms();

		for (int i = 0; i < 8; i++) r[i] ^= (unsigned char)(t >> (i * 5)) ^ (unsigned char)(getpid() >> i);
	}
	if (f) fclose(f);
	for (int i = 0; i < 8; i++) snprintf(salt + 2 * i, 3, "%02x", r[i]);
	pin_digest(salt, pin, d);
	snprintf(pin_hash, sizeof pin_hash, "%s:%s", salt, d);
	pin_digits = (int)strlen(pin);
}

static void pin_clear_entry(void)
{
	memset(pin_buf, 0, sizeof pin_buf);
	pin_len = 0;
}

static void pin_open(int mode)
{
	pin_mode = mode;
	pin_clear_entry();
	pin_msg = NULL;
	press_kill();
	dirty = 1;
}

static void pin_note(const char *m)
{
	pin_msg = m;
	pin_msg_until = now_ms() + 1800;
}

/* the screen locks whenever it goes dark (and at start): with the PIN when one is set and the lock is on, otherwise softly (a long press
   opens it) */
static void lock_engage(void)
{
	locked = 1;
	lock_soft = !(lock_on && pin_hash[0]);
	pin_after_lock = 0;
	pin_open(PIN_LOCKSCR);
}

static void state_write(void);

static void soft_unlock(void)
{
	locked = lock_soft = 0;
	pin_mode = PIN_NONE;
	dirty = 1;
	state_write();
}

static void state_write(void);

static void pin_press(int k)
{
	if (k == PIN_CANCEL) {
		if (pin_mode == PIN_NEW || pin_mode == PIN_AGAIN) { pin_mode = PIN_NONE; pin_after_lock = 0; memset(pin_first, 0, sizeof pin_first); }
		return;
	}
	if (k == PIN_BACK) {
		if (pin_mode == PIN_UNLOCK) pin_open(PIN_LOCKSCR);
		return;
	}
	if (pin_mode == PIN_UNLOCK && g_ms < pin_wait_until) return;
	if (k < 10) {
		if (pin_len < 6) { pin_buf[pin_len++] = (char)('0' + k); pin_buf[pin_len] = 0; }
		/* unlocking needs no confirm when the length of the PIN is known: once that many digits are in, they are checked (a wrong
		   entry counts as a try and is cleared); without the length the right PIN still opens at once, a wrong one needs confirm */
		if (pin_mode != PIN_UNLOCK || pin_len < 4) return;
		if (pin_digits ? pin_len < pin_digits : (!pin_matches(pin_buf) && pin_len < 6)) return;
	}
	if (k == PIN_DEL) {
		if (pin_len) pin_buf[--pin_len] = 0;
		return;
	}
	if (pin_len < 4) { pin_note("至少 4 位数字"); return; }
	if (pin_mode == PIN_UNLOCK) {
		if (pin_matches(pin_buf)) { locked = 0; pin_fails = 0; pin_mode = PIN_NONE; state_write(); }
		else {
			/* a wrong PIN flashes the ring red; after 5 of them each further try waits 30 s, doubling up to 4 min */
			pin_err_until = now_ms() + 700;
			if (++pin_fails >= 5) {
				int n = pin_fails - 5 > 3 ? 3 : pin_fails - 5;

				pin_wait_until = now_ms() + (30000L << n);
			}
		}
	} else if (pin_mode == PIN_NEW) {
		memcpy(pin_first, pin_buf, sizeof pin_first);
		pin_mode = PIN_AGAIN;
	} else if (strcmp(pin_first, pin_buf)) {
		pin_note("两次输入不一致");
		pin_mode = PIN_NEW;
	} else {
		pin_store(pin_buf);
		if (pin_after_lock) lock_on = 1;
		pin_after_lock = 0;
		pin_mode = PIN_NONE;
		settings_save();
		toast_show("密码已设置", C_OK, 2000);
	}
	if (pin_mode != PIN_AGAIN) memset(pin_first, 0, sizeof pin_first);
	pin_clear_entry();
}

/* ------------------------------------------------------------------ power state */
static void state_write(void)
{
#ifndef UI_HOST
	/* the state is the ubus method status; the feeder is asked for a run at once when the screen comes on or the signal page, whose
	   SINR is only asked for while it is on screen, opens */
	static int last_page = -1, last_on = -1;
	int on = !asleep;

	if (on && (last_on == 0 || (page == PG_SIGNAL && last_page != PG_SIGNAL))) bus_request("kick");
	last_page = page;
	last_on = on;
#endif
}

static void compose(void);

/* sleep switches the display off (display_power(): a backlight level of 0 alone leaves the light on); waking draws the current state
 * into the buffer the display will show before switching it back on, so the old frame never comes back */
static void wake(void)
{
	if (asleep) {
		asleep = 0;
		g_ms = now_ms();
		D.now = time(NULL);
		compose();
		present_off(&scr);
		display_power(1);
	}
	backlight_level(bright_max);
	last_act = now_ms();
	dirty = 1;
	state_write();
}

static void go_sleep(void)
{
	asleep = 1;
	power_menu = power_choice = 0;
	backlight_level(0);
	display_power(0);
	lock_engage();
	state_write();
}

/* the settings changed behind our back (the web interface): adopt them; a lock that was switched off or lost its PIN opens the screen */
static void settings_apply(void)
{
	int theme = theme_idx;

	settings_load();
	if (theme != theme_idx) bg_build();
	wp_check();
	if (locked && !lock_soft && !(lock_on && pin_hash[0])) { locked = 0; pin_mode = PIN_NONE; }
	if (!lock_soft && !pin_hash[0] && (pin_mode == PIN_UNLOCK || pin_mode == PIN_LOCKSCR)) pin_mode = PIN_NONE;
	dirty = 1;
	state_write();
}

#ifdef UI_HOST
static void settings_check(void)
{
	struct stat st;

	if (stat(SETTINGS_PATH, &st) || (st.st_mtim.tv_sec == settings_mtime.tv_sec && st.st_mtim.tv_nsec == settings_mtime.tv_nsec)) return;
	settings_apply();
}
#endif

static void apply_idle(long now)
{
	int secs = OFF_SECS[off_idx % NOFF];

	/* an unlock pad left alone for 10 s goes back to the lock screen */
	if (pin_mode == PIN_UNLOCK && !tc.down && now - last_act >= 10000 && now >= pin_wait_until) pin_open(PIN_LOCKSCR);
	if (asleep || notice[0] || tc.down) return;
	if (secs > 0) { if (now - last_act >= secs * 1000L) go_sleep(); }
	else if (!locked && !pin_mode && now - last_act >= RELOCK_MS) lock_engage();     /* always on: lock again after a minute alone */
}

/* ------------------------------------------------------------------ navigation */
static void nav_anim(float to, int commit)
{
	anim_from = tx_off;
	anim_to = to;
	anim_commit = commit;
	anim_t0 = now_ms();
	anim_on = 1;
	dragging = 0;
	dirty = 1;
}

static int sheet_is_open(void) { return sheet != SH_NONE && sheet_pos > 0.001f; }

static void sheet_anim_to(float to)
{
	sheet_from = sheet_pos;
	sheet_to = to;
	sheet_t0 = now_ms();
	sheet_anim = 1;
	dirty = 1;
}

static void sheet_open(int which)
{
	if (which == SH_SMS) sms_scroll = 0;
	sheet = which;
	sheet_anim_to(1.f);
}

static void sheet_close(void) { if (sheet != SH_NONE) sheet_anim_to(0.f); }

static void sheet_tick(long now)
{
	if (!sheet_anim) return;
	float t = (float)(now - sheet_t0) / 220.f;

	if (t > 1.f) t = 1.f;
	float e = 1.f - (1.f - t) * (1.f - t) * (1.f - t);

	sheet_pos = sheet_from + (sheet_to - sheet_from) * e;
	dirty = 1;
	if (t >= 1.f) {
		sheet_pos = sheet_to;
		sheet_anim = 0;
		if (sheet_to <= 0.f) sheet = SH_NONE;
	}
}

/* ------------------------------------------------------------------ scrolling */
/* The function list and the long second-level pages follow the finger.  Past either end the content gives way less and less (rubber band,
 * the iOS formula); a flick carries on with momentum that decays with a time constant of 400 ms (fast past an end), and whenever the finger
 * is off the content settles back inside its range. */
static struct { int on; float v; long t; } fling;
static struct { float y; long t; } sv_hist[6];
static int sv_n;
static float scroll_base;

static float rubber(float over) { return 180.f * (1.f - 1.f / (over * .55f / 180.f + 1.f)); }

static void scroll_follow(int y)
{
	float *p = &scroll_pos[scroll_live], raw = scroll_base + (float)(tc.y0 - y), max = scroll_max[scroll_live];

	*p = raw < 0.f ? -rubber(-raw) : (raw > max ? max + rubber(raw - max) : raw);
	memmove(&sv_hist[1], &sv_hist[0], sizeof sv_hist - sizeof *sv_hist);
	sv_hist[0].y = (float)y;
	sv_hist[0].t = g_ms;
	if (sv_n < 6) sv_n++;
	scroll_moving = 1;
	dirty = 1;
}

/* speed of the content when the finger lifts (px/ms; positive scrolls on down the content), over the last 50 ms of movement */
static float scroll_release_speed(void)
{
	int k = 1;
	long dt;

	if (sv_n < 2 || g_ms - sv_hist[0].t > 80) return 0.f;	/* the finger rested before it lifted */
	while (k < sv_n - 1 && sv_hist[0].t - sv_hist[k].t < 50) k++;
	dt = sv_hist[0].t - sv_hist[k].t;
	return dt > 0 ? -(sv_hist[0].y - sv_hist[k].y) / (float)dt : 0.f;
}

static void scroll_release(void)
{
	float v = scroll_release_speed();

	fling.on = 1;
	fling.v = fabsf(v) < .12f ? 0.f : gx_clamp(v, -4.f, 4.f);
	fling.t = g_ms;
	sv_n = 0;
}

static void scroll_tick(long now)
{
	float *p, max, dt;

	if (scroll_live < 0) { fling.on = 0; return; }
	p = &scroll_pos[scroll_live];
	max = scroll_max[scroll_live];
	if (!fling.on) {
		if (!tc.down && (*p < 0.f || *p > max)) { fling.on = 1; fling.v = 0.f; fling.t = now; }	/* left outside the range: settle */
		else {
			if (scroll_moving && !(tc.down && tc.sdrag == 5)) { scroll_moving = 0; dirty = 1; }
			return;
		}
	}
	dt = (float)(now - fling.t);
	if (dt <= 0.f) return;
	if (dt > 40.f) dt = 40.f;
	fling.t = now;
	if (fling.v != 0.f) {
		int out = *p < 0.f || *p > max;

		*p += fling.v * dt;
		fling.v *= expf(-dt / (out ? 20.f : 400.f));
		if (fabsf(fling.v) < .03f || *p < -50.f || *p > max + 50.f) fling.v = 0.f;
	} else {
		float target = *p < 0.f ? 0.f : (*p > max ? max : *p);

		*p += (target - *p) * (1.f - expf(-dt / 80.f));
		if (fabsf(target - *p) < .5f) { *p = target; fling.on = 0; }
	}
	scroll_moving = 1;
	dirty = 1;
}

static void go_home(void)
{
	scroll_pos[0] = scroll_pos[1] = 0.f;
	fling.on = 0;
	ov = OV_NONE;
	fn_sub = -1;
	sheet = SH_NONE;
	sheet_pos = 0.f;
	sheet_anim = 0;
	page = 0;
	tx_off = 0;
	anim_on = 0;
}

static void page_step(int dir)
{
	if (ov != OV_NONE) ov = OV_NONE;
	tx_off = 0;
	nav_anim(dir > 0 ? -SCREEN : SCREEN, dir);
	gesture_count++;
}

static void nav_tick(long now)
{
	if (!anim_on) return;
	float t = (float)(now - anim_t0) / 240.f;

	if (t > 1.f) t = 1.f;
	float e = 1.f - (1.f - t) * (1.f - t) * (1.f - t);

	tx_off = anim_from + (anim_to - anim_from) * e;
	dirty = 1;
	if (t >= 1.f) {
		if (anim_commit) { page = wrap_page(page + anim_commit); state_write(); }
		tx_off = 0;
		anim_on = 0;
		anim_commit = 0;
	}
}

/* ------------------------------------------------------------------ function menu */
static void fn_open(int i)
{
	const FnEntry *e;

	if (i < 0 || i >= nfn_entries) return;
	e = &fn_entries[i];
	if (e->plugin < 0) { fn_sub = i; return; }
	Plugin *pl = &plugs[e->plugin];

	switch (pl->type) {
	case PT_INFO:
	case PT_VIEW:
		fn_sub = i;
		pl->next_run = 0;
		break;
	case PT_ACTION:
		pl->msg_pending = 1;
		pl->msg[0] = 0;
		plug_exec(pl, pl->cmd, "out");
		toast_show("执行中…", TH()->a, 1500);
		break;
	case PT_TOGGLE:
		pl->state = !pl->state;
		plug_exec(pl, pl->cmd, "tg");
		pl->next_state = g_ms + 1500;
		break;
	default:
		if (pl->cmd[0] && !pl->text[0]) plug_exec(pl, pl->cmd, "out");
		ov = OV_QR;
		ov_arg = 2 + e->plugin;
		break;
	}
}

/* Showing/dismissing power controls never unlocks the screen or mutates the
 * PIN state. An update notice retains ownership of its non-interactive view. */
static void power_menu_open(void)
{
	if (notice[0]) return;
	power_menu = 1;
	power_choice = 0;
	fling.on = 0;
	memset(&tc, 0, sizeof tc);
	press_kill();
	hold_id = hold_pct = 0;
	if (asleep) wake();
	last_act = now_ms();
	dirty = 1;
}

/* ------------------------------------------------------------------ actions */
static int hit_at(int x, int y)
{
	for (int i = nhits - 1; i >= 0; i--) {
		const Hit *h = &hits[i];

		if (x >= h->x && x < h->x + h->w && y >= h->y && y < h->y + h->h) return i;
	}
	return -1;
}

/* the shown messages are read now: the feeder marks them */
static void ack_sms(void)
{
#ifdef UI_HOST
	FILE *f = fopen(UI_RUN "/sms.ack", "w");

	if (f) fclose(f);
#else
	bus_request("sms_ack");
#endif
}

static void do_action(int id)
{
	switch (id) {
	case H_WIFI_TOGGLE:
		if (g_ms < wifi_busy_until) break;
		wifi_busy_until = g_ms + 9000;
		wifi_want = !D.wifi_active;
		/* the log says which changes came from the screen (the web interface and the apps go through u30pro-native-control alone) */
		printf("TOUCH hotspot %s\n", wifi_want ? "on" : "off"); fflush(stdout);
		toast_show(D.wifi_active ? "正在关闭 Wi-Fi…" : "正在开启 Wi-Fi…", C_WARN, 2500);
		run_async("/usr/sbin/u30pro-panel-control wifi-toggle");
		break;
	case H_RELAY_TOGGLE:
		/* the switch only turns the saved relay on or off; the network and its password are set in the web interface */
		if (g_ms < relay_busy_until) break;
		if (!D.relay_cfg[0]) { toast_show("请先在网页设置中继", C_WARN, 2500); break; }
		relay_busy_until = g_ms + 6000;
		relay_want = !D.relay_on;
		printf("TOUCH relay %s\n", relay_want ? "on" : "off"); fflush(stdout);
		toast_show(relay_want ? "正在开启中继…" : "正在关闭中继…", C_WARN, 2000);
		run_async(relay_want ? "/usr/sbin/u30pro-panel-control relay-on"
				     : "/usr/sbin/u30pro-panel-control relay-off");
		break;
	case H_BYPASS:
		/* direct supply = the charger's "inhibit-charge"; a pause of the temperature protection is not the user's to end here */
		if (g_ms < bypass_busy_until) break;
		if (D.chg_paused) { toast_show("温度过高，已暂停充电", C_WARN, 2500); break; }
		bypass_busy_until = g_ms + 2500;
		bypass_want = !D.chg_bypass;
		toast_show(bypass_want ? "直供电：电池停止充电" : "已恢复充电", TH()->a, 2000);
		run_async(bypass_want ? "/usr/sbin/u30pro-panel-control charge-pause"
				      : "/usr/sbin/u30pro-panel-control charge-resume");
		break;
	case H_QR:
		if (D.ssid[0] && D.wifi_active) { ov = OV_QR; ov_arg = 0; }
		else toast_show(D.ssid[0] ? "热点未开启" : "尚无热点配置", C_WARN, 2000);
		break;
	case H_RECONNECT:
		if (g_ms < recon_busy_until) break;
		recon_busy_until = g_ms + 6000;
		toast_show("正在重新拨号…", C_WARN, 2500);
		run_async("/usr/sbin/u30pro-panel-control reconnect");
		break;
	case H_QR_ADMIN: ov = OV_QR; ov_arg = 1; break;
	case H_SLEEP: off_idx = (off_idx + 1) % NOFF; printf("TOUCH sleep %d\n", OFF_SECS[off_idx]); fflush(stdout); settings_save(); toast_show(OFF_NAMES[off_idx], TH()->a, 1500); break;
	case H_WAKE: wake_idx = (wake_idx + 1) % NWAKE; printf("TOUCH wake %s\n", WAKE_KEYS[wake_idx]); fflush(stdout); settings_save(); toast_show(WAKE_NAMES[wake_idx], TH()->a, 1500); break;
	case H_LOCK_TOGGLE:
		if (lock_on && pin_hash[0]) { lock_on = 0; settings_save(); toast_show("锁屏已关闭", TH()->a, 1500); }
		else if (pin_hash[0]) { lock_on = 1; settings_save(); toast_show("锁屏已开启", TH()->a, 1500); }
		else { pin_after_lock = 1; pin_open(PIN_NEW); }
		break;
	case H_PIN_SET: pin_after_lock = 0; pin_open(PIN_NEW); break;
	case H_THEME: theme_idx = (theme_idx + 1) % NTHEMES; bg_build(); settings_save(); wp_check(); toast_show(TH()->name, TH()->a, 1500); break;
	case H_GLASS: glass_clear = glass_clear >= 80 ? 20 : glass_clear + 15; settings_save(); wp_check(); break;
	case H_GLASS_BLUR: glass_blur = glass_blur >= 100 ? 0 : glass_blur + 25; settings_save(); wp_check(); break;
	case H_GLASS_TINT: glass_tint = (glass_tint + 1) % NTINT; settings_save(); wp_check(); break;
	case H_WP_DIM: wp_dim = wp_dim >= 60 ? 0 : wp_dim + 15; settings_save(); wp_check(); break;
	case H_WP_LIVE: wp_live = (wp_live + 1) % 3; settings_save(); break;
	case H_REBOOT: power_menu_open(); break;
	case H_POWER_OFF: if (power_menu) power_choice = 1; break;
	case H_POWER_RESTART: if (power_menu) power_choice = 2; break;
	case H_POWER_CANCEL:
		if (power_choice) power_choice = 0; else power_menu = 0;
		press_kill();
		break;
	case H_POWER_CONFIRM:
		if (power_menu && power_choice) {
			int shutdown = power_choice == 1;
			power_menu = power_choice = 0;
			snprintf(notice, sizeof notice, "%s", shutdown ? "正在关机…" : "正在重启…");
			run_async(shutdown ? "/usr/sbin/u30pro-panel-control poweroff" : "/usr/sbin/u30pro-panel-control reboot");
		}
		break;
	case H_CLOSE: ov = OV_NONE; break;
	case H_BACK: fn_sub = -1; break;
	case H_SIG_RAT:
		if (D.nr || D.nr_idle) sig_lte = !sig_lte;
		else toast_show("NR 有流量时才连接", TH()->b, 2500);
		break;
	case H_SHEET_CLOSE: sheet_close(); break;
	case H_OPEN_SMS: sheet_open(SH_SMS); break;
	case H_OPEN_POWER: page = PG_FUNC; tx_off = 0; anim_on = 0; fn_build(); fn_sub = FN_POWER; state_write(); break;
	case H_OPEN_BATLOG: fn_sub = FN_BATLOG; state_write(); break;
	case H_SMS_REFRESH:
#ifdef UI_HOST
		{ FILE *f = fopen(UI_RUN "/sms.refresh", "w"); if (f) fclose(f); }
#else
		bus_request("sms_refresh");
#endif
		toast_show("正在读取短信…", TH()->a, 2000);
		break;
	default:
			if (id >= H_PIN && id <= H_PIN + PIN_BACK) pin_press(id - H_PIN);
			else if (id >= H_GOTO && id < H_GOTO + NPAGES) { page = id - H_GOTO; tx_off = 0; anim_on = 0; fn_sub = -1; state_write(); }
			else if (id >= H_FN && id < H_FN + NFN_BUILTIN + MAXPLUG) fn_open(id - H_FN);
			else if (id >= H_BH_RANGE && id < H_BH_RANGE + NBH) bh_range = id - H_BH_RANGE;
		else if (id >= H_SMS_ROW && id < H_SMS_ROW + 6) { ov = OV_SMS; ov_arg = id - H_SMS_ROW; sms_scroll = 0; ack_sms(); }
		break;
	}
	/* a press that opened another screen must not leave its highlight on it */
	if (id == H_QR || id == H_QR_ADMIN || id == H_CLOSE || id == H_BACK || id == H_SHEET_CLOSE || id == H_OPEN_SMS || id == H_OPEN_POWER || id == H_OPEN_BATLOG ||
	    (id >= H_SMS_ROW && id < H_SMS_ROW + 6) || (id >= H_GOTO && id < H_GOTO + NPAGES) || id >= H_FN)
		press_kill();
	dirty = 1;
}

static void fire_hold(int id)
{
	if (id == H_REBOOT) power_menu_open();
}

/* ------------------------------------------------------------------ input handlers (touch + keys) */
static float clamp01(float v) { return v < 0.f ? 0.f : (v > 1.f ? 1.f : v); }

/* a vertical drag: pulls the control centre down from the top edge, the SMS list up from the bottom edge, closes an open sheet, scrolls lists */
static void vertical_drag(int dy)
{
	if (ov == OV_SMS) {
		int s = tc.base_scroll + (tc.y0 - tc.y) / 18;

		if (s != sms_scroll) { sms_scroll = s < 0 ? 0 : s; dirty = 1; }
		return;
	}
	if (ov != OV_NONE) return;
	if (tc.sdrag == 1 || tc.sdrag == 2) { sheet_pos = clamp01((float)abs(dy) / 170.f); dirty = 1; return; }
	if (tc.sdrag == 3 || tc.sdrag == 4) { sheet_pos = clamp01(1.f - (float)abs(dy) / 170.f); dirty = 1; return; }
	if (sheet_is_open() && !sheet_anim) {
		if (sheet == SH_CTRL) {
			if (dy < 0) tc.sdrag = 3;
		} else {
			int s = tc.base_scroll + (tc.y0 - tc.y) / 40;

			if (dy > 0 && tc.base_scroll == 0) tc.sdrag = 4;
			else if (s != sms_scroll) { sms_scroll = s < 0 ? 0 : s; dirty = 1; }
		}
		return;
	}
	if (sheet_is_open()) return;
	if (tc.sdrag == 5) { scroll_follow(tc.y); return; }
	/* a scrolling view: a pull down from its title band still opens the control centre, every other drag scrolls (the bottom edge too) */
	if (scroll_live >= 0 && !(tc.y0 <= 44 && dy > 0)) {
		tc.sdrag = 5;
		fling.on = 0;
		sv_n = 0;
		scroll_base = scroll_pos[scroll_live] - (float)(tc.y0 - tc.y);	/* the content starts moving from where it is, not by the slop */
		scroll_follow(tc.y);
		return;
	}
	if (tc.zone == 1 && dy > 0) { sheet = SH_CTRL; sheet_anim = 0; tc.sdrag = 1; return; }
	if (tc.zone == 2 && dy < 0) { sheet = SH_SMS; sms_scroll = 0; sheet_anim = 0; tc.sdrag = 2; return; }
}

static void ui_touch(int type, int x, int y, long ms)
{
	long now = now_ms();

	(void)ms;
	if (type == T_DOWN) {
		memset(&tc, 0, sizeof tc);
		tc.down = 1; tc.x0 = tc.x = x; tc.y0 = tc.y = y; tc.t0 = now;
		tc.zone = y <= 56 ? 1 : (y >= 184 ? 2 : 0);
		press_kill();
		dirty = 1;
		last_act = now;
		if (asleep) {
			/* a touch on the dark screen only wakes it: at once, on the second touch within 450 ms, or not at all (keys only) */
			static long first_tap;

			tc.swallow = 1;
			if (wake_idx == WAKE_TAP || (wake_idx == WAKE_DOUBLE && first_tap && now - first_tap <= 450)) { first_tap = 0; wake(); }
			else if (wake_idx == WAKE_DOUBLE) first_tap = now;
			return;
		}
		if (pin_mode && !power_menu) {
			/* the ring pad has wedge-shaped keys: found by angle and radius, not by the rectangle hits */
			int k = pin_mode == PIN_LOCKSCR ? -1 : pin_hit(x, y);

			hold_id = 0;
			hold_pct = 0;
			if (k >= 0) {
				Hit hk = { x, y, 1, 1, H_PIN + k, 0, 0, 0, 0, 0, 0, 0, FB_BG };

				press_begin(&hk, x, y, now);
			}
			return;
		}
		/* a finger on a list that is still moving stops it, and that touch is no tap */
		if (fling.on && fling.v != 0.f) tc.caught = 1;
		fling.on = 0;
		{
			int h = tc.caught ? -1 : hit_at(x, y);

			tc.on_hit = h >= 0;
			hold_id = h >= 0 && hits[h].hold ? hits[h].id : 0;
			hold_pct = 0;
			if (h >= 0) press_begin(&hits[h], x, y, now);
		}
		tc.base_scroll = sms_scroll;
		return;
	}
	if (!tc.down) return;
	last_act = now;
	if (type == T_MOVE) {
		tc.x = x; tc.y = y;
		if (tc.swallow) return;
		int dx = x - tc.x0, dy = y - tc.y0;

		/* past the touch slop the finger is moving, not pressing: the highlight lets go at once (a swipe or sheet drag follows at 12 px) */
		if (press_on && (abs(dx) > 6 || abs(dy) > 6)) press_end(now, 0);
		if (!tc.dir && (abs(dx) > 12 || abs(dy) > 12)) tc.dir = abs(dx) * 10 > abs(dy) * 13 ? 1 : 2;
		if (tc.dir && hold_id) { hold_id = 0; hold_pct = 0; }
		if (power_menu) return;
		/* the lock screen opens the unlock pad on a swipe in any direction (the soft lock only on a long press); the pad itself takes
		   taps only */
		if (pin_mode == PIN_LOCKSCR && !lock_soft && (abs(dx) > 30 || abs(dy) > 30)) { pin_open(PIN_UNLOCK); tc.swallow = 1; last_gesture = "swipe-unlock"; }
		if (pin_mode) return;
		if (tc.dir == 1) {
			if (ov == OV_NONE && !sheet_is_open() && fn_sub < 0) {
				dragging = 1;
				anim_on = 0;
				tx_off = (float)(dx > 230 ? 230 : (dx < -230 ? -230 : dx));
				dirty = 1;
			}
		} else if (tc.dir == 2) vertical_drag(dy);
		return;
	}
	/* release */
	tc.down = 0;
	hold_id = 0;
	hold_pct = 0;
	press_end(now, !tc.dir && !tc.swallow && !tc.hold_fired);
	dirty = 1;
	if (tc.swallow) return;
	if (power_menu) {
		if (!tc.dir && !tc.hold_fired) {
			int h = hit_at(tc.x0, tc.y0);
			if (h >= 0 && hits[h].id >= H_POWER_OFF && hits[h].id <= H_POWER_CONFIRM) do_action(hits[h].id);
		}
		state_write();
		return;
	}
	if (pin_mode) {
		int k = tc.dir || pin_mode == PIN_LOCKSCR ? -1 : pin_hit(tc.x0, tc.y0);

		if (k >= 0) do_action(H_PIN + k);
		return;
	}
	if (tc.sdrag == 5) {
		tc.sdrag = 0;
		scroll_release();
		last_gesture = "scroll";
		gesture_count++;
		return;
	}
	if (tc.sdrag) {
		float pos = sheet_pos;

		if (tc.sdrag <= 2) sheet_anim_to(pos > 0.35f ? 1.f : 0.f);
		else sheet_anim_to(pos < 0.65f ? 0.f : 1.f);
		last_gesture = tc.sdrag == 1 ? "pull-down" : (tc.sdrag == 2 ? "pull-up" : "close-sheet");
		gesture_count++;
		tc.sdrag = 0;
		return;
	}
	if (tc.dir == 1) {
		int dx = tc.x - tc.x0;

		if (sheet_is_open()) { if (abs(dx) > 50) { sheet_close(); last_gesture = "close-sheet"; } return; }
		if (ov != OV_NONE) { if (abs(dx) > 40) { ov = OV_NONE; last_gesture = "close"; } return; }
		if (fn_sub >= 0) { if (dx > 50) { fn_sub = -1; last_gesture = "back"; } return; }
		if (dragging && (tx_off <= -60.f || tx_off >= 60.f)) {
			int dir = tx_off < 0 ? 1 : -1;

			last_gesture = dir > 0 ? "swipe-left" : "swipe-right";
			gesture_count++;
			nav_anim(dir > 0 ? -SCREEN : SCREEN, dir);
		} else if (dragging) nav_anim(0, 0);
		dragging = 0;
		return;
	}
	if (tc.dir == 2) return;
	if (tc.hold_fired || tc.caught) return;
	/* a long press on empty space goes home; on a control it is just a slow tap */
	if (now - tc.t0 >= 800 && !tc.on_hit) {
		if (ov != OV_NONE) ov = OV_NONE; else go_home();
		last_gesture = "hold-home";
		gesture_count++;
		state_write();
		return;
	}
	last_gesture = "tap";
	gesture_count++;
	{
		int h = hit_at(tc.x0, tc.y0);

		if (h >= 0) do_action(hits[h].id);
	}
	state_write();
}

static void touch_tick(long now)
{
	if (!tc.down || tc.swallow || tc.dir || tc.hold_fired || power_menu) return;
	if (pin_mode) {
		/* a long press on the lock screen opens the unlock pad, or the soft lock itself; a finger that moved does not count */
		if (pin_mode == PIN_LOCKSCR && lock_soft && now - tc.t0 >= SOFT_HOLD_MS && abs(tc.x - tc.x0) <= 12 && abs(tc.y - tc.y0) <= 12) {
			soft_unlock(); tc.swallow = 1; last_gesture = "hold-unlock";
		} else if (pin_mode == PIN_LOCKSCR && !lock_soft && now - tc.t0 >= 450) { pin_open(PIN_UNLOCK); tc.swallow = 1; last_gesture = "hold-unlock"; }
		return;
	}
	long held = now - tc.t0;

	if (hold_id) {
		int pct = held < 250 ? 0 : (int)((held - 250) * 100 / 1000);

		if (pct != hold_pct) { hold_pct = pct > 100 ? 100 : pct; dirty = 1; }
		if (pct >= 100) { tc.hold_fired = 1; fire_hold(hold_id); hold_id = 0; hold_pct = 0; press_end(now, 0); }
	} else if (held >= 800 && !tc.on_hit && !tc.caught) {
		tc.hold_fired = 1;
		press_end(now, 0);
		if (ov != OV_NONE) ov = OV_NONE; else go_home();
		last_gesture = "hold-home";
		gesture_count++;
		dirty = 1;
		state_write();
	}
}

/* runs a Wi-Fi key action of the web settings (native-control.uc key-action reads the setting again) */
static void key_run(int action, int press_long)
{
	run_async(press_long ? "/usr/sbin/u30pro-panel-control key-long"
			     : "/usr/sbin/u30pro-panel-control key-short");
	toast_show(KEY_ACTION_NAMES[action], TH()->a, 1500);
}

static void ui_key(int code, long duration_ms)
{
	int home = code == 102;                  /* the Wi-Fi key (KEY_HOME) */

	key_count++;
	last_key = (unsigned)code;
	/* a long press of the Wi-Fi key runs its long action, also on the dark screen, but not past a PIN lock */
	if (home && duration_ms >= key_long_ms && !power_menu) {
		if (key_long != KA_NONE && !(pin_mode && !lock_soft)) { if (!asleep) last_act = now_ms(); key_run(key_long, 1); }
		return;
	}
	if (code == 116) {                       /* KEY_POWER */
		if (duration_ms >= POWER_HOLD_MS) power_menu_open();
		else if (power_menu) {
			if (power_choice) power_choice = 0; else power_menu = 0;
			dirty = 1;
		} else if (asleep) wake(); else go_sleep();
		return;
	}
	if (power_menu) return;
	if (duration_ms > 1800 && !home) return;
	if (asleep) { wake(); return; }
	last_act = now_ms();
	if (pin_mode == PIN_LOCKSCR && lock_soft) { soft_unlock(); return; }  /* a key is no stray touch: it opens the soft lock */
	if (pin_mode == PIN_UNLOCK || pin_mode == PIN_LOCKSCR) return;     /* a locked screen ignores the other keys */
	if (pin_mode) { pin_press(PIN_CANCEL); dirty = 1; state_write(); return; }
	if (sheet_is_open()) { sheet_close(); state_write(); return; }
	if (ov != OV_NONE) { ov = OV_NONE; state_write(); return; }
	if (fn_sub >= 0) { fn_sub = -1; state_write(); return; }
	if (home && key_short != KA_PAGE) {
		if (key_short != KA_NONE) key_run(key_short, 0);
		return;
	}
	page_step(code == 114 ? -1 : 1);        /* KEY_VOLUMEDOWN goes back; the Wi-Fi key (KEY_HOME) goes forward */
	state_write();
}

/* ------------------------------------------------------------------ composition */
static void blit(Cv *dst, const Cv *src, int dx)
{
	int x0 = dx < 0 ? 0 : dx, x1 = dx + SCREEN > SCREEN ? SCREEN : dx + SCREEN;

	if (x1 <= x0) return;
	for (int y = 0; y < SCREEN; y++)
		memcpy(&dst->p[y * SCREEN + x0], &src->p[y * SCREEN + (x0 - dx)], (size_t)(x1 - x0) * 4);
}

static void blit_y(Cv *dst, const Cv *src, int dy)
{
	for (int y = 0; y < SCREEN; y++) {
		int sy = y - dy;

		if (sy < 0 || sy >= SCREEN) continue;
		memcpy(&dst->p[y * SCREEN], &src->p[sy * SCREEN], (size_t)SCREEN * 4);
	}
}

/* the background of a canvas that will be shown shifted by dx, dy: the gradient, or the pinned wallpaper (frost: the frosted one of a sheet);
 * glass drawn into the canvas until the next call samples the wallpaper from there too */
static void bg_fill(Cv *dst, int dx, int dy, int frost)
{
	g_bgx = dx;
	g_bgy = dy;
	if (wp_on) wp_paint(dst, frost ? wp_frost : wp_bg);
	else memcpy(dst->p, bgc.p, sizeof(uint32_t) * SCREEN * SCREEN);
}

/* over a wallpaper the home page and the function list show it sharp; the pages with bare text on them (signal, traffic, the second-level
 * pages) show it frosted */
static void render_page(Cv *dst, int p, int reg, int dx)
{
	bg_fill(dst, dx, 0, !(p == PG_HOME || (p == PG_FUNC && fn_sub < 0)));
	g_reg = reg;
	draw_page(dst, p);
	g_reg = 0;
}

static void draw_splash(Cv *c)
{
	const Theme *th = TH();
	float a = (float)(g_ms % 1200) / 1200.f * 360.f;

	TXTC(c, FB34, 120, 126, "U30 Pro", C_TXT);
	TXTC(c, FN14, 120, 152, "OpenWrt · 6.12", C_DIM);
	gx_arc(c, 120, 120, 58, 3, 0, 360, C_TXT, 28, 0);
	gx_arc(c, 120, 120, 58, 3, a, a + 80.f, th->a, 255, 1);
}

/* the arcs of the signal and system pages ease towards their value (time constant 140 ms); entering such a view sweeps them in from zero */
static float sm_target(int i)
{
	switch (i) {
	case 0: return sig_rsrp_ok() ? rsrp_level(sig_rsrp()) : 0.f;
	case 1: return D.cpu_pct;
	case 2: return D.mem_pct;
	default: return sig_sinr_ok() ? sinr_level(sig_sinr()) : 0.f;
	}
}

/* the LTE and the NR view of the signal page are two views: swapping them sweeps the arcs in again */
static int sm_view(void) { return page * 64 + fn_sub + 1 + (page == PG_SIGNAL && sig_nr() ? 32 : 0); }

static void smooth_tick(int snap)
{
	static long last;
	static int view = -1;
	float *sm[4] = { &sm_lv, &sm_cpu, &sm_mem, &sm_snr }, dt = (float)(g_ms - last), k;

	if (sm_view() != view) {
		view = sm_view();
		if (snap) dt = 1e6f;
		else { for (int i = 0; i < 3; i++) *sm[i] = 0.f; dt = 0.f; }
	}
	k = dt <= 0.f ? 0.f : (dt >= 1000.f ? 1.f : 1.f - expf(-dt / 140.f));
	for (int i = 0; i < 4; i++) *sm[i] += (sm_target(i) - *sm[i]) * k;
	last = g_ms;
}

static int smooth_busy(void)
{
	int sig = page == PG_SIGNAL, sys = page == PG_FUNC && fn_sub >= 0 && fn_sub < nfn_entries && fn_entries[fn_sub].plugin < 0 && fn_entries[fn_sub].fn == FN_SYSTEM;

	return (sig && (fabsf(sm_lv - sm_target(0)) > .004f || fabsf(sm_snr - sm_target(3)) > .004f)) || (sys && (fabsf(sm_cpu - sm_target(1)) > .3f || fabsf(sm_mem - sm_target(2)) > .3f));
}

static void compose(void)
{
	nhits = 0;
	tile_moving = 0;
	want_charger = 0;
	scroll_live = -1;
	marquee_on = marquee_moving = 0;
	press_env = press_now(g_ms);
	smooth_tick(0);
	lw_sync();
	gx_shadow = wp_on ? 136 : 0;
	if (g_ms < splash_until && !D.feed_ok) {
		bg_fill(&scr, 0, 0, 0);
		draw_splash(&scr);
		return;
	}
	if (notice[0]) {
		bg_fill(&scr, 0, 0, 1);
		draw_notice(&scr);
		return;
	}
	if (power_menu) {
		bg_fill(&scr, 0, 0, 1);
		g_reg = 1;
		draw_power_menu(&scr);
		g_reg = 0;
		draw_press(&scr);
		return;
	}
	if (pin_mode) {
		bg_fill(&scr, 0, 0, pin_mode != PIN_LOCKSCR);
		g_reg = pin_mode != PIN_LOCKSCR;      /* the lock screen shows the home screen without its touch areas */
		if (pin_mode == PIN_LOCKSCR) draw_lockscr(&scr); else draw_pin(&scr);
		g_reg = 0;
		draw_press(&scr);
		draw_toast(&scr);
		return;
	}
	if (tx_off > -.5f && tx_off < .5f) {
		render_page(&scr, page, ov == OV_NONE && !sheet_is_open(), 0);
	} else {
		int nb = tx_off < 0 ? wrap_page(page + 1) : wrap_page(page - 1);

		render_page(&pa, page, 0, (int)tx_off);
		render_page(&pb, nb, 0, (int)tx_off + (tx_off < 0 ? SCREEN : -SCREEN));
		blit(&scr, &pa, (int)tx_off);
		blit(&scr, &pb, (int)tx_off + (tx_off < 0 ? SCREEN : -SCREEN));
		g_bgx = 0;
	}
	if (!(page == PG_FUNC && fn_sub >= 0)) {
		float a = tx_off < 0.f ? -tx_off / (float)SCREEN : tx_off / (float)SCREEN;

		dots(&scr, page, tx_off < 0.f ? wrap_page(page + 1) : wrap_page(page - 1), a > 1.f ? 1.f : a);
	}
	if (sheet_is_open()) {
		int off = (int)((1.f - sheet_pos) * (float)SCREEN);

		scrim(&scr, (unsigned)(sheet_pos * 150.f));
		bg_fill(&shc, 0, sheet == SH_CTRL ? -off : off, 1);
		g_reg = sheet_pos >= 0.999f && !sheet_anim && ov == OV_NONE;
		draw_sheet(&shc, sheet);
		g_reg = 0;
		blit_y(&scr, &shc, sheet == SH_CTRL ? -off : off);
		g_bgy = 0;
	}
	g_reg = 1;
	draw_overlay(&scr);
	g_reg = 0;
	draw_press(&scr);
	draw_toast(&scr);
}

/* ------------------------------------------------------------------ events -> toasts */
static void detect_events(void)
{
	static struct { int init, conn, chg, low, clients, unread, relay; } p;

	if (!D.feed_ok) return;
	if (!p.init) {
		p.init = 1; p.conn = D.cell_connected; p.chg = D.charging; p.clients = D.nclients; p.unread = D.sms_unread; p.relay = D.relay_state;
		return;
	}
	if (D.cell_connected != p.conn) {
		toast_show(D.cell_connected ? "网络已连接" : "网络已断开", D.cell_connected ? C_OK : C_BAD, 3000);
		p.conn = D.cell_connected;
	}
	if (D.relay_state != p.relay) {
		char m[64];

		if (D.relay_state == 2) { snprintf(m, sizeof m, "中继已连接 %.20s", D.relay_ssid); toast_show(m, C_OK, 3000); }
		else if (p.relay == 2) toast_show("Wi-Fi 中继已断开", C_WARN, 3000);
		p.relay = D.relay_state;
	}
	if (D.charging != p.chg) {
		toast_show(D.charging ? "开始充电" : "已断开外部电源", D.charging ? C_OK : C_WARN, 3000);
		p.chg = D.charging;
	}
	if (D.bat_pct >= 0 && D.bat_pct <= 15 && !D.charging && !p.low) {
		char m[48];

		snprintf(m, sizeof m, "电量低 %d%%", D.bat_pct);
		toast_show(m, C_BAD, 5000);
		p.low = 1;
	}
	if (D.bat_pct > 20 || D.charging) p.low = 0;
	if (D.nclients > p.clients) toast_show("新设备接入热点", TH()->a, 3000);
	p.clients = D.nclients;
	if (D.sms_unread > p.unread) {
		toast_show("收到新短信", TH()->b, 5000);
		if (!asleep) last_act = now_ms();
	}
	p.unread = D.sms_unread;
}

/* a live wallpaper plays on the lock screen (wp_live 1 and 2) and on the home page (2) while nothing moves or touches the screen; the
 * frame it stops at stays the wallpaper everywhere else */
static int live_here(void)
{
	if (asleep || notice[0] || tc.down || wp_live < 1) return 0;
	if (pin_mode == PIN_LOCKSCR) return 1;
	return wp_live == 2 && !pin_mode && page == PG_HOME && ov == OV_NONE && !sheet_is_open() && tx_off > -.5f && tx_off < .5f && !anim_on &&
	       !dragging;
}

static void live_tick(long now)
{
	if (lw_tick(now, live_here())) dirty = 1;
}

/* a toast sliding in or out wants full frame rate */
static int toast_moving(void) { return g_ms < toast.until && (g_ms - toast.start < 200 || toast.until - g_ms < 260); }

/* dynamic refresh rate: the frame interval follows what is moving.  Page swipes and sheet drags run as fast as the panel link takes frames (16 ms
 * target; a flip still in progress simply postpones the next one), small effects (button feedback, toast, switches, gauges) run at 30 fps, other
 * activity at about 9 fps and a still screen at 1 fps.  Every flip rewrites the whole panel (present() in platform.h), and the 1 fps floor is a
 * real full-screen refresh: a still screen is rewritten once a second even when the frame did not change. */
static long frame_interval(void);

static int fast_frames(void)
{
	int power_page = fn_sub >= 0 && fn_sub < nfn_entries && fn_entries[fn_sub].plugin < 0 && fn_entries[fn_sub].fn == FN_POWER;

	return anim_on || dragging || sheet_anim || tc.sdrag || fling.on || scroll_moving || press_busy() || smooth_busy() || tile_moving || hold_pct > 0 ||
	       g_ms < toast.until || g_ms < wifi_busy_until || g_ms < recon_busy_until || g_ms < relay_busy_until || g_ms < bypass_busy_until || g_ms < splash_until || marquee_on ||
	       ((page == PG_HOME || power_page || pin_mode == PIN_LOCKSCR) && D.charging);
}

static long frame_interval(void)
{
	if (anim_on || dragging || sheet_anim || tc.sdrag || fling.on) return 16;
	if (lw.playing) return lw_interval();
	if (notice[0]) return 33;
	if (press_busy() || smooth_busy() || tile_moving || toast_moving() || hold_pct > 0 || marquee_moving) return 33;
	return fast_frames() ? 110 : 1000;
}

static void ui_init_canvases(void)
{
	scr = cv_new(SCREEN, SCREEN);
	pa = cv_new(SCREEN, SCREEN);
	pb = cv_new(SCREEN, SCREEN);
	shc = cv_new(SCREEN, SCREEN);
	bg_build();
	wp_check();
	fn_build();
}

static void tz_init(void)
{
	char b[64];

	if (getenv("TZ")) { tzset(); return; }
	if (rd_text("/tmp/TZ", b, sizeof b) || rd_text("/etc/TZ", b, sizeof b)) setenv("TZ", b, 1);
	else setenv("TZ", "CST-8", 1);
	tzset();
}

/* ------------------------------------------------------------------ plugins */
static void plug_tick(long now)
{
	int open_idx = -1;

	plug_scan(now);
	fn_build();
	if (fn_sub >= 0 && fn_sub < nfn_entries && fn_entries[fn_sub].plugin >= 0) open_idx = fn_entries[fn_sub].plugin;
	else if (fn_sub >= nfn_entries) fn_sub = -1;
	plug_poll(now, open_idx, page == PG_FUNC && fn_sub < 0 && !asleep);
	for (int i = 0; i < nplug; i++) {
		Plugin *pl = &plugs[i];

		if (pl->type == PT_ACTION && pl->msg[0] && !pl->msg_pending) {
			toast_show(pl->msg, TH()->a, 3000);
			pl->msg[0] = 0;
		}
	}
}

/* ------------------------------------------------------------------ host simulator */
#ifdef UI_HOST
static int guide;

static void shot_write(const char *dir, const char *name)
{
	char path[256];

	snprintf(path, sizeof path, "%s/%s.ppm", dir, name);
	FILE *f = fopen(path, "wb");

	if (!f) return;
	fprintf(f, "P6\n%d %d\n255\n", SCREEN, SCREEN);
	for (int y = 0; y < SCREEN; y++)
		for (int x = 0; x < SCREEN; x++) {
			uint32_t c = scr.p[y * SCREEN + x];
			float dx = (float)x + .5f - 120.f, dy = (float)y + .5f - 120.f, d = sqrtf(dx * dx + dy * dy);

			if (guide && fabsf(d - 108.f) < .8f) c = gx_mix(c, 0xFF0000, 160);
			if (!guide && d > 111.f) c = gx_mix(c, 0x000000, 235);
			unsigned char rgb[3] = { (unsigned char)(c >> 16), (unsigned char)(c >> 8), (unsigned char)c };
			fwrite(rgb, 1, 3, f);
		}
	fclose(f);
}

static void mock_data(void)
{
	memset(&D, 0, sizeof D);
	D.now = 1792368120;
	D.clock_ok = 1;
	D.bat_ok = 1; D.bat_pct = 78; D.charging = 1; snprintf(D.bat_status, sizeof D.bat_status, "Charging");
	D.bat_v = 4.02f; D.bat_a = .62f; D.bat_i = .62f; D.bat_temp = 31.2f; D.bat_full_mah = 3500.f;
	D.chg_online = 1; D.chg_vbus = 5.07f; D.chg_ilim = 3.f;
	snprintf(D.cell_state, sizeof D.cell_state, "connected");
	D.cell_connected = 1;
	snprintf(D.op, sizeof D.op, "CMHK"); snprintf(D.rat, sizeof D.rat, "5G"); snprintf(D.ip, sizeof D.ip, "10.64.3.7");
	D.csq = 24; D.have_sig = 1; D.band = 41; D.pci = 428; D.earfcn = 504990; D.rsrp = -93.f; D.rsrq = -11.f; D.sinr = 14.f; D.sinr_ok = 1; D.bw_idx = 5;
	D.wifi_enabled = D.wifi_active = 1; D.channel = 36; D.nclients = 4;
	snprintf(D.ssid, sizeof D.ssid, "U30Pro-5G"); snprintf(D.psk, sizeof D.psk, "12345678"); snprintf(D.wifi_band, sizeof D.wifi_band, "5 GHz");
	snprintf(D.cl[0].name, sizeof D.cl[0].name, "iPhone-Alex"); snprintf(D.cl[0].ip, sizeof D.cl[0].ip, "192.168.25.101");
	snprintf(D.cl[1].name, sizeof D.cl[1].name, "MacBook-Pro"); snprintf(D.cl[1].ip, sizeof D.cl[1].ip, "192.168.25.114");
	snprintf(D.cl[2].name, sizeof D.cl[2].name, "小米电视"); snprintf(D.cl[2].ip, sizeof D.cl[2].ip, "192.168.25.130");
	D.usb_up = 1; D.admin_wifi = 1; D.endc = 0;
	D.nsms = 4; D.sms_unread = 1; D.sms_total = 4;
	snprintf(D.sms[0].addr, sizeof D.sms[0].addr, "10086"); snprintf(D.sms[0].ts, sizeof D.sms[0].ts, "2026-10-19 08:12:00 +08:00");
	D.sms[0].unread = 1;
	snprintf(D.sms[0].text, sizeof D.sms[0].text, "【中国移动】您本月已使用流量 12.3GB，剩余 87.7GB。如需办理流量包请回复 LL。祝您生活愉快！这是一条用于测试自动换行与滚动显示的较长短信内容，包含 English words and numbers 12345。");
	snprintf(D.sms[1].addr, sizeof D.sms[1].addr, "+85291234567"); snprintf(D.sms[1].ts, sizeof D.sms[1].ts, "2026-10-18 21:40:00 +08:00");
	snprintf(D.sms[1].text, sizeof D.sms[1].text, "晚上到家说一声");
	snprintf(D.sms[2].addr, sizeof D.sms[2].addr, "95588"); snprintf(D.sms[2].ts, sizeof D.sms[2].ts, "2026-10-17 10:02:00 +08:00");
	snprintf(D.sms[2].text, sizeof D.sms[2].text, "Your verification code is 482913");
	snprintf(D.sms[3].addr, sizeof D.sms[3].addr, "Telecom"); snprintf(D.sms[3].ts, sizeof D.sms[3].ts, "2026-10-15 09:00:00 +08:00");
	snprintf(D.sms[3].text, sizeof D.sms[3].text, "ok");
	D.cpu_pct = 23.f; D.mem_pct = 41.f; D.load1 = .42f; D.temp_c = 52.f; D.uptime = 189000; D.mem_total_mb = 1996;
	snprintf(D.kernel, sizeof D.kernel, "6.12.51"); snprintf(D.slot, sizeof D.slot, "A");
	D.rx_bps = 3.4e6; D.tx_bps = 410e3; D.rx_total = 5210ull << 20; D.tx_total = 640ull << 20;
	for (int i = 0; i < 60; i++) {
		D.hist_rx[i] = (float)(1.5e6 + 1.4e6 * sin(i / 5.0) + (i > 40 ? 2.5e6 : 0));
		D.hist_tx[i] = (float)(2e5 + 1.5e5 * sin(i / 3.0 + 1) + (i % 17 == 0 ? 3e5 : 0));
	}
	D.hist_n = 60;
	D.feed_ok = 1;
}

static void sim_advance(long ms)
{
	long end = vclock + ms;

	while (vclock < end) {
		long step = end - vclock > 20 ? 20 : end - vclock;

		vclock += step;
		g_ms = vclock;
			nav_tick(vclock);
			sheet_tick(vclock);
			scroll_tick(vclock);
			touch_tick(vclock);
			apply_idle(vclock);
			live_tick(vclock);
		}
		g_ms = vclock;
}

static int host_main(const char *dir, int argc, char **argv)
{
	char line[256], name[96];

	for (int i = 0; i < argc; i++) if (!strcmp(argv[i], "--guide")) guide = 1;
	tz_init();
	ui_init_canvases();
	mock_data();
	bright_max = 1024;
	last_act = 0;
	plug_scan(0);
	while (fgets(line, sizeof line, stdin)) {
		int a, b, c2, d, n;
		long ms;

		g_ms = vclock;
		wp_check();
		if (sscanf(line, "tap %d %d", &a, &b) == 2) {
			compose();
			ui_touch(T_DOWN, a, b, vclock); sim_advance(60); ui_touch(T_UP, a, b, vclock);
		} else if (sscanf(line, "swipe %d %d %d %d %d", &a, &b, &c2, &d, &n) == 5) {
			compose();
			ui_touch(T_DOWN, a, b, vclock);
			for (int i = 1; i <= 8; i++) {
				sim_advance(n / 8);
				compose();
				ui_touch(T_MOVE, a + (c2 - a) * i / 8, b + (d - b) * i / 8, vclock);
			}
			ui_touch(T_UP, c2, d, vclock);
		} else if (sscanf(line, "down %d %d", &a, &b) == 2) {
			compose();
			ui_touch(T_DOWN, a, b, vclock);
		} else if (sscanf(line, "move %d %d", &a, &b) == 2) {
			ui_touch(T_MOVE, a, b, vclock);
		} else if (!strncmp(line, "up", 2)) {
			ui_touch(T_UP, tc.x, tc.y, vclock);
		} else if (sscanf(line, "hold %d %d %ld", &a, &b, &ms) == 3) {
			compose();
			ui_touch(T_DOWN, a, b, vclock); sim_advance(ms); ui_touch(T_UP, a, b, vclock);
		} else if (sscanf(line, "wait %ld", &ms) == 1) {
			sim_advance(ms);
		} else if (sscanf(line, "key %d %ld", &a, &ms) == 2) {
			ui_key(a, ms);
		} else if (sscanf(line, "key %d", &a) == 1) {
			ui_key(a, 100);
		} else if (sscanf(line, "page %d", &a) == 1) {
			page = wrap_page(a); tx_off = 0; anim_on = 0; ov = OV_NONE; fn_sub = -1; sheet = SH_NONE; sheet_pos = 0.f; sheet_anim = 0;
			press_kill(); smooth_tick(1);
		} else if (sscanf(line, "sheet %d", &a) == 1) {
			ov = OV_NONE; sheet = a; sheet_pos = a ? 1.f : 0.f; sheet_anim = 0; sms_scroll = 0;
		} else if (sscanf(line, "fn %d", &a) == 1) {
			page = PG_FUNC; tx_off = 0; anim_on = 0; ov = OV_NONE; sheet = SH_NONE; sheet_pos = 0.f; fn_build(); fn_sub = a < nfn_entries ? a : -1;
			plug_tick(vclock + 100000);
			smooth_tick(1);
		} else if (sscanf(line, "fnscroll %d", &a) == 1) {
			scroll_pos[0] = (float)a;
		} else if (!strncmp(line, "plugins", 7)) {
			plug_scan_next = 0; plug_dir_mtime = 0; plug_tick(vclock + 100000);
		} else if (sscanf(line, "overlay %d %d", &a, &b) == 2 && a >= OV_NONE && a <= OV_SMS) {
			compose(); ov = a; ov_arg = b; sms_scroll = 0;
		} else if (sscanf(line, "set battery %d", &a) == 1) {
			D.bat_pct = a; D.charging = 0;
		} else if (!strncmp(line, "set nosig", 9)) {
			D.have_sig = 0; D.cell_connected = 0; snprintf(D.cell_state, sizeof D.cell_state, "waiting-sim");
		} else if (!strncmp(line, "notice", 6)) {
			line[strcspn(line, "\r\n")] = 0;
			snprintf(notice, sizeof notice, "%s", line[6] ? line + 7 : "");
		} else if (sscanf(line, "set charger %d", &a) == 1) {
			/* charger state for the power page: 0 unplugged (discharging), 1 charging, 2 direct supply, 3 paused by the temperature protection */
			D.chg_online = a != 0; D.chg_bypass = a == 2 || a == 3; D.chg_paused = a == 3; D.charging = a == 1;
			D.bat_i = a == 0 ? -.48f : (a == 1 ? .62f : .01f); D.bat_a = fabsf(D.bat_i);
		} else if (!strncmp(line, "set nosinr", 10)) {
			D.sinr_ok = 0;
		} else if (!strncmp(line, "set endc", 8)) {
			/* EN-DC with its NR leg up, as the modem reported it on 2026-10-28: LTE B3 anchor, NR n78 */
			D.endc = D.nr = 1; D.nr_idle = 0; D.band = 3; D.pci = 196; D.earfcn = 1850; D.bw_idx = 3; D.rsrp = -91.f; D.sinr = 17.f; D.sinr_ok = 1;
			D.nr_band = 78; D.nr_pci = 307; D.nr_arfcn = 628704; D.nr_bw = 80; D.nr_rsrp = -92.f; D.nr_sinr = 19.5f; D.nr_rsrp_ok = D.nr_sinr_ok = 1;
		} else if (!strncmp(line, "set nrleg0", 10)) {
			/* the anchor of EN-DC with its NR leg released (an idle link) */
			D.endc = 1; D.nr = D.nr_idle = 0;
		} else if (!strncmp(line, "set nridle", 10)) {
			/* the NR leg released, the last one seen kept (set endc first) */
			D.nr = 0; D.nr_idle = 1;
		} else if (!strncmp(line, "unlock", 6)) {
			/* the scripts that test other pages start unlocked */
			if (lock_soft) soft_unlock();
		} else if (!strncmp(line, "set siglte", 10)) {
			sig_lte = 1;
		} else if (sscanf(line, "set ssid %39s", name) == 1) {
			snprintf(D.ssid, sizeof D.ssid, "%s", name);
		} else if (sscanf(line, "set relay %d %39s", &a, name) == 2) {
			/* relay: switch, state (0 off, 1 connecting, 2 connected) in a*10+state, network ("-" for none) */
			D.relay_on = a / 10; D.relay_state = a % 10;
			snprintf(D.relay_cfg, sizeof D.relay_cfg, "%s", strcmp(name, "-") ? name : "");
			snprintf(D.relay_ssid, sizeof D.relay_ssid, "%s", D.relay_state == 2 ? D.relay_cfg : "");
		} else if (!strncmp(line, "set clock0", 10)) {
			D.clock_ok = 0;
		} else if (sscanf(line, "theme %d", &a) == 1) {
			theme_idx = a; bg_build();
		} else if (sscanf(line, "setpin %7s", name) == 1) {
			/* a PIN and the lock on, as the web interface would set them; prints the stored form for checking against sha256sum */
			pin_store(name); lock_on = 1; settings_save();
			printf("PIN %s\n", pin_hash);
		} else if (sscanf(line, "wakemode %d", &a) == 1) {
			wake_idx = a % NWAKE;
		} else if (!strncmp(line, "loadsettings", 12)) {
			settings_load();
			lock_engage();
		} else if (!strncmp(line, "sleep", 5)) {
			go_sleep();
		} else if (!strncmp(line, "state", 5)) {
			printf("STATE asleep=%d locked=%d pin_mode=%d lock_on=%d wake=%d fails=%d\n", asleep, locked, pin_mode, lock_on, wake_idx, pin_fails);
		} else if (sscanf(line, "shot %95s", name) == 1) {
			compose();
			shot_write(dir, name);
		} else if (!strncmp(line, "loadfeed", 8)) {
			memset(&D, 0, sizeof D);
			D.now = 1792368120; D.clock_ok = 1; D.bat_ok = 1; D.bat_pct = 64;
			feed_load();
			printf("FEED ok=%d ssid=%s clients=%d nsms=%d unread=%d op=%s rat=%s rsrp=%.0f cl0=%s/%s sms0=%s/%s/%d\n", D.feed_ok, D.ssid, D.nclients, D.nsms,
			       D.sms_unread, D.op, D.rat, D.rsrp, D.cl[0].name, D.cl[0].ip, D.sms[0].addr, D.sms[0].ts, D.sms[0].unread);
		} else if (!strncmp(line, "toast", 5)) {
			{ line[strcspn(line, "\r\n")] = 0; toast_show(line + 6, C_OK, 3000); }
		}
		(void)c2;
	}
	return 0;
}
#endif

/* ------------------------------------------------------------------ device main loop */
#ifndef UI_HOST
static void capture(void)
{
	FILE *f = fopen("/run/u30pro-screen.new", "wb");

	if (!f) return;
	fprintf(f, "P6\n%d %d\n255\n", SCREEN, SCREEN);
	for (int i = 0; i < SCREEN * SCREEN; i++) {
		uint32_t c = scr.p[i];
		unsigned char rgb[3] = { (unsigned char)(c >> 16), (unsigned char)(c >> 8), (unsigned char)c };

		fwrite(rgb, 1, 3, f);
	}
	fclose(f);
	rename("/run/u30pro-screen.new", "/run/u30pro-screen.ppm");
}

static volatile sig_atomic_t running = 1;
static void finish(int sig) { (void)sig; running = 0; }

/* one UI command (ubus u30pro.panel command): "page N", "overlay K ARG", "home", "wake", "toast TEXT", "notice [TEXT]", ... -- for tests and
 * the web UI; the next frame is captured to /run/u30pro-screen.ppm */
static int force_capture;
static long capture_after;	/* a toast is still fading in: the forced capture waits for it */

static void ui_cmd_line(const char *cmd)
{
	char line[160];
	int a, b;

	snprintf(line, sizeof line, "%s", cmd);
	line[strcspn(line, "\r\n")] = 0;
	{
			if (sscanf(line, "page %d", &a) == 1) { go_home(); page = wrap_page(a); }
			else if (sscanf(line, "sheet %d", &a) == 1) { ov = OV_NONE; sheet = a; sheet_pos = a ? 1.f : 0.f; sheet_anim = 0; sms_scroll = 0; }
			else if (sscanf(line, "fn %d", &a) == 1) { go_home(); page = PG_FUNC; fn_build(); fn_sub = a < nfn_entries ? a : -1; }
		else if (sscanf(line, "overlay %d %d", &a, &b) == 2 && a >= OV_NONE && a <= OV_SMS) { ov = a; ov_arg = b; sms_scroll = 0; }
			else if (!strcmp(line, "home")) go_home();
		else if (!strcmp(line, "wake")) wake();
		else if (!strncmp(line, "toast ", 6)) toast_show(line + 6, TH()->a, 3000);
		else if (!strncmp(line, "notice", 6) && (line[6] == ' ' || !line[6])) {
			/* "notice TEXT" covers the screen with TEXT until a bare "notice" (the firmware update), and keeps it awake */
			snprintf(notice, sizeof notice, "%s", line[6] ? line + 7 : "");
			if (notice[0] && asleep) wake();
		}
		else if (sscanf(line, "theme %d", &a) == 1 && a >= 0 && a < NTHEMES) { theme_idx = a; bg_build(); }
		dirty = 1;
		force_capture = 1;
		capture_after = !strncmp(line, "toast ", 6) ? now_ms() + 300 : 0;
		last_act = now_ms();
	}
	state_write();
}

int main(int argc, char **argv)
{
	if (argc != 2) { fprintf(stderr, "Usage: u30pro-panel probe|test|run\n"); return 2; }
	int probe = !strcmp(argv[1], "probe"), test = !strcmp(argv[1], "test");

	if (!probe && !test && strcmp(argv[1], "run")) return 2;
	umask(077);
	mkdir(UI_RUN, 0700);
	drm_setup(!probe);
	if (probe) { close(fd); return 0; }
	signal(SIGTERM, finish);
	signal(SIGINT, finish);
	signal(SIGCHLD, SIG_IGN);
	signal(SIGPIPE, SIG_IGN);
	tz_init();
	backlight_read_limits();
	settings_load();
	ui_init_canvases();
	if (!test) { keys_open(); touch_open(); }
	lock_engage();
	state_write();
	long now = now_ms(), next_collect = 0, retry_touch = now + 2000, next_capture = 0, next_frame = 0, next_stat = 0;

	g_ms = now;
	last_act = now;
	splash_until = now + 2500;
	/* Publish the first frame before querying runtime status.
	 * The driver keeps the firmware logo until this first KMS commit. */
	if (!test) {
		bg_fill(&scr, 0, 0, 0);
		draw_splash(&scr);
		present(&scr);
		backlight_level(bright_max);
	}
	while (running) {
		now = now_ms();
		g_ms = now;
		if (test) {
			splash_until = 0;
			collect_all(now);
			compose();
			present(&scr);
			capture();
			sleep(2);
			break;
		}
		if (flip_pending) flip_in_flight();	/* take the completion event as soon as it is there (the DRM fd stays readable until it is read) */
		keys_read();
		touch_read();
		if (touchfd < 0 && now >= retry_touch) { touch_open(); retry_touch = now + 2000; }
		if (now >= next_collect) {
			collect_all(now);
				plug_tick(now);
				detect_events();
			wp_check();
			next_collect = now + (asleep ? 3000 : 1000);
			dirty = 1;
		}
		nav_tick(now);
		sheet_tick(now);
		scroll_tick(now);
		touch_tick(now);
		apply_idle(now);
		live_tick(now);
		if (!asleep && (dirty || now >= next_frame) && !flip_in_flight()) {
			long long t0 = now_us(), t1;

			compose();
			t1 = now_us();
			stat_compose_us += (unsigned long long)(t1 - t0);
			if (present(&scr)) {
				stat_present_us += (unsigned long long)(now_us() - t1);
				dirty = 0;
				next_frame = now + frame_interval();
				if (force_capture && now >= capture_after) { capture(); force_capture = 0; }
				else if (!fast_frames() && now >= next_capture) { capture(); next_capture = now + 2000; }
			} else next_frame = now + 20;
		}
		if (now >= next_stat) {
			FILE *sf = fopen(UI_RUN "/present-stats.new", "w");

			next_stat = now + 10000;
			if (sf) {
				fprintf(sf, "frames %lu\nsame %lu\nflips %lu\nrows_dithered %lu\ncompose_ms %llu\npresent_ms %llu\n", stat_frames, stat_same, stat_flips,
					stat_rows, stat_compose_us / 1000, stat_present_us / 1000);
				fclose(sf);
				rename(UI_RUN "/present-stats.new", UI_RUN "/present-stats");
			}
		}
		/* wake on input, on the completion of the flip in flight (DRM fd), on a ubus request, or when the next frame is due */
		struct pollfd p[] = { { .fd = keyfd, .events = POLLIN }, { .fd = touchfd, .events = POLLIN }, { .fd = flip_pending ? fd : -1, .events = POLLIN },
				      { .fd = bus_fd(now), .events = POLLIN } };
		long wait = asleep ? 200 : 30;

		if (!asleep && !flip_pending && next_frame - now < wait) wait = next_frame - now < 1 ? 1 : next_frame - now;
		if (poll(p, 4, (int)wait) > 0 && (p[3].revents & POLLIN)) bus_handle();
	}
	if (keyfd >= 0) close(keyfd);
	if (touchfd >= 0) close(touchfd);
	backlight_level(bright_max);
	close(fd);
	return 0;
}
#else
int main(int argc, char **argv)
{
	if (argc < 3 || strcmp(argv[1], "sim")) { fprintf(stderr, "usage: host sim <dir> [--guide]\n"); return 2; }
	return host_main(argv[2], argc, argv);
}
#endif
