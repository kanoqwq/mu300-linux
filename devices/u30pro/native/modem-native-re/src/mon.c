/*
 * mon.c - runtime supervision of the Unisoc CP, replacing the post-boot half of the
 * stock modem_control. Behaviour is documented in docs/mon-flow.md; the names of the
 * stock functions each piece mirrors are given in the comments.
 */
#define _GNU_SOURCE
#include "mon.h"

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <time.h>
#include <unistd.h>

#define MODEM_ASSERT_CMD        0x4d0e   /* _IO('M', 0x0e): needs current->comm == "modem_control" */
#define MODEM_GET_ASSERT_INFO   0x4d14   /* _IO('M', 0x14): fills 256 bytes                         */
#define ASSERT_INFO_SIZE        256
#define POLL_MS                 200      /* granularity at which threads notice mon_stop()          */

#define MSG_PREPARE_RESET "Prepare Reset"
#define MSG_MODEM_RESET   "Modem Reset"
#define MSG_CH_RESET      "CH Reset"
#define MSG_MODEM_BLOCKED "Modem Blocked"

static struct {
	struct mon_cfg cfg;
	struct mon_ops ops;

	pthread_mutex_t st_lock;     /* protects the state block below                 */
	int state;                   /* g_modem_state     (DAT_0011d6b0)               */
	int ch_state;                /* s_chState         (DAT_0011c9c4)               */
	int client_state;            /* DAT_0011ca40: 0 offline, 1 alive, 2 assert      */
	int alive_flag;              /* DAT_0011d6b4: raised by "Modem Alive"           */
	int wait_reset;              /* DAT_0011d694: diag reset command pending        */
	int reboot_all;              /* DAT_0011d690: reboot whole system on next reset */
	int normal_reboot;           /* DAT_0011d698: sysrq 'b' instead of 'c'          */
	int was_booted;              /* DAT_0011d68c                                    */
	int dump_done;

	pthread_mutex_t msg_lock;    /* serialises process_message (DAT_0011c9c8)       */
	pthread_mutex_t dump_lock;
	pthread_cond_t  dump_cond;

	pthread_mutex_t wl_lock;
	int wl_ref;

	int pipe_r, pipe_w;          /* internal control pipe (DAT_0011c018/c024)        */
	pthread_t th[4];
	int nth;
	int stop;                    /* accessed with __atomic only */
	int started;
} M = {
	.st_lock = PTHREAD_MUTEX_INITIALIZER,
	.msg_lock = PTHREAD_MUTEX_INITIALIZER,
	.dump_lock = PTHREAD_MUTEX_INITIALIZER,
	.wl_lock = PTHREAD_MUTEX_INITIALIZER,
	.pipe_r = -1, .pipe_w = -1,
};

/* ------------------------------------------------------------------ helpers */

#define STOPPED() __atomic_load_n(&M.stop, __ATOMIC_SEQ_CST)

static void LOG(int prio, const char *fmt, ...)
{
	char b[512];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(b, sizeof b, fmt, ap);
	va_end(ap);
	if (M.ops.log)
		M.ops.log(prio, b, M.ops.ctx);
	else
		fprintf(stderr, "MODEM_CTRL[%d]: %s\n", prio, b);
}

static const char *PATH(char *buf, size_t n, const char *p)
{
	snprintf(buf, n, "%s%s", M.cfg.root ? M.cfg.root : "", p);
	return buf;
}

static int get_state(void)
{
	int v;
	pthread_mutex_lock(&M.st_lock);
	v = M.state;
	pthread_mutex_unlock(&M.st_lock);
	return v;
}

/* modem_ctrl_set_modem_state */
void mon_set_state(int s)
{
	pthread_mutex_lock(&M.st_lock);
	if (s == MON_ST_ALIVE)
		M.alive_flag = 1;
	M.state = s;
	pthread_mutex_unlock(&M.st_lock);
	LOG(MON_LOG_INFO, "modem_ctrl_set_modem_state: g_modem_state = %d", s);
}

int mon_get_state(void) { return get_state(); }

void mon_set_ch_state(int s)
{
	pthread_mutex_lock(&M.st_lock);
	M.ch_state = s;
	pthread_mutex_unlock(&M.st_lock);
	LOG(MON_LOG_INFO, "modem_ctrl_set_ch_state: s_chState = %d", s);
}

int mon_get_ch_state(void)
{
	int v;
	pthread_mutex_lock(&M.st_lock);
	v = M.ch_state;
	pthread_mutex_unlock(&M.st_lock);
	return v;
}

int mon_was_booted(void)
{
	int v;
	pthread_mutex_lock(&M.st_lock);
	v = M.was_booted;
	pthread_mutex_unlock(&M.st_lock);
	return v;
}

#define SETI(field, v) do { pthread_mutex_lock(&M.st_lock); M.field = (v); pthread_mutex_unlock(&M.st_lock); } while (0)

static int GETI_(const int *p)
{
	int v;
	pthread_mutex_lock(&M.st_lock);
	v = *p;
	pthread_mutex_unlock(&M.st_lock);
	return v;
}
#define GETI(field) GETI_(&M.field)

/* modem_ctrl_enable_wake_lock: stock writes "modem_control" to /sys/power/wake_[un]lock.
 * Reference counted here (the stock code is not and leaks a lock on one path). */
static void wake_lock(int on)
{
	char pb[512];
	int fd, n = 0;
	const char *p;

	if (M.ops.wake_lock) {
		M.ops.wake_lock(on, M.ops.ctx);
		return;
	}
	pthread_mutex_lock(&M.wl_lock);
	if (on) {
		n = M.wl_ref++;
		if (n != 0) { pthread_mutex_unlock(&M.wl_lock); return; }
	} else {
		if (M.wl_ref == 0) { pthread_mutex_unlock(&M.wl_lock); return; }
		if (--M.wl_ref != 0) { pthread_mutex_unlock(&M.wl_lock); return; }
	}
	p = on ? M.cfg.wake_lock_path : M.cfg.wake_unlock_path;
	if (p) {
		fd = open(PATH(pb, sizeof pb, p), O_RDWR);
		if (fd >= 0) {
			if (write(fd, "modem_control", 13) < 0)
				LOG(MON_LOG_ERROR, "write %s failed, error: %s", p, strerror(errno));
			close(fd);
		} else {
			LOG(MON_LOG_ERROR, "open %s failed, error: %s", p, strerror(errno));
		}
	}
	pthread_mutex_unlock(&M.wl_lock);
}

static void prop_set(const char *k, const char *v)
{
	if (M.ops.prop_set)
		M.ops.prop_set(k, v, M.ops.ctx);
}

/* write one NUL terminated message into the internal control pipe */
static void pipe_msg(const char *m)
{
	if (M.pipe_w >= 0 && write(M.pipe_w, m, strlen(m) + 1) < 0)
		LOG(MON_LOG_ERROR, "control pipe write failed: %s", strerror(errno));
}

/* modem_ctrl_notify_sipa_modem_status: stock runs `echo <msg>> sipa_dele_reset` through
 * system(); only "Modem Assert" / "Modem Reset" are interpreted by the kernel
 * (sipa_dele_cp_in_ap.c), everything else is merely logged there. */
static void notify_sipa(const char *msg)
{
	char pb[512], b[160];
	int fd, n;

	if (!M.cfg.sipa_reset_path)
		return;
	PATH(pb, sizeof pb, M.cfg.sipa_reset_path);
	if (access(pb, F_OK) != 0)
		return;
	n = snprintf(b, sizeof b, "%.127s\n", msg);
	fd = open(pb, O_WRONLY);
	if (fd < 0) {
		LOG(MON_LOG_ERROR, "sipa_dele_reset open failed: %s", strerror(errno));
		return;
	}
	if (write(fd, b, (size_t)n) < 0)
		LOG(MON_LOG_ERROR, "sipa_dele_reset write failed: %s", strerror(errno));
	close(fd);
	LOG(MON_LOG_INFO, "modem_ctrl_notify_sipa_modem_status: buf_cmd : echo %.127s> %s", msg,
	    M.cfg.sipa_reset_path);
}

/* whole-system reboot: state 8, sleep(5), sysrq b (normal) or c (crash -> ramdump). */
static void do_reboot(void)
{
	char pb[512];
	int normal = GETI(normal_reboot);
	int fd;

	mon_set_state(MON_ST_REBOOTING);
	if (M.cfg.reboot_delay_s > 0)
		sleep((unsigned)M.cfg.reboot_delay_s);
	if (M.ops.reboot) {
		M.ops.reboot(normal, M.ops.ctx);
		return;
	}
	fd = open(PATH(pb, sizeof pb, M.cfg.sysrq_path ? M.cfg.sysrq_path : "/proc/sysrq-trigger"), O_WRONLY);
	if (fd >= 0) {
		if (write(fd, normal ? "b" : "c", 1) < 0)
			LOG(MON_LOG_ERROR, "sysrq write failed: %s", strerror(errno));
		close(fd);
	}
	for (;;)
		sleep(1);
}

static int do_reload(int type)
{
	int rc;

	if (!M.ops.reload) {
		LOG(MON_LOG_ERROR, "no reload handler, cannot reload type 0x%x", type);
		return -1;
	}
	mon_set_state(MON_ST_LOADING);
	rc = M.ops.reload(type, M.ops.ctx);
	if (rc == 0)
		mon_set_state(MON_ST_RUNNING);
	else
		LOG(MON_LOG_ERROR, "reload(0x%x) failed: %d", type, rc);
	return rc;
}

/* ------------------------------------------------------------ process_message */

struct waiter { const char *msg; };

/* write_reset_to_modem_ctrl: wait for a dump consumer, then queue `msg`. */
static void *dump_waiter(void *arg)
{
	struct waiter *w = arg;
	struct timespec dl, now, s;

	LOG(MON_LOG_DEBUG, "reset_md_thread wait dump complete...");
	pthread_mutex_lock(&M.dump_lock);
	clock_gettime(CLOCK_MONOTONIC, &dl);
	dl.tv_sec += M.cfg.dump_wait_s;
	/* wait in slices so mon_stop() is honoured */
	while (!M.dump_done && !STOPPED()) {
		clock_gettime(CLOCK_MONOTONIC, &now);
		if (now.tv_sec > dl.tv_sec || (now.tv_sec == dl.tv_sec && now.tv_nsec >= dl.tv_nsec))
			break;
		s = now;
		s.tv_nsec += POLL_MS * 1000000L;
		if (s.tv_nsec >= 1000000000L) { s.tv_sec++; s.tv_nsec -= 1000000000L; }
		if (s.tv_sec > dl.tv_sec || (s.tv_sec == dl.tv_sec && s.tv_nsec > dl.tv_nsec))
			s = dl;
		pthread_cond_timedwait(&M.dump_cond, &M.dump_lock, &s);
	}
	pthread_mutex_unlock(&M.dump_lock);
	if (!STOPPED()) {
		LOG(MON_LOG_DEBUG, "dump complete, Write %s to modem_control.", w->msg);
		pipe_msg(w->msg);
	}
	return NULL;
}

static void spawn_dump_waiter(const char *msg)
{
	static struct waiter w_prepare = { MSG_PREPARE_RESET }, w_reset = { MSG_MODEM_RESET };
	pthread_t t;
	pthread_attr_t a;

	pthread_attr_init(&a);
	pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
	if (pthread_create(&t, &a, dump_waiter, strcmp(msg, MSG_PREPARE_RESET) == 0 ? &w_prepare : &w_reset))
		LOG(MON_LOG_ERROR, "modem_ctrl_process_message:create reset pthread error, errno = %s", strerror(errno));
	pthread_attr_destroy(&a);
}

void mon_notify_dump_complete(void)
{
	pthread_mutex_lock(&M.dump_lock);
	M.dump_done = 1;
	pthread_cond_broadcast(&M.dump_cond);
	pthread_mutex_unlock(&M.dump_lock);
	LOG(MON_LOG_DEBUG, "send dump complete.");
}

/* modem_write_data_to_clients == modem_ctrl_process_message. The Android clients and the
 * IMEI read for wifi-only SKUs are dropped; the state machine and the control pipe stay. */
static void process_message(const char *msg, int len)
{
	int reset = M.cfg.reset_opt;

	pthread_mutex_lock(&M.msg_lock);
	LOG(MON_LOG_INFO, "process_message(%d) : %s", len, msg);

	if (strstr(msg, "Modem Alive")) {
		SETI(client_state, 1);
		LOG(MON_LOG_DEBUG, "control nvitemd! isStart 1");
		prop_set("ctl.start", "vendor.cp_diskserver");
	} else if (strstr(msg, "Modem Assert")) {
		if (!strstr(msg, "P-ARM Modem Assert") && get_state() != MON_ST_NOT_ALIVE) {
			SETI(client_state, 2);
			LOG(MON_LOG_DEBUG, "control nvitemd! isStart 0");
			prop_set("ctl.stop", "vendor.cp_diskserver");
			LOG(MON_LOG_DEBUG, "reload modem: %d, dump: %d", reset, M.cfg.save_dump);
			if (reset) {
				if (M.cfg.save_dump && M.cfg.dump_wait_s > 0) {
					pthread_mutex_lock(&M.dump_lock);
					M.dump_done = 0;
					pthread_mutex_unlock(&M.dump_lock);
					spawn_dump_waiter(MSG_PREPARE_RESET);
				} else {
					pipe_msg(MSG_PREPARE_RESET);
				}
			}
		} else if (mon_get_ch_state() == MON_CH_ASSERT && reset) {
			LOG(MON_LOG_DEBUG, "modem_ctrl_process_message: isReset = 1, Reset CH");
			pipe_msg(MSG_CH_RESET);
		}
	} else if (strstr(msg, "Modem Reset")) {
		LOG(MON_LOG_DEBUG, "control nvitemd! isStart 0");
		prop_set("ctl.stop", "vendor.cp_diskserver");
		pipe_msg(MSG_MODEM_RESET);
	} else if (strstr(msg, "CH Watchdog Timeout") && reset) {
		LOG(MON_LOG_DEBUG, "modem_ctrl_process_message: CH Watchdog Timeout, isReset = 1, Reset CH");
		pipe_msg(MSG_CH_RESET);
	}

	notify_sipa(msg);
	if (M.ops.on_message)
		M.ops.on_message(msg, len, M.ops.ctx);
	pthread_mutex_unlock(&M.msg_lock);
}

void mon_inject(const char *msg)
{
	process_message(msg, (int)strlen(msg));
}

/* ------------------------------------------------------------ device threads */

/* open a node, retrying like the stock threads: ENOENT -> "unsupported", give up;
 * other errors: up to 61 tries one second apart. */
static int open_retry(const char *path, int flags, const char *who)
{
	char pb[512];
	int fd, i, e = 0;

	for (i = 0; i < 61 && !STOPPED(); i++) {
		fd = open(PATH(pb, sizeof pb, path), flags);
		if (fd >= 0)
			return fd;
		e = errno;
		if (e == ENOENT) {
			LOG(MON_LOG_DEBUG, "unsupport %s, path is %s", who, path);
			return -1;
		}
		sleep(1);
	}
	if (!STOPPED())
		LOG(MON_LOG_ERROR, "%s: open %s failed, %s", who, path, strerror(e));
	return -1;
}

/* select() with a stop-poll timeout. >0 ready, 0 timeout, <0 error (errno set). */
static int wait_fd(int fd)
{
	fd_set r;
	struct timeval tv = { 0, POLL_MS * 1000 };

	FD_ZERO(&r);
	FD_SET(fd, &r);
	return select(fd + 1, &r, NULL, NULL, &tv);
}

/* modem_ctrl_listen_sp: /dev/sctl_pm carries events of the SP (pm_sys) core. */
static void *th_sp(void *arg)
{
	char buf[256];
	int fd, n, rc, opt;

	(void)arg;
	LOG(MON_LOG_DEBUG, "enter modem_ctrl_listen_sp");
	fd = open_retry(M.cfg.sctl_pm, O_RDWR, "modem_ctrl_listen_sp");
	if (fd < 0)
		return NULL;
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_sp: open %s success", M.cfg.sctl_pm);
	prop_set("vendor.modem.sp-boot", "1");
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_sp: enter read ...");
	while (!STOPPED()) {
		rc = wait_fd(fd);
		if (rc == 0)
			continue;
		if (rc < 0) {
			LOG(MON_LOG_ERROR, "select error: %s", strerror(errno));
			if (errno != EAGAIN && errno != EINTR)
				break;
			sleep(1);
			continue;
		}
		wake_lock(1);
		n = (int)read(fd, buf, 0x7f);
		if (n <= 0) {
			LOG(MON_LOG_ERROR, "modem_ctrl_listen_sp: read %d return %d, errno = %s", fd, n, strerror(errno));
			wake_lock(0);
			usleep(200000);
			continue;
		}
		buf[n] = 0;
		LOG(MON_LOG_DEBUG, "modem_ctrl_listen_sp: buf=%s", buf);
		if (strstr(buf, "CH Watchdog Timeout")) {
			mon_set_ch_state(MON_CH_WATCHDOG);
			opt = 0;
		} else if (strstr(buf, "P-ARM Modem Assert")) {
			opt = M.cfg.reset_opt;
		} else {
			wake_lock(0);
			continue;
		}
		if (!M.cfg.boot_mode_cali)
			process_message(buf, n);
		if (opt) {
			LOG(MON_LOG_DEBUG, "P-ARM Modem Assert: modem_ctrl_listen_sp: [SP] reboot system");
			do_reboot();
		}
		wake_lock(0);
	}
	close(fd);
	return NULL;
}

/* modem_ctrl_listen_ch: /dev/sctl_ch carries events of the CH (audio/sensor DSP) core. */
static void *th_ch(void *arg)
{
	char buf[256];
	int fd, n, rc;

	(void)arg;
	LOG(MON_LOG_DEBUG, "enter modem_ctrl_listen_ch");
	fd = open_retry(M.cfg.sctl_ch, O_RDWR, "modem_ctrl_listen_ch");
	if (fd < 0)
		return NULL;
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_ch: open %s success", M.cfg.sctl_ch);
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_ch: enter read ...");
	while (!STOPPED()) {
		rc = wait_fd(fd);
		if (rc == 0)
			continue;
		if (rc < 0) {
			LOG(MON_LOG_ERROR, "select error: %s", strerror(errno));
			if (errno != EAGAIN && errno != EINTR)
				break;
			sleep(1);
			continue;
		}
		wake_lock(1);
		n = (int)read(fd, buf, 0x7f);
		if (n <= 0) {
			LOG(MON_LOG_ERROR, "modem_ctrl_listen_ch: read %d return %d, errno = %s", fd, n, strerror(errno));
			wake_lock(0);
			usleep(200000);
			continue;
		}
		buf[n] = 0;
		LOG(MON_LOG_DEBUG, "modem_ctrl_listen_ch: buf=%s", buf);
		if (strstr(buf, "CH Alive")) {
			mon_set_ch_state(MON_CH_ALIVE);
			if (!M.cfg.debuggable) {
				/* user build: CH's own watchdog must be switched on by us */
				LOG(MON_LOG_DEBUG, "modem_ctrl_listen_ch:user mode need notified CH open the watchdog");
				if (write(fd, "watchdog on", 11) < 1)
					LOG(MON_LOG_ERROR, "modem_ctrl_listen_ch: write %d error, errno = %s", fd, strerror(errno));
			}
		} else if (strstr(buf, "P-ARM Modem Assert")) {
			mon_set_ch_state(MON_CH_ASSERT);
		} else {
			wake_lock(0);
			continue;
		}
		if (!M.cfg.boot_mode_cali)
			process_message(buf, n);
		wake_lock(0);
	}
	close(fd);
	return NULL;
}

/* modem_ctrl_listen_modem: assert/alive/reset announcements of the PS CP (spipe_nr2). */
static void *th_modem(void *arg)
{
	char buf[256], pb[512];
	int afd, wfd, lfd = -1, n, rc, i, maxfd, fds[3], opt;
	const char *alive;

	(void)arg;
	LOG(MON_LOG_DEBUG, "Enter modem_ctrl_listen_modem !");
	/* the stock code retries forever, one second apart */
	while ((afd = open(PATH(pb, sizeof pb, M.cfg.assert_dev), O_RDWR)) < 0 && !STOPPED()) {
		LOG(MON_LOG_ERROR, "modem_ctrl_listen_modem: open %s failed, error: %s", M.cfg.assert_dev, strerror(errno));
		sleep(1);
	}
	if (STOPPED())
		return NULL;
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_modem: open assert dev: %s, fd = %d", M.cfg.assert_dev, afd);
	wfd = M.cfg.wdt_dev ? open(PATH(pb, sizeof pb, M.cfg.wdt_dev), O_RDONLY) : -1;
	if (wfd < 0)
		LOG(MON_LOG_DEBUG, "open %s unsupport watchdog, errno: %s", M.cfg.wdt_dev ? M.cfg.wdt_dev : "(none)", strerror(errno));
	/* a separate alive node is only watched when it differs from the assert node */
	alive = M.cfg.alive_dev;
	if (alive && strcmp(alive, M.cfg.assert_dev) != 0) {
		lfd = open(PATH(pb, sizeof pb, alive), O_RDONLY);
		if (lfd < 0)
			LOG(MON_LOG_ERROR, "open %s  , errno: %s", alive, strerror(errno));
	}
	fds[0] = afd; fds[1] = wfd; fds[2] = lfd;

	while (!STOPPED()) {
		fd_set r;
		struct timeval tv = { 0, POLL_MS * 1000 };

		FD_ZERO(&r);
		maxfd = -1;
		for (i = 0; i < 3; i++)
			if (fds[i] >= 0) {
				FD_SET(fds[i], &r);
				if (fds[i] > maxfd)
					maxfd = fds[i];
			}
		LOG(MON_LOG_DEBUG, "modem_ctrl_listen_modem: wait for modem assert/hangup event ...");
		rc = select(maxfd + 1, &r, NULL, NULL, &tv);
		if (rc == 0)
			continue;
		if (rc < 0) {
			if (errno == EINTR)
				continue;
			LOG(MON_LOG_ERROR, "modem_ctrl_listen_modem: ret=%d, errno=%s!", rc, strerror(errno));
			sleep(1);
			continue;
		}
		for (i = 0; i < 3 && !(fds[i] >= 0 && FD_ISSET(fds[i], &r)); i++)
			;
		if (i == 3) {
			LOG(MON_LOG_DEBUG, "modem_ctrl_listen_modem: no fd is readalbe");
			sleep(1);
			continue;
		}
		wake_lock(1);
		LOG(MON_LOG_DEBUG, "modem_ctrl_listen_modem: enter read ...");
		n = (int)read(fds[i], buf, 0xff);
		if (n < 1) {
			LOG(MON_LOG_ERROR, "modem_ctrl_listen_modem: read %d return %d, errno = %s", fds[i], n, strerror(errno));
			wake_lock(0);
			sleep(1);
			continue;
		}
		buf[n] = 0;
		LOG(MON_LOG_INFO, "modem_ctrl_listen_modem: read %s from modem!", buf);
		if (strstr(buf, "Modem Alive")) {
			mon_set_state(MON_ST_ALIVE);
		} else if (strstr(buf, "Modem Reset")) {
			mon_set_state(MON_ST_RESET);
			SETI(wait_reset, 0);
		} else if (strstr(buf, "Modem Assert") && get_state() != MON_ST_BLOCKED) {
			mon_set_state(MON_ST_ASSERT);
			opt = M.cfg.reset_opt;
			LOG(MON_LOG_INFO, "persist.vendor.sys.modemreset = %d, reset_option = %d", opt, opt);
			if (opt) {
				if (opt == 2) {
					SETI(normal_reboot, 1);
					SETI(reboot_all, 1);
				}
				if (strstr(buf, "HUNG")) {
					LOG(MON_LOG_INFO, "modem_ctrl_listen_modem: dsp HUNG, set reboot system flag");
					SETI(reboot_all, 1);
				}
			}
		}
		if (!M.cfg.boot_mode_cali && get_state() != MON_ST_BLOCKED) {
			process_message(buf, n);
			LOG(MON_LOG_DEBUG, "modem_ctrl_listen_modem: write to clients len = %d", n);
		}
		wake_lock(0);
	}
	close(afd);
	if (wfd >= 0) close(wfd);
	if (lfd >= 0) close(lfd);
	return NULL;
}

/* ------------------------------------------------------------ wait alive */

/* wait_for_modem_alive */
int mon_wait_alive(int timeout_s)
{
	char pb[512], b[ASSERT_INFO_SIZE + 1];
	int has_prop = M.cfg.alive_dev != NULL;
	const char *dev = has_prop ? M.cfg.alive_dev : M.cfg.tty_dev;
	int fd = -1, i, ok = 0, e;

	if (timeout_s <= 0)
		timeout_s = M.cfg.alive_timeout_s > 0 ? M.cfg.alive_timeout_s : 60;
	wake_lock(1);

	for (i = 0; i < 60; i++) {
		fd = open(PATH(pb, sizeof pb, dev), O_RDWR | O_NONBLOCK);
		if (fd >= 0)
			break;
		e = errno;
		if (e == EINTR || e == EAGAIN) {
			i--;
			continue;
		}
		if (e == ENOENT) {
			LOG(MON_LOG_ERROR, "open path %s failed, %s", dev, strerror(e));
			wake_lock(0);
			return 0;
		}
		usleep(1000000);
	}

	if (has_prop) {
		/* the alive text is consumed by th_modem which raises alive_flag */
		for (i = 0; i < timeout_s * 10 && !STOPPED(); i++) {
			if (GETI(alive_flag)) { ok = 1; break; }
			usleep(100000);
		}
	} else if (fd >= 0) {
		/* no dedicated alive node: read the tty ourselves */
		time_t end = time(NULL) + timeout_s;
		while (!ok && !STOPPED() && time(NULL) < end) {
			int rc = wait_fd(fd), n;
			if (rc <= 0)
				continue;
			n = (int)read(fd, b, ASSERT_INFO_SIZE);
			if (n < 1)
				continue;
			b[n] = 0;
			LOG(MON_LOG_DEBUG, "modem_ctrl_read_modem_alive: read response %s from %d", b, fd);
			if (strstr(b, "Modem Alive")) {
				mon_set_state(MON_ST_ALIVE);
				process_message("Modem Alive", 11);
				ok = 1;
			}
		}
	}

	if (!ok && GETI(alive_flag))
		ok = 1;
	if (ok) {
		LOG(MON_LOG_DEBUG, "wait_for_modem_alive: wait modem alive!");
		pthread_mutex_lock(&M.st_lock);
		M.alive_flag = 0;
		M.was_booted = 1;
		pthread_mutex_unlock(&M.st_lock);
	} else {
		int n = -1, mfd;

		LOG(MON_LOG_ERROR, "wait_for_modem_alive: wait modem alive timeout!");
		memset(b, 0, sizeof b);
		mfd = M.cfg.modem_dev ? open(PATH(pb, sizeof pb, M.cfg.modem_dev), O_RDWR) : -1;
		if (mfd >= 0) {
			n = ioctl(mfd, MODEM_GET_ASSERT_INFO, b);
			close(mfd);
		}
		b[ASSERT_INFO_SIZE] = 0;
		if (n >= 1 && strstr(b, "Assert")) {
			mon_set_state(MON_ST_ASSERT);
			process_message(b, (int)strlen(b));
		} else {
			mon_set_state(MON_ST_NOT_ALIVE);
			process_message("Modem Assert: modem not alive!", 30);
		}
		if (M.cfg.reboot_on_alive_timeout) {
			LOG(MON_LOG_ERROR, "wait_for_modem_alive: wait modem alive timeout, reboot system");
			do_reboot();
		}
	}
	if (fd >= 0)
		close(fd);
	wake_lock(0);
	return ok;
}

/* ------------------------------------------------------------ control thread */

/* modem_load_assert_modem: make the CP assert so that it can be dumped. The kernel only
 * accepts the ioctl from a task named "modem_control". */
static void force_assert(void)
{
	char pb[512];
	int fd, rc;

	if (!M.cfg.modem_dev)
		return;
	fd = open(PATH(pb, sizeof pb, M.cfg.modem_dev), O_RDWR);
	if (fd < 0) {
		LOG(MON_LOG_ERROR, "modem_iocmd: %s failed, error: %s", M.cfg.modem_dev, strerror(errno));
		return;
	}
	rc = ioctl(fd, MODEM_ASSERT_CMD, 0);
	LOG(MON_LOG_DEBUG, "modem_ioctrl_assert, path=%s ret=%d", M.cfg.modem_dev, rc);
	close(fd);
}

static void drain(int fd)
{
	char b[0x800];
	while (read(fd, b, sizeof b) > 0)
		;
}

/* prepare_reset_modem: flush the log channel, then ask the CP (through the diag channel)
 * to reset itself. Returns 1 when the CP did not answer in time and must be force reloaded. */
static int prepare_reset(void)
{
	static const unsigned char cmd[2] = { 0x7a, 0x0a };   /* the stock 16-bit constant 0x0a7a */
	char pb[512];
	int lfd, dfd, i, waits;

	LOG(MON_LOG_DEBUG, "Modem Reset");
	lfd = open(PATH(pb, sizeof pb, M.cfg.log_dev), O_RDWR | O_NONBLOCK);
	if (lfd < 0) {
		lfd = open(PATH(pb, sizeof pb, M.cfg.diag_dev), O_RDWR | O_NONBLOCK);
		LOG(MON_LOG_DEBUG, "prepare_reset_modem: log chanel open failed, use diag chanel, %s", strerror(errno));
		if (lfd < 0)
			LOG(MON_LOG_ERROR, "prepare_reset_modem: MODEM cannot open log chanel, %s", strerror(errno));
	}
	if (lfd >= 0) {
		for (;;) {
			fd_set r;
			struct timeval tv = { M.cfg.drain_timeout_ms / 1000, (M.cfg.drain_timeout_ms % 1000) * 1000 };
			int rc;
			char b[0x800];

			FD_ZERO(&r);
			FD_SET(lfd, &r);
			rc = select(lfd + 1, &r, NULL, NULL, &tv);
			if (rc < 0 && errno == EINTR)
				continue;
			if (rc <= 0) {
				LOG(MON_LOG_DEBUG, " time out, read log over!");
				break;
			}
			while ((rc = (int)read(lfd, b, sizeof b)) > 0)
				;
			if (rc < 0 && errno != EAGAIN)
				break;
		}
		LOG(MON_LOG_DEBUG, "prepare_reset_modem: read log over %s!", M.cfg.log_dev);
		close(lfd);
	}

	dfd = open(PATH(pb, sizeof pb, M.cfg.diag_dev), O_RDWR | O_NONBLOCK);
	if (dfd < 0) {
		/* stock: logs and silently does nothing. We force the reload instead. */
		LOG(MON_LOG_ERROR, "prepare_reset_modem: MODEM cannot open %s, %s", M.cfg.diag_dev, strerror(errno));
		return 1;
	}
	drain(dfd);
	LOG(MON_LOG_DEBUG, "prepare_reset_modem: ready write diag cmd = %02x%02x!", cmd[1], cmd[0]);
	SETI(wait_reset, 1);
	if (write(dfd, cmd, 2) != 2)
		LOG(MON_LOG_DEBUG, "prepare_reset_modem: MODEM write diag_chan failed, %s", strerror(errno));
	close(dfd);

	waits = M.cfg.reset_wait_s * 10;
	for (i = 0; i < waits && !STOPPED(); i++) {
		usleep(100000);
		if (!GETI(wait_reset))
			break;
	}
	if (GETI(wait_reset)) {
		LOG(MON_LOG_DEBUG, "modem_ctrl_listen_clients: wait modem rest timeout, force reset modem");
		return 1;
	}
	return 0;
}

/* one control message (stock modem_ctrl_listen_clients body) */
static void handle_ctl(const char *m)
{
	int st;

	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_clients: read str= %s", m);
	wake_lock(1);

	if (strstr(m, "CH Reset")) {
		mon_set_ch_state(MON_CH_RESET);
		do_reload(MON_LOAD_CH);
		wake_lock(0);
		return;
	}
	if (strstr(m, MSG_MODEM_BLOCKED)) {
		mon_set_state(MON_ST_BLOCKED);
		if (M.cfg.save_dump && M.cfg.dump_wait_s > 0)
			force_assert();
	}
	st = get_state();
	if (st != MON_ST_ASSERT && st != MON_ST_BLOCKED && st != MON_ST_RESET) {
		LOG(MON_LOG_DEBUG, "state(%d) not block or assert skip it!", st);
		wake_lock(0);
		return;
	}
	if (strstr(m, MSG_MODEM_RESET)) {
		do_reload(MON_LOAD_CP);
		LOG(MON_LOG_DEBUG, "Modem Reset");
		mon_wait_alive(0);
	} else if (strstr(m, MSG_PREPARE_RESET)) {
		if (GETI(reboot_all)) {
			LOG(MON_LOG_DEBUG, "modem_ctrl_listen_clients: reboot system");
			do_reboot();
		} else if (prepare_reset()) {
			notify_sipa(MSG_MODEM_RESET);
			do_reload(MON_LOAD_CP);
			mon_wait_alive(0);
		}
		/* else: the CP acknowledged with "Modem Reset" which arrives here as a new message */
	}
	wake_lock(0);
}

/* modem_ctrl_listen_clients: the supervisor. Reads the internal control pipe. */
static void *th_ctl(void *arg)
{
	char buf[256];
	int n, rc;

	(void)arg;
	LOG(MON_LOG_DEBUG, "modem_ctrl_listen_clients: start listen clients...");
	while (!STOPPED()) {
		rc = wait_fd(M.pipe_r);
		if (rc <= 0)
			continue;
		n = (int)read(M.pipe_r, buf, sizeof buf - 1);
		if (n <= 0) {
			sleep(1);
			continue;
		}
		buf[n] = 0;
		/* messages are NUL terminated; several may have been queued back to back */
		for (int off = 0; off < n && !STOPPED(); off += (int)strlen(buf + off) + 1)
			if (buf[off])
				handle_ctl(buf + off);
	}
	return NULL;
}

/* "Modem Blocked" as reported by a client in the stock program (modem_ctrl_read_thread /
 * dispatch_modem_blocked). */
void mon_report_blocked(void)
{
	int run = 0;

	pipe_msg(MSG_MODEM_BLOCKED);
	pthread_mutex_lock(&M.st_lock);
	if (M.client_state == 1) {
		M.client_state = 2;
		run = 1;
	}
	pthread_mutex_unlock(&M.st_lock);
	if (!run)
		return;
	if (M.cfg.loop_dev) {
		/* stock: write a 32 byte "AT\r" packet to the loop pipe and expect it back */
		char pb[512], pk[0x20] = "AT\r", rb[0x80];
		int fd = open(PATH(pb, sizeof pb, M.cfg.loop_dev), O_RDWR | O_NONBLOCK);
		if (fd >= 0) {
			if (write(fd, pk, sizeof pk) >= 0) {
				usleep(100000);
				if (read(fd, rb, sizeof rb - 1) >= 4 && !memcmp(rb, "AT\r", 4))
					LOG(MON_LOG_DEBUG, "dispatch_modem_blocked: loop spipe %s is OK", M.cfg.loop_dev);
			}
			close(fd);
		} else {
			LOG(MON_LOG_ERROR, "dispatch_modem_blocked: open %s failed, error: %s", M.cfg.loop_dev, strerror(errno));
		}
	}
	LOG(MON_LOG_ERROR, "Info all the sock clients that modem is blocked");
	process_message(MSG_MODEM_BLOCKED, 14);
	prop_set("ctl.stop", "vendor.cp_diskserver");
	if (!M.cfg.reset_opt)
		LOG(MON_LOG_DEBUG, "dispatch_modem_blocked: reset is not enabled , not reset");
	else if (M.cfg.save_dump && M.cfg.dump_wait_s > 0)
		spawn_dump_waiter(MSG_MODEM_RESET);     /* later reset, after the dump */
	else
		pipe_msg(MSG_MODEM_RESET);
}

/* ------------------------------------------------------------ lifecycle */

void mon_cfg_default(struct mon_cfg *c)
{
	memset(c, 0, sizeof *c);
	c->root = "";
	c->sctl_pm = "/dev/sctl_pm";
	c->sctl_ch = "/dev/sctl_ch";
	c->assert_dev = "/dev/spipe_nr2";
	c->alive_dev = "/dev/spipe_nr2";
	c->wdt_dev = "/dev/modemwdtirq";
	c->tty_dev = "/dev/stty_nr";
	c->modem_dev = "/dev/modem";
	c->log_dev = "/dev/slog_nr";
	c->diag_dev = "/dev/sdiag_nr";
	c->loop_dev = "/dev/spipe_nr0";
	c->sipa_reset_path = "/sys/devices/platform/soc/soc:ipa-apb/soc:ipa-apb:sipa-dele/sipa_dele_reset";
	c->wake_lock_path = "/sys/power/wake_lock";
	c->wake_unlock_path = "/sys/power/wake_unlock";
	c->sysrq_path = "/proc/sysrq-trigger";
	c->reset_opt = 1;
	c->save_dump = 0;       /* stock props say 1 but there is no slogmodem to answer */
	c->dump_wait_s = 0;
	c->debuggable = 1;
	c->boot_mode_cali = 0;
	c->reboot_on_alive_timeout = 1;
	c->reboot_delay_s = 5;
	c->alive_timeout_s = 60;
	c->reset_wait_s = 5;
	c->drain_timeout_ms = 1000;
}

int mon_start(const struct mon_cfg *cfg, const struct mon_ops *ops)
{
	int fds[2];
	pthread_condattr_t ca;

	if (M.started)
		return 0;
	M.cfg = *cfg;
	if (ops)
		M.ops = *ops;
	if (pipe(fds) < 0)
		return -1;
	fcntl(fds[0], F_SETFD, FD_CLOEXEC);
	fcntl(fds[1], F_SETFD, FD_CLOEXEC);
	M.pipe_r = fds[0];
	M.pipe_w = fds[1];
	pthread_condattr_init(&ca);
	pthread_condattr_setclock(&ca, CLOCK_MONOTONIC);
	pthread_cond_init(&M.dump_cond, &ca);
	pthread_condattr_destroy(&ca);
	__atomic_store_n(&M.stop, 0, __ATOMIC_SEQ_CST);
	M.nth = 0;
	M.started = 1;
	/* start order mirrors modem_ctrl_boot_modem: sp, ch, control, modem */
	if (pthread_create(&M.th[M.nth], NULL, th_sp, NULL) == 0) M.nth++;
	else LOG(MON_LOG_ERROR, "modem_ctrl_listen_sp  create error!");
	if (pthread_create(&M.th[M.nth], NULL, th_ch, NULL) == 0) M.nth++;
	else LOG(MON_LOG_ERROR, "modem_ctrl_listen_ch  create error!");
	if (pthread_create(&M.th[M.nth], NULL, th_ctl, NULL) == 0) M.nth++;
	else LOG(MON_LOG_ERROR, "modem_ctrl_listen_clients  create error!");
	if (pthread_create(&M.th[M.nth], NULL, th_modem, NULL) == 0) M.nth++;
	else LOG(MON_LOG_ERROR, "modem_ctrl_listen_modem create error!");
	return M.nth == 4 ? 0 : -1;
}

void mon_stop(void)
{
	int i;

	if (!M.started)
		return;
	__atomic_store_n(&M.stop, 1, __ATOMIC_SEQ_CST);
	for (i = 0; i < M.nth; i++)
		pthread_join(M.th[i], NULL);
	close(M.pipe_r);
	close(M.pipe_w);
	M.pipe_r = M.pipe_w = -1;
	M.started = 0;
	M.nth = 0;
	/* reset the state block so a following mon_start() starts clean (tests) */
	M.state = M.ch_state = M.client_state = M.alive_flag = M.wait_reset = 0;
	M.reboot_all = M.normal_reboot = M.was_booted = M.dump_done = 0;
	M.wl_ref = 0;
}
