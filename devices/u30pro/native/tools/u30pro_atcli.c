/*
 * u30pro-atcli: send one AT command to a native modem channel and print the complete response.
 *
 *   u30pro-atcli [-p /dev/stty_nrN] [-t timeout_ms] [-r] "AT+CMD"
 *
 * -r prints the raw bytes (default prints the response text without the echo).  Exit status: 0 OK, 1 ERROR/+CME/+CMS,
 * 2 usage, 3 timeout, 5 port busy / cannot open.
 *
 * The port is taken with flock(LOCK_EX|LOCK_NB), the same convention as u30pro-celld, so it never talks over the dialler.
 * Every command is sent exactly as typed; there is no filter.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static long now_ms(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000L + t.tv_nsec / 1000000;
}

static int final_line(const char *buf)
{
	/* returns 1 for OK-like, 2 for error-like, 0 otherwise; looks only at complete lines */
	const char *p = buf;

	while (*p) {
		const char *e = strpbrk(p, "\r\n");
		size_t len = e ? (size_t)(e - p) : strlen(p);

		if (len == 2 && !strncmp(p, "OK", 2))
			return 1;
		if (len >= 5 && !strncmp(p, "ERROR", 5))
			return 2;
		if (len >= 10 && !strncmp(p, "+CME ERROR", 10))
			return 2;
		if (len >= 10 && !strncmp(p, "+CMS ERROR", 10))
			return 2;
		if (len >= 10 && !strncmp(p, "NO CARRIER", 10))
			return 2;
		if (!e)
			break;
		p = e + strspn(e, "\r\n");
	}
	return 0;
}

int main(int argc, char **argv)
{
	const char *port = "/dev/stty_nr1";
	long timeout = 5000;
	int raw = 0, opt;
	char buf[65536], line[1024];
	size_t used = 0;
	int fd, state = 0;
	struct termios t;
	long deadline;

	while ((opt = getopt(argc, argv, "p:t:r")) != -1) {
		if (opt == 'p')
			port = optarg;
		else if (opt == 't')
			timeout = atol(optarg);
		else if (opt == 'r')
			raw = 1;
		else
			return 2;
	}
	if (optind + 1 != argc || strncmp(port, "/dev/stty_nr", 12) || timeout < 100 || timeout > 600000)
		return 2;
	if (!*argv[optind] || strlen(argv[optind]) > 1000)
		return 2;
	fd = open(port, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0 || flock(fd, LOCK_EX | LOCK_NB)) {
		perror(port);
		return 5;
	}
	if (!tcgetattr(fd, &t)) {
		cfmakeraw(&t);
		t.c_cflag |= CLOCAL | CREAD;
		t.c_cflag &= ~CRTSCTS;
		tcsetattr(fd, TCSANOW, &t);
	}
	tcflush(fd, TCIOFLUSH);
	snprintf(line, sizeof(line), "%s\r", argv[optind]);
	for (size_t sent = 0, n = strlen(line); sent < n;) {
		ssize_t w = write(fd, line + sent, n - sent);

		if (w > 0) {
			sent += (size_t)w;
		} else if (w < 0 && (errno == EAGAIN || errno == EINTR)) {
			struct pollfd p = { fd, POLLOUT, 0 };

			poll(&p, 1, 100);
		} else {
			perror("write");
			return 5;
		}
	}
	deadline = now_ms() + timeout;
	while (now_ms() < deadline && used < sizeof(buf) - 1) {
		struct pollfd p = { fd, POLLIN, 0 };
		ssize_t r;

		if (poll(&p, 1, 100) <= 0)
			continue;
		r = read(fd, buf + used, sizeof(buf) - 1 - used);
		if (r > 0) {
			used += (size_t)r;
			buf[used] = 0;
			state = final_line(buf);
			if (state)
				break;
		}
	}
	buf[used] = 0;
	if (raw) {
		for (size_t i = 0; i < used; i++) {
			unsigned char c = (unsigned char)buf[i];

			if (c == '\r' || c == '\n' || c == '\t' || (c >= 32 && c < 127))
				putchar(c);
			else
				printf("\\x%02x", c);
		}
	} else {
		/* drop the echo of the command and blank lines */
		char *save = NULL;

		for (char *l = strtok_r(buf, "\r\n", &save); l; l = strtok_r(NULL, "\r\n", &save)) {
			if (!strcmp(l, argv[optind]))
				continue;
			puts(l);
		}
	}
	return state == 1 ? 0 : state == 2 ? 1 : 3;
}
