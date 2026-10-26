/*
 * mu300-carry: the file work of an update that keeps everything the user made (mu300-update, the carry block;
 * docs/FINDINGS.md 85). One static program does it on every path an update takes - Ubuntu, OpenWrt's busybox, and
 * Android's toybox when an installer or the Magisk zip updates - because none of their cp and tar keep all of a file
 * (measured: busybox cp -a makes a sparse file dense and drops extended attributes and the fraction of the second;
 * toybox cp -a also breaks hard links).
 *
 *   mu300-carry scan ROOT              one line for everything below ROOT, never leaving its filesystem:
 *                                        TYPE MODE UID GID SIZE NLINK INODE BLOCKS PATH
 *                                      TYPE f d l c b p s as in a manifest, m for a directory another filesystem is
 *                                      mounted on (not entered)
 *   mu300-carry sum ROOT < PATHS       "HASH PATH" for each: sha256 of a file's content or a link's target, - for
 *                                      anything else, ! when it is not there
 *   mu300-carry manifest ROOT IMAGE TAG   the manifest of an image's files (tools/image-manifest.py has the format
 *                                      and makes the same lines from a tarball), without etc/mu300/image-manifest.gz
 *   mu300-carry copy SRC DST [IDMAP] < PATHS   make DST/PATH what SRC/PATH is: content (holes stay holes), owner,
 *                                      mode, extended attributes (capabilities, ACLs), times to the nanosecond; names
 *                                      of one file in the list stay one file; a directory is made, not filled
 *   mu300-carry link SRC DST [IDMAP] < PATHS   the same, but a file becomes a second name of SRC's (no data copied)
 *
 * The unpack of an update that writes only what changed (docs/FINDINGS.md 131; mu300-update, the delta block):
 *   mu300-carry same OLD < LINES       for lines of the new image's manifest: "B PATH" (a binary) or "T PATH" (text)
 *                                      for each file OLD has byte for byte with that mode and owner
 *   mu300-carry tarfilter OLD LIST LOG   the image's tar stream, stdin to stdout, without the files LIST names that
 *                                      are OLD's byte for byte (compared as the stream passes) and their hard links
 *   mu300-carry tarlink OLD NEW < LOG  after tar: those files in NEW as second names of OLD's
 *   mu300-carry features               the commands this build has
 *
 * PATHS: one per line, relative to the root, a backslash written \\ and a newline \n, parents before what is in
 * them. IDMAP: lines "u OLD NEW" and "g OLD NEW", owners to change on the way (an account whose id differs in the
 * new system); a file that needs it is copied even by link, and loses its set-id bits and its capability on the
 * way ("! PATH" on stdout says so). Nothing is ever written through a symbolic link of DST,
 * and nothing but a non-directory at PATH itself is replaced: a directory in the way is an error. A path that is
 * gone from SRC (a running system) is passed over and counted. Exit 0, or 1 with the reason on stderr.
 *
 * Build: gcc -O2 -static -o mu300-carry mu300-carry.c
 */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/types.h>
#include <sys/xattr.h>
#include <unistd.h>

#define SELF "etc/mu300/image-manifest.gz"

static void die(const char *fmt, ...)
{
	va_list ap;

	fprintf(stderr, "mu300-carry: ");
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
	exit(1);
}

static void *xmalloc(size_t n)
{
	void *p = malloc(n ? n : 1);

	if (!p)
		die("out of memory");
	return p;
}

/* ---- sha256 (FIPS 180-4) */
struct sha256 { uint32_t h[8]; uint64_t len; unsigned char buf[64]; size_t fill; };

static const uint32_t K256[64] = {
	0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5, 0xd807aa98,
	0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
	0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8,
	0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
	0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819,
	0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
	0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
	0xc67178f2
};

#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))

static void sha256_block(struct sha256 *s, const unsigned char *p)
{
	uint32_t w[64], a, b, c, d, e, f, g, h, t1, t2;
	int i;

	for (i = 0; i < 16; i++)
		w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
	for (; i < 64; i++)
		w[i] = (ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10)) + w[i - 7] +
		       (ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3)) + w[i - 16];
	a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
	for (i = 0; i < 64; i++) {
		t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
		t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
		h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
	}
	s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}

static void sha256_init(struct sha256 *s)
{
	static const uint32_t iv[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
					0x1f83d9ab, 0x5be0cd19 };

	memcpy(s->h, iv, sizeof(iv));
	s->len = 0;
	s->fill = 0;
}

static void sha256_update(struct sha256 *s, const unsigned char *p, size_t n)
{
	s->len += n;
	while (n) {
		size_t k = 64 - s->fill;

		if (k > n)
			k = n;
		memcpy(s->buf + s->fill, p, k);
		s->fill += k; p += k; n -= k;
		if (s->fill == 64) {
			sha256_block(s, s->buf);
			s->fill = 0;
		}
	}
}

static void sha256_hex(struct sha256 *s, char out[65])
{
	uint64_t bits = s->len * 8;
	unsigned char pad[72] = { 0x80 }, len[8];
	size_t n = (s->fill < 56 ? 56 : 120) - s->fill;
	int i;

	for (i = 0; i < 8; i++)
		len[i] = bits >> (56 - 8 * i);
	sha256_update(s, pad, n);
	sha256_update(s, len, 8);
	for (i = 0; i < 8; i++)
		sprintf(out + 8 * i, "%08x", (unsigned)s->h[i]);
}

/* the hash of what PATH (under DIRFD) is: a file's content, a link's target; "-" for anything else, "!" when it is
 * not there */
static void hash_of(int dirfd, const char *path, const struct stat *st, char out[65])
{
	static unsigned char buf[1 << 16];
	struct sha256 s;
	ssize_t n;

	strcpy(out, "-");
	sha256_init(&s);
	if (S_ISREG(st->st_mode)) {
		int fd = openat(dirfd, path, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);

		if (fd < 0) {
			strcpy(out, "!");
			return;
		}
		while ((n = read(fd, buf, sizeof(buf))) > 0)
			sha256_update(&s, buf, n);
		close(fd);
		if (n < 0)
			die("%s: %s", path, strerror(errno));
		sha256_hex(&s, out);
	} else if (S_ISLNK(st->st_mode)) {
		n = readlinkat(dirfd, path, (char *)buf, sizeof(buf));
		if (n < 0) {
			strcpy(out, "!");
			return;
		}
		sha256_update(&s, buf, n);
		sha256_hex(&s, out);
	}
}

/* ---- paths as the lists and manifests write them */
static void put_escaped(FILE *f, const char *p)
{
	for (; *p; p++) {
		if (*p == '\\')
			fputs("\\\\", f);
		else if (*p == '\n')
			fputs("\\n", f);
		else
			fputc(*p, f);
	}
}

static char *escaped(const char *p)
{
	char *out = xmalloc(2 * strlen(p) + 1), *o = out;

	for (; *p; p++) {
		if (*p == '\\' || *p == '\n') {
			*o++ = '\\';
			*o++ = *p == '\n' ? 'n' : '\\';
		} else {
			*o++ = *p;
		}
	}
	*o = 0;
	return out;
}

/* a line of a list, in place: the path it names. Relative, no empty, "." or ".." part: anything else is refused */
static char *unescape(char *line)
{
	char *i = line, *o = line, *part;

	for (; *i && *i != '\n'; i++) {
		if (*i == '\\' && i[1] == 'n') {
			*o++ = '\n';
			i++;
		} else if (*i == '\\' && i[1] == '\\') {
			*o++ = '\\';
			i++;
		} else {
			*o++ = *i;
		}
	}
	*o = 0;
	if (!*line || *line == '/')
		die("not a relative path: '%s'", line);
	for (part = line; part; part = strchr(part, '/') ? strchr(part, '/') + 1 : NULL) {
		size_t n = strcspn(part, "/");

		if (n == 0 || (n == 1 && part[0] == '.') || (n == 2 && part[0] == '.' && part[1] == '.'))
			die("not a plain path: '%s'", line);
	}
	return line;
}

static char type_of(mode_t m)
{
	return S_ISREG(m) ? 'f' : S_ISDIR(m) ? 'd' : S_ISLNK(m) ? 'l' : S_ISCHR(m) ? 'c' : S_ISBLK(m) ? 'b' :
	       S_ISFIFO(m) ? 'p' : S_ISSOCK(m) ? 's' : '?';
}

/* ---- scan and manifest */
struct walk {
	dev_t dev;
	int manifest;		/* manifest lines (with hashes) into lines[] instead of scan lines to stdout */
	char **lines;
	size_t n, cap;
};

static void walk_dir(struct walk *w, int fd, char **path, size_t *cap, size_t len)
{
	DIR *d = fdopendir(fd);
	struct dirent *e;

	if (!d)
		die("%s: %s", *path, strerror(errno));
	while ((errno = 0, e = readdir(d))) {
		struct stat st;
		size_t l = strlen(e->d_name);
		char t;

		if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
			continue;
		if (len + l + 2 > *cap)
			*path = realloc(*path, *cap = 2 * (len + l + 2));
		if (!*path)
			die("out of memory");
		memcpy(*path + len, e->d_name, l + 1);
		if (fstatat(fd, e->d_name, &st, AT_SYMLINK_NOFOLLOW)) {
			if (errno == ENOENT)	/* gone while we looked: a running system */
				continue;
			die("%s: %s", *path, strerror(errno));
		}
		t = type_of(st.st_mode);
		if (t == 'd' && st.st_dev != w->dev)
			t = 'm';
		if (w->manifest) {
			char h[65], *esc, *line;

			if (!strcmp(*path, SELF))
				continue;
			if (t == 's' || t == 'm' || t == '?')
				die("%s: an image cannot hold this", *path);
			hash_of(fd, e->d_name, &st, h);
			if (h[0] == '!')
				die("%s: cannot read it", *path);
			esc = escaped(*path);
			line = xmalloc(strlen(esc) + 160);
			sprintf(line, "%c %o %u %u %llu %s %s", t, t == 'l' ? 0777 : (unsigned)(st.st_mode & 07777),
				(unsigned)st.st_uid, (unsigned)st.st_gid,
				t == 'f' || t == 'l' ? (unsigned long long)st.st_size : 0ULL, h, esc);
			free(esc);
			if (w->n == w->cap)
				w->lines = realloc(w->lines, (w->cap = w->cap ? 2 * w->cap : 4096) * sizeof(char *));
			if (!w->lines)
				die("out of memory");
			w->lines[w->n++] = line;
		} else {
			printf("%c %o %u %u %llu %lu %llu %llu ", t, (unsigned)(st.st_mode & 07777), (unsigned)st.st_uid,
			       (unsigned)st.st_gid, (unsigned long long)st.st_size, (unsigned long)st.st_nlink,
			       (unsigned long long)st.st_ino, (unsigned long long)st.st_blocks);
			put_escaped(stdout, *path);
			putchar('\n');
		}
		if (t == 'd') {
			int sub = openat(fd, e->d_name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);

			if (sub < 0) {
				if (errno == ENOENT)
					continue;
				die("%s: %s", *path, strerror(errno));
			}
			(*path)[len + l] = '/';
			(*path)[len + l + 1] = 0;
			walk_dir(w, sub, path, cap, len + l + 1);
		}
	}
	if (errno)
		die("%s: %s", *path, strerror(errno));
	closedir(d);
}

/* the path of a manifest line: after the sixth blank */
static const char *line_path(const char *l)
{
	int i;

	for (i = 0; i < 6; i++)
		l = strchr(l, ' ') + 1;
	return l;
}

static int by_path(const void *a, const void *b)
{
	return strcmp(line_path(*(char *const *)a), line_path(*(char *const *)b));
}

static int cmd_walk(const char *root, const char *image, const char *tag)
{
	struct walk w = { 0 };
	struct stat st;
	size_t cap = 4096, i;
	char *path = xmalloc(cap);
	int fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (fd < 0 || fstat(fd, &st))
		die("%s: %s", root, strerror(errno));
	w.dev = st.st_dev;
	w.manifest = image != NULL;
	path[0] = 0;
	walk_dir(&w, fd, &path, &cap, 0);
	if (w.manifest) {
		qsort(w.lines, w.n, sizeof(char *), by_path);
		printf("mu300-image-manifest 1 %s %s\n", image, tag);
		for (i = 0; i < w.n; i++)
			puts(w.lines[i]);
	}
	if (fflush(stdout) || ferror(stdout))
		die("cannot write the list");
	return 0;
}

static int cmd_sum(const char *root)
{
	char *line = NULL, h[65];
	size_t cap = 0;
	int fd = open(root, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (fd < 0)
		die("%s: %s", root, strerror(errno));
	while (getline(&line, &cap, stdin) > 0) {
		struct stat st;
		char *p = unescape(line);

		if (fstatat(fd, p, &st, AT_SYMLINK_NOFOLLOW))
			strcpy(h, "!");
		else
			hash_of(fd, p, &st, h);
		printf("%s ", h);
		put_escaped(stdout, p);
		putchar('\n');
	}
	if (fflush(stdout) || ferror(stdout))
		die("cannot write the list");
	return 0;
}

/* ---- copy and link */
struct idmap { char kind; unsigned from, to; };
static struct idmap *idmap;
static size_t nidmap;

static unsigned map_id(char kind, unsigned id)
{
	size_t i;

	for (i = 0; i < nidmap; i++)
		if (idmap[i].kind == kind && idmap[i].from == id)
			return idmap[i].to;
	return id;
}

static void read_idmap(const char *file)
{
	FILE *f = fopen(file, "r");
	char kind;
	unsigned from, to;

	if (!f)
		die("%s: %s", file, strerror(errno));
	while (fscanf(f, " %c %u %u", &kind, &from, &to) == 3) {
		if (kind != 'u' && kind != 'g')
			die("%s: not an id map", file);
		idmap = realloc(idmap, (nidmap + 1) * sizeof(*idmap));
		if (!idmap)
			die("out of memory");
		idmap[nidmap++] = (struct idmap){ kind, from, to };
	}
	fclose(f);
}

static int lax;		/* not root (the tests): owners and the attributes only root may set are left out */

/* owner, mode, extended attributes and times of SRC's NAME onto DST's NAME, in the order that keeps them: a chown
 * clears set-id bits and capabilities, so it comes first. The attribute calls take a path: /proc/self/fd/N/NAME,
 * below the directory that was opened without following a link, and never through a link at NAME itself. */
static void copy_meta(int sfd, const char *sname, int dfd, const char *dname, const struct stat *st, const char *path)
{
	struct timespec ts[2] = { st->st_atim, st->st_mtim };
	char sp[320], dp[320], *names, *n;
	ssize_t len, vlen;
	uid_t uid = map_id('u', st->st_uid);
	gid_t gid = map_id('g', st->st_gid);
	/* The owner gets another number (IDMAP): what gives the file the rights of its owner does not go along - the
	 * set-user-id and set-group-id bits, a file capability. Said on stdout ("! PATH") when there was one. */
	int moved = uid != st->st_uid || gid != st->st_gid, took = 0;
	mode_t mode = st->st_mode & 07777;

	if (moved && (mode & 06000)) {
		mode &= ~06000;
		took = 1;
	}
	if (fchownat(dfd, dname, uid, gid, AT_SYMLINK_NOFOLLOW) && !lax)
		die("%s: owner: %s", path, strerror(errno));
	if (!S_ISLNK(st->st_mode) && fchmodat(dfd, dname, mode, 0))
		die("%s: mode: %s", path, strerror(errno));
	snprintf(sp, sizeof(sp), "/proc/self/fd/%d/%s", sfd, sname);
	snprintf(dp, sizeof(dp), "/proc/self/fd/%d/%s", dfd, dname);
	len = llistxattr(sp, NULL, 0);
	if (len < 0 && errno != ENOTSUP && errno != ENODATA)
		die("%s: attributes: %s%s", path, strerror(errno), errno == ENOENT ? " (is /proc mounted?)" : "");
	if (len > 0) {
		names = xmalloc(len);
		len = llistxattr(sp, names, len);
		for (n = names; len > 0 && n < names + len; n += strlen(n) + 1) {
			char *val;

			if (moved && !strcmp(n, "security.capability")) {
				took = 1;
				continue;
			}
			vlen = lgetxattr(sp, n, NULL, 0);
			if (vlen < 0)
				die("%s: attribute %s: %s", path, n, strerror(errno));
			val = xmalloc(vlen);
			vlen = lgetxattr(sp, n, val, vlen);
			/* (a label of Android's SELinux that this kernel will not take is left out: the systems
			 * have no SELinux) */
			if (vlen < 0 || (lsetxattr(dp, n, val, vlen, 0) && strcmp(n, "security.selinux") &&
					 !(lax && (errno == EPERM || errno == ENOTSUP || errno == EACCES))))
				die("%s: attribute %s: %s", path, n, strerror(errno));
			free(val);
		}
		free(names);
	}
	if (utimensat(dfd, dname, ts, AT_SYMLINK_NOFOLLOW))
		die("%s: times: %s", path, strerror(errno));
	if (took) {
		fputs("! ", stdout);
		put_escaped(stdout, path);
		putchar('\n');
	}
}
/* the content of SRC's file into a new file, holes as holes; GONE when the file went away meanwhile */
#define GONE (~0ULL)
static unsigned long long copy_data(int sfd, const char *sname, int dfd, const char *dname, const struct stat *st,
				    const char *path)
{
	static char buf[1 << 20];
	int in = openat(sfd, sname, O_RDONLY | O_NOFOLLOW | O_CLOEXEC);
	int out;
	off_t pos = 0, end;
	unsigned long long done = 0;
	int holes = 1;

	if (in < 0 && errno == ENOENT)
		return GONE;
	if (in < 0)
		die("%s: %s", path, strerror(errno));
	out = openat(dfd, dname, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
	if (out < 0)
		die("%s: %s", path, strerror(errno));
	while (pos < st->st_size) {
		off_t data = holes ? lseek(in, pos, SEEK_DATA) : pos;

		if (data < 0) {
			if (errno == ENXIO)	/* a hole to the end */
				break;
			holes = 0;		/* a filesystem that cannot say: every byte is read */
			data = pos;
		}
		end = holes ? lseek(in, data, SEEK_HOLE) : st->st_size;
		if (end < 0)
			end = st->st_size;
		if (lseek(in, data, SEEK_SET) < 0 || lseek(out, data, SEEK_SET) < 0)
			die("%s: %s", path, strerror(errno));
		for (pos = data; pos < end;) {
			size_t want = (size_t)(end - pos) < sizeof(buf) ? (size_t)(end - pos) : sizeof(buf);
			ssize_t n = read(in, buf, want), w, off;

			if (n < 0)
				die("%s: read: %s", path, strerror(errno));
			if (n == 0) {		/* it shrank while we read */
				pos = st->st_size;
				break;
			}
			for (off = 0; off < n; off += w) {
				w = write(out, buf + off, n - off);
				if (w < 0)
					die("%s: write: %s", path, strerror(errno));
			}
			pos += n;
			done += n;
		}
	}
	if (ftruncate(out, st->st_size))
		die("%s: %s", path, strerror(errno));
	if (close(out))
		die("%s: write: %s", path, strerror(errno));
	close(in);
	return done;
}

/* names of one file: the first one copied of each inode with several names (a hash table: a tree of backups has
 * millions of them) */
struct seen { dev_t dev; ino_t ino; char *path; };
static struct seen *seen;
static size_t nseen, seen_cap;

static size_t seen_slot(const struct seen *tab, size_t cap, dev_t dev, ino_t ino)
{
	size_t i = ((size_t)ino * 2654435761u ^ (size_t)dev) & (cap - 1);

	while (tab[i].path && !(tab[i].dev == dev && tab[i].ino == ino))
		i = (i + 1) & (cap - 1);
	return i;
}

static const char *seen_find(const struct stat *st)
{
	return seen_cap ? seen[seen_slot(seen, seen_cap, st->st_dev, st->st_ino)].path : NULL;
}

static void seen_add(const struct stat *st, const char *path)
{
	size_t i;

	if (2 * (nseen + 1) > seen_cap) {
		size_t cap = seen_cap ? 2 * seen_cap : 1024;
		struct seen *tab = calloc(cap, sizeof(*tab));

		if (!tab)
			die("out of memory");
		for (i = 0; i < seen_cap; i++)
			if (seen[i].path)
				tab[seen_slot(tab, cap, seen[i].dev, seen[i].ino)] = seen[i];
		free(seen);
		seen = tab;
		seen_cap = cap;
	}
	i = seen_slot(seen, seen_cap, st->st_dev, st->st_ino);
	if (!seen[i].path) {
		seen[i] = (struct seen){ st->st_dev, st->st_ino, strdup(path) };
		if (!seen[i].path)
			die("out of memory");
		nseen++;
	}
}

/* the directory PATH's last part is in, opened below ROOTFD without following a link; the last part in *NAME.
 * MAKE: a directory that is missing is made (0700; LIKEFD's of that name gives it its owner, mode and times at the
 * end, see dirs[]). -1 with errno when a part is not a directory. */
struct dir { char *path; };
static struct dir *dirs;	/* directories to give their attributes to at the end, children first */
static size_t ndirs;

static void later(const char *path)
{
	dirs = realloc(dirs, (ndirs + 1) * sizeof(*dirs));
	if (!dirs)
		die("out of memory");
	dirs[ndirs++].path = strdup(path);
}

static int parent_of(int rootfd, char *path, const char **name, int make)
{
	int fd = dup(rootfd), next;
	char *p = path, *slash;

	if (fd < 0)
		die("%s", strerror(errno));
	while ((slash = strchr(p, '/'))) {
		*slash = 0;
		next = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		if (next < 0 && errno == ENOENT && make) {
			if (mkdirat(fd, p, 0700) && errno != EEXIST)
				die("%s: %s", path, strerror(errno));
			later(path);
			next = openat(fd, p, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
		}
		*slash = '/';
		if (next < 0) {
			int e = errno;

			close(fd);
			errno = e;
			return -1;
		}
		close(fd);
		fd = next;
		p = slash + 1;
	}
	*name = p;
	return fd;
}

static int cmd_copy(const char *src, const char *dst, const char *map, int by_link)
{
	char *line = NULL;
	size_t cap = 0, i;
	unsigned long long entries = 0, bytes = 0, linked = 0, gone = 0;
	int sroot = open(src, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	int droot = open(dst, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (sroot < 0)
		die("%s: %s", src, strerror(errno));
	if (droot < 0)
		die("%s: %s", dst, strerror(errno));
	if (map)
		read_idmap(map);
	lax = geteuid() != 0;
	while (getline(&line, &cap, stdin) > 0) {
		char *path = unescape(line);
		const char *sname, *dname;
		struct stat st, dt;
		int sfd, dfd;

		sfd = parent_of(sroot, path, &sname, 0);
		if (sfd < 0 || fstatat(sfd, sname, &st, AT_SYMLINK_NOFOLLOW)) {
			if (errno != ENOENT && errno != ENOTDIR)
				die("%s: %s", path, strerror(errno));
			gone++;
			if (sfd >= 0)
				close(sfd);
			continue;
		}
		if (S_ISSOCK(st.st_mode)) {	/* a socket is made by the program that listens on it */
			close(sfd);
			continue;
		}
		dfd = parent_of(droot, path, &dname, 1);
		if (dfd < 0)
			die("%s: a part of it is not a directory in %s (a link is never written through)", path, dst);
		if (!fstatat(dfd, dname, &dt, AT_SYMLINK_NOFOLLOW)) {
			if (S_ISDIR(dt.st_mode) && !S_ISDIR(st.st_mode))
				die("%s: a directory is in the way in %s", path, dst);
			if (!S_ISDIR(dt.st_mode) && unlinkat(dfd, dname, 0))
				die("%s: %s", path, strerror(errno));
		}
		entries++;
		if (S_ISDIR(st.st_mode)) {
			if (mkdirat(dfd, dname, 0700) && errno != EEXIST)
				die("%s: %s", path, strerror(errno));
			later(path);
		} else if (S_ISLNK(st.st_mode)) {
			char *target = xmalloc(st.st_size + 2);
			ssize_t n = readlinkat(sfd, sname, target, st.st_size + 1);

			if (n < 0)
				die("%s: %s", path, strerror(errno));
			target[n] = 0;
			if (symlinkat(target, dfd, dname))
				die("%s: %s", path, strerror(errno));
			free(target);
			copy_meta(sfd, sname, dfd, dname, &st, path);
		} else if (S_ISREG(st.st_mode)) {
			int remap = map_id('u', st.st_uid) != st.st_uid || map_id('g', st.st_gid) != st.st_gid;

			const char *first = st.st_nlink > 1 ? seen_find(&st) : NULL;

			if (first) {
				/* another name of a file that is there already */
				if (linkat(droot, first, dfd, dname, 0))
					die("%s: %s", path, strerror(errno));
			} else {
				if (by_link && !remap && !linkat(sfd, sname, dfd, dname, 0)) {
					linked++;
				} else {
					unsigned long long n = copy_data(sfd, sname, dfd, dname, &st, path);

					if (n == GONE) {
						gone++;
						entries--;
						close(sfd);
						close(dfd);
						continue;
					}
					bytes += n;
					copy_meta(sfd, sname, dfd, dname, &st, path);
				}
				if (st.st_nlink > 1)
					seen_add(&st, path);
			}
		} else {
			if (mknodat(dfd, dname, st.st_mode, st.st_rdev)) {
				if (!lax)
					die("%s: %s", path, strerror(errno));
			} else {
				copy_meta(sfd, sname, dfd, dname, &st, path);
			}
		}
		close(sfd);
		close(dfd);
	}
	/* the directories last, the deepest first: what was put into them changed their times */
	for (i = ndirs; i-- > 0;) {
		char *path = dirs[i].path;
		const char *sname, *dname;
		struct stat st;
		int sfd = parent_of(sroot, path, &sname, 0), dfd;

		if (sfd < 0 || fstatat(sfd, sname, &st, AT_SYMLINK_NOFOLLOW) || !S_ISDIR(st.st_mode)) {
			if (sfd >= 0)
				close(sfd);
			continue;
		}
		dfd = parent_of(droot, path, &dname, 0);
		if (dfd < 0)
			die("%s: %s", path, strerror(errno));
		copy_meta(sfd, sname, dfd, dname, &st, path);
		close(sfd);
		close(dfd);
	}
	printf("%llu entries, %llu bytes copied, %llu files linked, %llu gone meanwhile\n", entries, bytes, linked, gone);
	return 0;
}

/* ---- an update that writes only what changed (docs/FINDINGS.md 131): the new image's files that the installed
 * system has byte for byte become second names of the installed ones instead of being written again */

/* a set of paths (open addressing); each entry remembers whether its file was really left out of the stream */
struct pset { char **path; unsigned char *done; size_t cap, n; };

static size_t pset_hash(const char *p)
{
	size_t h = 1469598103934665603ULL & (size_t)-1;

	for (; *p; p++)
		h = (h ^ (unsigned char)*p) * 1099511628211ULL;
	return h;
}

static size_t pset_slot(const struct pset *s, const char *p)
{
	size_t i = pset_hash(p) & (s->cap - 1);

	while (s->path[i] && strcmp(s->path[i], p))
		i = (i + 1) & (s->cap - 1);
	return i;
}

static void pset_add(struct pset *s, const char *p)
{
	size_t i;

	if (2 * (s->n + 1) > s->cap) {
		struct pset t = { 0 };

		t.cap = s->cap ? 2 * s->cap : 1024;
		t.path = calloc(t.cap, sizeof(char *));
		t.done = calloc(t.cap, 1);
		if (!t.path || !t.done)
			die("out of memory");
		for (i = 0; i < s->cap; i++)
			if (s->path[i]) {
				size_t j = pset_slot(&t, s->path[i]);

				t.path[j] = s->path[i];
				t.done[j] = s->done[i];
			}
		t.n = s->n;
		free(s->path);
		free(s->done);
		*s = t;
	}
	i = pset_slot(s, p);
	if (!s->path[i]) {
		s->path[i] = strdup(p);
		if (!s->path[i])
			die("out of memory");
		s->n++;
	}
}

/* the slot of P, or -1 */
static long pset_find(const struct pset *s, const char *p)
{
	size_t i;

	if (!s->cap)
		return -1;
	i = pset_slot(s, p);
	return s->path[i] ? (long)i : -1;
}

/* only an SELinux label (Android puts one on every file it unpacked) and no other extended attribute: a file
 * unpacked from the image would have none */
static int plain_attrs(int fd)
{
	char names[4096], *n;
	ssize_t len = flistxattr(fd, names, sizeof(names));

	if (len < 0)
		return errno == ENOTSUP || errno == ENODATA;
	for (n = names; n < names + len; n += strlen(n) + 1)
		if (strcmp(n, "security.selinux"))
			return 0;
	return 1;
}

/* same OLD < LINES: LINES are lines of the new image's manifest ("f MODE UID GID SIZE HASH PATH"). For each one whose
 * file in OLD is a regular file with that mode, owner, size and content, and no extended attribute but an SELinux
 * label: "B PATH" when the content has a NUL byte (a binary), "T PATH" when it has none (text). Nothing for the
 * others: changed, gone, or not a plain file (a link on the way is never followed). */
static int cmd_same(const char *old)
{
	static unsigned char buf[1 << 16];
	char *line = NULL, h[65];
	size_t cap = 0;
	int root = open(old, O_RDONLY | O_DIRECTORY | O_CLOEXEC);

	if (root < 0)
		die("%s: %s", old, strerror(errno));
	while (getline(&line, &cap, stdin) > 0) {
		unsigned mode, uid, gid;
		unsigned long long size;
		char type, want[80], *path;
		const char *name;
		struct stat st;
		struct sha256 s;
		int pfd, fd, bin = 0;
		ssize_t n;

		if (sscanf(line, "%c %o %u %u %llu %79s ", &type, &mode, &uid, &gid, &size, want) != 6 || type != 'f')
			die("not a manifest line of a file: %s", line);
		path = unescape((char *)line_path(line));
		pfd = parent_of(root, path, &name, 0);
		if (pfd < 0)
			continue;
		fd = openat(pfd, name, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
		close(pfd);
		if (fd < 0)
			continue;
		if (fstat(fd, &st) || !S_ISREG(st.st_mode) || (st.st_mode & 07777) != mode || st.st_uid != uid ||
		    st.st_gid != gid || (unsigned long long)st.st_size != size || !plain_attrs(fd)) {
			close(fd);
			continue;
		}
		sha256_init(&s);
		while ((n = read(fd, buf, sizeof(buf))) > 0) {
			sha256_update(&s, buf, n);
			if (!bin && memchr(buf, 0, n))
				bin = 1;
		}
		close(fd);
		if (n < 0)
			continue;
		sha256_hex(&s, h);
		if (strcmp(h, want))
			continue;
		printf("%c ", bin ? 'B' : 'T');
		put_escaped(stdout, path);
		putchar('\n');
	}
	if (fflush(stdout) || ferror(stdout))
		die("cannot write the list");
	return 0;
}

/* ---- tarfilter: a tar stream with the listed files left out */
static int read_full(int fd, void *b, size_t n)
{
	size_t got = 0;

	while (got < n) {
		ssize_t r = read(fd, (char *)b + got, n - got);

		if (r < 0 && errno == EINTR)
			continue;
		if (r < 0)
			die("reading the archive: %s", strerror(errno));
		if (r == 0)
			break;
		got += r;
	}
	return got == n;
}

static void write_full(int fd, const void *b, size_t n)
{
	while (n) {
		ssize_t w = write(fd, b, n);

		if (w < 0 && errno == EINTR)
			continue;
		if (w < 0)
			die("writing the archive: %s", strerror(errno));
		b = (const char *)b + w;
		n -= w;
	}
}

/* a number of a tar header: octal (spaces and NULs around it), or base-256 (GNU, first byte 0x80) */
static unsigned long long tar_num(const unsigned char *p, size_t n)
{
	unsigned long long v = 0;
	size_t i;

	if (p[0] & 0x80) {
		v = p[0] & 0x3f;
		for (i = 1; i < n; i++)
			v = v << 8 | p[i];
		return v;
	}
	for (i = 0; i < n && (p[i] == ' ' || p[i] == 0); i++)
		;
	for (; i < n && p[i] >= '0' && p[i] <= '7'; i++)
		v = v * 8 + (p[i] - '0');
	return v;
}

/* a member's name as a path of the lists: no leading ./ or /, no trailing / */
static void tar_norm(char *p)
{
	size_t n;

	for (;;) {
		if (p[0] == '.' && p[1] == '/')
			memmove(p, p + 2, strlen(p + 2) + 1);
		else if (p[0] == '/')
			memmove(p, p + 1, strlen(p + 1) + 1);
		else
			break;
	}
	n = strlen(p);
	while (n && p[n - 1] == '/')
		p[--n] = 0;
	if (!strcmp(p, "."))
		p[0] = 0;
}

static char *strndup_or_die(const char *p, size_t n)
{
	char *s = xmalloc(n + 1);

	memcpy(s, p, n);
	s[n] = 0;
	return s;
}

/* the records of a pax header that matter here: path, linkpath, size, and whether it carries extended attributes */
static void pax_parse(const char *d, size_t len, char **path, char **link, long long *size, int *xattr)
{
	size_t off = 0;

	while (off < len) {
		char *end;
		unsigned long rl = strtoul(d + off, &end, 10);
		const char *kv, *eq, *stop;

		if (end == d + off || *end != ' ' || rl == 0 || off + rl > len)
			break;
		kv = end + 1;
		stop = d + off + rl - 1;	/* the record's newline */
		eq = memchr(kv, '=', stop - kv);
		if (eq) {
			size_t kl = eq - kv;

			if (kl == 4 && !memcmp(kv, "path", 4)) {
				free(*path);
				*path = strndup_or_die(eq + 1, stop - eq - 1);
			} else if (kl == 8 && !memcmp(kv, "linkpath", 8)) {
				free(*link);
				*link = strndup_or_die(eq + 1, stop - eq - 1);
			} else if (kl == 4 && !memcmp(kv, "size", 4)) {
				*size = strtoll(eq + 1, NULL, 10);
			} else if ((kl > 13 && !memcmp(kv, "SCHILY.xattr.", 13)) || (kl > 17 && !memcmp(kv, "LIBARCHIVE.xattr.", 17))) {
				*xattr = 1;
			}
		}
		off += rl;
	}
}

/* a member's name that is a plain relative path: no empty, "." or ".." part (one with them is never left out) */
static int plain_path(const char *p)
{
	const char *part;

	if (!*p || *p == '/' || strchr(p, '\n'))
		return 0;
	for (part = p; part; part = strchr(part, '/') ? strchr(part, '/') + 1 : NULL) {
		size_t n = strcspn(part, "/");

		if (n == 0 || (n == 1 && part[0] == '.') || (n == 2 && part[0] == '.' && part[1] == '.'))
			return 0;
	}
	return 1;
}

/* tarfilter OLD LIST LOG: the tar stream on stdin to stdout, without the regular files LIST names (one path a
 * line) whose content is OLD's file of that path byte for byte - compared while the stream passes, so that a file
 * that differs after all is written as it is (its first bytes again from OLD, where they were the same) - and
 * without a hard link to such a file. LIST "*": every regular file that OLD has byte for byte and that is a binary
 * (a NUL byte in it; text is written all the same). LOG gets "D PATH" for each file left out, "H PATH" and
 * "T TARGET" for each hard link left out (tarlink makes them), and at the end of the archive "E LEFT_OUT WRITTEN":
 * the KiB of the files left out and of those written, in 4 KiB blocks. */
static int cmd_tarfilter(const char *old, const char *listfile, const char *logfile)
{
	static unsigned char buf[1 << 16], obuf[1 << 16];
	struct pset set = { 0 };
	int every = !strcmp(listfile, "*");
	FILE *lf = every ? NULL : fopen(listfile, "r"), *log;
	char *line = NULL;
	size_t cap = 0;
	unsigned char *pend = NULL;
	size_t npend = 0, pcap = 0;
	char *pax_path = NULL, *pax_link = NULL, *gnu_name = NULL, *gnu_link = NULL;
	long long pax_size = -1;
	int pax_xattr = 0, root = open(old, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	unsigned long long left_out = 0, written = 0;

	if (root < 0)
		die("%s: %s", old, strerror(errno));
	if (!every) {
		if (!lf)
			die("%s: %s", listfile, strerror(errno));
		while (getline(&line, &cap, lf) > 0)
			pset_add(&set, unescape(line));
		fclose(lf);
	}
	log = fopen(logfile, "w");
	if (!log)
		die("%s: %s", logfile, strerror(errno));
	for (;;) {
		unsigned char hdr[512];
		unsigned long long size, blocks;
		char type, *name, *link;
		long slot, hslot;
		int i, zero = 1, cand;

		if (!read_full(0, hdr, 512))
			die("the archive ends in the middle");
		for (i = 0; i < 512 && zero; i++)
			zero = !hdr[i];
		if (zero) {
			ssize_t n;

			/* the end of the archive: what follows (the second block, a record's padding) as it is */
			if (npend)
				die("the archive ends after a header of an extended name");
			write_full(1, hdr, 512);
			while ((n = read(0, buf, sizeof(buf))) > 0)
				write_full(1, buf, n);
			if (n < 0)
				die("reading the archive: %s", strerror(errno));
			break;
		}
		type = hdr[156];
		size = tar_num(hdr + 124, 12);
		blocks = (size + 511) / 512;
		if (type == 'L' || type == 'K' || type == 'x') {
			/* a header of the next member's: kept back until that member is decided */
			char *d;

			if (size > (64 << 20))
				die("an extended header of %llu bytes", size);
			if (npend + 512 + blocks * 512 > pcap) {
				pcap = 2 * (npend + 512 + blocks * 512);
				pend = realloc(pend, pcap);
				if (!pend)
					die("out of memory");
			}
			memcpy(pend + npend, hdr, 512);
			d = (char *)pend + npend + 512;
			if (blocks && !read_full(0, d, blocks * 512))
				die("the archive ends in the middle");
			npend += 512 + blocks * 512;
			if (type == 'L') {
				free(gnu_name);
				gnu_name = strndup_or_die(d, strnlen(d, size));
			} else if (type == 'K') {
				free(gnu_link);
				gnu_link = strndup_or_die(d, strnlen(d, size));
			} else {
				pax_parse(d, size, &pax_path, &pax_link, &pax_size, &pax_xattr);
			}
			continue;
		}
		if (type == 'g') {	/* for every member after it: never left out */
			write_full(1, pend, npend);
			npend = 0;
			write_full(1, hdr, 512);
			while (blocks) {
				size_t k = blocks * 512 < sizeof(buf) ? blocks * 512 : sizeof(buf);

				if (!read_full(0, buf, k))
					die("the archive ends in the middle");
				write_full(1, buf, k);
				blocks -= k / 512;
			}
			continue;
		}
		/* the member's name and link target, from the extended headers or the header itself */
		if (pax_path)
			name = strdup(pax_path);
		else if (gnu_name)
			name = strdup(gnu_name);
		else if (!memcmp(hdr + 257, "ustar\0", 6) && hdr[345]) {
			name = xmalloc(260);
			snprintf(name, 260, "%.155s/%.100s", (char *)hdr + 345, (char *)hdr);
		} else
			name = strndup_or_die((char *)hdr, strnlen((char *)hdr, 100));
		link = pax_link ? strdup(pax_link) : gnu_link ? strdup(gnu_link) : strndup_or_die((char *)hdr + 157, strnlen((char *)hdr + 157, 100));
		if (!name || !link)
			die("out of memory");
		tar_norm(name);
		tar_norm(link);
		if (pax_size >= 0)
			size = pax_size;
		if (type == '1' || type == '2' || type == '3' || type == '4' || type == '5' || type == '6')
			size = 0;
		blocks = (size + 511) / 512;
		hslot = type == '1' ? pset_find(&set, link) : -1;
		cand = (type == '0' || type == '\0' || type == '7') && !pax_xattr &&
		       (every ? plain_path(name) : pset_find(&set, name) >= 0);
		if (hslot >= 0 && set.done[hslot]) {
			/* a second name of a file left out: it is made from that one's */
			fputs("H ", log);
			put_escaped(log, name);
			fputs("\nT ", log);
			put_escaped(log, link);
			fputc('\n', log);
			npend = 0;
		} else if (cand) {
			const char *fname;
			int pfd = parent_of(root, name, &fname, 0), fd = -1;
			unsigned long long pos = 0;
			struct stat st;
			int same = 0, nul = 0;

			if (pfd >= 0) {
				fd = openat(pfd, fname, O_RDONLY | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK);
				close(pfd);
			}
			if (fd >= 0 && !fstat(fd, &st) && S_ISREG(st.st_mode) && (unsigned long long)st.st_size == size &&
			    plain_attrs(fd))
				same = 1;
			if (!same) {
				write_full(1, pend, npend);
				write_full(1, hdr, 512);
			}
			/* the data, block by block, against OLD's file; at the first difference everything goes out */
			while (pos < blocks * 512) {
				size_t k = blocks * 512 - pos < sizeof(buf) ? blocks * 512 - pos : sizeof(buf);
				size_t real = pos + k > size ? size - pos : k;

				if (!read_full(0, buf, k))
					die("the archive ends in the middle");
				if (same && (pread(fd, obuf, real, pos) != (ssize_t)real || memcmp(buf, obuf, real))) {
					unsigned long long o = 0;

					same = 0;
					write_full(1, pend, npend);
					write_full(1, hdr, 512);
					/* what passed already was OLD's bytes: from there again */
					while (o < pos) {
						size_t m = pos - o < sizeof(obuf) ? pos - o : sizeof(obuf);

						if (pread(fd, obuf, m, o) != (ssize_t)m)
							die("%s: read: %s", name, strerror(errno));
						write_full(1, obuf, m);
						o += m;
					}
				}
				if (same && !nul && memchr(buf, 0, real))
					nul = 1;
				if (!same)
					write_full(1, buf, k);
				pos += k;
			}
			if (same && every && !nul) {
				/* every file: a text file is written all the same (OLD's bytes, which are the archive's) */
				unsigned long long o = 0;

				same = 0;
				write_full(1, pend, npend);
				write_full(1, hdr, 512);
				while (o < size) {
					size_t m = size - o < sizeof(obuf) ? size - o : sizeof(obuf);

					if (pread(fd, obuf, m, o) != (ssize_t)m)
						die("%s: read: %s", name, strerror(errno));
					write_full(1, obuf, m);
					o += m;
				}
				memset(obuf, 0, 512);
				write_full(1, obuf, blocks * 512 - size);
			}
			if (fd >= 0)
				close(fd);
			if (same) {
				if (every)
					pset_add(&set, name);
				slot = pset_find(&set, name);
				set.done[slot] = 1;
				fputs("D ", log);
				put_escaped(log, name);
				fputc('\n', log);
				left_out += (size + 4095) / 4096 * 4;
			} else {
				written += (size + 4095) / 4096 * 4;
			}
			npend = 0;
		} else {
			write_full(1, pend, npend);
			npend = 0;
			write_full(1, hdr, 512);
			while (blocks) {
				size_t k = blocks * 512 < sizeof(buf) ? blocks * 512 : sizeof(buf);

				if (!read_full(0, buf, k))
					die("the archive ends in the middle");
				write_full(1, buf, k);
				blocks -= k / 512;
			}
			if (type == '0' || type == '\0' || type == '7')
				written += (size + 4095) / 4096 * 4;
		}
		free(name);
		free(link);
		free(pax_path);
		free(pax_link);
		free(gnu_name);
		free(gnu_link);
		pax_path = pax_link = gnu_name = gnu_link = NULL;
		pax_size = -1;
		pax_xattr = 0;
	}
	fprintf(log, "E %llu %llu\n", left_out, written);
	if (fclose(log))
		die("%s: %s", logfile, strerror(errno));
	return 0;
}

/* tarlink OLD NEW < LOG: what tarfilter left out, made in NEW once tar has unpacked the rest - "D PATH": NEW's PATH
 * a second name of OLD's (a copy of it, with its owner, mode and times, where a link cannot be: another filesystem,
 * too many names); "H PATH" "T TARGET": NEW's PATH a second name of NEW's TARGET. The directories they go into get
 * back the times tar gave them. The log must end with tarfilter's "E": else the stream was not whole. */
/* what is at DNAME already (an earlier archive put a file there: the later one wins, as with tar) goes; never a
 * directory */
static void clear_way(int dfd, const char *dname, const char *path)
{
	struct stat st;

	if (fstatat(dfd, dname, &st, AT_SYMLINK_NOFOLLOW))
		return;
	if (S_ISDIR(st.st_mode))
		die("%s: a directory is in the way", path);
	if (unlinkat(dfd, dname, 0))
		die("%s: %s", path, strerror(errno));
}

static int cmd_tarlink(const char *old, const char *new)
{
	struct pset dirs = { 0 };
	struct timespec *dtimes = NULL;
	char **dorder = NULL;
	size_t ndir = 0, i;
	char *line = NULL, *hpath = NULL;
	size_t cap = 0;
	unsigned long long linked = 0, copied = 0;
	int sroot = open(old, O_RDONLY | O_DIRECTORY | O_CLOEXEC), droot = open(new, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
	int ended = 0;

	if (sroot < 0)
		die("%s: %s", old, strerror(errno));
	if (droot < 0)
		die("%s: %s", new, strerror(errno));
	lax = geteuid() != 0;
	while (getline(&line, &cap, stdin) > 0) {
		char kind = line[0], *path, *slash;
		const char *sname, *dname;
		int sfd, dfd;

		if (kind == 'E') {
			ended = 1;
			continue;
		}
		if ((kind != 'D' && kind != 'H' && kind != 'T') || line[1] != ' ')
			die("not a line of tarfilter's log: %s", line);
		path = unescape(line + 2);
		if (kind == 'H') {
			free(hpath);
			hpath = strdup(path);
			if (!hpath)
				die("out of memory");
			continue;
		}
		if (kind == 'T' && !hpath)
			die("a target without its link in the log");
		/* the directory it goes into: its times as tar left them, once */
		{
			char *d = strdup(kind == 'T' ? hpath : path);

			if (!d)
				die("out of memory");
			slash = strrchr(d, '/');
			if (slash)
				*slash = 0;
			else
				d[0] = 0;
			if (pset_find(&dirs, d) < 0) {
				struct stat ds;
				int r;

				if (d[0]) {
					const char *dn;
					int pf = parent_of(droot, d, &dn, 0);

					r = pf < 0 ? -1 : fstatat(pf, dn, &ds, AT_SYMLINK_NOFOLLOW);
					if (pf >= 0)
						close(pf);
				} else {
					r = fstat(droot, &ds);
				}
				if (r == 0) {
					pset_add(&dirs, d);
					dtimes = realloc(dtimes, 2 * (ndir + 1) * sizeof(*dtimes));
					dorder = realloc(dorder, (ndir + 1) * sizeof(*dorder));
					if (!dtimes || !dorder)
						die("out of memory");
					dtimes[2 * ndir] = ds.st_atim;
					dtimes[2 * ndir + 1] = ds.st_mtim;
					dorder[ndir++] = strdup(d);
				}
			}
			free(d);
		}
		if (kind == 'T') {
			sfd = parent_of(droot, path, &sname, 0);
			if (sfd < 0)
				die("%s: %s", path, strerror(errno));
			dfd = parent_of(droot, hpath, &dname, 0);
			if (dfd < 0)
				die("%s: its directory is not there in %s", hpath, new);
			clear_way(dfd, dname, hpath);
			if (linkat(sfd, sname, dfd, dname, 0))
				die("%s: %s", hpath, strerror(errno));
			linked++;
			free(hpath);
			hpath = NULL;
		} else {
			struct stat st;

			sfd = parent_of(sroot, path, &sname, 0);
			if (sfd < 0 || fstatat(sfd, sname, &st, AT_SYMLINK_NOFOLLOW) || !S_ISREG(st.st_mode))
				die("%s: not in %s any more", path, old);
			dfd = parent_of(droot, path, &dname, 0);
			if (dfd < 0)
				die("%s: its directory is not there in %s", path, new);
			clear_way(dfd, dname, path);
			if (!linkat(sfd, sname, dfd, dname, 0)) {
				linked++;
			} else if (errno == EXDEV || errno == EMLINK || errno == EPERM) {
				if (copy_data(sfd, sname, dfd, dname, &st, path) == GONE)
					die("%s: gone from %s", path, old);
				copy_meta(sfd, sname, dfd, dname, &st, path);
				copied++;
			} else {
				die("%s: %s", path, strerror(errno));
			}
		}
		close(sfd);
		close(dfd);
	}
	if (!ended)
		die("the log does not end as tarfilter ends it: the archive was not read to its end");
	for (i = 0; i < ndir; i++) {
		if (dorder[i][0]) {
			const char *dn;
			int pf = parent_of(droot, dorder[i], &dn, 0);

			if (pf < 0 || utimensat(pf, dn, dtimes + 2 * i, AT_SYMLINK_NOFOLLOW))
				die("%s: times: %s", dorder[i], strerror(errno));
			close(pf);
		} else if (futimens(droot, dtimes + 2 * i)) {
			die("%s: times: %s", new, strerror(errno));
		}
	}
	printf("%llu files linked, %llu copied\n", linked, copied);
	return 0;
}

int main(int argc, char **argv)
{
	if (argc == 3 && !strcmp(argv[1], "scan"))
		return cmd_walk(argv[2], NULL, NULL);
	if (argc == 5 && !strcmp(argv[1], "manifest"))
		return cmd_walk(argv[2], argv[3], argv[4]);
	if (argc == 3 && !strcmp(argv[1], "sum"))
		return cmd_sum(argv[2]);
	if ((argc == 4 || argc == 5) && (!strcmp(argv[1], "copy") || !strcmp(argv[1], "link")))
		return cmd_copy(argv[2], argv[3], argc == 5 ? argv[4] : NULL, argv[1][0] == 'l');
	if (argc == 3 && !strcmp(argv[1], "same"))
		return cmd_same(argv[2]);
	if (argc == 5 && !strcmp(argv[1], "tarfilter"))
		return cmd_tarfilter(argv[2], argv[3], argv[4]);
	if (argc == 4 && !strcmp(argv[1], "tarlink"))
		return cmd_tarlink(argv[2], argv[3]);
	/* what this build can do (an updater asks before it relies on a command) */
	if (argc == 2 && !strcmp(argv[1], "features")) {
		puts("scan sum manifest copy link same tarfilter tarlink");
		return 0;
	}
	fprintf(stderr, "usage: mu300-carry scan ROOT | sum ROOT < PATHS | manifest ROOT IMAGE TAG |\n"
			"       mu300-carry copy|link SRC DST [IDMAP] < PATHS | same OLD < MANIFEST-LINES |\n"
			"       mu300-carry tarfilter OLD LIST LOG < TAR > TAR | tarlink OLD NEW < LOG | features\n");
	return 2;
}
