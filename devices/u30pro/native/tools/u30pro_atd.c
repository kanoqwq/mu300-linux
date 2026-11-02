/*
 * u30pro-atd: serialising AT command broker for the native baseband, ubus object "u30pro.at".
 *
 *   u30pro-atd [-p /dev/stty_nr2]
 *
 * It owns one modem AT channel (flock, like u30pro-celld which keeps /dev/stty_nr0 for the data session) and executes
 * one command at a time on behalf of its clients (u30pro-atc, the rpcd plugin, the LuCI AT console, the screen feed).
 *
 * ubus methods of u30pro.at:
 *     command {"cmd": "AT...", "timeout": ms}    run a command, default timeout 5000 ms (200 .. 600000)
 *     sms {"len": n, "pdu": "hex", "timeout": ms} send one SMS-SUBMIT PDU: AT+CMGS=<len>, wait for the '>' prompt, PDU + Ctrl-Z
 *     status {}                                  broker state, counters and wedge flag
 *     urc {}                                     unsolicited result codes seen since start (ring of 256 lines)
 * Reply of command and sms:
 *     {"ok":true,"status":"OK","lines":["+CSQ: 31,13"],"ms":23}
 *     status is OK, ERROR, "CME ERROR: n", "CMS ERROR: n", NO CARRIER, timeout, invalid (empty request) or a broker error.
 * Event: "u30pro.at.urc" {"t": unix time, "line": "..."} for every unsolicited line.
 *
 * Commands run one after the other: a request that arrives while one runs waits in ubus.  No command filter: every command
 * is passed to the modem exactly as typed (it ends at the first CR or LF).  Only an empty request is answered without sending
 * anything.  The broker pings the modem with "AT" while idle; three missed pings set the wedge flag shown by status.
 *
 * Built with -DATD_TEST (unit tests on a pty, tests/test_atd.py) the transport is a unix socket instead of ubus, one request
 * line per connection: [T<timeout_ms> ]AT... | !status | !urc | !sms T<ms> <len> <hexpdu>, the reply the same JSON object.
 */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/un.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#ifndef ATD_TEST
#include <libubox/uloop.h>
#include <libubox/blobmsg.h>
#include <libubus.h>
#endif

#define MAXLINE 2048
#ifdef ATD_TEST
#define PING_MS 600   /* unit tests: any pty, fast idle ping */
#else
#define PING_MS 20000
#endif
#define URC_RING 256
#define MAX_LINES 400

static char port[128] = "/dev/stty_nr2";
static int mfd = -1;
static unsigned served, rejected, timeouts, missed;
static int wedged;
static long next_ping;
static char urc[URC_RING][400];
static unsigned urc_head, urc_count;
static time_t urc_time[URC_RING];

static long now_ms(void)
{
	struct timespec t;

	clock_gettime(CLOCK_MONOTONIC, &t);
	return t.tv_sec * 1000L + t.tv_nsec / 1000000;
}

/* where the response lines of a command go: a JSON array (test transport) or a blobmsg array (ubus) */
struct sink {
	FILE *json;
#ifndef ATD_TEST
	struct blob_buf *b;
#endif
	int n;
};

static void json_str(FILE *f, const char *s)
{
	fputc('"', f);
	for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
		if (*p == '"' || *p == '\\')
			fprintf(f, "\\%c", *p);
		else if (*p < 32)
			fprintf(f, "\\u%04x", *p);
		else
			fputc(*p, f);
	}
	fputc('"', f);
}

static void sink_add(struct sink *s, const char *line)
{
	if (!s || s->n >= MAX_LINES)
		return;
	if (s->json) {
		if (s->n)
			fputc(',', s->json);
		json_str(s->json, line);
	}
#ifndef ATD_TEST
	else if (s->b) {
		blobmsg_add_string(s->b, NULL, line);
	}
#endif
	s->n++;
}

static void urc_event(const char *line, time_t t);

static void keep_urc(const char *line)
{
	unsigned i = (urc_head + urc_count) % URC_RING;

	if (urc_count == URC_RING) {
		urc_head = (urc_head + 1) % URC_RING;
		i = (urc_head + URC_RING - 1) % URC_RING;
	} else {
		urc_count++;
	}
	snprintf(urc[i], sizeof(urc[i]), "%.399s", line);
	urc_time[i] = time(NULL);
	urc_event(urc[i], urc_time[i]);
}

static const char *empty_request(const char *cmd)
{
	return *cmd ? NULL : "empty command";
}

static int final_status(const char *line, char *status, size_t len)
{
	if (!strcmp(line, "OK")) {
		snprintf(status, len, "OK");
		return 1;
	}
	if (!strncmp(line, "ERROR", 5) || !strncmp(line, "NO CARRIER", 10) || !strncmp(line, "NO ANSWER", 9) ||
	    !strncmp(line, "BUSY", 4) || !strncmp(line, "NO DIALTONE", 11)) {
		snprintf(status, len, "%s", line);
		return 1;
	}
	if (!strncmp(line, "+CME ERROR", 10) || !strncmp(line, "+CMS ERROR", 10)) {
		snprintf(status, len, "%s", line + 1);
		return 1;
	}
	return 0;
}

static int send_line(const char *cmd)
{
	char b[MAXLINE + 4];
	size_t n = (size_t)snprintf(b, sizeof(b), "%s\r", cmd), pos = 0;

	while (pos < n) {
		ssize_t w = write(mfd, b + pos, n - pos);

		if (w > 0) {
			pos += (size_t)w;
		} else if (w < 0 && (errno == EAGAIN || errno == EINTR)) {
			struct pollfd p = { mfd, POLLOUT, 0 };

			poll(&p, 1, 100);
		} else {
			return -1;
		}
	}
	return 0;
}

/* Run one command; the response lines (without the echo) go to the sink. Returns 0 if a final result code was seen. */
static int run_at(const char *cmd, long timeout, char *status, size_t slen, struct sink *lines)
{
	char acc[MAXLINE * 2];
	size_t used = 0;
	long deadline = now_ms() + timeout;

	snprintf(status, slen, "timeout");
	if (send_line(cmd)) {
		snprintf(status, slen, "write-failed");
		return -1;
	}
	while (now_ms() < deadline) {
		struct pollfd p = { mfd, POLLIN, 0 };
		ssize_t r;
		char *nl;

		if (poll(&p, 1, 100) <= 0)
			continue;
		r = read(mfd, acc + used, sizeof(acc) - 1 - used);
		if (r <= 0)
			continue;
		used += (size_t)r;
		acc[used] = 0;
		while ((nl = strpbrk(acc, "\r\n"))) {
			size_t len = (size_t)(nl - acc);
			char line[MAXLINE];

			if (len >= sizeof(line))
				len = sizeof(line) - 1;
			memcpy(line, acc, len);
			line[len] = 0;
			used -= (size_t)(nl - acc) + 1;
			memmove(acc, nl + 1, used + 1);
			if (!*line)
				continue;
			if (!strcasecmp(line, cmd))
				continue; /* echo */
			if (final_status(line, status, slen))
				return 0;
			sink_add(lines, line);
		}
		if (used >= sizeof(acc) - 2)
			used = 0;
	}
	return -1;
}

static void drain_idle(void)
{
	char b[1024];
	static char pending[MAXLINE];
	static size_t plen;
	ssize_t r;

	while ((r = read(mfd, b, sizeof(b))) > 0) {
		for (ssize_t i = 0; i < r; i++) {
			if (b[i] == '\r' || b[i] == '\n') {
				if (plen) {
					pending[plen] = 0;
					keep_urc(pending);
					plen = 0;
				}
			} else if (plen + 1 < sizeof(pending)) {
				pending[plen++] = b[i];
			}
		}
	}
}

static int open_modem(void)
{
	struct termios t;

	mfd = open(port, O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
	if (mfd < 0 || flock(mfd, LOCK_EX | LOCK_NB)) {
		if (mfd >= 0)
			close(mfd);
		mfd = -1;
		return -1;
	}
	if (!tcgetattr(mfd, &t)) {
		cfmakeraw(&t);
		t.c_cflag |= CLOCAL | CREAD;
		t.c_cflag &= ~CRTSCTS;
		tcsetattr(mfd, TCSANOW, &t);
	}
	tcflush(mfd, TCIOFLUSH);
	return 0;
}

/* AT+CMGS in PDU mode: the command is followed by a "> " prompt, then the PDU and Ctrl-Z.  Returns 0 on a final result code. */
static int run_sms_send(int len, const char *hex, long timeout, char *status, size_t slen, struct sink *lines)
{
	char cmd[64], acc[MAXLINE];
	size_t used = 0;
	long deadline;
	int prompted = 0;

	snprintf(status, slen, "timeout");
	snprintf(cmd, sizeof(cmd), "AT+CMGS=%d", len);
	drain_idle();
	if (send_line(cmd)) {
		snprintf(status, slen, "write-failed");
		return -1;
	}
	deadline = now_ms() + 4000;
	while (now_ms() < deadline && !prompted) {
		struct pollfd p = { mfd, POLLIN, 0 };
		ssize_t r;

		if (poll(&p, 1, 100) <= 0)
			continue;
		r = read(mfd, acc + used, sizeof(acc) - 1 - used);
		if (r <= 0)
			continue;
		used += (size_t)r;
		acc[used] = 0;
		if (strchr(acc, '>'))
			prompted = 1;
		else if (strstr(acc, "ERROR")) {
			snprintf(status, slen, "ERROR");
			return -1;
		}
	}
	if (!prompted) {
		snprintf(status, slen, "no-prompt");
		return -1;
	}
	{
		char body[MAXLINE + 4];
		size_t n = (size_t)snprintf(body, sizeof(body), "%s%c", hex, 0x1a), pos = 0;

		while (pos < n) {
			ssize_t w = write(mfd, body + pos, n - pos);

			if (w > 0) {
				pos += (size_t)w;
			} else if (w < 0 && (errno == EAGAIN || errno == EINTR)) {
				struct pollfd p = { mfd, POLLOUT, 0 };

				poll(&p, 1, 100);
			} else {
				snprintf(status, slen, "write-failed");
				return -1;
			}
		}
	}
	deadline = now_ms() + timeout;
	used = 0;
	while (now_ms() < deadline) {
		struct pollfd p = { mfd, POLLIN, 0 };
		ssize_t r;
		char *nl;

		if (poll(&p, 1, 100) <= 0)
			continue;
		r = read(mfd, acc + used, sizeof(acc) - 1 - used);
		if (r <= 0)
			continue;
		used += (size_t)r;
		acc[used] = 0;
		while ((nl = strpbrk(acc, "\r\n"))) {
			size_t l = (size_t)(nl - acc);
			char line[MAXLINE];

			if (l >= sizeof(line))
				l = sizeof(line) - 1;
			memcpy(line, acc, l);
			line[l] = 0;
			used -= (size_t)(nl - acc) + 1;
			memmove(acc, nl + 1, used + 1);
			if (!*line)
				continue;
			if (final_status(line, status, slen))
				return 0;
			sink_add(lines, line);
		}
		if (used >= sizeof(acc) - 2)
			used = 0;
	}
	return -1;
}

static int sms_request_ok(int len, const char *hex)
{
	size_t n = strlen(hex);

	return len >= 7 && len <= 200 && strspn(hex, "0123456789abcdefABCDEF") == n && n >= 14 && n <= 400;
}

/* one command for a client; updates the counters.  Returns the run_at result. */
static int client_command(const char *cmd, long timeout, char *status, size_t slen, struct sink *lines)
{
	int rc;

	drain_idle();
	rc = run_at(cmd, timeout, status, slen, lines);
	served++;
	next_ping = now_ms() + PING_MS;
	if (rc && !strcmp(status, "timeout")) {
		timeouts++;
	} else if (!rc) {
		missed = 0;
		wedged = 0;
	}
	return rc;
}

static void idle_ping(void)
{
	char st[96];

	drain_idle();
	if (run_at("AT", 1500, st, sizeof(st), NULL) || strcmp(st, "OK")) {
		if (++missed >= 3)
			wedged = 1;
	} else {
		missed = 0;
		wedged = 0;
	}
	next_ping = now_ms() + PING_MS;
}

#ifdef ATD_TEST
/* ------------------------------------------------------------------ test transport: unix socket */
static volatile sig_atomic_t quitting;
static char sockpath[256] = "/run/u30pro-modem/atd.sock";

static void urc_event(const char *line, time_t t) { (void)line; (void)t; }

static void on_signal(int n)
{
	(void)n;
	quitting = 1;
}

static void serve(int c)
{
	char req[MAXLINE], status[96];
	ssize_t n;
	long timeout = 5000;
	char *cmd;
	FILE *f = fdopen(dup(c), "w");
	const char *why;

	n = read(c, req, sizeof(req) - 1);
	if (n <= 0 || !f) {
		if (f)
			fclose(f);
		return;
	}
	req[n] = 0;
	req[strcspn(req, "\r\n")] = 0;
	cmd = req;
	if (!strcmp(cmd, "!status")) {
		fprintf(f, "{\"ok\":true,\"port\":");
		json_str(f, port);
		fprintf(f, ",\"wedged\":%s,\"served\":%u,\"rejected\":%u,\"timeouts\":%u,\"missed_pings\":%u,\"urc_buffered\":%u}\n",
			wedged ? "true" : "false", served, rejected, timeouts, missed, urc_count);
		fclose(f);
		return;
	}
	if (!strcmp(cmd, "!urc")) {
		fprintf(f, "{\"ok\":true,\"urc\":[");
		for (unsigned i = 0; i < urc_count; i++) {
			unsigned k = (urc_head + i) % URC_RING;

			if (i)
				fputc(',', f);
			fprintf(f, "{\"t\":%ld,\"line\":", (long)urc_time[k]);
			json_str(f, urc[k]);
			fputc('}', f);
		}
		fprintf(f, "]}\n");
		fclose(f);
		return;
	}
	if (!strncmp(cmd, "!sms ", 5)) {
		long t = 60000;
		int len = -1, rc;
		char hex[MAXLINE];
		char *lines_buf = NULL;
		size_t lines_len = 0;
		struct sink s = { 0 };
		const char *q = cmd + 5;
		long t0 = now_ms();

		if ((*q == 'T' || *q == 't') && isdigit((unsigned char)q[1])) {
			char *e;

			t = strtol(q + 1, &e, 10);
			q = e;
		}
		while (*q == ' ')
			q++;
		if (t < 5000 || t > 180000)
			t = 60000;
		if (sscanf(q, "%d %1900s", &len, hex) != 2 || !sms_request_ok(len, hex)) {
			fprintf(f, "{\"ok\":false,\"status\":\"invalid\",\"error\":\"bad SMS request\",\"lines\":[]}\n");
			fclose(f);
			return;
		}
		s.json = open_memstream(&lines_buf, &lines_len);
		rc = run_sms_send(len, hex, t, status, sizeof(status), &s);
		fclose(s.json);
		served++;
		next_ping = now_ms() + PING_MS;
		fprintf(f, "{\"ok\":%s,\"status\":", !rc && !strcmp(status, "OK") ? "true" : "false");
		json_str(f, status);
		fprintf(f, ",\"lines\":[%s],\"ms\":%ld}\n", lines_buf ? lines_buf : "", now_ms() - t0);
		free(lines_buf);
		fclose(f);
		return;
	}
	if ((*cmd == 'T' || *cmd == 't') && isdigit((unsigned char)cmd[1])) {
		timeout = strtol(cmd + 1, &cmd, 10);
		while (*cmd == ' ')
			cmd++;
		if (timeout < 200 || timeout > 600000)
			timeout = 5000;
	}
	why = empty_request(cmd);
	if (why) {
		rejected++;
		fprintf(f, "{\"ok\":false,\"status\":\"invalid\",\"error\":");
		json_str(f, why);
		fprintf(f, ",\"lines\":[]}\n");
		fclose(f);
		return;
	}
	{
		char *lines_buf = NULL;
		size_t lines_len = 0;
		struct sink s = { 0 };
		int rc;
		long t0 = now_ms();

		s.json = open_memstream(&lines_buf, &lines_len);
		rc = client_command(cmd, timeout, status, sizeof(status), &s);
		fclose(s.json);
		fprintf(f, "{\"ok\":%s,\"status\":", !rc && !strcmp(status, "OK") ? "true" : "false");
		json_str(f, status);
		fprintf(f, ",\"lines\":[%s],\"ms\":%ld}\n", lines_buf ? lines_buf : "", now_ms() - t0);
		free(lines_buf);
	}
	fclose(f);
}

int main(int argc, char **argv)
{
	int opt, lfd;
	struct sockaddr_un a = { .sun_family = AF_UNIX };

	while ((opt = getopt(argc, argv, "p:s:")) != -1) {
		if (opt == 'p')
			snprintf(port, sizeof(port), "%s", optarg);
		else if (opt == 's')
			snprintf(sockpath, sizeof(sockpath), "%s", optarg);
		else
			return 2;
	}
	if (strlen(sockpath) >= sizeof(a.sun_path))
		return 2;
	signal(SIGTERM, on_signal);
	signal(SIGINT, on_signal);
	signal(SIGPIPE, SIG_IGN);
	if (open_modem()) {
		perror(port);
		return 1;
	}
	unlink(sockpath);
	lfd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
	strcpy(a.sun_path, sockpath);
	if (lfd < 0 || bind(lfd, (struct sockaddr *)&a, sizeof(a)) || chmod(sockpath, 0600) || listen(lfd, 8)) {
		perror("socket");
		return 1;
	}
	{
		char st[96];

		run_at("ATE0", 2000, st, sizeof(st), NULL);
	}
	next_ping = now_ms() + PING_MS;
	while (!quitting) {
		struct pollfd p[2] = { { lfd, POLLIN, 0 }, { mfd, POLLIN, 0 } };

		if (poll(p, 2, 1000) < 0 && errno != EINTR)
			break;
		if (p[1].revents & POLLIN)
			drain_idle();
		if (p[0].revents & POLLIN) {
			int c = accept4(lfd, NULL, NULL, SOCK_CLOEXEC);

			if (c >= 0) {
				struct timeval tv = { 2, 0 };

				setsockopt(c, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
				serve(c);
				close(c);
			}
		}
		if (now_ms() >= next_ping)
			idle_ping();
	}
	unlink(sockpath);
	return 0;
}

#else
/* ------------------------------------------------------------------ ubus transport */
static struct ubus_context *ctx;
static struct blob_buf b;

static void urc_event(const char *line, time_t t)
{
	static struct blob_buf e;

	if (!ctx)
		return;
	blob_buf_init(&e, 0);
	blobmsg_add_u64(&e, "t", (uint64_t)t);
	blobmsg_add_string(&e, "line", line);
	ubus_send_event(ctx, "u30pro.at.urc", e.head);
}

enum { C_CMD, C_TIMEOUT, __C_MAX };
static const struct blobmsg_policy command_policy[__C_MAX] = {
	[C_CMD] = { "cmd", BLOBMSG_TYPE_STRING },
	[C_TIMEOUT] = { "timeout", BLOBMSG_TYPE_INT32 },
};

enum { S_LEN, S_PDU, S_TIMEOUT, __S_MAX };
static const struct blobmsg_policy sms_policy[__S_MAX] = {
	[S_LEN] = { "len", BLOBMSG_TYPE_INT32 },
	[S_PDU] = { "pdu", BLOBMSG_TYPE_STRING },
	[S_TIMEOUT] = { "timeout", BLOBMSG_TYPE_INT32 },
};

static void reply_result(struct ubus_request_data *req, int rc, const char *status, const char *error, long t0, void *lines)
{
	blobmsg_close_array(&b, lines);
	blobmsg_add_u8(&b, "ok", !rc && !strcmp(status, "OK"));
	blobmsg_add_string(&b, "status", status);
	if (error)
		blobmsg_add_string(&b, "error", error);
	blobmsg_add_u32(&b, "ms", (uint32_t)(now_ms() - t0));
	ubus_send_reply(ctx, req, b.head);
}

static int m_command(struct ubus_context *c, struct ubus_object *obj, struct ubus_request_data *req, const char *method,
		     struct blob_attr *msg)
{
	struct blob_attr *tb[__C_MAX];
	char cmd[MAXLINE], status[96];
	long timeout = 5000, t0 = now_ms();
	struct sink s = { 0 };
	void *lines;
	int rc = -1;

	(void)c; (void)obj; (void)method;
	blobmsg_parse(command_policy, __C_MAX, tb, blob_data(msg), blob_len(msg));
	if (!tb[C_CMD])
		return UBUS_STATUS_INVALID_ARGUMENT;
	snprintf(cmd, sizeof(cmd), "%s", blobmsg_get_string(tb[C_CMD]));
	cmd[strcspn(cmd, "\r\n")] = 0;
	if (tb[C_TIMEOUT]) {
		timeout = (long)blobmsg_get_u32(tb[C_TIMEOUT]);
		if (timeout < 200 || timeout > 600000)
			timeout = 5000;
	}
	blob_buf_init(&b, 0);
	lines = blobmsg_open_array(&b, "lines");
	s.b = &b;
	if (empty_request(cmd)) {
		rejected++;
		snprintf(status, sizeof(status), "invalid");
		reply_result(req, -1, status, empty_request(cmd), t0, lines);
		return 0;
	}
	rc = client_command(cmd, timeout, status, sizeof(status), &s);
	reply_result(req, rc, status, NULL, t0, lines);
	return 0;
}

static int m_sms(struct ubus_context *c, struct ubus_object *obj, struct ubus_request_data *req, const char *method,
		 struct blob_attr *msg)
{
	struct blob_attr *tb[__S_MAX];
	char status[96];
	long timeout = 60000, t0 = now_ms();
	struct sink s = { 0 };
	void *lines;
	int rc;

	(void)c; (void)obj; (void)method;
	blobmsg_parse(sms_policy, __S_MAX, tb, blob_data(msg), blob_len(msg));
	blob_buf_init(&b, 0);
	lines = blobmsg_open_array(&b, "lines");
	if (!tb[S_LEN] || !tb[S_PDU] || !sms_request_ok((int)blobmsg_get_u32(tb[S_LEN]), blobmsg_get_string(tb[S_PDU]))) {
		reply_result(req, -1, "invalid", "bad SMS request", t0, lines);
		return 0;
	}
	if (tb[S_TIMEOUT]) {
		timeout = (long)blobmsg_get_u32(tb[S_TIMEOUT]);
		if (timeout < 5000 || timeout > 180000)
			timeout = 60000;
	}
	s.b = &b;
	rc = run_sms_send((int)blobmsg_get_u32(tb[S_LEN]), blobmsg_get_string(tb[S_PDU]), timeout, status, sizeof(status), &s);
	served++;
	next_ping = now_ms() + PING_MS;
	reply_result(req, rc, status, NULL, t0, lines);
	return 0;
}

static int m_status(struct ubus_context *c, struct ubus_object *obj, struct ubus_request_data *req, const char *method,
		    struct blob_attr *msg)
{
	(void)c; (void)obj; (void)method; (void)msg;
	blob_buf_init(&b, 0);
	blobmsg_add_u8(&b, "ok", 1);
	blobmsg_add_string(&b, "port", port);
	blobmsg_add_u8(&b, "wedged", wedged);
	blobmsg_add_u32(&b, "served", served);
	blobmsg_add_u32(&b, "rejected", rejected);
	blobmsg_add_u32(&b, "timeouts", timeouts);
	blobmsg_add_u32(&b, "missed_pings", missed);
	blobmsg_add_u32(&b, "urc_buffered", urc_count);
	ubus_send_reply(ctx, req, b.head);
	return 0;
}

static int m_urc(struct ubus_context *c, struct ubus_object *obj, struct ubus_request_data *req, const char *method,
		 struct blob_attr *msg)
{
	void *arr;

	(void)c; (void)obj; (void)method; (void)msg;
	drain_idle();
	blob_buf_init(&b, 0);
	blobmsg_add_u8(&b, "ok", 1);
	arr = blobmsg_open_array(&b, "urc");
	for (unsigned i = 0; i < urc_count; i++) {
		unsigned k = (urc_head + i) % URC_RING;
		void *o = blobmsg_open_table(&b, NULL);

		blobmsg_add_u64(&b, "t", (uint64_t)urc_time[k]);
		blobmsg_add_string(&b, "line", urc[k]);
		blobmsg_close_table(&b, o);
	}
	blobmsg_close_array(&b, arr);
	ubus_send_reply(ctx, req, b.head);
	return 0;
}

static const struct ubus_method methods[] = {
	UBUS_METHOD("command", m_command, command_policy),
	UBUS_METHOD("sms", m_sms, sms_policy),
	UBUS_METHOD_NOARG("status", m_status),
	UBUS_METHOD_NOARG("urc", m_urc),
};
static struct ubus_object_type object_type = UBUS_OBJECT_TYPE("u30pro.at", methods);
static struct ubus_object object = {
	.name = "u30pro.at", .type = &object_type, .methods = methods, .n_methods = ARRAY_SIZE(methods),
};

static void modem_readable(struct uloop_fd *u, unsigned int events)
{
	(void)u; (void)events;
	drain_idle();
}

static void ping_timer(struct uloop_timeout *t)
{
	if (now_ms() >= next_ping)
		idle_ping();
	uloop_timeout_set(t, 1000);
}

int main(int argc, char **argv)
{
	int opt;
	struct uloop_fd modem_fd = { .cb = modem_readable };
	struct uloop_timeout ping = { .cb = ping_timer };

	while ((opt = getopt(argc, argv, "p:s:")) != -1) {
		if (opt == 'p')
			snprintf(port, sizeof(port), "%s", optarg);
		else if (opt != 's')   /* -s: the socket of earlier versions, ignored */
			return 2;
	}
	if (strncmp(port, "/dev/stty_nr", 12))
		return 2;
	signal(SIGPIPE, SIG_IGN);
	if (open_modem()) {
		perror(port);
		return 1;
	}
	{
		char st[96];

		run_at("ATE0", 2000, st, sizeof(st), NULL);
	}
	uloop_init();
	ctx = ubus_connect(NULL);
	if (!ctx) {
		fprintf(stderr, "u30pro-atd: cannot connect to ubus\n");
		return 1;
	}
	ubus_add_uloop(ctx);
	if (ubus_add_object(ctx, &object)) {
		fprintf(stderr, "u30pro-atd: cannot register u30pro.at\n");
		return 1;
	}
	modem_fd.fd = mfd;
	uloop_fd_add(&modem_fd, ULOOP_READ);
	next_ping = now_ms() + PING_MS;
	uloop_timeout_set(&ping, 1000);
	uloop_run();
	ubus_free(ctx);
	uloop_done();
	return 0;
}
#endif
