/* util.c — logging and kernel cmdline / bootconfig parsing. */
#include "mc.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static int g_level = MC_LOG_INFO;

void mc_set_log_level(int prio) { g_level = prio; }

void mc_log(int prio, const char *fmt, ...)
{
    if (prio > g_level)
        return;
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    char line[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(line, sizeof line, fmt, ap);
    va_end(ap);
    fprintf(stderr, "[%5ld.%03ld] MODEM_CTRL: %s%s", (long)ts.tv_sec, ts.tv_nsec / 1000000, line,
            line[0] && line[strlen(line) - 1] == '\n' ? "" : "\n");
}

int mc_read_file(const char *path, void *buf, size_t cap)
{
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -errno;
    size_t got = 0;
    while (got < cap) {
        ssize_t r = read(fd, (char *)buf + got, cap - got);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            int e = errno;
            close(fd);
            return -e;
        }
        if (r == 0)
            break;
        got += (size_t)r;
    }
    close(fd);
    return (int)got;
}

/* Find the whitespace-delimited token "key=..." and copy the value.  The original used
 * strstr(), which also matches substrings of other tokens; token matching is stricter but
 * yields the same result for every key this program asks for. */
int mc_cmdline_get(const char *text, const char *key, char *val, size_t n)
{
    size_t kl = strlen(key);
    const char *p = text;
    while (*p) {
        while (*p == ' ' || *p == '\t' || *p == '\n')
            p++;
        const char *s = p;
        while (*p && *p != ' ' && *p != '\t' && *p != '\n')
            p++;
        if ((size_t)(p - s) > kl && !strncmp(s, key, kl) && s[kl] == '=') {
            size_t vl = (size_t)(p - s) - kl - 1;
            if (vl >= n)
                vl = n - 1;
            memcpy(val, s + kl + 1, vl);
            val[vl] = 0;
            return 0;
        }
    }
    return -ENOENT;
}

/* /proc/bootconfig lines look like:  androidboot.slot_suffix = "_a" */
int mc_bootconfig_get(const char *text, const char *key, char *val, size_t n)
{
    size_t kl = strlen(key);
    const char *p = text;
    while (*p) {
        const char *eol = strchr(p, '\n');
        size_t ll = eol ? (size_t)(eol - p) : strlen(p);
        if (ll > kl && !strncmp(p, key, kl) && (p[kl] == ' ' || p[kl] == '=')) {
            const char *q = memchr(p, '"', ll);
            if (q) {
                q++;
                const char *e = memchr(q, '"', ll - (size_t)(q - p));
                size_t vl = e ? (size_t)(e - q) : ll - (size_t)(q - p);
                if (vl >= n)
                    vl = n - 1;
                memcpy(val, q, vl);
                val[vl] = 0;
                return 0;
            }
        }
        if (!eol)
            break;
        p = eol + 1;
    }
    return -ENOENT;
}
