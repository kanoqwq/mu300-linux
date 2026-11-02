/* u30pro-atc (test client): the socket transport of u30pro-atd built with -DATD_TEST (tests/test_atd.py); on the device u30pro-atc is a ucode
 * script that calls the ubus object u30pro.at.   u30pro-atc [-s sock] [-t ms] "AT..." | "!status" | "!urc"
 * Prints the broker's JSON reply (add -p for the plain response lines).  Exit 0 when the command returned OK. */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	const char *sock = "/run/u30pro-modem/atd.sock";
	long timeout = 0;
	int opt, plain = 0, fd;
	struct sockaddr_un a = { .sun_family = AF_UNIX };
	char req[1024], buf[65536];
	size_t used = 0;
	ssize_t r;

	while ((opt = getopt(argc, argv, "s:t:p")) != -1) {
		if (opt == 's')
			sock = optarg;
		else if (opt == 't')
			timeout = atol(optarg);
		else if (opt == 'p')
			plain = 1;
		else
			return 2;
	}
	if (optind + 1 != argc || strlen(sock) >= sizeof(a.sun_path) || strlen(argv[optind]) > 900)
		return 2;
	if (timeout && argv[optind][0] != '!')
		snprintf(req, sizeof(req), "T%ld %s\n", timeout, argv[optind]);
	else
		snprintf(req, sizeof(req), "%s\n", argv[optind]);
	fd = socket(AF_UNIX, SOCK_STREAM, 0);
	strcpy(a.sun_path, sock);
	if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof(a))) {
		perror("u30pro-atd");
		return 3;
	}
	if (write(fd, req, strlen(req)) < 0)
		return 3;
	while ((r = read(fd, buf + used, sizeof(buf) - 1 - used)) > 0)
		used += (size_t)r;
	buf[used] = 0;
	if (!plain) {
		fputs(buf, stdout);
	} else {
		/* crude: print every "..." element of the lines array, one per line */
		char *p = strstr(buf, "\"lines\":[");

		if (p) {
			p += 9;
			while (*p && *p != ']') {
				if (*p == '"') {
					p++;
					while (*p && *p != '"') {
						if (*p == '\\' && p[1])
							p++;
						putchar(*p++);
					}
					putchar('\n');
				}
				p++;
			}
		}
	}
	return strstr(buf, "\"ok\":true") ? 0 : 1;
}
