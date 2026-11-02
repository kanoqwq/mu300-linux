/*
 * logchan.c - the " log_chan=sblock,<dst>,<chan>,<smem>" parameters that the stock
 * modem_control appends to the cmdline it writes to the CP (modem type nr only).
 * Mirrors get_log_chan_param / append_log_chan_param / is_same_name / parse_hex_number.
 *
 * The values come from the device tree through sysfs:
 *   /sys/devices/platform/sipc-virt/sipc-virt:core@N/sipc-virt:core@N:channel@5/of_node/label
 *   ".../base_addr" containing "<dst> <chan> 0x<smem> ..." e.g. "5 5 0x8790B000 1 0 0 1000 2048"
 * Older trees expose "spool@..." nodes below /sys/devices/platform with of_node/sprd,name.
 */
#define _GNU_SOURCE
#include "mon.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int read_small(const char *path, char *buf, size_t max)
{
	int fd = open(path, O_RDONLY);
	ssize_t n;

	if (fd < 0)
		return -1;
	n = read(fd, buf, max);
	close(fd);
	if (n <= 0)
		return -1;
	buf[n] = 0;
	return (int)n;
}

/* is_same_name: prefix compare of the file content (at most 0x1f bytes) with `label` */
static int label_matches(const char *path, const char *label)
{
	char b[0x20];
	int n = read_small(path, b, 0x1f);
	size_t l = strlen(label);

	return n > 0 && l <= (size_t)n && memcmp(label, b, l) == 0;
}

static int is_dir(const char *p)
{
	struct stat st;

	return lstat(p, &st) == 0 && S_ISDIR(st.st_mode);
}

static int tok_dec(const char **p, const char *end, unsigned *v)
{
	unsigned acc = 0;
	const char *s;

	while (*p < end && (**p == ' ' || **p == '\t' || **p == '\n' || **p == '\r'))
		(*p)++;
	s = *p;
	while (*p < end && **p >= '0' && **p <= '9') {
		acc = acc * 10 + (unsigned)(**p - '0');
		(*p)++;
	}
	if (*p == s || (*p < end && !(**p == ' ' || **p == '\t' || **p == '\n' || **p == '\r' || **p == 0)))
		return -1;
	*v = acc;
	return 0;
}

/* parse_hex_number: at most 8 hex digits (stock rejects anything that overflows 32 bits) */
static int parse_hex(const char *s, size_t len, uint32_t *v)
{
	uint32_t acc = 0;

	for (; len; len--, s++) {
		unsigned d;
		if (acc >> 28)
			return -1;
		if (*s >= '0' && *s <= '9') d = (unsigned)(*s - '0');
		else if (*s >= 'a' && *s <= 'f') d = (unsigned)(*s - 'a' + 10);
		else if (*s >= 'A' && *s <= 'F') d = (unsigned)(*s - 'A' + 10);
		else return -1;
		acc = (acc << 4) | d;
	}
	*v = acc;
	return 0;
}

static int read_base_addr(const char *chan_dir, const char *label, struct mon_log_chan *out)
{
	char p[512], b[0x40];
	const char *c, *end, *t;
	int n;
	unsigned dst, chan;
	uint32_t smem;
	size_t l;

	snprintf(p, sizeof p, "%s/base_addr", chan_dir);
	n = read_small(p, b, 0x3f);
	if (n <= 0) {
		fprintf(stderr, "MODEM_CTRL: read %s err, %s\n", p, strerror(errno));
		return -1;
	}
	c = b;
	end = b + n;
	if (tok_dec(&c, end, &dst) || tok_dec(&c, end, &chan))
		return -1;
	while (c < end && (*c == ' ' || *c == '\t'))
		c++;
	t = c;
	while (c < end && !(*c == ' ' || *c == '\t' || *c == '\n' || *c == '\r' || *c == 0))
		c++;
	l = (size_t)(c - t);
	if (l <= 2 || t[0] != '0' || (t[1] | 0x20) != 'x' || parse_hex(t + 2, l - 2, &smem))
		return -1;
	(void)label;
	out->dst = dst;
	out->chan = chan;
	out->smem = smem;
	return 0;
}

int mon_get_log_chan(const char *sysroot, const char *label, struct mon_log_chan *out)
{
	char plat[400], sipc[420], ent[1000], lab[1700], chan_dir[1600];
	DIR *d;
	struct dirent *e;
	int rc = -1;

	if (!sysroot)
		sysroot = "";
	snprintf(plat, sizeof plat, "%s/sys/devices/platform", sysroot);
	snprintf(sipc, sizeof sipc, "%s/sipc-virt", plat);

	d = opendir(sipc);
	if (d) {
		while ((e = readdir(d))) {
			if (strncmp(e->d_name, "sipc-virt:core@", 15) != 0 || strlen(e->d_name) < 15)
				continue;
			snprintf(ent, sizeof ent, "%s/%s", sipc, e->d_name);
			if (!is_dir(ent))
				continue;
			snprintf(chan_dir, sizeof chan_dir, "%s/%s:channel@5", ent, e->d_name);
			snprintf(lab, sizeof lab, "%s/of_node/label", chan_dir);
			if (!label_matches(lab, label))
				continue;
			rc = read_base_addr(chan_dir, label, out);
			break;
		}
		closedir(d);
		return rc;
	}

	d = opendir(plat);
	if (!d) {
		fprintf(stderr, "MODEM_CTRL: open %s err, %s\n", plat, strerror(errno));
		return -1;
	}
	while ((e = readdir(d))) {
		if (strlen(e->d_name) <= 5 || strncmp(e->d_name, "spool@", 6) != 0)
			continue;
		snprintf(ent, sizeof ent, "%s/%s", plat, e->d_name);
		if (!is_dir(ent))
			continue;
		snprintf(lab, sizeof lab, "%s/of_node/sprd,name", ent);
		if (!label_matches(lab, label))
			continue;
		rc = read_base_addr(ent, label, out);
		break;
	}
	closedir(d);
	return rc;
}

int mon_append_log_chan(const char *sysroot, char *buf, size_t size)
{
	struct mon_log_chan a, b;
	char tmp[128];
	size_t cur = strlen(buf);
	int n1, n2;

	if (mon_get_log_chan(sysroot, "slog_nr", &a) < 0) {
		fprintf(stderr, "MODEM_CTRL: Can not get PSCP log channel param\n");
		return -1;
	}
	n1 = snprintf(tmp, sizeof tmp, " log_chan=sblock,%u,%u,%08X", a.dst, a.chan, a.smem);
	if (mon_get_log_chan(sysroot, "slog_phy", &b) < 0) {
		fprintf(stderr, "MODEM_CTRL: Can not get PHY log channel param\n");
		return -1;
	}
	n2 = snprintf(tmp + n1, sizeof tmp - (size_t)n1, " log_chan=sblock,%u,%u,%08X", b.dst, b.chan, b.smem);
	if (cur + (size_t)n1 + (size_t)n2 + 1 > size)
		return -1;
	memcpy(buf + cur, tmp, (size_t)(n1 + n2) + 1);
	return n1 + n2;
}
