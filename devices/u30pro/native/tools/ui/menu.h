/* Control centre, function menu and second-level pages of the screen UI (included by pages.h after the Wi-Fi, power and system pages). */
#ifndef U30PRO_UI_MENU_H
#define U30PRO_UI_MENU_H

/* round tile (48 px): while pressed the disc brightens, shrinks by 1.5 px and an accent rim lights up - all behind the icon and the label.
 * The label sits 14 px under the disc; the touch box covers both.  Returns how "on" the tile looks (0..1) for the icon colour the caller picks. */
static float tile(Cv *c, int cx, int cy, int on, int busy, const char *label, int id, int hold)
{
	const Theme *th = TH();
	float p = press_amt(id), r = 24.f - 1.5f * p, v = tile_ease(id, on);
	uint32_t face = gx_mix(C_CARD, th->a, (unsigned)(v * 255.f));
	char fit[48];

	if (p > 0.f) face = gx_mix(face, 0xFFFFFF, (unsigned)(60.f * p * v));
	if (wp_on) {
		/* over a wallpaper the disc is glass, filled with the accent while on */
		if (v < .98f) gx_glass(c, (float)cx - r, (float)cy - r, 2.f * r, 2.f * r, r, 0, p, 255);
		if (v > .02f) gx_circle(c, (float)cx, (float)cy, r, face, (int)(255.f * v));
	} else gx_circle(c, (float)cx, (float)cy, r, face, 22 + (int)(233.f * v) + (int)(36.f * p * (1.f - v)));
	if (v < .98f && !wp_on)
		gx_ring(c, (float)cx, (float)cy, r - 1.f, r + .5f, p > 0.f ? gx_mix(C_CARD, th->a, (unsigned)(200.f * p)) : C_CARD, (int)((30.f + 130.f * p) * (1.f - v)));
	if (busy) gx_arc(c, (float)cx, (float)cy, 27.f, 3.f, (float)(g_ms % 1000) * .36f, (float)(g_ms % 1000) * .36f + 90.f, C_WARN, 255, 1);
	gx_fit(FN14, label, 58, fit, sizeof fit);
	TXTC(c, FN14, cx, cy + 50, fit, C_DIM);
	hit_add(cx - 28, cy - 28, 56, 82, id, 1, hold);
	hit_fb(FB_BG, 0, 0, 0, 0, 0);
	return v;
}

/* control centre (pulled down from the top edge; like a watch it has no title): two rows of round tiles, each label 14 px under its tile and
 * 14 px clear of the next row; the grabber at the bottom closes it */
static void page_control(Cv *c)
{
	const Theme *th = TH();
	uint32_t ink = 0x10151F;

	/* row 1 */
	float v = tile(c, 64, 70, D.wifi_active, g_ms < wifi_busy_until, "Wi-Fi", H_WIFI_TOGGLE, 0);

	ic_wifi(c, 64, 70, 11.f, gx_mix(C_TXT, ink, (unsigned)(v * 255.f)), 255);
	tile(c, 120, 70, 0, g_ms < recon_busy_until, "重新拨号", H_RECONNECT, 0);
	ic_refresh(c, 120, 70, 10.f, C_TXT);
	tile(c, 176, 70, 0, 0, OFF_NAMES[off_idx % NOFF], H_SLEEP, 0);
	ic_moon(c, 176, 70, 11.f, C_TXT, 0x2a3345);
	/* row 2 (the backlight cannot dim, so there is no brightness tile) */
	tile(c, 92, 160, 0, 0, th->name, H_THEME, 0);
	ic_palette(c, 92, 160, 12.f);
	tile(c, 148, 160, 0, 0, "电源", H_REBOOT, 0);
	ic_power(c, 148, 160, 12.f, C_BAD);
	pill(c, 108.f, 221.f, 24.f, 4.f, C_TXT, 80);
}

/* ------------------------------------------------------------------ function menu and second-level pages */
static void ic_gear(Cv *c, float cx, float cy, float s, uint32_t col)
{
	for (int i = 0; i < 8; i++) {
		float a = (float)i * GX_PI / 4.f, ca = cosf(a), sa = sinf(a);

		gx_line(c, cx + ca * s * .6f, cy + sa * s * .6f, cx + ca * s * 1.0f, cy + sa * s * 1.0f, s * .3f, col, 255);
	}
	gx_ring(c, cx, cy, s * .3f, s * .78f, col, 255);
}

static void ic_chart(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_rrect(c, cx - s * .9f, cy, s * .5f, s * .9f, s * .12f, col, 255);
	gx_rrect(c, cx - s * .25f, cy - s * .5f, s * .5f, s * 1.4f, s * .12f, col, 255);
	gx_rrect(c, cx + s * .4f, cy - s * .95f, s * .5f, s * 1.85f, s * .12f, col, 255);
}

static void ic_globe(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_ring(c, cx, cy, s * .86f, s, col, 255);
	gx_line(c, cx - s, cy, cx + s, cy, s * .14f, col, 255);
	gx_line(c, cx, cy - s, cx, cy + s, s * .14f, col, 255);
	gx_arc(c, cx, cy, s * .55f, s * .12f, 90.f, 270.f, col, 255, 0);
	gx_arc(c, cx, cy, s * .55f, s * .12f, -90.f, 90.f, col, 255, 0);
}

static void ic_info(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_ring(c, cx, cy, s * .86f, s, col, 255);
	gx_circle(c, cx, cy - s * .4f, s * .12f, col, 255);
	gx_line(c, cx, cy - s * .1f, cx, cy + s * .5f, s * .2f, col, 255);
}

static void ic_app(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_rrect_line(c, cx - s * .8f, cy - s * .8f, s * 1.6f, s * 1.6f, s * .3f, 2.f, col, 255);
	gx_rrect(c, cx - s * .4f, cy - s * .4f, s * .8f, s * .8f, s * .15f, col, 255);
}

static void ic_lock(Cv *c, float cx, float cy, float s, uint32_t col)
{
	gx_arc(c, cx, cy - s * .3f, s * .48f, s * .2f, -90.f, 90.f, col, 255, 0);
	gx_line(c, cx - s * .48f, cy - s * .3f, cx - s * .48f, cy, s * .2f, col, 255);
	gx_line(c, cx + s * .48f, cy - s * .3f, cx + s * .48f, cy, s * .2f, col, 255);
	gx_rrect(c, cx - s * .8f, cy - s * .08f, s * 1.6f, s * 1.08f, s * .22f, col, 255);
}

/* icon by name; an unknown name falls back to a glyph (the plugin's glyph or the first character of its title) */
static void fn_icon(Cv *c, const char *name, const char *glyph, const char *ttl, float cx, float cy, uint32_t col)
{
	if (!strcmp(name, "wifi")) ic_wifi(c, cx, cy, 10.f, col, 255);
	else if (!strcmp(name, "bolt")) ic_bolt(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "gear")) ic_gear(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "sun")) ic_sun(c, cx, cy, 12.f, col);
	else if (!strcmp(name, "moon")) ic_moon(c, cx, cy, 10.f, col, 0x2a3345);
	else if (!strcmp(name, "mail")) ic_mail(c, cx, cy, 11.f, col, 255);
	else if (!strcmp(name, "chart")) ic_chart(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "globe")) ic_globe(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "power")) ic_power(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "refresh")) ic_refresh(c, cx, cy, 9.f, col);
	else if (!strcmp(name, "info")) ic_info(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "app")) ic_app(c, cx, cy, 11.f, col);
	else if (!strcmp(name, "lock")) ic_lock(c, cx, cy, 12.f, col);
	else if (!strcmp(name, "battery")) ic_battery(c, cx - 10.f, cy - 6.f, 21.f, 12.f, 60, col);
	else {
		const char *src = glyph && glyph[0] ? glyph : ttl;
		char g[8] = "?";
		uint32_t cp;
		int n = gx_utf8(src, &cp);

		if (n > 0 && n < 7) { memcpy(g, src, (size_t)n); g[n] = 0; }
		TXTM(c, gx_has_all(FB22, g) ? FB22 : FN16, (int)cx, (int)cy, g, col);
	}
}

static const char *const fn_builtin_icons[NFN_BUILTIN] = { "wifi", "bolt", "battery", "chart", "sun", "info", "lock" };

/* the function list (watch style): full-width rows of 46 px with 8 px between them, the icon in a disc on the left, the name in 16 px, and on
 * the right a switch (a toggle plugin) or a chevron (opens a page); the list scrolls with the finger */
static void page_func(Cv *c)
{
	int off;

	fn_build();
	title(c, PG_FUNC, 0);
	off = scroll_begin(0);
	for (int i = 0; i < nfn_entries; i++) {
		const FnEntry *e = &fn_entries[i];
		const Plugin *pl = e->plugin >= 0 ? &plugs[e->plugin] : NULL;
		int y = 50 + i * 54 - off, tog = pl && pl->type == PT_TOGGLE;
		float cy = (float)y + 23.f;
		char fit[48];

		if (y > VIEW_BOT || y + 46 < VIEW_TOP) continue;
		card_p(c, ROW_X, (float)y, ROW_W, 46.f, 23.f, H_FN + i);
		hit_add(ROW_X, y, ROW_W, 46, H_FN + i, 0, 0);
		hit_fb(FB_BG, 0, 0, 0, 0, 0);
		gx_circle(c, ROW_X + 23.f, cy, 16.f, C_CARD, 30);
		if (!pl) fn_icon(c, fn_builtin_icons[e->fn], NULL, e->title, ROW_X + 23.f, cy, C_TXT);
		else fn_icon(c, pl->icon, pl->glyph, e->title, ROW_X + 23.f, cy, C_TXT);
		/* the name runs up to the switch (4 px before it) or the chevron */
		gx_fit(FN16, e->title, tog ? 66 : 92, fit, sizeof fit);
		TXT(c, FN16, ROW_X + 46, y + 29, fit, C_TXT);
		if (tog) toggle_sw(c, ROW_X + ROW_W - 54.f, (float)y + 10.f, pl->state, H_FN + i, 0);
		else if (!pl || pl->type != PT_ACTION) chevron_r(c, ROW_X + ROW_W - 18.f, cy);
	}
	scroll_end(c, 0, 50 + nfn_entries * 54 - 8);
}

static void page_display(Cv *c)
{
	const Theme *th = TH();
	char v[5][16];
	int off;

	/* sleep timeout, what a touch does to the dark screen, theme; with a wallpaper (set in the web interface) also the glass and the
	 * wallpaper itself, and the page scrolls.  The backlight can only be switched on or off, so there is no brightness. */
	title_sub(c, "显示");
	off = wp_on ? scroll_begin(1) : 0;
	setting_row(c, 54 - off, H_SLEEP, "息屏", OFF_NAMES[off_idx % NOFF]);
	setting_row(c, 106 - off, H_WAKE, "触摸亮屏", WAKE_NAMES[wake_idx % NWAKE]);
	setting_row(c, 158 - off, H_THEME, "主题", th->name);
	if (wp_on) {
		snprintf(v[0], sizeof v[0], "%d%%", glass_clear);
		snprintf(v[1], sizeof v[1], "%d", glass_blur);
		snprintf(v[2], sizeof v[2], "%d%%", wp_dim);
		setting_row(c, 210 - off, H_GLASS, "玻璃透明度", v[0]);
		setting_row(c, 262 - off, H_GLASS_BLUR, "玻璃模糊", v[1]);
		setting_row(c, 314 - off, H_GLASS_TINT, "玻璃颜色", TINT_NAMES[glass_tint % NTINT]);
		setting_row(c, 366 - off, H_WP_DIM, "壁纸压暗", v[2]);
		if (lw.n > 1) setting_row(c, 418 - off, H_WP_LIVE, "动态壁纸", LIVE_NAMES[wp_live % 3]);
		scroll_end(c, 1, lw.n > 1 ? 470 : 418);
	}
}

static void page_lock(Cv *c)
{
	int on = lock_on && pin_hash[0];

	title_sub(c, "锁屏");
	setting_toggle(c, 62, H_LOCK_TOGGLE, "锁屏密码", on, 0);
	setting_row(c, 114, H_PIN_SET, pin_hash[0] ? "修改密码" : "设置密码", "");
	TXTC(c, FN14, 120, 188, on ? "熄屏后需输入密码解锁" : "开启后熄屏即锁定", C_DIM);
}

/* The locked screen has two faces.  The lock screen (PIN_LOCKSCR) is the home screen with a lock; a swipe or a long press opens the
 * unlock pad.  The unlock pad (PIN_UNLOCK) has only the digits and one round key in the middle: delete while digits are entered (the dots
 * of the entered digits sit on it), back to the lock screen while none are.  The ten digits sit on a ring along the edge (like a rotary
 * dial), each owning a 36 degree wedge from the centre key out to the edge (about 50 x 64 px under the finger).  Digit 1 is at the top,
 * then clockwise to 9, and 0 just left of the top.  Setting a new PIN uses the same ring with a larger centre that holds a prompt, cancel,
 * delete and confirm. */
#define PIN_CX 120.f
#define PIN_CY 120.f

/* radius of the centre: small on the unlock pad (one key), larger while a new PIN is set (four things in it) */
static float pin_rin(void) { return pin_mode == PIN_UNLOCK ? 42.f : 54.f; }

static int pin_slot_digit(int s) { return s == 9 ? 0 : s + 1; }

/* the key under a touch: a digit, PIN_DEL, PIN_OK, PIN_CANCEL, PIN_BACK, or -1 */
static int pin_hit(int x, int y)
{
	float dx = (float)x - PIN_CX, dy = (float)y - PIN_CY, r = pin_rin();

	if (dx * dx + dy * dy >= r * r) {
		float a = atan2f(dx, -dy) * 180.f / GX_PI;

		if (a < 0.f) a += 360.f;
		return pin_slot_digit((int)((a + 18.f) / 36.f) % 10);
	}
	if (pin_mode == PIN_UNLOCK) return pin_len ? PIN_DEL : PIN_BACK;
	if ((float)y < PIN_CY - 6.f) return PIN_CANCEL;
	return (float)x >= PIN_CX ? PIN_OK : PIN_DEL;
}

static void pin_del_icon(Cv *c, float x, float y, float s, uint32_t col)
{
	float w = 1.8f * s;

	gx_line(c, x - 11.f * s, y, x - 5.f * s, y - 7.f * s, w, col, 255);
	gx_line(c, x - 11.f * s, y, x - 5.f * s, y + 7.f * s, w, col, 255);
	gx_line(c, x - 5.f * s, y - 7.f * s, x + 10.f * s, y - 7.f * s, w, col, 255);
	gx_line(c, x - 5.f * s, y + 7.f * s, x + 10.f * s, y + 7.f * s, w, col, 255);
	gx_line(c, x + 10.f * s, y - 7.f * s, x + 10.f * s, y + 7.f * s, w, col, 255);
	gx_line(c, x - 1.f * s, y - 3.f * s, x + 5.f * s, y + 3.f * s, w * .9f, col, 255);
	gx_line(c, x + 5.f * s, y - 3.f * s, x - 1.f * s, y + 3.f * s, w * .9f, col, 255);
}

static void draw_page(Cv *c, int p);

/* the lock screen: the home screen as it is (time, date, operator and signal, rates, hotspot, battery) but without its touch areas, with
 * a lock and how to get to the pad in the free space above the time */
static void draw_lockscr(Cv *c)
{
	const char *hint = lock_soft ? "长按解锁" : "滑动解锁";
	int x0 = 120 - (gx_text_w(FN14, hint) + 20) / 2;

	draw_page(c, PG_HOME);
	ic_lock(c, (float)(x0 + 7), 38.f, 7.f, C_DIM);
	TXT(c, FN14, x0 + 20, 44, hint, C_DIM);
}

/* the PIN pad: the unlock pad or the pad for a new PIN */
static void draw_pin(Cv *c)
{
	const Theme *th = TH();
	int unlock = pin_mode == PIN_UNLOCK;
	float rin = pin_rin(), rmid = (rin + 108.f) * .5f, rlab = unlock ? 79.f : 81.f;
	const UiFont *df = unlock && gx_has_all(FB44, "0123456789") ? FB44 : (gx_has_all(FB34, "0123456789") ? FB34 : FB22);
	char b[48];
	long wait = unlock && g_ms < pin_wait_until ? (pin_wait_until - g_ms + 999) / 1000 : 0;
	int err = unlock && g_ms < pin_err_until;

	/* the ring: a faint band (over a wallpaper on a disc of glass), thin dividers, the pressed wedge lit */
	if (wp_on) gx_glass(c, PIN_CX - 108.f, PIN_CY - 108.f, 216.f, 216.f, 108.f, 0, 0.f, 255);
	gx_ring(c, PIN_CX, PIN_CY, rin + 2.f, 108.f, err ? C_BAD : C_CARD, err ? 40 : 18);
	for (int s = 0; s < 10; s++) {
		float a = (36.f * (float)s + 18.f) * GX_PI / 180.f;

		gx_line(c, PIN_CX + (rin + 4.f) * sinf(a), PIN_CY - (rin + 4.f) * cosf(a), PIN_CX + 106.f * sinf(a), PIN_CY - 106.f * cosf(a), 1.f, C_TXT, 22);
	}
	for (int s = 0; s < 10; s++) {
		int d = pin_slot_digit(s);
		float p = press_amt(H_PIN + d), a = 36.f * (float)s, ra = a * GX_PI / 180.f;
		char t[2] = { (char)('0' + d), 0 };

		if (p > 0.f) gx_arc(c, PIN_CX, PIN_CY, rmid, 108.f - rin - 4.f, a - 17.f, a + 17.f, th->a, (int)(60.f * p), 0);
		TXTM(c, df, (int)(PIN_CX + rlab * sinf(ra) + .5f), (int)(PIN_CY - rlab * cosf(ra) + .5f), t, wait ? C_FAINT : C_TXT);
	}
	if (unlock) {
		/* the one key: a round button with the entered digits on it (delete) or a back arrow (none entered); a cool-down after wrong
		   PINs shows its seconds instead, a wrong PIN shows red dots for a moment */
		float pk = press_amt(H_PIN + (pin_len ? PIN_DEL : PIN_BACK));

		gx_circle(c, PIN_CX, PIN_CY, rin - 3.f, 0x101A2A, 220);
		gx_ring(c, PIN_CX, PIN_CY, rin - 3.5f, rin - 2.f, C_TXT, 40 + (int)(60.f * pk));
		if (pk > 0.f) gx_circle(c, PIN_CX, PIN_CY, rin - 4.f, C_TXT, (int)(18.f * pk));
		if (wait) {
			snprintf(b, sizeof b, "%ld", wait);
			TXTM(c, FB22, 120, 120, b, C_WARN);
		} else if (err || pin_len) {
			int n = err ? (pin_digits ? pin_digits : 4) : pin_len;
			float y = err ? 120.f : 111.f;

			for (int i = 0; i < n; i++) gx_circle(c, PIN_CX + ((float)i - (float)(n - 1) / 2.f) * 11.f, y, 4.f, err ? C_BAD : C_TXT, 255);
			if (!err) pin_del_icon(c, 120.f, 131.f, .8f, C_DIM);
		} else {
			/* back to the lock screen */
			gx_line(c, 113.f, 120.f, 125.f, 108.f, 3.f, C_TXT, 255);
			gx_line(c, 113.f, 120.f, 125.f, 132.f, 3.f, C_TXT, 255);
		}
		return;
	}
	gx_circle(c, PIN_CX, PIN_CY, rin - 2.f, 0x0E1624, 140);
	{
		int msg = pin_msg && g_ms < pin_msg_until;
		const char *t = msg ? pin_msg : (pin_mode == PIN_NEW ? "设置新密码" : "再次输入新密码");
		float p = press_amt(H_PIN + PIN_CANCEL), pd = press_amt(H_PIN + PIN_DEL), po = press_amt(H_PIN + PIN_OK);

		if (p > 0.f) gx_circle(c, PIN_CX, 96.f, 20.f, C_TXT, (int)(18.f * p));
		TXTC(c, FN14, 120, 93, "‹ 取消", C_DIM);
		TXTC(c, FN14, 120, 110, t, msg ? C_BAD : C_TXT);
		if (pin_len)
			for (int i = 0; i < pin_len; i++) gx_circle(c, PIN_CX + ((float)i - (float)(pin_len - 1) / 2.f) * 11.f, 122.f, 3.8f, C_TXT, 255);
		else gx_circle(c, PIN_CX, 122.f, 1.5f, C_FAINT, 255);
		if (pd > 0.f) gx_circle(c, 102.f, 146.f, 15.f, C_TXT, (int)(22.f * pd));
		pin_del_icon(c, 102.f, 146.f, 1.f, pin_len ? C_TXT : C_FAINT);
		if (po > 0.f) gx_circle(c, 138.f, 146.f, 15.f, th->a, (int)(40.f * po));
		gx_line(c, 131.f, 146.f, 136.f, 151.f, 2.4f, pin_len >= 4 ? th->a : C_FAINT, 255);
		gx_line(c, 136.f, 151.f, 145.f, 141.f, 2.4f, pin_len >= 4 ? th->a : C_FAINT, 255);
	}
}

/* About: the device and the software, and where the web interface is.  The web interface answers on the USB link; Wi-Fi hotspot clients are
 * kept out of it until a router password is set and "management access from Wi-Fi" is turned on in the web interface (u30pro-webwifi). */
static void page_about(Cv *c)
{
	char b[96], ver[40], fit[96];
	size_t n = strcspn(D.kernel, "-");
	int off, y;

	title_sub(c, "关于");
	off = scroll_begin(1);
	y = 74 - off;
	TXTC(c, FB22, 120, y, "U30 Pro", C_TXT);
	TXTC(c, FN14, 120, y + 20, "OpenWrt", C_DIM);
	snprintf(ver, sizeof ver, "%.*s", (int)n, D.kernel);
	snprintf(b, sizeof b, "Linux %s%s%s", ver, D.slot[0] ? " · 槽位 " : "", D.slot[0] ? D.slot : "");
	gx_fit(FN14, b, 156, fit, sizeof fit);
	TXTC(c, FN14, 120, y + 40, fit, C_DIM);
	/* the web interface: its address on a card that shows the QR code when hotspot clients may open it */
	TXT(c, FN14, ROW_X + 18, y + 72, "网页管理", C_DIM);
	y += 82;
	if (D.admin_wifi) {
		card_p(c, ROW_X, (float)y, ROW_W, 62, 20, H_QR_ADMIN);
		hit_add(ROW_X, y, ROW_W, 62, H_QR_ADMIN, 0, 0);
		hit_fb(FB_BG, 0, 0, 0, 0, 0);
	} else card(c, ROW_X, (float)y, ROW_W, 62, 20);
	TXTC(c, FN16, 120, y + 27, "192.168.25.1", C_TXT);
	TXTC(c, FN14, 120, y + 48, D.admin_wifi ? "点按显示二维码" : "仅限 USB 连接访问", C_DIM);
	y += 62;
	if (!D.admin_wifi) {
		TXTC(c, FN14, 120, y + 26, "设置登录密码后", C_FAINT);
		TXTC(c, FN14, 120, y + 46, "可在网页中对热点开放", C_FAINT);
		y += 52;
	}
	scroll_end(c, 1, y + off + 8);
}

/* a plugin's text page: its lines are the plugin's own output, drawn in the 16 px font that carries the common Chinese characters; it
 * scrolls when there are more lines than fit */
static void page_plugin(Cv *c, const Plugin *p)
{
	char fit[96];
	int off;

	title_sub(c, p->title);
	if (p->nlines == 0) { TXTC(c, FN16, 120, 124, "读取中…", C_DIM); return; }
	off = scroll_begin(1);
	for (int i = 0; i < p->nlines && i < PLUG_LINES; i++) {
		gx_fit(FN16, p->lines[i], 156, fit, sizeof fit);
		TXTC(c, FN16, 120, 72 + i * 28 - off, fit, i == 0 ? C_TXT : C_DIM);
	}
	scroll_end(c, 1, 72 + (p->nlines < PLUG_LINES ? p->nlines : PLUG_LINES) * 28 - 14);
}

/* a plugin's view page (type=view, plugins.h): the elements of its output from the top down; a gauge runs along the top edge of the window
 * around the title, consecutive spark lines share one chart */
static uint32_t view_colour(const char *k)
{
	const Theme *th = TH();

	if (!strcmp(k, "b")) return th->b;
	if (!strcmp(k, "ok")) return C_OK;
	if (!strcmp(k, "warn")) return C_WARN;
	if (!strcmp(k, "bad")) return C_BAD;
	if (!strcmp(k, "dim")) return C_DIM;
	return th->a;
}

static void page_view(Cv *c, const Plugin *p)
{
	char buf[PLUG_VIEW], fit[96];
	int y = 54, spark_open = 0;
	float sx = 52.f, sw = 136.f, sh = 28.f, sy = 0.f;

	title_sub(c, p->title);
	if (!p->nlines) { TXTC(c, FN16, 120, 124, "读取中…", C_DIM); return; }
	snprintf(buf, sizeof buf, "%s", p->view);
	for (char *save = NULL, *l = strtok_r(buf, "\n", &save); l; l = strtok_r(NULL, "\n", &save)) {
		char *arg = strchr(l, ' ');

		if (arg) *arg++ = 0; else arg = l + strlen(l);
		if (strcmp(l, "spark")) spark_open = 0;
		if (!strcmp(l, "gauge")) {
			char col[8] = "a";
			float v = 0.f, max = 0.f, f;

			if (sscanf(arg, "%f %f %7s", &v, &max, col) < 2 || max <= 0.f) continue;
			f = gx_clamp(v / max, 0.f, 1.f);
			gx_arc(c, 120, 120, 108, 6, -130.f, 130.f, C_TXT, 24, 1);
			if (f > .005f) gx_arc(c, 120, 120, 108, 6, -130.f, -130.f + 260.f * f, view_colour(col), 255, 1);
		} else if (!strcmp(l, "label")) {
			gx_fit(FN14, arg, 170, fit, sizeof fit);
			TXTC(c, FN14, 120, y + 14, fit, C_DIM);
			y += 20;
		} else if (!strcmp(l, "big")) {
			char num[24] = "", *unit = strchr(arg, ' ');
			int wn, wu;

			snprintf(num, sizeof num, "%.*s", unit ? (int)(unit - arg) : (int)strlen(arg), arg);
			if (unit) unit++;
			wn = gx_text_w(FB34, num);
			wu = unit && *unit ? gx_text_w(FN16, unit) + 4 : 0;
			TXT(c, FB34, 120 - (wn + wu) / 2, y + 36, num, C_TXT);
			if (wu) TXT(c, FN16, 120 - (wn + wu) / 2 + wn + 4, y + 36, unit, C_DIM);
			y += 46;
		} else if (!strcmp(l, "spark")) {
			char col[8] = "a", *e;
			float max, px = 0.f, py = 0.f;
			int n = 0;

			if (sscanf(arg, "%7s", col) != 1 || !(arg = strchr(arg, ' '))) continue;
			max = strtof(arg, &e);
			if (e == arg || max <= 0.f) continue;
			arg = e;
			if (!spark_open) {
				sy = (float)y;
				card(c, sx, sy, sw, sh, 8);
				y += (int)sh + 8;
				spark_open = 1;
			}
			float vals[60];

			for (; n < 60; n++) {
				vals[n] = strtof(arg, &e);
				if (e == arg) break;
				arg = e;
			}
			/* the values spread over the chart's width */
			for (int k = 0; k < n; k++) {
				float vx = sx + 6.f + (sw - 12.f) * (float)k / (float)(n > 1 ? n - 1 : 1), vy = sy + sh - 5.f - (sh - 10.f) * gx_clamp(vals[k] / max, 0.f, 1.f);

				if (k) gx_line(c, px, py, vx, vy, 1.8f, view_colour(col), 255);
				px = vx; py = vy;
			}
		} else if (!strcmp(l, "row")) {
			char *bar = strchr(arg, '|');

			if (!bar) continue;
			*bar++ = 0;
			gx_fit(FN14, arg, 70, fit, sizeof fit);
			TXT(c, FN14, 54, y + 15, fit, C_DIM);
			gx_fit(FN14, bar, 100, fit, sizeof fit);
			TXTR(c, FN14, 186, y + 15, fit, C_TXT);
			y += 20;
		} else if (!strcmp(l, "text")) {
			gx_fit(FN14, arg, y > 180 ? 150 : 176, fit, sizeof fit);
			TXTC(c, FN14, 120, y + 15, fit, C_DIM);
			y += 19;
		}
	}
}

static void draw_sub(Cv *c)
{
	const FnEntry *e;

	if (fn_sub < 0 || fn_sub >= nfn_entries) return;
	e = &fn_entries[fn_sub];
	if (e->plugin >= 0) { if (plugs[e->plugin].type == PT_VIEW) page_view(c, &plugs[e->plugin]); else page_plugin(c, &plugs[e->plugin]); return; }
	switch (e->fn) {
	case FN_WIFI: page_wifi(c); break;
	case FN_POWER: page_power(c); break;
	case FN_BATLOG: page_batlog(c); break;
	case FN_SYSTEM: page_system(c); break;
	case FN_DISPLAY: page_display(c); break;
	case FN_LOCK: page_lock(c); break;
	default: page_about(c); break;
	}
}

/* ------------------------------------------------------------------ press feedback */
/* widgets draw their own pressed state (FB_BG); what is left for the top layer are the borderless hotspots: a soft glow that grows from the
 * touch point (FB_RIPPLE) or a small dot on an icon (FB_DOT) */
static void draw_press(Cv *c)
{
	const Hit *h = &press_hit;
	float e = press_env;

	if (e <= 0.f || !h->id) return;
	if (h->style == FB_RIPPLE) {
		float r = 16.f + 12.f * ease_out((float)(g_ms - press_t0 - PRESS_DELAY) / 260.f);

		gx_circle(c, (float)press_x, (float)press_y, r, C_TXT, (int)(14.f * e));
		gx_circle(c, (float)press_x, (float)press_y, r * .55f, C_TXT, (int)(16.f * e));
	} else if (h->style == FB_DOT) {
		gx_circle(c, (float)h->vx, (float)h->vy, (float)h->vr * (.75f + .25f * e), C_TXT, (int)(46.f * e));
	}
}
#endif
