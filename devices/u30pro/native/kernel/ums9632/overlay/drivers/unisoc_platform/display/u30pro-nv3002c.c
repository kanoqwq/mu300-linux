// SPDX-License-Identifier: GPL-2.0
/*
 * DRM driver for the NV3002C 240x240 SPI panel of the ZTE U30 Pro (UMS9632).
 *
 * The stock kernel drives this panel through the Unisoc display stack ("sw-dpu" + sprd_spi_panel).  Here the
 * panel is an ordinary SPI device (spi3, chip select 0) with a command/data GPIO, handled by the in-tree
 * mipi-dbi helpers.  Everything board specific is read from the stock device tree, which is not modified:
 *
 *   - the SPI child "sprd,generic-spi-panel": cd / reset / avdd / bl_en GPIOs
 *   - /lcds/<spi_panel_name from the kernel command line>: init-data, reset timing, sleep-out/in, size
 *   - the "sprd,qogirn5-backlight" node, registered by u30pro-backlight.c
 *
 * init-data is a list of { type, wait_ms, len_hi, len_lo, payload[len] }; payload[0] is the DCS command.
 */
#include <linux/backlight.h>
#include <linux/completion.h>
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/init.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/uaccess.h>
#include <linux/of.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/spi/spi.h>

#include <drm/drm_atomic_helper.h>
#include <drm/drm_drv.h>
#include <drm/drm_damage_helper.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_gem_atomic_helper.h>
#include <drm/drm_gem_dma_helper.h>
#include <drm/drm_managed.h>
#include <drm/drm_mipi_dbi.h>
#include <drm/drm_modeset_helper.h>
#include <video/mipi_display.h>

#define MADCTL_MY	0x80
#define MADCTL_MX	0x40
#define MADCTL_MV	0x20

static int u30_rotation;
module_param_named(rotation, u30_rotation, int, 0444);
MODULE_PARM_DESC(rotation, "panel rotation in degrees (0, 90, 180, 270)");


static uint u30_spi_hz;
module_param_named(spi_hz, u30_spi_hz, uint, 0444);
MODULE_PARM_DESC(spi_hz, "override the SPI clock (0 = stock panel table value)");

/*
 * Off by default since 2026-10-21: with the changed-window writes the clock of the UI stayed at the old minute on the glass although the
 * counters showed the window had been sent (the full-frame path of the same commands always shows).  The panel-side reason is not isolated
 * yet (window alignment or a RAM-write quirk of this controller); the UI also clears the parameter at start.  Kept for experiments.
 */
static bool u30_partial;
module_param_named(partial, u30_partial, bool, 0644);
MODULE_PARM_DESC(partial, "send only the changed regions of the frame to the panel (dynamic region refresh; default off)");

/*
 * Vertical synchronisation.  The panel refreshes its picture from its own RAM (first RAM row first; RAM row 0 is the bottom of the
 * picture as the user sees it, u30_command mirrors the frame) and pulses its TE pin once per refresh (a ~55 us high pulse every 18.9 ms,
 * 53 Hz, measured with tools/u30pro_tetest.c; the stock init-data turns it on, 0x35 0x00).  The stock software DPU started every whole-frame
 * SPI write right after that pulse; the stock device tree names the pin in the sw-dispc node (spite-gpio, pin 12 of the AP GPIO controller).
 *
 * The RAM write is slower than the refresh (a whole frame measured 37 ms on the 48 MHz link with the controller's default gap between
 * words, about 26 ms with the gap at its minimum, see spi_word_delay; the refresh passes the 240 rows in well under 18.9 ms), so the write
 * must trail the refresh: it starts when the refresh has just passed RAM row 0 (right after the TE pulse plus te_delay_us) and stays behind
 * it, and the next refresh, 18.9 ms later, only reaches rows the write has already finished as long as the write takes less than
 * the period plus the scan of the rows (about 33 ms).  Then no refresh ever shows a half-written frame.  A write that started earlier
 * (inside the blanking) would lead the refresh and be overtaken part-way down, a fixed tear line; a write at a random time is overtaken
 * once per refresh, the tearing seen during page swipes without TE sync.  Costs up to one period of latency per frame and limits the rate
 * to one frame per two periods (about 26 fps).  te=0 switches the wait off at run time; te_edge, te_delay_us and spi_word_delay are
 * also run-time tunable, so the start phase and the write length can be adjusted on the device while watching the glass.
 */
static int u30_te_mode = 1;
module_param_named(te, u30_te_mode, int, 0644);
MODULE_PARM_DESC(te, "start every RAM write of a frame right after the panel's TE pulse: 0 off, 1 on (default)");

static int u30_te_edge;
module_param_named(te_edge, u30_te_edge, int, 0644);
MODULE_PARM_DESC(te_edge, "TE edge that starts a RAM write: 0 rising (default), 1 falling (the pulse is about 55 us wide)");

static unsigned int u30_te_delay_us;
module_param_named(te_delay_us, u30_te_delay_us, uint, 0644);
MODULE_PARM_DESC(te_delay_us, "extra delay after the TE edge before the RAM write starts, 0..20000 (default 0)");

static unsigned int u30_word_delay = 14;
module_param_named(spi_word_delay, u30_word_delay, uint, 0644);
MODULE_PARM_DESC(spi_word_delay, "idle SCK cycles the SPI controller leaves between words: 14 = minimum (default), 0 = the controller's default of about 44 source-clock cycles");

#define U30_TE_TIMEOUT_MS	50	/* no TE for this long (panel asleep, pin not wired): write anyway */
#define U30_TE_DEAD_MS		100	/* no TE edge for this long: the line is silent, writes do not wait until it pulses again */
#define U30_TE_DELAY_MAX_US	20000

/* a frame is cut into at most this many bands of changed rows; clean gaps of at least U30_GAP rows separate two bands */
#define U30_BANDS	6
#define U30_GAP		6
#define U30_W		240
#define U30_H		240

struct u30_stats {
	u64 commits;		/* atomic updates seen */
	u64 skipped;		/* updates without any changed pixel (nothing sent) */
	u64 windows;		/* windows (RAM writes) sent */
	u64 pixels;		/* pixels sent */
	u64 full;		/* full-frame writes */
	u64 te_pulses;		/* TE edges seen */
	u64 te_waits;		/* RAM writes that waited for TE */
	u64 te_timeouts;	/* ... and gave up waiting */
	u64 te_dead;		/* RAM writes that did not wait because the line had been silent */
	u64 write_ns;		/* duration of the last whole-frame RAM write (command byte to the end of the transfer) */
	u64 write_max_ns;
};

struct u30_panel {
	struct mipi_dbi_dev dbidev;	/* must stay first for drm_to_mipi_dbi_dev() users */
	struct gpio_desc *reset;
	struct gpio_desc *avdd;
	struct gpio_desc *bl_en;
	struct gpio_desc *te;
	int te_irq;
	int te_type;			/* IRQ_TYPE_EDGE_* in effect (te_edge is applied when it changes) */
	bool te_armed;			/* set by u30_update: the next frame write of this update waits for TE (once per update, not per window) */
	struct completion te_done;
	u64 te_last_ns, te_period_ns;	/* time of the last TE edge and the interval to the one before (the panel's refresh period) */
	const u8 *init;
	int init_len;
	u32 *reset_seq;
	int reset_seq_len;
	u32 hz;		/* SPI clock for every transfer (stock: spi_freq, 48 MHz) */
	u16 *frame;	/* the window rotated by 180 degrees */
	u32 *prev;	/* XRGB8888 copy of what the panel RAM holds, to find the changed region of the next frame */
	bool prev_valid;
	struct mutex lock;	/* one SPI command/data sequence at a time (window writes of updates, debugfs commands) */
	struct u30_stats st;
};

static struct u30_panel *to_u30(struct mipi_dbi_dev *dbidev)
{
	return container_of(dbidev, struct u30_panel, dbidev);
}

/*
 * Transfers exactly as the stock driver (sprd_spi_panel + spi_sprd) performs them, verified by tracing the stock
 * kernel: SPI mode 3, the panel's spi_freq for every transfer, the command byte and the parameters as two 8-bit
 * transfers with D/C low/high, and a whole frame as one transfer.  The stock driver uses 32-bit words for the
 * frame; on this kernel that path only delivered half the data, while 16-bit words (verified on the panel) give
 * the same big-endian byte stream.
 */
static int u30_spi(struct mipi_dbi *dbi, bool data, const void *buf, size_t len, u8 bpw)
{
	struct u30_panel *pnl = container_of(dbi, struct u30_panel, dbidev.dbi);
	struct spi_transfer t = { .tx_buf = buf, .len = len, .speed_hz = pnl->hz, .bits_per_word = bpw,
				  /* the gap between words (spi-sprd honours an explicit request below its default); the panel needs none */
				  .word_delay = { .value = (u16)min(u30_word_delay, 130u), .unit = SPI_DELAY_UNIT_SCK } };

	gpiod_set_value_cansleep(dbi->dc, data);
	return spi_sync_transfer(dbi->spi, &t, 1);
}

/*
 * Transfers as the stock driver (sprd_spi_panel + spi_sprd) performs them, verified by tracing the stock kernel:
 * SPI mode 3, the panel's spi_freq for every transfer, the command byte and its parameters as two 8-bit
 * transfers with D/C low/high, and the frame as one transfer (16-bit words here = big-endian RGB565 on the wire).
 *
 * The image is upside down on this panel.  This is corrected here in software: the window is mirrored
 * (x -> 239 - x, y -> 239 - y) and the pixels of the window are sent in reverse order.  (Using MADCTL MX/MY
 * instead moved the image inside the controller's 240x320 RAM.)
 */
static irqreturn_t u30_te_irq(int irq, void *data)
{
	struct u30_panel *pnl = data;
	u64 now = ktime_get_ns(), last = pnl->te_last_ns;

	if (last)
		WRITE_ONCE(pnl->te_period_ns, now - last);
	WRITE_ONCE(pnl->te_last_ns, now);
	pnl->st.te_pulses++;
	complete(&pnl->te_done);
	return IRQ_HANDLED;
}

/* the frame write starts right after the next TE edge plus te_delay_us (see u30_te_mode); a silent line (no edge for U30_TE_DEAD_MS) or a
 * missing edge within the timeout does not hold the write */
static void u30_wait_te(struct u30_panel *pnl)
{
	unsigned int delay = min(u30_te_delay_us, (unsigned int)U30_TE_DELAY_MAX_US);
	int type = u30_te_edge ? IRQ_TYPE_EDGE_FALLING : IRQ_TYPE_EDGE_RISING;
	u64 last;

	if (!u30_te_mode || pnl->te_irq <= 0)
		return;
	if (type != pnl->te_type && !irq_set_irq_type(pnl->te_irq, type))
		pnl->te_type = type;
	last = READ_ONCE(pnl->te_last_ns);
	if (last && ktime_get_ns() - last > U30_TE_DEAD_MS * NSEC_PER_MSEC) {
		pnl->st.te_dead++;
		return;
	}
	pnl->st.te_waits++;
	reinit_completion(&pnl->te_done);
	if (!wait_for_completion_timeout(&pnl->te_done, msecs_to_jiffies(U30_TE_TIMEOUT_MS))) {
		pnl->st.te_timeouts++;
		return;
	}
	if (delay)
		usleep_range(delay, delay + 50);
}

static int u30_command(struct mipi_dbi *dbi, u8 *cmd, u8 *par, size_t num)
{
	struct u30_panel *pnl = container_of(dbi, struct u30_panel, dbidev.dbi);
	size_t i, n;
	u64 t0;
	int ret;

	if ((*cmd == MIPI_DCS_SET_COLUMN_ADDRESS || *cmd == MIPI_DCS_SET_PAGE_ADDRESS) && num == 4) {
		unsigned int s0 = (par[0] << 8) | par[1], e0 = (par[2] << 8) | par[3];
		unsigned int s1 = 239 - min(e0, 239u), e1 = 239 - min(s0, 239u);

		par[0] = s1 >> 8; par[1] = s1 & 0xff; par[2] = e1 >> 8; par[3] = e1 & 0xff;
	}

	if (*cmd != MIPI_DCS_WRITE_MEMORY_START || !num || (num & 1) || num > 240 * 240 * 2) {
		ret = u30_spi(dbi, false, cmd, 1, 8);
		if (ret || !num)
			return ret;
		return u30_spi(dbi, true, par, num, 8);
	}

	/* frame data: rotate it first, then start in the right phase of the panel refresh (u30_wait_te), then send command byte and pixels */
	n = num / 2;
	for (i = 0; i < n; i++)
		pnl->frame[n - 1 - i] = ((const u16 *)par)[i];
	if (pnl->te_armed) {
		pnl->te_armed = false;
		u30_wait_te(pnl);
	}
	t0 = ktime_get_ns();
	ret = u30_spi(dbi, false, cmd, 1, 8);
	if (!ret)
		ret = u30_spi(dbi, true, pnl->frame, num, 16);
	pnl->st.write_ns = ktime_get_ns() - t0;
	if (pnl->st.write_ns > pnl->st.write_max_ns)
		pnl->st.write_max_ns = pnl->st.write_ns;
	return ret;
}

static void u30_send_blob(struct mipi_dbi *dbi, const u8 *p, int len)
{
	while (len >= 4) {
		int plen = (p[2] << 8) | p[3];
		u8 wait = p[1];

		if (plen < 1 || 4 + plen > len)
			break;
		mipi_dbi_command_buf(dbi, p[4], (u8 *)&p[5], plen - 1);
		if (wait)
			msleep(wait);
		p += 4 + plen;
		len -= 4 + plen;
	}
}

static void u30_hw_reset(struct u30_panel *pnl)
{
	int i;

	if (!pnl->reset)
		return;
	/* pairs of { level, ms }; the stock property uses physical levels */
	for (i = 0; i + 1 < pnl->reset_seq_len; i += 2) {
		gpiod_set_value_cansleep(pnl->reset, pnl->reset_seq[i] ? 1 : 0);
		msleep(pnl->reset_seq[i + 1]);
	}
}

/*
 * Dynamic region refresh.  The panel keeps its picture in its own RAM, so only what changed has to travel over the SPI link (24..48 MHz, a full
 * 240x240 RGB565 frame is 0.92 Mbit: 19..38 ms).  The new frame is compared with a copy of what the panel holds (prev); the changed rows are cut into
 * bands (a clean gap of U30_GAP rows starts a new band) with the column range of each band, and every band is one window write.  A frame
 * without any change sends nothing.  The window mirroring of u30_command() applies to every window, so bands need no special handling.
 */
static int u30_find_bands(struct u30_panel *pnl, const u32 *src, unsigned int pitch, const struct drm_rect *clip, struct drm_rect *band)
{
	struct drm_rect cur = { };
	int n = 0, clean = 0, y, x;
	bool open = false;

	for (y = clip->y1; y < clip->y2; y++) {
		const u32 *a = src + (size_t)y * pitch, *b = pnl->prev + (size_t)y * U30_W;
		int x1 = clip->x2, x2 = -1;

		for (x = clip->x1; x < clip->x2; x++) {
			if ((a[x] ^ b[x]) & 0x00ffffff) {
				x1 = x;
				break;
			}
		}
		if (x1 < clip->x2) {
			for (x = clip->x2 - 1; x > x1; x--)
				if ((a[x] ^ b[x]) & 0x00ffffff)
					break;
			x2 = x + 1;
		}
		if (x2 > 0) {
			if (!open) {
				cur = (struct drm_rect){ x1, y, x2, y + 1 };
				open = true;
			} else {
				cur.x1 = min(cur.x1, x1);
				cur.x2 = max(cur.x2, x2);
				cur.y2 = y + 1;
			}
			clean = 0;
		} else if (open && ++clean >= U30_GAP && n < U30_BANDS - 1) {
			band[n++] = cur;
			open = false;
			clean = 0;
		}
	}
	if (open)
		band[n++] = cur;
	return n;
}

static void u30_send_window(struct u30_panel *pnl, struct iosys_map *src, struct drm_framebuffer *fb, const struct drm_rect *r,
			    struct drm_format_conv_state *fmtcnv)
{
	struct mipi_dbi_dev *dbidev = &pnl->dbidev;
	struct mipi_dbi *dbi = &dbidev->dbi;
	unsigned int w = r->x2 - r->x1, h = r->y2 - r->y1;
	struct drm_rect clip = *r;
	int ret;

	ret = mipi_dbi_buf_copy(dbidev->tx_buf, src, fb, &clip, dbi->swap_bytes, fmtcnv);
	if (ret)
		return;
	mipi_dbi_command(dbi, MIPI_DCS_SET_COLUMN_ADDRESS, (r->x1 >> 8) & 0xff, r->x1 & 0xff, ((r->x2 - 1) >> 8) & 0xff, (r->x2 - 1) & 0xff);
	mipi_dbi_command(dbi, MIPI_DCS_SET_PAGE_ADDRESS, (r->y1 >> 8) & 0xff, r->y1 & 0xff, ((r->y2 - 1) >> 8) & 0xff, (r->y2 - 1) & 0xff);
	ret = mipi_dbi_command_buf(dbi, MIPI_DCS_WRITE_MEMORY_START, (u8 *)dbidev->tx_buf, w * h * 2);
	if (ret)
		drm_err_once(fb->dev, "Failed to update display %d\n", ret);
	pnl->st.windows++;
	pnl->st.pixels += (u64)w * h;
}

static void u30_remember(struct u30_panel *pnl, const u32 *src, unsigned int pitch, const struct drm_rect *r)
{
	int y;

	for (y = r->y1; y < r->y2; y++)
		memcpy(pnl->prev + (size_t)y * U30_W + r->x1, src + (size_t)y * pitch + r->x1, (size_t)(r->x2 - r->x1) * 4);
}

static void u30_update(struct drm_simple_display_pipe *pipe, struct drm_plane_state *old_state)
{
	struct drm_plane_state *state = pipe->plane.state;
	struct drm_shadow_plane_state *sps = to_drm_shadow_plane_state(state);
	struct mipi_dbi_dev *dbidev = drm_to_mipi_dbi_dev(pipe->crtc.dev);
	struct u30_panel *pnl = to_u30(dbidev);
	struct drm_framebuffer *fb = state->fb;
	struct drm_rect clip, band[U30_BANDS];
	const u32 *src;
	unsigned int pitch;
	int idx, i, n;

	pnl->te_armed = true;	/* the first frame write of this update waits for the panel's TE pulse */
	if (!u30_partial || !fb || fb->format->format != DRM_FORMAT_XRGB8888 || fb->width != U30_W || fb->height != U30_H || !pnl->prev) {
		mipi_dbi_pipe_update(pipe, old_state);
		pnl->prev_valid = false;
		pnl->te_armed = false;
		return;
	}
	if (!pipe->crtc.state->active)
		return;
	if (!drm_dev_enter(fb->dev, &idx))
		return;

	pnl->st.commits++;
	if (!drm_atomic_helper_damage_merged(old_state, state, &clip))
		goto out;
	if (drm_gem_fb_begin_cpu_access(fb, DMA_FROM_DEVICE))
		goto out;
	src = sps->data[0].vaddr;
	pitch = fb->pitches[0] / 4;
	if (!pnl->prev_valid) {
		/* nothing known about the panel RAM: send everything */
		band[0] = (struct drm_rect){ 0, 0, U30_W, U30_H };
		n = 1;
		pnl->st.full++;
	} else {
		n = u30_find_bands(pnl, src, pitch, &clip, band);
	}
	drm_gem_fb_end_cpu_access(fb, DMA_FROM_DEVICE);
	if (!n) {
		pnl->st.skipped++;
		goto out;
	}
	mutex_lock(&pnl->lock);
	for (i = 0; i < n; i++) {
		u30_send_window(pnl, &sps->data[0], fb, &band[i], &sps->fmtcnv_state);
		u30_remember(pnl, src, pitch, &band[i]);
	}
	mutex_unlock(&pnl->lock);
	pnl->prev_valid = true;
out:
	pnl->te_armed = false;
	drm_dev_exit(idx);
}

static int u30_stats_show(struct seq_file *m, void *unused)
{
	struct spi_device *spi = to_spi_device(m->private);
	struct drm_device *drm = spi_get_drvdata(spi);
	struct u30_panel *pnl = to_u30(drm_to_mipi_dbi_dev(drm));

	seq_printf(m, "partial %d\n" "commits %llu\n" "skipped %llu\n" "windows %llu\n" "pixels %llu\n" "full %llu\n",
		   u30_partial, pnl->st.commits, pnl->st.skipped, pnl->st.windows, pnl->st.pixels, pnl->st.full);
	seq_printf(m, "te %d\n" "te_irq %d\n" "te_edge %s\n" "te_delay_us %u\n" "te_pulses %llu\n" "te_period_us %llu\n" "te_waits %llu\n"
		   "te_timeouts %llu\n" "te_dead %llu\n" "spi_word_delay %u\n" "write_us %llu\n" "write_max_us %llu\n",
		   u30_te_mode, pnl->te_irq, pnl->te_type == IRQ_TYPE_EDGE_FALLING ? "falling" : "rising", u30_te_delay_us, pnl->st.te_pulses,
		   div_u64(READ_ONCE(pnl->te_period_ns), 1000), pnl->st.te_waits, pnl->st.te_timeouts, pnl->st.te_dead, u30_word_delay,
		   div_u64(pnl->st.write_ns, 1000), div_u64(pnl->st.write_max_ns, 1000));
	return 0;
}

/* debugfs "u30pro_dcs": write "cmd [param ...]" in hex to send one command to the panel controller, e.g. "51 80" (for experiments with the panel's own
 * brightness registers) */
static ssize_t u30_dcs_write(struct file *f, const char __user *ubuf, size_t len, loff_t *off)
{
	struct u30_panel *pnl = file_inode(f)->i_private;
	char buf[96], *p, *tok;
	u8 v[16];
	int n = 0, ret;

	if (len >= sizeof(buf))
		return -EINVAL;
	if (copy_from_user(buf, ubuf, len))
		return -EFAULT;
	buf[len] = 0;
	p = buf;
	while ((tok = strsep(&p, " \t\n")) && n < (int)sizeof(v)) {
		if (!*tok)
			continue;
		if (kstrtou8(tok, 16, &v[n]))
			return -EINVAL;
		n++;
	}
	if (!n)
		return -EINVAL;
	mutex_lock(&pnl->lock);
	ret = mipi_dbi_command_buf(&pnl->dbidev.dbi, v[0], &v[1], n - 1);
	mutex_unlock(&pnl->lock);
	return ret ? ret : len;
}

static const struct file_operations u30_dcs_fops = {
	.owner = THIS_MODULE,
	.open = simple_open,
	.write = u30_dcs_write,
};

static void u30_debugfs_init(struct drm_minor *minor)
{
	/* called from inside drm_dev_register(), before probe stores the drvdata: take the panel from the DRM device itself */
	struct u30_panel *pnl = to_u30(drm_to_mipi_dbi_dev(minor->dev));

	mipi_dbi_debugfs_init(minor);
	debugfs_create_devm_seqfile(minor->dev->dev, "u30pro_stats", minor->debugfs_root, u30_stats_show);
	debugfs_create_file("u30pro_dcs", 0200, minor->debugfs_root, pnl, &u30_dcs_fops);
}

static void u30_enable(struct drm_simple_display_pipe *pipe,
		       struct drm_crtc_state *crtc_state,
		       struct drm_plane_state *plane_state)
{
	struct mipi_dbi_dev *dbidev = drm_to_mipi_dbi_dev(pipe->crtc.dev);
	struct u30_panel *pnl = to_u30(dbidev);
	struct mipi_dbi *dbi = &dbidev->dbi;
	u8 madctl;
	int idx, ret;

	if (!drm_dev_enter(pipe->crtc.dev, &idx))
		return;

	/* Probe left the firmware GPIO directions untouched. Take ownership only
	 * now, with a userspace frame ready; hide reset/initialisation behind BL. */
	ret = pnl->bl_en ? gpiod_direction_output(pnl->bl_en, 0) : 0;
	if (!ret && pnl->avdd)
		ret = gpiod_direction_output(pnl->avdd, 1);
	if (!ret && pnl->reset)
		ret = gpiod_direction_output(pnl->reset, 1);
	if (ret) {
		drm_err(pipe->crtc.dev, "panel GPIO takeover failed: %d\n", ret);
		drm_dev_exit(idx);
		return;
	}
	msleep(10);
	u30_hw_reset(pnl);
	u30_send_blob(dbi, pnl->init, pnl->init_len);

	madctl = 0x00;	/* stock value; orientation and colour order are handled in u30_command() */
	mipi_dbi_command(dbi, MIPI_DCS_SET_ADDRESS_MODE, madctl);

	mipi_dbi_enable_flush(dbidev, crtc_state, plane_state);
	/* the light comes on once the first frame is in the panel RAM, not over what a reset left there */
	if (pnl->bl_en)
		gpiod_set_value_cansleep(pnl->bl_en, 1);
	/* enable flushed the whole frame: it is what the panel RAM holds now */
	pnl->prev_valid = false;
	if (pnl->prev && plane_state->fb && plane_state->fb->format->format == DRM_FORMAT_XRGB8888 &&
	    plane_state->fb->width == U30_W && plane_state->fb->height == U30_H) {
		struct drm_shadow_plane_state *sps = to_drm_shadow_plane_state(plane_state);
		struct drm_rect all = { 0, 0, U30_W, U30_H };

		if (sps->data[0].vaddr) {
			u30_remember(pnl, sps->data[0].vaddr, plane_state->fb->pitches[0] / 4, &all);
			pnl->prev_valid = true;
		}
	}
	drm_dev_exit(idx);
}

static void u30_disable(struct drm_simple_display_pipe *pipe)
{
	struct mipi_dbi_dev *dbidev = drm_to_mipi_dbi_dev(pipe->crtc.dev);
	struct u30_panel *pnl = to_u30(dbidev);
	int idx;

	mipi_dbi_pipe_disable(pipe);
	pnl->prev_valid = false;
	/* a disabled panel must not keep its backlight enable asserted (the backlight of this board only switches on and off with it:
	 * neither the AP PWM nor the controller's 0x51 brightness nor pulses on this line dim it, docs/SCREEN_UI.md) */
	if (pnl->bl_en)
		gpiod_set_value_cansleep(pnl->bl_en, 0);
	/* the stock sleep-in sequence (display off, sleep in): with only the backlight off the liquid crystal keeps showing the last frame,
	 * visible against the light.  u30_enable resets and initialises the panel again. */
	if (drm_dev_enter(pipe->crtc.dev, &idx)) {
		mipi_dbi_command(&dbidev->dbi, MIPI_DCS_SET_DISPLAY_OFF);
		msleep(10);
		mipi_dbi_command(&dbidev->dbi, MIPI_DCS_ENTER_SLEEP_MODE);
		msleep(120);
		drm_dev_exit(idx);
	}
}

static struct drm_simple_display_pipe_funcs u30_pipe_funcs = {
	DRM_MIPI_DBI_SIMPLE_DISPLAY_PIPE_FUNCS(u30_enable),
};

static const struct drm_display_mode u30_mode = {
	DRM_SIMPLE_MODE(240, 240, 33, 33),
};

DEFINE_DRM_GEM_DMA_FOPS(u30_fops);

static const struct drm_driver u30_driver = {
	.driver_features	= DRIVER_GEM | DRIVER_MODESET | DRIVER_ATOMIC,
	.fops			= &u30_fops,
	DRM_GEM_DMA_DRIVER_OPS_VMAP,
	.debugfs_init		= u30_debugfs_init,
	.name			= "u30pro-nv3002c",
	.desc			= "ZTE U30 Pro NV3002C SPI panel",
	.date			= "20261018",
	.major			= 1,
	.minor			= 0,
};

/* the TE pin is a property of the stock sw-dispc node (the software DPU of the stock kernel, which this kernel does not have) */
static void u30_te_init(struct device *dev, struct u30_panel *pnl)
{
	struct device_node *np = of_find_node_by_path("/soc/dpuvsp/sw-dispc");
	struct gpio_desc *te;
	int irq, ret;

	init_completion(&pnl->te_done);
	if (!np) {
		dev_warn(dev, "no sw-dispc node: no TE sync\n");
		return;
	}
	te = devm_fwnode_gpiod_get_index(dev, of_fwnode_handle(np), "spite", 0, GPIOD_IN, "panel-te");
	of_node_put(np);
	if (IS_ERR(te)) {
		dev_warn(dev, "TE gpio unavailable (%ld): no TE sync\n", PTR_ERR(te));
		return;
	}
	irq = gpiod_to_irq(te);
	if (irq <= 0) {
		dev_warn(dev, "TE gpio has no interrupt (%d): no TE sync\n", irq);
		return;
	}
	ret = devm_request_irq(dev, irq, u30_te_irq, u30_te_edge ? IRQF_TRIGGER_FALLING : IRQF_TRIGGER_RISING, "u30pro-panel-te", pnl);
	if (ret) {
		dev_warn(dev, "TE irq %d request failed (%d): no TE sync\n", irq, ret);
		return;
	}
	pnl->te = te;
	pnl->te_irq = irq;
	pnl->te_type = u30_te_edge ? IRQ_TYPE_EDGE_FALLING : IRQ_TYPE_EDGE_RISING;
	dev_info(dev, "TE sync on irq %d (%s edge, +%u us)\n", irq, u30_te_edge ? "falling" : "rising", u30_te_delay_us);
}

static struct device_node *u30_find_lcd_node(void)
{
	char name[64] = "";
	char path[80];
	const char *p = strstr(saved_command_line, "spi_panel_name=");

	if (!p)
		return NULL;
	if (sscanf(p, "spi_panel_name=%63s", name) != 1)
		return NULL;
	snprintf(path, sizeof(path), "/lcds/%s", name);
	return of_find_node_by_path(path);
}


static int u30_probe(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	struct device_node *lcd, *blnode;
	struct backlight_device *bd;
	struct mipi_dbi_dev *dbidev;
	struct u30_panel *pnl;
	struct drm_device *drm;
	struct mipi_dbi *dbi;
	struct gpio_desc *dc;
	u32 freq = 24000000;
	u8 *init;
	int len, ret;

	lcd = u30_find_lcd_node();
	if (!lcd)
		return dev_err_probe(dev, -ENODEV, "no /lcds node for spi_panel_name\n");

	pnl = devm_drm_dev_alloc(dev, &u30_driver, struct u30_panel, dbidev.drm);
	if (IS_ERR(pnl))
		return PTR_ERR(pnl);
	dbidev = &pnl->dbidev;
	drm = &dbidev->drm;
	dbi = &dbidev->dbi;

	/* panel description from the stock tree */
	len = 0;
	if (!of_get_property(lcd, "init-data", &len) || len < 4)
		return dev_err_probe(dev, -EINVAL, "no init-data\n");
	init = devm_kmalloc(dev, len, GFP_KERNEL);
	if (!init)
		return -ENOMEM;
	of_property_read_u8_array(lcd, "init-data", init, len);
	pnl->init = init;
	pnl->init_len = len;

	len = of_property_count_u32_elems(lcd, "sprd,reset-on-sequence");
	if (len >= 2) {
		pnl->reset_seq = devm_kcalloc(dev, len, sizeof(u32), GFP_KERNEL);
		if (!pnl->reset_seq)
			return -ENOMEM;
		of_property_read_u32_array(lcd, "sprd,reset-on-sequence", pnl->reset_seq, len);
		pnl->reset_seq_len = len;
	}
	of_property_read_u32(lcd, "spi_freq", &freq);	/* stock sets max_speed_hz = spi_freq (48 MHz) */
	if (u30_spi_hz)
		freq = u30_spi_hz;

	/* Keep the bootloader logo powered until the first real KMS frame is ready.
	 * A deferred probe must not reset the glass or switch off its supply. */
	pnl->reset = devm_gpiod_get_optional(dev, "reset", GPIOD_ASIS);
	if (IS_ERR(pnl->reset))
		return dev_err_probe(dev, PTR_ERR(pnl->reset), "reset gpio\n");
	pnl->avdd = devm_gpiod_get_optional(dev, "avdd", GPIOD_ASIS);
	if (IS_ERR(pnl->avdd))
		return dev_err_probe(dev, PTR_ERR(pnl->avdd), "avdd gpio\n");
	pnl->bl_en = devm_gpiod_get_optional(dev, "bl_en", GPIOD_ASIS);
	if (IS_ERR(pnl->bl_en))
		return dev_err_probe(dev, PTR_ERR(pnl->bl_en), "bl_en gpio\n");
	dc = devm_gpiod_get(dev, "cd", GPIOD_OUT_LOW);
	if (IS_ERR(dc))
		return dev_err_probe(dev, PTR_ERR(dc), "cd gpio\n");

	blnode = of_find_compatible_node(NULL, NULL, "sprd,qogirn5-backlight");
	if (blnode) {
		bd = of_find_backlight_by_node(blnode);
		of_node_put(blnode);
		if (!bd)
			return -EPROBE_DEFER;
		dbidev->backlight = bd;
	}

	spi->mode = SPI_MODE_3;
	spi->bits_per_word = 8;
	spi->max_speed_hz = freq;
	ret = spi_setup(spi);
	if (ret)
		return dev_err_probe(dev, ret, "spi_setup\n");

	pnl->hz = freq;
	pnl->frame = devm_kmalloc(dev, 240 * 240 * 2, GFP_KERNEL);
	if (!pnl->frame)
		return -ENOMEM;
	mutex_init(&pnl->lock);
	u30_te_init(dev, pnl);
	pnl->prev = devm_kmalloc(dev, U30_W * U30_H * 4, GFP_KERNEL);	/* without it every update is a full frame */
	ret = mipi_dbi_spi_init(spi, dbi, dc);
	if (ret)
		return ret;
	dbi->command = u30_command;	/* stock transfer format (see u30_command) */
	dbi->write_memory_bpw = 16;
	dbi->swap_bytes = false;	/* native-endian RGB565 in, re-packed in u30_command */

	u30_pipe_funcs.disable = u30_disable;
	u30_pipe_funcs.update = u30_update;
	ret = mipi_dbi_dev_init(dbidev, &u30_pipe_funcs, &u30_mode, u30_rotation);
	if (ret)
		return ret;

	drm_mode_config_reset(drm);
	ret = drm_dev_register(drm, 0);
	if (ret)
		return ret;

	spi_set_drvdata(spi, drm);
	/* Native UI owns this KMS connector. Registering an fbdev client here
	 * would modeset a zero-filled buffer over the firmware logo before the
	 * UI starts. Screen sleep/wake is handled by connector DPMS, not fb0. */
	dev_info(dev, "NV3002C ready: 240x240, SPI %u Hz, rotation %d, TE sync %s\n", freq, u30_rotation, pnl->te_irq > 0 ? "on" : "off");
	return 0;
}

static void u30_remove(struct spi_device *spi)
{
	struct drm_device *drm = spi_get_drvdata(spi);

	drm_dev_unplug(drm);
	drm_atomic_helper_shutdown(drm);
}

static void u30_shutdown(struct spi_device *spi)
{
	drm_atomic_helper_shutdown(spi_get_drvdata(spi));
}

static const struct of_device_id u30_of_match[] = {
	{ .compatible = "sprd,generic-spi-panel" },
	{ }
};
MODULE_DEVICE_TABLE(of, u30_of_match);

static const struct spi_device_id u30_id[] = {
	{ "generic-spi-panel", 0 },
	{ }
};
MODULE_DEVICE_TABLE(spi, u30_id);

static struct spi_driver u30_spi_driver = {
	.driver = {
		.name = "u30pro-nv3002c",
		.of_match_table = u30_of_match,
	},
	.id_table = u30_id,
	.probe = u30_probe,
	.remove = u30_remove,
	.shutdown = u30_shutdown,
};
module_spi_driver(u30_spi_driver);

MODULE_DESCRIPTION("ZTE U30 Pro NV3002C SPI panel DRM driver");
MODULE_LICENSE("GPL");
