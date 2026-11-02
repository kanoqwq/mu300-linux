/* Device side of the screen UI: DRM/KMS output through two dumb buffers, the gpio-keys device, the touch controller,
 * the backlight and the child-process helper.  Not compiled into the host simulator (UI_HOST). */
#ifndef U30PRO_UI_PLATFORM_H
#define U30PRO_UI_PLATFORM_H
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>
#include <linux/input.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <drm/drm_fourcc.h>

enum { T_DOWN, T_MOVE, T_UP };
static void ui_touch(int type, int x, int y, long ms);
static void ui_key(int code, long duration_ms);
static void panel_full_frames(void);

static int fd = -1, keyfd = -1, touchfd = -1;
static unsigned width, height, pitch[2], fb[2], handles[2], crtc, connector, dpms_prop;
static uint32_t *pixels[2];
static struct drm_mode_modeinfo mode;
static int cur_buf = 1, flip_pending, use_events = 1, first_present = 1;
static long flip_at;
static unsigned touch_count;
static int touch_raw_x, touch_raw_y, touch_monotonic;
/* On the real panel the x axis arrives already upright (a swipe to the right must move the page to the right); the settings
 * (display.touch_flip_x / touch_flip_y, tools/ui/bus.h) can change either axis without rebuilding. */
static int touch_flip_x = 0, touch_flip_y = 0;

static long now_ms(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static long long now_us(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return (long long)t.tv_sec * 1000000 + t.tv_nsec / 1000;
}

static void die(const char *s) { perror(s); exit(1); }
static void xio(unsigned long req, void *arg, const char *name) { if (ioctl(fd, req, arg) < 0) die(name); }
static uint64_t ptr(void *p) { return (uint64_t)(uintptr_t)p; }

/* ------------------------------------------------------------------ backlight */
static int sysfs_write(const char *path, const char *text)
{
	FILE *f = fopen(path, "w");

	if (!f) return 0;
	fputs(text, f);
	return fclose(f) == 0;
}

static int bl_last = -1;

static void backlight_level(int level)
{
	char b[24];

	bl_last = level;
	snprintf(b, sizeof b, "%d\n", level);
	sysfs_write("/sys/class/backlight/sprd_backlight/brightness", b);
}


static void backlight_read_limits(void)
{
	long mx = rd_long("/sys/class/backlight/sprd_backlight/max_brightness", 0);

	if (mx >= 8 && mx <= 4095) bright_max = (int)mx;
}

/* ------------------------------------------------------------------ children */
static void run_async(const char *cmd)
{
	pid_t p = fork();

	if (p == 0) {
		setsid();
		int nul = open("/dev/null", O_RDWR);

		if (nul >= 0) { dup2(nul, 0); dup2(nul, 1); dup2(nul, 2); }
		execl("/bin/sh", "sh", "-c", cmd, (char *)NULL);
		_exit(127);
	}
}

/* ------------------------------------------------------------------ DRM */
static void drm_setup(int allocate)
{
	fd = open("/dev/dri/card0", O_RDWR | O_CLOEXEC);
	if (fd < 0) die("open DRM");
	struct drm_mode_card_res r = {0};

	xio(DRM_IOCTL_MODE_GETRESOURCES, &r, "resources count");
	if (r.count_crtcs > 16 || r.count_connectors > 16 || r.count_encoders > 16 || r.count_fbs > 64 || !r.count_crtcs || !r.count_connectors) {
		errno = ENODEV;
		die("resources bounds");
	}
	uint32_t crtcs[16] = {0}, cons[16] = {0}, enc[16] = {0}, fbs[64] = {0};

	r.crtc_id_ptr = ptr(crtcs); r.connector_id_ptr = ptr(cons); r.encoder_id_ptr = ptr(enc); r.fb_id_ptr = ptr(fbs);
	xio(DRM_IOCTL_MODE_GETRESOURCES, &r, "resources");
	struct drm_mode_get_connector c = {0};

	for (unsigned i = 0; i < r.count_connectors; i++) {
		memset(&c, 0, sizeof c);
		c.connector_id = cons[i];
		xio(DRM_IOCTL_MODE_GETCONNECTOR, &c, "connector count");
		if (c.connection != 1 || !c.count_modes) continue;
		if (c.count_modes > 64 || c.count_encoders > 16 || c.count_props > 128) { errno = EOVERFLOW; die("connector bounds"); }
		struct drm_mode_modeinfo modes[64];
		uint32_t encoders[16], props[128];
		uint64_t values[128];

		c.modes_ptr = ptr(modes); c.encoders_ptr = ptr(encoders); c.props_ptr = ptr(props); c.prop_values_ptr = ptr(values);
		xio(DRM_IOCTL_MODE_GETCONNECTOR, &c, "connector");
		for (unsigned j = 0; j < c.count_modes; j++)
			if (modes[j].hdisplay == 240 && modes[j].vdisplay == 240) { mode = modes[j]; connector = c.connector_id; break; }
		if (!connector) continue;
		for (unsigned j = 0; j < c.count_props; j++) {
			struct drm_mode_get_property gp = { .prop_id = props[j] };

			if (!ioctl(fd, DRM_IOCTL_MODE_GETPROPERTY, &gp) && !strcmp(gp.name, "DPMS")) { dpms_prop = props[j]; break; }
		}
		uint32_t encoder = c.encoder_id ? c.encoder_id : encoders[0];
		struct drm_mode_get_encoder e = { .encoder_id = encoder };

		xio(DRM_IOCTL_MODE_GETENCODER, &e, "encoder");
		for (unsigned j = 0; j < r.count_crtcs; j++)
			if (e.possible_crtcs & (1u << j)) { crtc = crtcs[j]; break; }
		break;
	}
	if (!connector || !crtc) { errno = ENODEV; die("240x240 connected mode / CRTC"); }
	width = mode.hdisplay;
	height = mode.vdisplay;
	printf("KMS connector=%u crtc=%u mode=%s %ux%u clock=%u\n", connector, crtc, mode.name, width, height, mode.clock);
	fflush(stdout);
	if (!allocate) return;
	panel_full_frames();
	if (ioctl(fd, DRM_IOCTL_SET_MASTER, 0) < 0) die("DRM master");
	for (int i = 0; i < 2; i++) {
		struct drm_mode_create_dumb d = { .width = width, .height = height, .bpp = 32 };

		xio(DRM_IOCTL_MODE_CREATE_DUMB, &d, "create dumb");
		if (d.pitch < width * 4 || d.size < (uint64_t)d.pitch * height || d.size > 4194304) { errno = EOVERFLOW; die("dumb bounds"); }
		pitch[i] = d.pitch;
		handles[i] = d.handle;
		struct drm_mode_map_dumb m = { .handle = d.handle };

		xio(DRM_IOCTL_MODE_MAP_DUMB, &m, "map offset");
		pixels[i] = mmap(NULL, (size_t)d.size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)m.offset);
		if (pixels[i] == MAP_FAILED) die("mmap dumb");
		memset(pixels[i], 0, (size_t)d.size);
		struct drm_mode_fb_cmd2 a = { .width = width, .height = height, .pixel_format = DRM_FORMAT_XRGB8888 };

		a.handles[0] = d.handle;
		a.pitches[0] = d.pitch;
		xio(DRM_IOCTL_MODE_ADDFB2, &a, "add framebuffer");
		fb[i] = a.fb_id;
	}
}

/* drain pending page-flip completion events */
static void drm_events(int wait_ms)
{
	struct pollfd p = { .fd = fd, .events = POLLIN };

	while (flip_pending && poll(&p, 1, wait_ms) > 0) {
		char buf[256];
		ssize_t n = read(fd, buf, sizeof buf), off = 0;

		if (n <= 0) break;
		while (off + (ssize_t)sizeof(struct drm_event) <= n) {
			struct drm_event *e = (struct drm_event *)(buf + off);

			if (e->type == DRM_EVENT_FLIP_COMPLETE) flip_pending = 0;
			if (e->length < sizeof *e) break;
			off += e->length;
		}
		wait_ms = 0;
	}
}

/* The panel takes RGB565 on the wire (the DRM driver truncates XRGB8888 to it).  Truncation turns the soft background gradients into visible
 * bands and tints.  The frame is therefore quantised here with an 8x8 ordered dither and expanded back to 8 bits per channel, which the
 * driver's truncation then reproduces exactly. */
static const uint8_t bayer8[64] = {
	 0, 32,  8, 40,  2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26, 12, 44,  4, 36, 14, 46,  6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
	 3, 35, 11, 43,  1, 33,  9, 41, 51, 19, 59, 27, 49, 17, 57, 25, 15, 47,  7, 39, 13, 45,  5, 37, 63, 31, 55, 23, 61, 29, 53, 21 };

static void dither_row(uint32_t *dst, const uint32_t *src, unsigned w, unsigned y)
{
	const uint8_t *t = bayer8 + (y & 7) * 8;

	for (unsigned x = 0; x < w; x++) {
		uint32_t p = src[x];
		unsigned th = t[x & 7];
		unsigned r = ((p >> 16) & 255) + (th >> 3), g = ((p >> 8) & 255) + (th >> 4), b = (p & 255) + (th >> 3);
		unsigned r5 = r > 255 ? 31 : r >> 3, g6 = g > 255 ? 63 : g >> 2, b5 = b > 255 ? 31 : b >> 3;

		dst[x] = ((r5 << 3 | r5 >> 2) << 16) | ((g6 << 2 | g6 >> 4) << 8) | (b5 << 3 | b5 >> 2);
	}
}

/* what each of the two dumb buffers holds, as composed (before dithering): only the rows that differ from what the buffer being written holds are
 * dithered again.  Every flip is a full-frame write to the panel (panel_full_frames()), and the glass is rewritten at least once a second even
 * when nothing changed (FULL_REFRESH_MS): a frame equal to what is on the glass is skipped only inside that second. */
static uint32_t *hist[2];
static unsigned long stat_frames, stat_same, stat_rows, stat_flips;
static unsigned long long stat_compose_us, stat_present_us;	/* time spent composing frames and handing them to the display */
static long last_flip_ms;
#define FULL_REFRESH_MS 1000

/* The kernel driver can send only the changed windows of a frame (module parameter "partial" of u30pro-nv3002c).  On the glass those windows
 * did not show up reliably (the clock stayed at the old minute while the counters said the window had been sent), so the UI asks for whole frames:
 * a full 240x240 RGB565 frame is 19 ms on the 48 MHz link, and the 1 fps floor costs 2 % of it. */
static void panel_full_frames(void)
{
	if (!sysfs_write("/sys/module/u30pro_nv3002c/parameters/partial", "0\n")) fprintf(stderr, "panel: partial-refresh switch not found; the driver decides\n");
}

/* the previous flip is still being written to the panel (the kernel starts the write at the panel's TE pulse and the transfer itself takes
 * tens of ms): nothing to compose until its completion event arrives, which the main loop polls the DRM fd for */
static int flip_in_flight(void)
{
	if (first_present || !flip_pending) return 0;
	drm_events(0);
	if (!flip_pending) return 0;
	if (now_ms() - flip_at >= 250) { flip_pending = 0; return 0; }
	return 1;
}

/* returns 1 when the frame was handed to the display (or is already on it), 0 when the previous flip is still in progress */
static int present(const Cv *c)
{
	size_t row = (size_t)c->w * 4;
	long now = now_ms();

	if (flip_in_flight()) return 0;
	if (!hist[0]) {
		hist[0] = calloc((size_t)c->w * c->h, 4);
		hist[1] = calloc((size_t)c->w * c->h, 4);
		if (!hist[0] || !hist[1]) die("frame history");
	}
	stat_frames++;
	/* the main loop wakes exactly when the next frame is due, so the floor gets a little tolerance against that jitter */
	if (!first_present && now - last_flip_ms < FULL_REFRESH_MS - 50 && !memcmp(hist[cur_buf], c->p, row * c->h)) { stat_same++; return 1; }
	int b = first_present ? 0 : cur_buf ^ 1;

	for (unsigned y = 0; y < height; y++) {
		const uint32_t *src = (const uint32_t *)(c->p + (size_t)y * c->w);
		uint32_t *mine = hist[b] + (size_t)y * c->w;

		if (!first_present && !memcmp(mine, src, row)) continue;
		dither_row((uint32_t *)((uint8_t *)pixels[b] + (size_t)y * pitch[b]), src, width, y);
		memcpy(mine, src, row);
		stat_rows++;
	}
	if (first_present) {
		struct drm_mode_crtc cr = { .set_connectors_ptr = ptr(&connector), .count_connectors = 1, .crtc_id = crtc, .fb_id = fb[b], .mode_valid = 1 };

		cr.mode = mode;
		xio(DRM_IOCTL_MODE_SETCRTC, &cr, "set CRTC");
		first_present = 0;
	} else {
		struct drm_mode_crtc_page_flip flip = { .crtc_id = crtc, .fb_id = fb[b], .flags = use_events ? DRM_MODE_PAGE_FLIP_EVENT : 0 };

		if (ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip) < 0) {
			if (errno == EBUSY) { flip_pending = 1; flip_at = now; return 0; }	/* the kernel is still writing the previous frame */
			if (errno == EINVAL && use_events) {
				use_events = 0;
				flip.flags = 0;
				if (ioctl(fd, DRM_IOCTL_MODE_PAGE_FLIP, &flip) < 0) return 0;
			} else return 0;
		} else if (use_events) {
			flip_pending = 1;
			flip_at = now;
		}
	}
	cur_buf = b;
	last_flip_ms = now;
	stat_flips++;
	return 1;
}

/* Screen off.  A backlight level of 0 is a PWM duty of 0, which on this board leaves the backlight lit: the screen kept showing the
 * last frame while the UI had stopped drawing ("the clock does not move").  The light goes out only when the panel driver drops its
 * enable line, which it does when the display pipe is disabled, so sleeping switches the display off through the connector's DPMS
 * property and waking switches it back on (the driver then initialises the panel and sends the buffer the CRTC points at). */
static void display_power(int on)
{
	struct drm_mode_connector_set_property sp = { .value = on ? DRM_MODE_DPMS_ON : DRM_MODE_DPMS_OFF, .prop_id = dpms_prop, .connector_id = connector };

	if (!dpms_prop || first_present) return;
	if (flip_pending) drm_events(250);
	if (ioctl(fd, DRM_IOCTL_MODE_SETPROPERTY, &sp) < 0) { perror("panel: DPMS"); return; }
	flip_pending = 0;
}

/* while the display is off: put the frame into the buffer the CRTC points at, so that switching the display on shows it at once
 * instead of the frame from before the sleep */
static void present_off(const Cv *c)
{
	size_t row = (size_t)c->w * 4;

	if (first_present || !hist[0]) return;
	for (unsigned y = 0; y < height; y++) {
		const uint32_t *src = (const uint32_t *)(c->p + (size_t)y * c->w);
		uint32_t *mine = hist[cur_buf] + (size_t)y * c->w;

		if (!memcmp(mine, src, row)) continue;
		dither_row((uint32_t *)((uint8_t *)pixels[cur_buf] + (size_t)y * pitch[cur_buf]), src, width, y);
		memcpy(mine, src, row);
	}
}

/* ------------------------------------------------------------------ keys */
static void keys_open(void)
{
	/* the event number depends on the probe order of the input drivers (touch and keys swap places between boots): find the keys by name */
	for (int i = 0; i < 32 && keyfd < 0; i++) {
		char path[64], name[256] = {0};

		snprintf(path, sizeof path, "/dev/input/event%d", i);
		int k = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);

		if (k < 0) continue;
		if (ioctl(k, EVIOCGNAME(sizeof name), name) >= 0 && !strcmp(name, "gpio-keys"))
			keyfd = k;
		else
			close(k);
	}
	if (keyfd >= 0) printf("KEYS device=gpio-keys power=116 down=114 up=115 home=102\n");
	else fprintf(stderr, "gpio-keys input unavailable; display continues\n");
}

static void keys_read(void)
{
	static long pressed_at[KEY_MAX + 1];
	static int power_hold_reported;
	struct input_event e;

	while (keyfd >= 0 && read(keyfd, &e, sizeof e) == (ssize_t)sizeof e) {
		if (e.type == EV_SYN && e.code == SYN_DROPPED) { memset(pressed_at, 0, sizeof pressed_at); power_hold_reported = 0; continue; }
		if (e.type != EV_KEY || e.code > KEY_MAX) continue;
		if (e.value == 1) { pressed_at[e.code] = now_ms(); if (e.code == KEY_POWER) power_hold_reported = 0; continue; }
		if (e.value != 0 || !pressed_at[e.code]) continue;
		long dur = now_ms() - pressed_at[e.code];

		pressed_at[e.code] = 0;
		if (e.code == KEY_POWER && power_hold_reported) { power_hold_reported = 0; continue; }
		if (e.code == KEY_POWER || e.code == KEY_HOME || e.code == KEY_VOLUMEUP || e.code == KEY_VOLUMEDOWN) {
			printf("KEY code=%u value=release duration_ms=%ld\n", e.code, dur);
			fflush(stdout);
			ui_key(e.code, dur);
		}
	}
	if (pressed_at[KEY_POWER] && !power_hold_reported && now_ms() - pressed_at[KEY_POWER] >= POWER_HOLD_MS) {
		power_hold_reported = 1;
		ui_key(KEY_POWER, POWER_HOLD_MS);
	}
}

/* ------------------------------------------------------------------ touch */
static void touch_open(void)
{
	if (touchfd >= 0) return;
	for (int i = 0; i < 32; i++) {
		char path[64], name[256] = {0};

		snprintf(path, sizeof path, "/dev/input/event%d", i);
		int t = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);

		if (t < 0) continue;
		struct input_absinfo x, y;

		if (ioctl(t, EVIOCGNAME(sizeof name), name) >= 0 && !strcmp(name, "tlsc6x_touch") &&
		    ioctl(t, EVIOCGABS(ABS_MT_POSITION_X), &x) >= 0 && ioctl(t, EVIOCGABS(ABS_MT_POSITION_Y), &y) >= 0 &&
		    x.minimum == 0 && x.maximum == 239 && y.minimum == 0 && y.maximum == 239) {
			int clk = CLOCK_MONOTONIC;

			touch_monotonic = ioctl(t, EVIOCSCLOCKID, &clk) == 0;
			touchfd = t;
			printf("TOUCH device=%s name=%s range=0..239 rich-ui=1\n", path, name);
			fflush(stdout);
			return;
		}
		close(t);
	}
}

/* The panel is mounted upside down and the display driver compensates; the touch controller needs per-axis correction. */
static void touch_read(void)
{
	static int slot, down, was_down, x, y, cancelled, dropped, lx = -1, ly = -1;
	struct input_event e;

	while (touchfd >= 0 && read(touchfd, &e, sizeof e) == (ssize_t)sizeof e) {
		if (e.type == EV_SYN && e.code == SYN_DROPPED) { down = 0; dropped = 1; cancelled = 1; continue; }
		if (dropped) { if (e.type == EV_SYN && e.code == SYN_REPORT) dropped = 0; continue; }
		if (e.type == EV_ABS) {
			if (e.code == ABS_MT_SLOT) slot = e.value;
			if (e.code == ABS_MT_TRACKING_ID) { if (slot == 0) down = e.value >= 0; else if (e.value >= 0) cancelled = 1; }
			if (slot == 0 && e.code == ABS_MT_POSITION_X) x = e.value;
			if (slot == 0 && e.code == ABS_MT_POSITION_Y) y = e.value;
		}
		if (e.type != EV_SYN || e.code != SYN_REPORT) continue;
		long ms = touch_monotonic ? e.time.tv_sec * 1000 + e.time.tv_usec / 1000 : now_ms();
		int ux = touch_flip_x ? 239 - x : x, uy = touch_flip_y ? 239 - y : y;

		if (ux < 0) ux = 0;
		if (uy < 0) uy = 0;
		touch_raw_x = x; touch_raw_y = y;
		if (down && !was_down) { ux = touch_flip_x ? 239 - x : x; uy = touch_flip_y ? 239 - y : y; ui_touch(T_DOWN, ux, uy, ms); lx = ux; ly = uy; touch_count++; }
		else if (down && (ux != lx || uy != ly)) { ui_touch(T_MOVE, ux, uy, ms); lx = ux; ly = uy; }
		else if (!down && was_down) ui_touch(T_UP, lx, ly, ms);
		was_down = down;
		if (!down) cancelled = 0;
	}
	(void)cancelled;
}
#endif
