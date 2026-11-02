/*
 * u30pro-nvsyncd: answer the CP's runtime NV sync requests on /dev/snv_nr, the part of the stock cp_diskserver that the CP waits for.
 *
 *   u30pro-nvsyncd [--port /dev/snv_nr] [--status FILE]
 *                  [--writeback=off|dry|on --out-dir DIR [--runtimenv1 /dev/mmcblk0p19] [--runtimenv2 /dev/mmcblk0p20]]
 *
 * The state (counters, write-back) is the ubus object "u30pro.nvsync", method status, served by a thread of its own so that the
 * answers to the CP never wait for it; --status FILE also writes it to a file (the offline tests).
 *
 * The CP sends its changed NV items as fixed 0x40014-byte units (0x14-byte head "VNMR", 0, grp, sub, size + a 0x40000-byte data area
 * of which the first size bytes are a stream of 16-byte tokens: HEAD group/sync, BODY n + n 12-byte dirty items, TAIL).  For every
 * unit it waits up to 5 s for a 0x24-byte reply "VNMR", 0, grp, sub, 1, result.  Nobody answering left the 0x40400-byte ring full
 * from the first seconds of a boot; a forced sync (RamDiskSync_Triger) then blocks its caller forever, which is how the MN task got
 * stuck ("MN_AL Task's Q full" asserts, AT+CGDCONT never answered).  See modem-native-re/docs/cp-diskserver-re.md.
 *
 * Every unit gets result 1 (success).  Nothing is written to any partition: the NV changes stay in the CP's RAM, as they did while no
 * daemon ran at all, but the CP clears its dirty table and lets the waiting task go on.  The tokens are logged (stdout) for a later
 * write-back.  Exit status: 2 usage; otherwise it runs until SIGTERM.
 * Run one instance only: the SIPC ring has one reader, and a second one waits for its lock in uninterruptible sleep.
 *
 * --writeback=dry (see modem-native-re/docs/nv-writeback.md) additionally does on every TAIL of a group 1/2 transaction what the
 * stock cp_diskserver does before it writes runtimenv1/2: each dirty item is read from the CP's fixnv region (/dev/modem), its length
 * and checksum are checked, it is copied into an in-memory image of runtimenv, the fragment header is recomputed and the spans the
 * stock program would write are applied to a copy of each partition.  The copies and a JSON report go to DIR; the partitions are only
 * ever opened read-only, after their PARTNAME / first sector / size in sysfs were checked.  Group 0 (factorynv) is never touched.
 * A failed check (or a missing out-dir) only switches the write-back off: the units are still answered.
 *
 * --writeback=on (user-authorised 2026-10-24, runtimenv1/2 only) writes what dry computes: runtimenv1 first, then runtimenv2 (the
 * loader prefers runtimenv2, so a power cut during either write leaves one valid copy), each one switched writable (BLKROSET 0) only
 * for the write and read-only again after it, fsync'd and read back with O_DIRECT; runtimenv2 is written only after runtimenv1 read
 * back identical to the image.  The unit that ends the transaction is answered result 1 only when both copies verified, else 0 (the
 * CP sends its accumulated dirty items again).  Before any write the kernel write windows for both whole partitions must be on the
 * command line (u30pro_rwx=...,661504+71680,733184+71680) and each partition must be 0x2300000 bytes; otherwise, and after three
 * failed transactions in a row, it falls back to dry behaviour (result 1, nothing written) and says so loudly.  Reports go to DIR.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>
#include <linux/fs.h>
#if __has_include(<libubus.h>)
#define NVSYNC_UBUS 1
#include <poll.h>
#include <pthread.h>
#include <libubox/blobmsg.h>
#include <libubus.h>
#endif

#define UNIT 0x40014u
#define HEAD 0x14u
#define MAGIC 0x524d4e56u   /* "VNMR" */

static volatile sig_atomic_t quitting;
static void on_signal(int n) { (void)n; quitting = 1; }

static const char *port = "/dev/snv_nr", *status_path;
static unsigned long units, replies, items, resyncs, reopens, bad_units;
static uint32_t last_grp, last_sub, last_size;

enum { WB_OFF, WB_DRY, WB_ON };
static int wb;
static const char *out_dir, *sysfs = "/sys", *ram_path;   /* ram_path: a file holding the fixnv region, for the offline test */
static const char *rt_path[2] = {"/dev/mmcblk0p19", "/dev/mmcblk0p20"};
static const char *const rt_name[2] = {"nr_runtimenv1", "nr_runtimenv2"};
/* Resolve named partitions from this boot's GPT, not a reference unit's LBAs. */
static unsigned long rt_lba[2];
static unsigned long txns, applied, skipped, wb_errors;
static const char *cmdline_path = "/proc/cmdline";   /* --cmdline FILE: offline test */
static int wb_stopped;                               /* --writeback=on fell back to dry behaviour */
static char wb_stop_reason[256];
static unsigned long writes_ok, write_failures;
static unsigned fail_run;

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint16_t le16(const uint8_t *p) { return (uint16_t)(p[0] | p[1] << 8); }
static void put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static double uptime(void) { struct timespec t; clock_gettime(CLOCK_BOOTTIME, &t); return t.tv_sec + t.tv_nsec / 1e9; }

static void save_status(void)
{
	char tmp[256];
	if (!status_path || snprintf(tmp, sizeof tmp, "%s.tmp", status_path) >= (int)sizeof tmp) return;
	FILE *f = fopen(tmp, "w");
	if (!f) return;
	fprintf(f, "{\"port\":\"%s\",\"units\":%lu,\"replies\":%lu,\"items\":%lu,\"bad_units\":%lu,\"resyncs\":%lu,\"reopens\":%lu,"
		"\"last_grp\":%u,\"last_sub\":%u,\"last_size\":%u,\"writes_partitions\":%s",
		port, units, replies, items, bad_units, resyncs, reopens, last_grp, last_sub, last_size, wb == WB_ON && !wb_stopped ? "true" : "false");
	if (wb != WB_OFF)
		fprintf(f, ",\"writeback\":\"%s\",\"out_dir\":\"%s\",\"txns\":%lu,\"applied\":%lu,\"skipped\":%lu,\"wb_errors\":%lu",
			wb == WB_ON ? "on" : "dry", out_dir, txns, applied, skipped, wb_errors);
	else if (*wb_stop_reason)
		fprintf(f, ",\"writeback\":\"off\",\"stop_reason\":\"%s\"", wb_stop_reason);
	if (wb == WB_ON)
		fprintf(f, ",\"writes_ok\":%lu,\"write_failures\":%lu,\"failures_in_a_row\":%u,\"stopped\":%s,\"stop_reason\":\"%s\"",
			writes_ok, write_failures, fail_run, wb_stopped ? "true" : "false", wb_stop_reason);
	fputs("}\n", f);
	if (fclose(f) == 0) rename(tmp, status_path);
}

/* the token stream of one unit, for the log: HEAD (type 0: group, sync), BODY (type 1: n items of 12 bytes), TAIL (type 2) */
static void log_tokens(const uint8_t *p, uint32_t size)
{
	uint32_t o = 0;
	while (o + 16 <= size) {
		uint32_t type = le32(p + o), seq = le32(p + o + 4), a = le32(p + o + 8), b = le32(p + o + 12);
		o += 16;
		if (type == 0) printf("  HEAD seq %u group %u sync %u\n", seq, a, b);
		else if (type == 2) printf("  TAIL seq %u\n", seq);
		else if (type == 1) {
			printf("  BODY seq %u items %u\n", seq, a);
			for (uint32_t i = 0; i < a && o + 12 <= size; i++, o += 12) {
				items++;
				printf("    id %u len 0x%x offset 0x%x checksum 0x%04x group %u\n", le16(p + o), le16(p + o + 2), le32(p + o + 4),
				       le16(p + o + 8), le16(p + o + 10));
			}
		} else { printf("  token type %u (unknown), rest not parsed\n", type); return; }
	}
}

static int open_port(void)
{
	int fd = open(port, O_RDWR | O_NOCTTY | O_CLOEXEC);
	if (fd < 0) return -1;
	struct termios t;
	if (isatty(fd) && tcgetattr(fd, &t) == 0) { cfmakeraw(&t); tcsetattr(fd, TCSANOW, &t); }   /* the offline test feeds a pty */
	return fd;
}

static int write_all(int fd, const uint8_t *b, size_t n)
{
	while (n) {
		ssize_t w = write(fd, b, n);
		if (w > 0) { b += w; n -= (size_t)w; }
		else if (w < 0 && errno == EINTR) continue;
		else return -1;
	}
	return 0;
}

/* ---- --writeback=dry ---------------------------------------------------------------------------------------------------------
 * runtimenv1/2 hold [inherit header + data][overwrite header + data]; HEAD group 1 = inherit, 2 = overwrite.  A fragment header is
 * 0x200 bytes: magic 0x4e56, data size, checksum, ...; the data is a u32 timestamp, then items (u16 id, u16 len, data, 4-aligned) up
 * to an id 0xffff.  Stock functions named below are in modem-native-re/out/diskserver/cp_diskserver.c.
 */
#define NVHDR 0x200u
#define NV_MAGIC 0x4e56u
#define PART_SECTORS 71680u                  /* nr_runtimenv1/2: 0x2300000 bytes each */
#define MAX_ITEM_BYTES (8192u * 12u)          /* dirty items of one transaction */
#define SPAN_GAP 0x400u                       /* getDirtyOffsetInfo: dirty runs closer than this go into one write */
#define WHOLE_MARKS 40u                       /* write_nv_flash: more marked ids than this -> write the whole image */
#define MAX_SPANS 64u
#define MODEM_GET_LOAD_INFO 0x83604d05u       /* _IOR('M', 5, struct modem_load_info), 0x360 bytes */
#define MODEM_SET_READ_REGION 0x80044d07u     /* _IOR('M', 7, int): region read() serves (global to the device) */

struct ent { uint16_t id, len; uint32_t off; int mark; };   /* buildFlashIdList: one item of a fragment, off = its data */
struct span { uint32_t off, len; };
static struct {
	uint8_t *img, *sim[2];             /* stock's part buffer, and each partition as the writes so far would have left it */
	uint32_t len, start[2], size[2];   /* [g]: fragment offset and header+data length, g 0 = inherit, 1 = overwrite */
	int wide, base, valid[2], same[2]; /* wide: header checksums use the 64-bit sum; same[k]: sim[k] equals img */
	struct ent *ent[2];
	uint32_t nent[2];
} nv;
static struct { int fd, region; uint32_t size; } ram = {-1, -1, 0};   /* where dirty items are read from */
static struct {
	int state;                         /* 0 want HEAD, 1 want BODY/TAIL, 2 inside the items of a BODY */
	uint8_t tok[16], item[MAX_ITEM_BYTES];
	uint32_t fill, group, sync, seq, left, nbytes, overflow;
} tx;

/* calc_checksum_nv / calc_data_checksum: one's-complement sum of LE u16 words (an odd last byte added as is), 32/64-bit accumulator */
static uint16_t nv_sum(const uint8_t *p, uint32_t n, int wide)
{
	uint64_t s = 0;
	for (uint32_t i = 0; i + 1 < n; i += 2) s += le16(p + i);
	if (n & 1) s += p[n - 1];
	if (!wide) s = (uint32_t)s;
	s = (s >> 16) + (s & 0xffff);
	return (uint16_t)~(s + (s >> 16));
}

static int read_at(int fd, void *b, size_t n, off_t o)
{
	if (lseek(fd, o, SEEK_SET) != o) return -1;
	for (uint8_t *p = b; n; ) {
		ssize_t r = read(fd, p, n);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return -1;
		p += r; n -= (size_t)r;
	}
	return 0;
}

/* a fragment at o: magic, size inside the image, checksum (64- or 32-bit, as read_nv_fragment accepts) */
static int frag_ok(const uint8_t *b, uint32_t n, uint32_t o)
{
	if ((uint64_t)o + NVHDR > n || le32(b + o) != NV_MAGIC) return 0;
	uint32_t sz = le32(b + o + 4), c = le32(b + o + 8);
	if (sz < 12 || (uint64_t)o + NVHDR + sz > n) return 0;
	return c == nv_sum(b + o + NVHDR, sz, 1) || c == nv_sum(b + o + NVHDR, sz, 0);
}
static int image_ok(const uint8_t *b, uint32_t n)
{
	return frag_ok(b, n, 0) && frag_ok(b, n, le32(b + 4) + NVHDR) && (uint64_t)le32(b + 4) + le32(b + le32(b + 4) + NVHDR + 4) + 2 * NVHDR == n;
}

static int slurp(const char *path, char *b, size_t n)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);
	ssize_t r = fd < 0 ? -1 : read(fd, b, n - 1);
	if (fd >= 0) close(fd);
	if (r < 0) return -1;
	b[r] = 0;
	return 0;
}

/* refuse anything but the partition itself: PARTNAME, first sector and size from sysfs (block device: by major:minor) */
static int check_part(int k, char *why, size_t wn)
{
	struct stat st;
	char rp[PATH_MAX], d[PATH_MAX + 64], p[PATH_MAX + 80], ue[1024], num[2][32];
	if (stat(rt_path[k], &st)) return snprintf(why, wn, "%s: %s", rt_path[k], strerror(errno)), -1;
	if (S_ISBLK(st.st_mode)) snprintf(d, sizeof d, "%s/dev/block/%u:%u", sysfs, major(st.st_rdev), minor(st.st_rdev));
	else if (S_ISREG(st.st_mode) && realpath(rt_path[k], rp)) snprintf(d, sizeof d, "%s/class/block/%s", sysfs, strrchr(rp, '/') + 1);
	else return snprintf(why, wn, "%s is neither a block device nor a file", rt_path[k]), -1;
	snprintf(p, sizeof p, "%s/uevent", d);
	if (slurp(p, ue, sizeof ue)) return snprintf(why, wn, "%s: no %s", rt_path[k], p), -1;
	const char *pn = strstr(ue, "PARTNAME=");
	size_t l = strlen(rt_name[k]);
	if (!pn || (pn != ue && pn[-1] != '\n') || strncmp(pn + 9, rt_name[k], l) || (pn[9 + l] && pn[9 + l] != '\n'))
		return snprintf(why, wn, "%s: PARTNAME is not %s", rt_path[k], rt_name[k]), -1;
	snprintf(p, sizeof p, "%s/start", d);
	int bad = slurp(p, num[0], sizeof num[0]);
	snprintf(p, sizeof p, "%s/size", d);
	bad |= slurp(p, num[1], sizeof num[1]);
	unsigned long start = strtoul(num[0], NULL, 10);
	if (bad || !start || (rt_lba[k] && start != rt_lba[k]) || strtoul(num[1], NULL, 10) != PART_SECTORS)
		return snprintf(why, wn, "%s: GPT identity/size changed or unsupported NV container", rt_path[k]), -1;
	rt_lba[k] = start;
	return 0;
}

/* out-dir files are always replaced through a new temporary file: nothing that existed before is opened for writing */
static int put_file(const char *name, const void *data, size_t n)
{
	char p[PATH_MAX], t[PATH_MAX];
	if (snprintf(p, sizeof p, "%s/%s", out_dir, name) >= (int)sizeof p || snprintf(t, sizeof t, "%s/.%s.tmp", out_dir, name) >= (int)sizeof t)
		return -1;
	unlink(t);
	int fd = open(t, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0644);
	if (fd < 0) return -1;
	int bad = write_all(fd, data, n) || fsync(fd);
	bad |= close(fd);
	if (bad || rename(t, p)) { unlink(t); return -1; }
	return 0;
}

static int put_images(void)
{
	return put_file("runtimenv1.img", nv.sim[0], nv.len) | put_file("runtimenv2.img", nv.sim[1], nv.len);
}

static int build_ids(int g)
{
	uint32_t end = nv.start[g] + nv.size[g], o = nv.start[g] + NVHDR + 4, n = 0, cap = 0;
	while (o + 4 <= end && le16(nv.img + o) != 0xffff && o + 4 + le16(nv.img + o + 2) <= end) {
		if (n == cap) {
			struct ent *e = realloc(nv.ent[g], (cap = cap ? cap * 2 : 1024) * sizeof *e);
			if (!e) return -1;
			nv.ent[g] = e;
		}
		nv.ent[g][n++] = (struct ent){le16(nv.img + o), le16(nv.img + o + 2), o + 4, 0};
		o = (o + 4 + le16(nv.img + o + 2) + 3) & ~3u;
	}
	nv.nent[g] = n;
	return 0;
}

/* initBuf: the image comes from runtimenv2 if it is valid (modem_control loads the CP from it), else from runtimenv1 */
static int wb_load(char *why, size_t wn)
{
	int fd[2] = {-1, -1}, rc = -1;
	for (int k = 0; k < 2; k++)
		if (check_part(k, why, wn)) return -1;
	for (int k = 0; k < 2; k++)
		if ((fd[k] = open(rt_path[k], O_RDONLY | O_CLOEXEC)) < 0) { snprintf(why, wn, "%s: %s", rt_path[k], strerror(errno)); goto out; }
	for (int k = 1; k >= 0 && !nv.img; k--) {
		uint8_t h[8], h2[8];
		if (read_at(fd[k], h, 8, 0) || le32(h) != NV_MAGIC || read_at(fd[k], h2, 8, (off_t)le32(h + 4) + NVHDR) || le32(h2) != NV_MAGIC)
			continue;
		uint64_t len = (uint64_t)le32(h + 4) + le32(h2 + 4) + 2 * NVHDR;
		uint8_t *b = len <= PART_SECTORS * 512ull ? malloc(len) : NULL;
		if (b && !read_at(fd[k], b, len, 0) && image_ok(b, len)) { nv.img = b; nv.len = len; nv.base = k; }
		else free(b);
	}
	if (!nv.img) { snprintf(why, wn, "neither runtimenv1 nor runtimenv2 holds a valid image"); goto out; }
	for (int k = 0; k < 2; k++) {
		if (!(nv.sim[k] = malloc(nv.len)) || read_at(fd[k], nv.sim[k], nv.len, 0)) { snprintf(why, wn, "reading %s failed", rt_path[k]); goto out; }
		nv.valid[k] = image_ok(nv.sim[k], nv.len);
		nv.same[k] = !memcmp(nv.sim[k], nv.img, nv.len);
	}
	nv.size[0] = nv.start[1] = le32(nv.img + 4) + NVHDR;
	nv.size[1] = le32(nv.img + nv.start[1] + 4) + NVHDR;
	nv.wide = le32(nv.img + 8) == nv_sum(nv.img + NVHDR, nv.size[0] - NVHDR, 1);   /* _chkNVEcc tries 64-bit first */
	if (build_ids(0) || build_ids(1)) { snprintf(why, wn, "out of memory"); goto out; }
	if (wb == WB_DRY && put_images()) { snprintf(why, wn, "cannot write into %s", out_dir); goto out; }
	printf("%.3f writeback %s: image 0x%x bytes from %s (inherit %u ids, overwrite %u ids, %s-bit header checksum); "
	       "runtimenv1 %s%s, runtimenv2 %s%s; out %s\n", uptime(), wb == WB_ON ? "on" : "dry", nv.len, rt_name[nv.base], nv.nent[0], nv.nent[1],
	       nv.wide ? "64" : "32",
	       nv.valid[0] ? "valid" : "INVALID", nv.same[0] ? "" : " (differs)", nv.valid[1] ? "valid" : "INVALID", nv.same[1] ? "" : " (differs)", out_dir);
	rc = 0;
out:
	for (int k = 0; k < 2; k++) if (fd[k] >= 0) close(fd[k]);
	return rc;
}

/* openShareMem + getNvRegionProp: the region of /dev/modem whose name contains "fixnv" */
static int ram_open(char *why, size_t wn)
{
	struct stat st;
	uint8_t li[0x360];
	if (ram.fd >= 0) return 0;
	if (ram_path) {
		int fd = open(ram_path, O_RDONLY | O_CLOEXEC);
		if (fd < 0 || fstat(fd, &st) || !S_ISREG(st.st_mode)) { if (fd >= 0) close(fd); return snprintf(why, wn, "%s unusable", ram_path), -1; }
		ram.fd = fd; ram.size = st.st_size;
		return 0;
	}
	/* O_NONBLOCK: the loader's locks are then tried, not waited for (modem_cmd_lock) */
	int fd = open("/dev/modem", O_RDONLY | O_NONBLOCK | O_CLOEXEC);
	if (fd < 0 || ioctl(fd, MODEM_GET_LOAD_INFO, li)) {
		snprintf(why, wn, "/dev/modem load info: %s", strerror(errno));
		if (fd >= 0) close(fd);
		return -1;
	}
	for (uint32_t i = 0; i < le32(li) && i < 20; i++) {
		const uint8_t *r = li + 0x40 + 40 * i;   /* struct modem_region_info: u64 address, u32 size, char name[21] */
		char name[22];
		memcpy(name, r + 12, 21); name[21] = 0;
		if (strstr(name, "fixnv") || strstr(name, "FIXNV")) { ram.fd = fd; ram.region = (int)i; ram.size = le32(r + 8); return 0; }
	}
	close(fd);
	return snprintf(why, wn, "no fixnv region in the /dev/modem load info"), -1;
}

/* ReadNvIdBuf: select the fixnv region, read from the 8-byte aligned start, take len bytes */
static int ram_read(uint32_t off, uint32_t len, uint8_t *dst)
{
	static uint8_t tmp[0x10008];
	uint32_t n = len + (off & 7);
	if ((uint64_t)off + len > ram.size || n > sizeof tmp) return -1;
	for (int t = 0; t < 50; t++) {
		if ((ram.region < 0 || !ioctl(ram.fd, MODEM_SET_READ_REGION, &ram.region)) && !read_at(ram.fd, tmp, n, off & ~7u)) {
			memcpy(dst, tmp + (off & 7), len);
			return 0;
		}
		if (errno != EBUSY) break;
		usleep(20000);   /* the loader's lock is held by u30-modemd for a moment */
	}
	return -1;
}

/* ---- --writeback=on --------------------------------------------------------------------------------------------------------- */

/* the kernel admits writes only inside the u30pro_rwx windows of the command line: one of them must hold the whole partition */
static int window_ok(unsigned long lba)
{
	char cl[4096];
	/* MU300's persistent root uses normal block writes, unlike the original
	 * RAM-only port. Respect that explicit kernel policy; do not invent LBAs. */
	char policy[16];
	if (!slurp("/sys/module/sdhci_sprd/parameters/write_protect", policy, sizeof policy) &&
	    (policy[0] == '0' || policy[0] == 'N' || policy[0] == 'n')) return 1;
	if (slurp(cmdline_path, cl, sizeof cl)) return 0;
	for (const char *p = cl; (p = strstr(p, "u30pro_rwx=")); ) {
		if (p != cl && p[-1] != ' ') { p++; continue; }
		p += 11;
		for (;;) {
			char *e;
			unsigned long long a = strtoull(p, &e, 10), n;
			if (e == p || *e != '+') break;
			n = strtoull(e + 1, &e, 10);
			if (a <= lba && lba + PART_SECTORS <= a + n) return 1;
			if (*e != ',') break;
			p = e + 1;
		}
	}
	return 0;
}

/* before the first write: both windows, and each partition exactly 0x2300000 bytes (BLKGETSIZE64; the offline test uses files) */
static int on_ready(char *why, size_t wn)
{
	for (int k = 0; k < 2; k++) {
		struct stat st;
		unsigned long long size = 0;
		if (!window_ok(rt_lba[k]))
			return snprintf(why, wn, "no kernel write window for %s (u30pro_rwx ...,%lu+%u) on the command line", rt_name[k], rt_lba[k], PART_SECTORS), -1;
		int fd = open(rt_path[k], O_RDONLY | O_CLOEXEC);
		if (fd < 0 || fstat(fd, &st)) { if (fd >= 0) close(fd); return snprintf(why, wn, "%s: %s", rt_path[k], strerror(errno)), -1; }
		if (S_ISBLK(st.st_mode)) { if (ioctl(fd, BLKGETSIZE64, &size)) size = 0; }
		else size = (unsigned long long)st.st_size;
		close(fd);
		if (size != PART_SECTORS * 512ull) return snprintf(why, wn, "%s is %llu bytes, not 0x%x", rt_path[k], size, PART_SECTORS * 512u), -1;
	}
	return 0;
}

static int pwrite_all(int fd, const uint8_t *b, size_t n, off_t o)
{
	while (n) {
		ssize_t w = pwrite(fd, b, n, o);
		if (w > 0) { b += w; n -= (size_t)w; o += w; }
		else if (w < 0 && errno == EINTR) continue;
		else return -1;
	}
	return 0;
}

/* what the partition holds now, past every cache (O_DIRECT on the block device): sim[k], valid[k] and same[k] follow it */
static int readback(int k, int blk)
{
	static uint8_t *b;
	size_t n = (nv.len + 4095) & ~4095u;
	if (!b && posix_memalign((void **)&b, 4096, n)) { b = NULL; return -1; }
	int fd = open(rt_path[k], O_RDONLY | O_CLOEXEC | (blk ? O_DIRECT : 0));
	if (fd < 0) return -1;
	int bad = read_at(fd, b, blk ? n : nv.len, 0);
	close(fd);
	if (bad) return -1;
	memcpy(nv.sim[k], b, nv.len);
	nv.valid[k] = image_ok(nv.sim[k], nv.len);
	nv.same[k] = !memcmp(nv.sim[k], nv.img, nv.len);
	return 0;
}

/* the kernel's eMMC write protection (sdhci_sprd.write_protect): while it is off nothing is protected, so a partition this
 * daemon made writable stays writable */
static int write_protect_on(void)
{
	int c = 'Y';
	FILE *f = fopen("/sys/module/sdhci_sprd/parameters/write_protect", "r");

	if (f) { c = fgetc(f); fclose(f); }
	return c != 'N' && c != 'n' && c != '0';
}

/* ramDisk_Write for one copy: the whole image, or the spans widened to 4096 bytes (still inside the image), then fsync and read back */
static int part_write(int k, int whole, const struct span *sp, unsigned ns, char *why, size_t wn)
{
	struct stat st;
	int blk = !stat(rt_path[k], &st) && S_ISBLK(st.st_mode), ctl = -1, fd = -1, rc = -1, ro = 0;
	if (blk && ((ctl = open(rt_path[k], O_RDONLY | O_CLOEXEC)) < 0 || ioctl(ctl, BLKROSET, &ro))) {
		snprintf(why, wn, "%s: cannot switch it writable: %s", rt_name[k], strerror(errno));
		goto out;
	}
	if ((fd = open(rt_path[k], O_WRONLY | O_CLOEXEC)) < 0) { snprintf(why, wn, "%s: %s", rt_name[k], strerror(errno)); goto out; }
	int bad = 0;
	if (whole) bad = pwrite_all(fd, nv.img, nv.len, 0);
	else for (unsigned s = 0; s < ns && !bad; s++) {
		uint32_t a = sp[s].off & ~0xfffu, e = (sp[s].off + sp[s].len + 0xfff) & ~0xfffu;
		if (e > nv.len) e = nv.len;
		bad = pwrite_all(fd, nv.img + a, e - a, a);
	}
	if (bad || fsync(fd)) { snprintf(why, wn, "%s: write failed: %s", rt_name[k], strerror(errno)); goto out; }
	rc = 0;
out:
	if (fd >= 0 && close(fd) && !rc) { snprintf(why, wn, "%s: close: %s", rt_name[k], strerror(errno)); rc = -1; }
	if (ctl >= 0) { ro = 1; if (write_protect_on()) ioctl(ctl, BLKROSET, &ro); close(ctl); }
	/* whatever happened, the copy is what the device now holds */
	if (readback(k, blk)) { nv.valid[k] = 0; nv.same[k] = 0; if (!rc) snprintf(why, wn, "%s: read back failed: %s", rt_name[k], strerror(errno)); rc = -1; }
	else if (!rc && !(nv.valid[k] && nv.same[k])) { snprintf(why, wn, "%s: read back differs from the image", rt_name[k]); rc = -1; }
	return rc;
}

/* getDirtyOffsetInfo: per dirty group its 0x200-byte header (unless the last write already passed it), then the runs of marked
 * ids, runs less than SPAN_GAP apart merged, each widened to 512-byte boundaries and cut at the end of the image */
static unsigned dirty_spans(struct span *sp)
{
	unsigned n = 0;
	uint32_t end = 0;
	for (int g = 0; g < 2; g++) {
		const struct ent *e = nv.ent[g];
		uint32_t i = 0, m = nv.nent[g];
		while (i < m && !e[i].mark) i++;
		if (i < m && end <= nv.start[g]) { sp[n++] = (struct span){nv.start[g], NVHDR}; end = nv.start[g] + NVHDR; }
		while (i < m && n < MAX_SPANS) {
			uint32_t first = i, last, j;
			for (;;) {
				while (i < m && e[i].mark) i++;
				last = i - 1;
				for (j = i; j < m && !e[j].mark; j++) {}
				i = j;
				if (j == m || e[j].off - (e[last].off + e[last].len) >= SPAN_GAP) break;
			}
			uint32_t s = e[first].off & ~0x1ffu, l = (e[last].off - s + e[last].len + 0x1ff) & ~0x1ffu;
			if (s + l > nv.len) l = nv.len - s;
			sp[n++] = (struct span){s, l};
			end = s + l;
		}
	}
	return n;
}

/* writeData + ramDisk_Write for one finished transaction; the report goes to DIR/report.json (and report-N.json for the first 1000) */
/* failure accounting of --writeback=on: three failed transactions in a row stop the writing */
static void wb_failed(const char *why)
{
	write_failures++; wb_errors++; fail_run++;
	printf("  WRITEBACK FAILED (%u in a row): %s\n", fail_run, why);
	if (fail_run >= 3) {
		wb_stopped = 1;
		snprintf(wb_stop_reason, sizeof wb_stop_reason, "3 failed transactions in a row, the last: %.200s", why);
		printf("  WRITEBACK STOPPED: %s; answering without writing from now on\n", wb_stop_reason);
	}
}

/* returns 1 when the unit that ended the transaction is to be answered result 0 (on mode: the write did not happen) */
static int wb_tail(void)
{
	struct timespec t0, t1;
	char why[200] = "", name[32], werr[300] = "";
	char *rep = NULL;
	size_t rlen = 0;
	int writing = wb == WB_ON && !wb_stopped, failed = 0;
	FILE *r = open_memstream(&rep, &rlen);
	if (!r) { wb_errors++; return 0; }
	clock_gettime(CLOCK_MONOTONIC, &t0);
	txns++;
	uint32_t g = tx.group - 1, nitems = tx.nbytes / 12, marks = 0;
	fprintf(r, "{\"txn\":%lu,\"uptime\":%.3f,\"group\":%u,\"sync\":%u,\"dirty\":%u", txns, uptime(), tx.group, tx.sync, nitems);
	if (tx.group == 0) snprintf(why, sizeof why, "factory group ignored");
	else if (tx.overflow) snprintf(why, sizeof why, "more than %u dirty items, not applied", MAX_ITEM_BYTES / 12);
	else ram_open(why, sizeof why);
	if (*why) {
		printf("  writeback: %s\n", why);
		fprintf(r, ",\"result\":\"%s\"}\n", why);
		/* the factory group is never written: answered as done; anything else could not be written in on mode */
		if (tx.group && writing) { failed = 1; wb_failed(why); }
		else if (tx.group) wb_errors++;
		goto done;
	}
	fputs(",\"items\":[", r);
	for (uint32_t i = 0; i < nitems; i++) {
		static uint8_t data[0x10000];
		const uint8_t *d = tx.item + 12 * i;
		uint16_t id = le16(d), len = le16(d + 2), ck = le16(d + 8);
		uint32_t off = le32(d + 4), k;
		struct ent *e = NULL;
		const char *res = NULL;
		int changed = 0;
		for (k = 0; k < nv.nent[g]; k++) if (nv.ent[g][k].id == id) { e = &nv.ent[g][k]; break; }
		if (le16(d + 10) != tx.group) res = "group mismatch";
		else if (!e) res = "id not in the image";
		else if (e->len != len) res = "length differs from the image";
		else if (ram_read(off, len, data)) res = "read error";
		else if (nv_sum(data, len, 0) != ck) res = "checksum mismatch";
		uint16_t old = e ? nv_sum(nv.img + e->off, e->len, 0) : 0;
		if (!res) {
			changed = memcmp(nv.img + e->off, data, len) != 0;
			memcpy(nv.img + e->off, data, len);
			e->mark = 1; applied++; res = "applied";
		} else skipped++;
		printf("  writeback id %u len 0x%x ram 0x%x checksum 0x%04x: %s", id, len, off, ck, res);
		if (e) printf(" (image 0x%x, was 0x%04x%s)", e->off, old, changed ? ", changed" : "");
		printf("\n");
		fprintf(r, "%s{\"id\":%u,\"len\":%u,\"ram_offset\":%u,\"checksum\":%u,\"result\":\"%s\"", i ? "," : "", id, len, off, ck, res);
		if (e) fprintf(r, ",\"image_offset\":%u,\"old_checksum\":%u,\"changed\":%s", e->off, old, changed ? "true" : "false");
		fputs("}", r);
	}
	/* updateHeaderChecksum: data size and checksum of the whole fragment of this group */
	uint8_t *h = nv.img + nv.start[g];
	uint32_t oldc = le32(h + 8), newc = nv_sum(h + NVHDR, nv.size[g] - NVHDR, nv.wide);
	put32(h + 4, nv.size[g] - NVHDR); put32(h + 8, newc);
	printf("  writeback group %u header at 0x%x: checksum 0x%04x -> 0x%04x\n", tx.group, nv.start[g], oldc, newc);
	fprintf(r, "],\"header\":{\"offset\":%u,\"old_checksum\":%u,\"new_checksum\":%u},\"copies\":[", nv.start[g], oldc, newc);
	/* write_nv_flash, runtimenv1 first: the dirty spans, or the whole image when too many ids are marked or the copy is not
	 * byte-identical to the image (stock: only when it was found damaged) */
	for (int q = 0; q < 2; q++)
		for (uint32_t k = 0; k < nv.nent[q]; k++) marks += nv.ent[q][k].mark;
	struct span sp[MAX_SPANS];
	unsigned ns = marks <= WHOLE_MARKS ? dirty_spans(sp) : 0;
	int bad_files = 0;
	for (int k = 0; k < 2; k++) {
		int whole = marks > WHOLE_MARKS || !nv.same[k], written = 0;
		if (writing && !failed) {
			/* runtimenv2 only after runtimenv1 read back identical: one valid copy at every moment */
			failed = part_write(k, whole, sp, ns, werr, sizeof werr) != 0;
			written = !failed;
		} else if (!writing) {
			if (whole) memcpy(nv.sim[k], nv.img, nv.len);
			else for (unsigned s = 0; s < ns; s++) memcpy(nv.sim[k] + sp[s].off, nv.img + sp[s].off, sp[s].len);
			nv.valid[k] = image_ok(nv.sim[k], nv.len);
			nv.same[k] = !memcmp(nv.sim[k], nv.img, nv.len);
		}
		printf("  writeback %s: %s", rt_name[k], whole ? "whole image" : "spans");
		fprintf(r, "%s{\"name\":\"%s\",\"whole\":%s,\"spans\":[", k ? "," : "", rt_name[k], whole ? "true" : "false");
		for (unsigned s = 0; !whole && s < ns; s++) {
			printf(" 0x%x+0x%x", sp[s].off, sp[s].len);
			fprintf(r, "%s[%u,%u]", s ? "," : "", sp[s].off, sp[s].len);
		}
		printf("%s; result %s, %s the image\n", writing ? (written ? " written" : " NOT written") : "", nv.valid[k] ? "valid" : "INVALID",
		       nv.same[k] ? "equals" : "DIFFERS FROM");
		fprintf(r, "],\"written\":%s,\"valid\":%s,\"equals_image\":%s}", written ? "true" : "false",
			nv.valid[k] ? "true" : "false", nv.same[k] ? "true" : "false");
	}
	for (int q = 0; q < 2; q++)
		for (uint32_t k = 0; k < nv.nent[q]; k++) nv.ent[q][k].mark = 0;
	/* the planned copies are only kept in dry mode: in on mode the partitions are the result */
	if (wb == WB_DRY && (bad_files = put_images())) wb_errors++;
	clock_gettime(CLOCK_MONOTONIC, &t1);
	long ms = (long)((t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000);
	if (writing) {
		if (failed) wb_failed(werr);
		else { writes_ok++; fail_run = 0; }
		if (ms > 3000) printf("  writeback took %ld ms (the CP waits at most 5 s)\n", ms);
	}
	fprintf(r, "],\"marked\":%u,\"mode\":\"%s\",\"write_ok\":%s,\"write_error\":\"%s\",\"files_written\":%s,\"ms\":%ld}\n", marks,
		writing ? "on" : "dry", writing && !failed ? "true" : "false", werr, wb == WB_DRY && !bad_files ? "true" : "false", ms);
done:
	if (fclose(r) == 0) {
		if (put_file("report.json", rep, rlen)) wb_errors++;
		if (txns <= 1000) { snprintf(name, sizeof name, "report-%06lu.json", txns); put_file(name, rep, rlen); }
	}
	free(rep);
	/* result 0 makes the CP send its dirty items again; once writing stopped every unit gets result 1 again */
	return failed && !wb_stopped;
}

#ifdef NVSYNC_UBUS
/* ubus object u30pro.nvsync: the same fields as the status file; the counters are read while the main loop may change them (a
 * reading can be one unit old) */
static struct blob_buf sb;

static int m_status(struct ubus_context *c, struct ubus_object *o, struct ubus_request_data *req, const char *method, struct blob_attr *msg)
{
	(void)o; (void)method; (void)msg;
	blob_buf_init(&sb, 0);
	blobmsg_add_string(&sb, "port", port);
	blobmsg_add_u64(&sb, "units", units); blobmsg_add_u64(&sb, "replies", replies); blobmsg_add_u64(&sb, "items", items);
	blobmsg_add_u64(&sb, "bad_units", bad_units); blobmsg_add_u64(&sb, "resyncs", resyncs); blobmsg_add_u64(&sb, "reopens", reopens);
	blobmsg_add_u32(&sb, "last_grp", last_grp); blobmsg_add_u32(&sb, "last_sub", last_sub); blobmsg_add_u32(&sb, "last_size", last_size);
	blobmsg_add_u8(&sb, "writes_partitions", wb == WB_ON && !wb_stopped);
	if (wb != WB_OFF) {
		blobmsg_add_string(&sb, "writeback", wb == WB_ON ? "on" : "dry"); blobmsg_add_string(&sb, "out_dir", out_dir);
		blobmsg_add_u64(&sb, "txns", txns); blobmsg_add_u64(&sb, "applied", applied); blobmsg_add_u64(&sb, "skipped", skipped);
		blobmsg_add_u64(&sb, "wb_errors", wb_errors);
	} else {
		blobmsg_add_string(&sb, "writeback", "off");
		if (*wb_stop_reason) blobmsg_add_string(&sb, "stop_reason", wb_stop_reason);
	}
	if (wb == WB_ON) {
		blobmsg_add_u64(&sb, "writes_ok", writes_ok); blobmsg_add_u64(&sb, "write_failures", write_failures);
		blobmsg_add_u32(&sb, "failures_in_a_row", fail_run); blobmsg_add_u8(&sb, "stopped", wb_stopped);
		blobmsg_add_string(&sb, "stop_reason", wb_stop_reason);
	}
	ubus_send_reply(c, req, sb.head);
	return 0;
}

static const struct ubus_method methods[] = { UBUS_METHOD_NOARG("status", m_status) };
static struct ubus_object_type object_type = UBUS_OBJECT_TYPE("u30pro.nvsync", methods);
static struct ubus_object object = { .name = "u30pro.nvsync", .type = &object_type, .methods = methods, .n_methods = ARRAY_SIZE(methods) };

/* without uloop (it would take over the signals the main loop relies on): poll the connection, reconnect when it goes away */
static void *ubus_thread(void *arg)
{
	(void)arg;
	for (;;) {
		struct ubus_context *c = ubus_connect(NULL);
		if (c && !ubus_add_object(c, &object)) {
			while (!c->sock.eof && !c->sock.error) {
				struct pollfd p = { c->sock.fd, POLLIN, 0 };
				if (poll(&p, 1, 1000) > 0) ubus_handle_event(c);
			}
		}
		if (c) ubus_free(c);
		sleep(2);
	}
	return NULL;
}
#endif

/* syncAnalyzer: the token stream of a transaction may run over several units: HEAD, any number of BODY + items, TAIL.
 * Returns 1 when a transaction ended in this unit could not be written (on mode). */
static int wb_feed(const uint8_t *p, uint32_t n, uint32_t sub)
{
	int fail = 0;
	if (sub == 0 && (tx.state || tx.fill)) { printf("  writeback: unfinished transaction dropped\n"); tx.state = 0; tx.fill = 0; }
	while (n) {
		if (tx.state == 2) {
			uint32_t k = n < tx.left ? n : tx.left;
			if (tx.nbytes + k <= sizeof tx.item) { memcpy(tx.item + tx.nbytes, p, k); tx.nbytes += k; }
			else tx.overflow = 1;
			p += k; n -= k; tx.left -= k;
			if (!tx.left) tx.state = 1;
			continue;
		}
		uint32_t k = 16 - tx.fill < n ? 16 - tx.fill : n;
		memcpy(tx.tok + tx.fill, p, k);
		tx.fill += k; p += k; n -= k;
		if (tx.fill < 16) break;
		tx.fill = 0;
		uint32_t type = le32(tx.tok), seq = le32(tx.tok + 4), a = le32(tx.tok + 8), b = le32(tx.tok + 12);
		if (tx.state == 0) {
			if (type == 0 && a < 3 && b < 2) { tx.state = 1; tx.group = a; tx.sync = b; tx.seq = seq; tx.nbytes = 0; tx.overflow = 0; }
			else printf("  writeback: token type %u without a transaction head, ignored\n", type);
		} else if (seq != tx.seq + 1 || type == 0 || type > 2 || (type == 1 && a > 0x100000)) {
			printf("  writeback: token type %u seq %u out of order, transaction dropped\n", type, seq);
			tx.state = 0;
		} else if ((tx.seq = seq), type == 1) {
			if ((tx.left = a * 12)) tx.state = 2;
		} else { fail |= wb_tail(); tx.state = 0; }
	}
	return fail;
}

int main(int argc, char **argv)
{
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--port") && i + 1 < argc) port = argv[++i];
		else if (!strcmp(argv[i], "--status") && i + 1 < argc) status_path = argv[++i];
		else if (!strcmp(argv[i], "--writeback=off")) wb = WB_OFF;
		else if (!strcmp(argv[i], "--writeback=dry")) wb = WB_DRY;
		else if (!strcmp(argv[i], "--writeback=on")) wb = WB_ON;
		else if (!strcmp(argv[i], "--out-dir") && i + 1 < argc) out_dir = argv[++i];
		else if (!strcmp(argv[i], "--runtimenv1") && i + 1 < argc) rt_path[0] = argv[++i];
		else if (!strcmp(argv[i], "--runtimenv2") && i + 1 < argc) rt_path[1] = argv[++i];
		else if (!strcmp(argv[i], "--sysfs") && i + 1 < argc) sysfs = argv[++i];           /* offline test */
		else if (!strcmp(argv[i], "--fixnv-ram") && i + 1 < argc) ram_path = argv[++i];    /* offline test */
		else if (!strcmp(argv[i], "--cmdline") && i + 1 < argc) cmdline_path = argv[++i];  /* offline test */
		else {
			fprintf(stderr, "usage: u30pro-nvsyncd [--port DEV] [--status FILE] [--writeback=off|dry|on --out-dir DIR "
				"[--runtimenv1 DEV] [--runtimenv2 DEV]]\n");
			return 2;
		}
	}
	setvbuf(stdout, NULL, _IOLBF, 0);
	if (wb != WB_OFF) {
		struct stat st;
		char why[PATH_MAX + 100] = "";
		if (!out_dir || stat(out_dir, &st) || !S_ISDIR(st.st_mode)) snprintf(why, sizeof why, "--out-dir must name an existing directory");
		else wb_load(why, sizeof why);
		/* started by the system: whatever is wrong, the CP must still be answered (a task that forces a sync would block for
		 * good), so a problem only switches the write-back off */
		if (*why) {
			printf("%.3f WRITEBACK OFF: %s; answering without writing\n", uptime(), why);
			snprintf(wb_stop_reason, sizeof wb_stop_reason, "%.250s", why);
			wb = WB_OFF;
		} else if (wb == WB_ON && on_ready(why, sizeof why)) {
			printf("%.3f WRITEBACK STOPPED before the first write: %s; dry run only\n", uptime(), why);
			snprintf(wb_stop_reason, sizeof wb_stop_reason, "%.250s", why);
			wb_stopped = 1;
		}
	}
	/* no SA_RESTART: a stop request has to interrupt the read that waits for the CP */
	struct sigaction sa = {.sa_handler = on_signal};
	sigaction(SIGTERM, &sa, NULL); sigaction(SIGINT, &sa, NULL); signal(SIGPIPE, SIG_IGN);
	uint8_t *buf = malloc(UNIT);
	if (!buf) return 1;
#ifdef NVSYNC_UBUS
	{
		pthread_t t;
		sigset_t all, old;
		/* the signals stay with the main loop (its read must be interrupted by SIGTERM) */
		sigfillset(&all);
		pthread_sigmask(SIG_BLOCK, &all, &old);
		if (pthread_create(&t, NULL, ubus_thread, NULL) == 0) pthread_detach(t);
		pthread_sigmask(SIG_SETMASK, &old, NULL);
	}
#endif
	int fd = -1;
	size_t have = 0;
	save_status();
	while (!quitting) {
		if (fd < 0) {
			fd = open_port();
			if (fd < 0) { sleep(1); continue; }
			have = 0;
			printf("%.3f open %s\n", uptime(), port);
		}
		ssize_t n = read(fd, buf + have, UNIT - have);
		if (n <= 0) {
			if (n < 0 && errno == EINTR) continue;
			/* the CP was reset or the channel closed: start again on a fresh open, as the stock daemon does */
			printf("%.3f read %s: %s, reopening\n", uptime(), port, n ? strerror(errno) : "end of file");
			close(fd); fd = -1; reopens++; save_status(); sleep(1);
			continue;
		}
		have += (size_t)n;
		/* framing: a unit starts with the magic; if it does not, drop bytes up to the next "VNMR" */
		if (have >= 4 && le32(buf) != MAGIC) {
			size_t k = 1;
			while (k + 4 <= have && le32(buf + k) != MAGIC) k++;
			if (k + 4 > have) k = have >= 3 ? have - 3 : 0;
			memmove(buf, buf + k, have - k); have -= k; resyncs++;
			continue;
		}
		if (have < UNIT) continue;
		uint32_t grp = le32(buf + 8), sub = le32(buf + 12), size = le32(buf + 16);
		units++; last_grp = grp; last_sub = sub; last_size = size;
		printf("%.3f unit grp %u sub %u size 0x%x\n", uptime(), grp, sub, size);
		if (size <= UNIT - HEAD) log_tokens(buf + HEAD, size); else bad_units++;
		/* result 1, except in on mode for a unit whose transaction could not be written */
		int fail = wb != WB_OFF && size <= UNIT - HEAD ? wb_feed(buf + HEAD, size, sub) : 0;
		uint8_t r[0x24] = {0};
		put32(r, MAGIC); put32(r + 8, grp); put32(r + 12, sub); put32(r + 16, 1); r[20] = fail ? 0 : 1;
		if (write_all(fd, r, sizeof r)) { printf("%.3f reply failed: %s\n", uptime(), strerror(errno)); close(fd); fd = -1; reopens++; }
		else replies++;
		have = 0;
		save_status();
	}
	if (fd >= 0) close(fd);
	save_status();
	return 0;
}
