/*
 * mu300-keys: the device's buttons as lines of text, for mu300-buttons (a shell script: OpenWrt has no Python and
 * a pipe from od is block-buffered, so the events came too late).
 *
 * Watches every /dev/input/event* that has keys and prints, flushed at once:
 *   <code> short      released before the long-press time
 *   <code> long       still held when the long-press time is reached (once; nothing on its release)
 *   0 tick            every -t seconds (the LED timeout of mu300-buttons runs on these)
 * Codes are Linux key codes: 116 KEY_POWER, 138 the U30 Air's Wi-Fi key, 114/115 volume.
 *
 * usage: mu300-keys [-l MS] [-t SEC]   (long press: MS milliseconds, default 3000; tick: none by default)
 * SPDX-License-Identifier: MIT
 */
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define MAXDEV 16
#define MAXKEY KEY_MAX

static long long now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000LL + ts.tv_nsec / 1000000;
}

static int has_keys(int fd)
{
	unsigned long ev = 0;
	if (ioctl(fd, EVIOCGBIT(0, sizeof(ev)), &ev) < 0)
		return 0;
	return (ev >> EV_KEY) & 1;
}

int main(int argc, char **argv)
{
	long long hold = 3000, tick = 0, next_tick = 0;
	static long long down[MAXKEY + 1];	/* press time, 0: not held */
	static char reported[MAXKEY + 1];	/* "long" already printed for this press */
	struct pollfd pfd[MAXDEV];
	int n = 0, opt;

	while ((opt = getopt(argc, argv, "l:t:")) != -1) {
		if (opt == 'l')
			hold = atoll(optarg);
		else if (opt == 't')
			tick = atoll(optarg) * 1000;
		else {
			fprintf(stderr, "usage: mu300-keys [-l MS] [-t SEC]\n");
			return 2;
		}
	}

	DIR *d = opendir("/dev/input");
	struct dirent *e;
	while (d && (e = readdir(d)) && n < MAXDEV) {
		char path[300];
		if (strncmp(e->d_name, "event", 5))
			continue;
		snprintf(path, sizeof(path), "/dev/input/%s", e->d_name);
		int fd = open(path, O_RDONLY | O_CLOEXEC);
		if (fd < 0)
			continue;
		if (!has_keys(fd)) {
			close(fd);
			continue;
		}
		pfd[n].fd = fd;
		pfd[n].events = POLLIN;
		n++;
	}
	if (d)
		closedir(d);
	if (!n) {
		fprintf(stderr, "mu300-keys: no input device with keys\n");
		return 1;
	}
	setvbuf(stdout, NULL, _IOLBF, 0);
	if (tick > 0)
		next_tick = now_ms() + tick;

	for (;;) {
		/* wake up in time to report a long press while the key is still held */
		int timeout = -1;
		long long t = now_ms();
		for (int k = 0; k <= MAXKEY; k++) {
			if (down[k] && !reported[k]) {
				long long left = down[k] + hold - t;
				if (left < 0)
					left = 0;
				if (timeout < 0 || left < timeout)
					timeout = (int)left;
			}
		}
		if (tick > 0) {
			long long left = next_tick - t;
			if (left < 0)
				left = 0;
			if (timeout < 0 || left < timeout)
				timeout = (int)left;
		}
		if (poll(pfd, n, timeout) < 0 && errno != EINTR)
			return 1;
		t = now_ms();
		if (tick > 0 && t >= next_tick) {
			printf("0 tick\n");
			next_tick = t + tick;
		}
		for (int k = 0; k <= MAXKEY; k++) {
			if (down[k] && !reported[k] && t - down[k] >= hold) {
				printf("%d long\n", k);
				reported[k] = 1;
			}
		}
		for (int i = 0; i < n; i++) {
			if (!(pfd[i].revents & POLLIN))
				continue;
			struct input_event ev[16];
			ssize_t r = read(pfd[i].fd, ev, sizeof(ev));
			if (r <= 0)
				continue;
			for (int j = 0; j < (int)(r / sizeof(ev[0])); j++) {
				int k = ev[j].code;
				if (ev[j].type != EV_KEY || k > MAXKEY)
					continue;
				if (ev[j].value == 1) {		/* press */
					down[k] = t;
					reported[k] = 0;
				} else if (ev[j].value == 0 && down[k]) {	/* release */
					if (!reported[k])
						printf("%d short\n", k);
					down[k] = 0;
				}
			}
		}
	}
}
