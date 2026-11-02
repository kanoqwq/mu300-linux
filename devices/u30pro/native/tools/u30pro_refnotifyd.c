/*
 * u30pro-refnotifyd: keep the CP's refnotify (/dev/spipe_nr8) and SRMI (/dev/spipe_nr15) channels read, the part of the stock
 * refnotify daemon and IMS framework the CP depends on.
 *
 *   u30pro-refnotifyd [--ref /dev/spipe_nr8] [--srmi /dev/spipe_nr15]
 *
 * The CP writes both channels with an unbounded wait, so a ring nobody reads eventually blocks the sending task for good (the same
 * failure as the NV channel, see u30pro_nvsyncd.c and modem-native-re/docs/cp-diskserver-re.md section 2).
 *   refnotify: frames {u32 cmd, u32 len (with the head)} + payload.  3 (time) and 4 (date) are answered from the system clock in UTC,
 *              zone 0, the way the stock RefNotify_DoGetTime / date handler format them; everything else is only logged.  In
 *              particular 5 (stock: reboot into the download mode) and 8 (stock: reboot) are never acted on.
 *   SRMI:      the VoWiFi service registration of the CP (stock reader: the IMS app).  Read, logged, dropped; no reply.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static volatile sig_atomic_t quitting;
static void on_signal(int n) { (void)n; quitting = 1; }
static const char *ref_path = "/dev/spipe_nr8", *srmi_path = "/dev/spipe_nr15";

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static double uptime(void) { struct timespec t; clock_gettime(CLOCK_BOOTTIME, &t); return t.tv_sec + t.tv_nsec / 1e9; }

/* the SIPC pipe of an empty ring answers a non-blocking read with ENODATA, a ring another reader holds with EBUSY */
static int quiet_errno(int e) { return e == EAGAIN || e == EINTR || e == ENODATA || e == EBUSY; }

static int open_dev(const char *path)
{
	int fd = open(path, O_RDWR | O_NOCTTY | O_CLOEXEC | O_NONBLOCK);
	if (fd < 0) return -1;
	struct termios t;
	if (isatty(fd) && tcgetattr(fd, &t) == 0) { cfmakeraw(&t); tcsetattr(fd, TCSANOW, &t); }   /* the offline test feeds ptys */
	return fd;
}

static void reply(int fd, const uint8_t *b, size_t n, uint32_t cmd)
{
	ssize_t w = write(fd, b, n);
	if (w != (ssize_t)n) printf("%.3f refnotify reply to cmd %u failed: %s\n", uptime(), cmd, w < 0 ? strerror(errno) : "short write");
}

/* one complete refnotify frame */
static void ref_frame(int fd, const uint8_t *f, uint32_t len)
{
	uint32_t cmd = le32(f);
	time_t now = time(NULL);
	struct tm tm;
	gmtime_r(&now, &tm);
	if (cmd == 3) {
		uint8_t r[12];
		put32(r, 3); put32(r + 4, sizeof r);
		r[8] = (uint8_t)tm.tm_sec; r[9] = (uint8_t)tm.tm_min; r[10] = (uint8_t)tm.tm_hour; r[11] = 0;
		reply(fd, r, sizeof r, cmd);
		printf("%.3f refnotify cmd 3 (time): answered %02d:%02d:%02d UTC\n", uptime(), tm.tm_hour, tm.tm_min, tm.tm_sec);
	} else if (cmd == 4) {
		uint8_t r[14];
		int year = tm.tm_year + 1900;
		put32(r, 4); put32(r + 4, sizeof r);
		r[8] = (uint8_t)tm.tm_mday; r[9] = (uint8_t)(tm.tm_mon + 1); r[10] = (uint8_t)year; r[11] = (uint8_t)(year >> 8);
		r[12] = (uint8_t)tm.tm_wday; r[13] = 0;
		reply(fd, r, sizeof r, cmd);
		printf("%.3f refnotify cmd 4 (date): answered %04d-%02d-%02d\n", uptime(), year, tm.tm_mon + 1, tm.tm_mday);
	} else {
		printf("%.3f refnotify cmd %u len %u: logged only%s", uptime(), cmd, len,
		       cmd == 5 ? " (stock: reboot into the download mode; not done)" : cmd == 8 ? " (stock: reboot; not done)" : "");
		for (uint32_t i = 8; i < len && i < 40; i++) printf("%s%02x", i == 8 ? " payload " : " ", f[i]);
		printf("\n");
	}
}

int main(int argc, char **argv)
{
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--ref") && i + 1 < argc) ref_path = argv[++i];
		else if (!strcmp(argv[i], "--srmi") && i + 1 < argc) srmi_path = argv[++i];
		else { fprintf(stderr, "usage: u30pro-refnotifyd [--ref DEV] [--srmi DEV]\n"); return 2; }
	}
	setvbuf(stdout, NULL, _IOLBF, 0);
	struct sigaction sa = {.sa_handler = on_signal};
	sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL); signal(SIGPIPE, SIG_IGN);
	int ref = -1, srmi = -1;
	uint8_t rb[4096];
	size_t rhave = 0;
	unsigned long srmi_bytes = 0;
	while (!quitting) {
		if (ref < 0 && (ref = open_dev(ref_path)) >= 0) { rhave = 0; printf("%.3f open %s\n", uptime(), ref_path); }
		if (srmi < 0 && (srmi = open_dev(srmi_path)) >= 0) printf("%.3f open %s\n", uptime(), srmi_path);
		struct pollfd p[2] = {{ref, POLLIN, 0}, {srmi, POLLIN, 0}};
		if (poll(p, 2, ref < 0 || srmi < 0 ? 1000 : -1) < 0) { if (errno == EINTR) continue; break; }
		if (ref >= 0 && p[0].revents) {
			ssize_t n = read(ref, rb + rhave, sizeof rb - rhave);
			if (n > 0) {
				rhave += (size_t)n;
				for (;;) {
					if (rhave < 8) break;
					uint32_t len = le32(rb + 4);
					if (len < 8 || len > sizeof rb) {   /* lost the framing: drop everything buffered and start again */
						printf("%.3f refnotify bad length %u, %zu bytes dropped\n", uptime(), len, rhave);
						rhave = 0; break;
					}
					if (rhave < len) break;
					ref_frame(ref, rb, len);
					memmove(rb, rb + len, rhave - len); rhave -= len;
				}
			} else if (n == 0 || !quiet_errno(errno)) {
				printf("%.3f read %s: %s, reopening\n", uptime(), ref_path, n ? strerror(errno) : "end of file");
				close(ref); ref = -1; sleep(1);
			}
		}
		if (srmi >= 0 && p[1].revents) {
			uint8_t b[512];
			ssize_t n = read(srmi, b, sizeof b);
			if (n > 0) {
				srmi_bytes += (unsigned long)n;
				printf("%.3f srmi %zd bytes dropped (total %lu):", uptime(), n, srmi_bytes);
				for (ssize_t i = 0; i < n && i < 24; i++) printf(" %02x", b[i]);
				printf("\n");
			} else if (n == 0 || !quiet_errno(errno)) {
				printf("%.3f read %s: %s, reopening\n", uptime(), srmi_path, n ? strerror(errno) : "end of file");
				close(srmi); srmi = -1; sleep(1);
			}
		}
	}
	if (ref >= 0) close(ref);
	if (srmi >= 0) close(srmi);
	return 0;
}
