/*
 * nv.c - read-only NV loader, rewrite of modem_control/libnv_utils NV path.
 * Format and algorithm: docs/nv-format.md.  C99, little-endian host assumed
 * for the on-disk integers (explicit LE accessors are used anyway).
 */
#define _FILE_OFFSET_BITS 64
#define _POSIX_C_SOURCE 200809L
#include "nv.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* ------------------------------------------------------------------ log */
static void default_log(int prio, const char *msg)
{
    fprintf(stderr, "[nv:%d] %s\n", prio, msg);
}
static nv_log_fn g_log = default_log;

void nv_set_logger(nv_log_fn fn) { g_log = fn ? fn : default_log; }

static void nvlog(int prio, const char *fmt, ...)
{
    char buf[400];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    g_log(prio, buf);
}

/* ------------------------------------------------------- LE accessors */
static uint32_t rd32(const uint8_t *p)
{
    return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 |
           (uint32_t)p[3] << 24;
}
static void wr32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

/* ------------------------------------------------------------ checksum */
/*
 * calc_checksum_nv / calc_checksum64_nv: sum of little-endian u16 words
 * (odd trailing byte added as a plain byte), fold twice, complement.
 * The header stores only the low 16 bits.
 */
uint32_t nv_checksum(const uint8_t *p, size_t n, int wide)
{
    uint64_t s = 0;
    size_t i;
    for (i = 0; i + 1 < n; i += 2)
        s += (uint32_t)p[i] | (uint32_t)p[i + 1] << 8;
    if (n & 1)
        s += p[n - 1];
    if (!wide)
        s &= 0xffffffffu;                 /* 32-bit accumulator wraps */
    {
        uint32_t t = (uint32_t)(s >> 16) + (uint32_t)(s & 0xffff);
        t = t + (t >> 16);
        return (~t) & 0xffffu;
    }
}

/* --------------------------------------------------------- padding item */
uint32_t nv_padding_len(uint32_t total)
{
    uint32_t rem = total & 0x1ff;
    uint32_t base = (rem < 0x1fc) ? 0x200u : 0x400u;
    return base - rem;
}

uint32_t nv_padding_item_header(uint32_t padlen)
{
    /* padlen*0x10000 - 0x30002  ==  id 0xFFFE, length padlen-4 */
    return padlen * 0x10000u - 0x30002u;
}

/* ------------------------------------------------------------ fragment */
struct nv_frag {
    uint8_t  hdr[NV_HDR_SIZE];
    uint8_t *buf;        /* data area, `size` bytes                         */
    uint32_t size;
    uint32_t ts;         /* first u32 of data (the "timestamp")             */
    uint32_t cksum;      /* header checksum field                           */
    int      valid;
};

static void frag_free(struct nv_frag *f)
{
    free(f->buf);
    memset(f, 0, sizeof *f);
}

static int read_full(int fd, void *dst, size_t n)
{
    uint8_t *p = dst;
    while (n) {
        ssize_t r = read(fd, p, n > 0x1000000 ? 0x1000000 : n);
        if (r < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (r == 0)
            return -1;
        p += r;
        n -= (size_t)r;
    }
    return 0;
}

static int read_hdr_at(int fd, off_t off, uint8_t *hdr)
{
    if (lseek(fd, off, SEEK_SET) != off)
        return -1;
    return read_full(fd, hdr, NV_HDR_SIZE);
}

/*
 * read_nv_fragment().  `overwrite` selects the second header (located at
 * hdr1.size + 0x200) exactly as read_and_check_nv_header() does.
 * Returns NV_OK even for a damaged fragment (frag->valid == 0), like the
 * original; only I/O-level problems before the header are errors.
 */
static int frag_read(const char *path, int overwrite, struct nv_frag *f)
{
    int fd;
    uint32_t size;
    uint32_t ecc, ecc64;

    memset(f, 0, sizeof *f);
    if (!path)
        return NV_E_ARG;
    fd = open(path, O_RDONLY);
    if (fd < 0) {
        nvlog(6, "read_nv_fragment: open %s failed: %s", path, strerror(errno));
        return NV_E_IO;
    }
    if (read_hdr_at(fd, 0, f->hdr) != 0) {
        nvlog(6, "read nv header failed: %s", path);
        goto bad;
    }
    if (rd32(f->hdr) != NV_MAGIC) {
        nvlog(6, "%s: magic 0x%x is not match", path, rd32(f->hdr));
        goto bad;
    }
    if (overwrite) {
        off_t off = (off_t)rd32(f->hdr + 4) + NV_HDR_SIZE;
        if (read_hdr_at(fd, off, f->hdr) != 0) {
            nvlog(6, "%s: read overwritenv header failed", path);
            goto bad;
        }
        if (rd32(f->hdr) != NV_MAGIC) {
            nvlog(6, "%s: overwritenv magic 0x%x is not match", path, rd32(f->hdr));
            goto bad;
        }
    }
    size = rd32(f->hdr + 4);
    if (size < 12 || size > 0x10000000u) {          /* ts + tail at least */
        nvlog(6, "%s: implausible data size 0x%x", path, size);
        goto bad;
    }
    f->buf = malloc(size);
    if (!f->buf) {
        close(fd);
        return NV_E_MEM;
    }
    if (read_full(fd, f->buf, size) != 0) {
        nvlog(6, "%s: read nv failed size=%u", path, size);
        goto bad;
    }
    close(fd);
    f->size = size;
    f->cksum = rd32(f->hdr + 8);
    ecc = nv_checksum(f->buf, size, 0);
    ecc64 = nv_checksum(f->buf, size, 1);
    nvlog(3, "%s: ecc %x, ecc64 %x, header_ptr->checksum %x", path, ecc, ecc64, f->cksum);
    if (f->cksum == ecc || f->cksum == ecc64) {
        f->valid = 1;
        f->ts = rd32(f->buf);
    }
    return NV_OK;
bad:
    close(fd);
    free(f->buf);
    f->buf = NULL;
    f->size = 0;
    return NV_OK;                                   /* valid stays 0 */
}

/* Core records: name[16], u32 offset, u32 size at hdr+0x14+0x18*i. */
static unsigned frag_ncores(const struct nv_frag *f) { return rd32(f->hdr + 0x10); }
static uint32_t rec_off(const struct nv_frag *f, unsigned i)
{
    return rd32(f->hdr + 0x14 + 0x18 * i + 16);
}
static uint32_t rec_size(const struct nv_frag *f, unsigned i)
{
    return rd32(f->hdr + 0x14 + 0x18 * i + 20);
}

/* Bounds check of every record against the data area. */
static int frag_check_records(const struct nv_frag *f, const char *what, unsigned need)
{
    unsigned n = frag_ncores(f), i;
    if (n > NV_MAX_CORES || n < need) {
        nvlog(6, "%s: core count %u unusable (need %u)", what, n, need);
        return NV_E_FORMAT;
    }
    for (i = 0; i < n; i++) {
        uint64_t end = (uint64_t)rec_off(f, i) + rec_size(f, i);
        if (end > f->size) {
            nvlog(6, "%s: core %u [%u,+%u) outside data (%u)", what, i,
                  rec_off(f, i), rec_size(f, i), f->size);
            return NV_E_FORMAT;
        }
    }
    return NV_OK;
}

/* ------------------------------------------------- DDR stream builder */
struct xfer {
    nv_sink_fn sink;
    void      *ctx;
    uint8_t   *buf;      /* 4 MiB transfer buffer                          */
    size_t     fill;
    size_t     total;
    int        err;
};

static int x_emit(struct xfer *x, const uint8_t *d, size_t n)
{
    if (!n)
        return 0;
    if (x->sink(x->ctx, d, n)) {
        x->err = NV_E_SINK;
        return -1;
    }
    x->total += n;
    return 0;
}

/* proc_trans_buf(): copy into the 4 MiB buffer, flush whenever it fills. */
static int x_copy(struct xfer *x, const uint8_t *src, size_t n)
{
    while (n) {
        size_t room = NV_XFER_BUF - x->fill;
        size_t c = n < room ? n : room;
        memcpy(x->buf + x->fill, src, c);
        x->fill += c;
        src += c;
        n -= c;
        if (x->fill == NV_XFER_BUF) {
            if (x_emit(x, x->buf, NV_XFER_BUF))
                return -1;
            x->fill = 0;
        }
    }
    return 0;
}

static int paths_ok(const struct nv_paths *p)
{
    return p && p->factorynv && p->runtimenv1 && p->runtimenv2;
}

/* Load inherit (+overwrite) from one runtimenv copy. */
static int load_runtime(const char *path, struct nv_frag *inh, struct nv_frag *ow)
{
    int rc = frag_read(path, 0, inh);
    if (rc < 0 && rc != NV_E_IO)
        return rc;
    if (rc == NV_E_IO || !inh->valid)
        return NV_OK;
    rc = frag_read(path, 1, ow);
    return rc < 0 && rc != NV_E_IO ? rc : NV_OK;
}

static int emit_abnormal(struct xfer *x)
{
    static const uint8_t zeros[0x400];
    nvlog(6, "NV fragments damaged: writing 0x400 zero bytes, giving up");
    if (x_emit(x, zeros, sizeof zeros))
        return x->err;
    return NV_ABNORMAL;
}

int nv_stream_fixnv(const struct nv_paths *p, nv_sink_fn sink, void *ctx,
                    struct nv_info *info)
{
    struct nv_frag ow, inh, fac;
    struct xfer x;
    struct nv_info local;
    uint8_t ddr_hdr[NV_HDR_SIZE];
    unsigned n, i;
    size_t region;
    int rc = NV_OK;

    if (!paths_ok(p) || !sink)
        return NV_E_ARG;
    if (!info)
        info = &local;
    memset(info, 0, sizeof *info);
    memset(&ow, 0, sizeof ow);
    memset(&inh, 0, sizeof inh);
    memset(&fac, 0, sizeof fac);
    memset(&x, 0, sizeof x);
    x.sink = sink;
    x.ctx = ctx;
    region = p->fixnv_region_size ? p->fixnv_region_size : NV_DEFAULT_FIXNV_REGION;

    /* 1. inherit + overwrite from runtimenv2, else from runtimenv1 */
    rc = load_runtime(p->runtimenv2, &inh, &ow);
    if (rc < 0)
        goto out;
    info->runtime_used = 2;
    if (!inh.valid || !ow.valid) {
        nvlog(6, "inheritnv or overwritenv on runtimenv2 is damaged!");
        frag_free(&inh);
        frag_free(&ow);
        rc = load_runtime(p->runtimenv1, &inh, &ow);
        if (rc < 0)
            goto out;
        info->runtime_used = 1;
    }
    /* 2. factory */
    rc = frag_read(p->factorynv, 0, &fac);
    if (rc < 0 && rc != NV_E_IO)
        goto out;
    rc = NV_OK;
    if (!fac.valid)
        nvlog(6, "check_factorynv, factorynv is damaged, need to recover in recovery mode");
    if (!inh.valid)
        nvlog(6, "check_runtimenv, inheritnv is damaged, can not recover from downloadnv");
    else if (!ow.valid)
        nvlog(6, "check_runtimenv, overwritenv is damaged, would recover from downloadnv (not implemented)");

    /* is_fragements_data_normal(): all three must be valid */
    x.buf = malloc(NV_XFER_BUF);
    if (!x.buf) {
        rc = NV_E_MEM;
        goto out;
    }
    if (!(ow.valid && inh.valid && fac.valid)) {
        rc = emit_abnormal(&x);
        goto out;
    }

    /* timestamp consistency with downloadnv (is_need_do_upgrade) */
    info->timestamp = inh.ts;
    info->ow_cksum = ow.cksum;
    info->inh_cksum = inh.cksum;
    info->fac_cksum = fac.cksum;
    if (p->downloadnv) {
        int fd = open(p->downloadnv, O_RDONLY);
        uint8_t t[NV_HDR_SIZE + 4];
        if (fd >= 0) {
            if (read_full(fd, t, sizeof t) == 0)
                info->downloadnv_timestamp = rd32(t + NV_HDR_SIZE);
            close(fd);
        }
        if (info->downloadnv_timestamp && info->downloadnv_timestamp != inh.ts) {
            nvlog(6, "timestamp is different (download 0x%x, inherit 0x%x), original would upgrade",
                  info->downloadnv_timestamp, inh.ts);
            if (!p->ignore_timestamp) {
                rc = NV_E_STALE;
                goto out;
            }
        }
    }

    n = frag_ncores(&ow);
    info->ncores = n;
    if ((rc = frag_check_records(&ow, "overwritenv", 1)) != 0 ||
        (rc = frag_check_records(&inh, "inheritnv", n)) != 0 ||
        (rc = frag_check_records(&fac, "factorynv", n)) != 0)
        goto out;

    /* 3. DDR header = copy of the overwrite header, per-core offset/size
     *    replaced by the layout in the DDR image (init_ddr_header).      */
    memcpy(ddr_hdr, ow.hdr, NV_HDR_SIZE);
    {
        uint64_t off = 0;
        uint64_t total = NV_HDR_SIZE;
        for (i = 0; i < n; i++) {
            uint64_t payload = (uint64_t)rec_size(&ow, i) + rec_size(&inh, i) + rec_size(&fac, i);
            uint32_t pad, dsz;
            if (payload > 0x7fffffffu) {
                rc = NV_E_FORMAT;
                goto out;
            }
            pad = nv_padding_len((uint32_t)payload);
            dsz = (uint32_t)payload + pad + NV_CORE_RES_SIZE;
            info->core_payload[i] = (uint32_t)payload;
            info->core_pad[i] = pad;
            info->ddr_offset[i] = (uint32_t)off;
            info->ddr_size[i] = dsz;
            wr32(ddr_hdr + 0x14 + 0x18 * i + 16, (uint32_t)off);
            wr32(ddr_hdr + 0x14 + 0x18 * i + 20, dsz);
            nvlog(3, "ddr_core_info_ptr->dataOffset %u, ddr_core_info_ptr->dataSize %u",
                  (unsigned)off, dsz);
            off += dsz;
            total += dsz;
        }
        if (total > region) {
            nvlog(6, "fixnv image 0x%llx bytes exceeds region 0x%zx",
                  (unsigned long long)total, region);
            rc = NV_E_TOOBIG;
            goto out;
        }
    }

    /* 4. write_nv_to_ddr(): header, then per core the three chunks + pad item
     *    + 0xFF resource block.                                           */
    if (x_emit(&x, ddr_hdr, NV_HDR_SIZE)) {
        rc = x.err;
        goto out;
    }
    for (i = 0; i < n; i++) {
        uint8_t padbuf[520];
        uint32_t pad = info->core_pad[i];
        static uint8_t ff[NV_CORE_RES_SIZE];
        static int ff_init;

        x.fill = 0;
        if (rec_size(&ow, i) && x_copy(&x, ow.buf + rec_off(&ow, i), rec_size(&ow, i)))
            goto sinkerr;
        if (rec_size(&inh, i) && x_copy(&x, inh.buf + rec_off(&inh, i), rec_size(&inh, i)))
            goto sinkerr;
        if (rec_size(&fac, i) && x_copy(&x, fac.buf + rec_off(&fac, i), rec_size(&fac, i)))
            goto sinkerr;
        /* padding item: u32 header word + zero filler (original leaves the
         * filler as uninitialised malloc memory). */
        memset(padbuf, 0, sizeof padbuf);
        wr32(padbuf, nv_padding_item_header(pad));
        if (x_copy(&x, padbuf, pad))
            goto sinkerr;
        nvlog(3, "trans_buf_offset is %u", (unsigned)x.fill);
        if (x.fill && x_emit(&x, x.buf, x.fill))
            goto sinkerr;
        x.fill = 0;
        if (!ff_init) {
            memset(ff, 0xff, sizeof ff);
            ff_init = 1;
        }
        if (x_emit(&x, ff, sizeof ff))
            goto sinkerr;
    }
    info->total_len = x.total;
    rc = NV_OK;
    goto out;
sinkerr:
    rc = x.err ? x.err : NV_E_SINK;
out:
    if (rc == NV_OK || rc == NV_ABNORMAL)
        info->total_len = x.total;
    free(x.buf);
    frag_free(&ow);
    frag_free(&inh);
    frag_free(&fac);
    return rc;
}

/* ------------------------------------------------ collect-to-buffer sink */
struct collect {
    uint8_t *p;
    size_t   len, cap;
};

static int collect_sink(void *ctx, const uint8_t *d, size_t n)
{
    struct collect *c = ctx;
    if (c->len + n > c->cap) {
        size_t nc = c->cap ? c->cap : 1u << 20;
        uint8_t *np;
        while (nc < c->len + n)
            nc *= 2;
        np = realloc(c->p, nc);
        if (!np)
            return -1;
        c->p = np;
        c->cap = nc;
    }
    memcpy(c->p + c->len, d, n);
    c->len += n;
    return 0;
}

int nv_build_fixnv(const struct nv_paths *p, uint8_t **out, size_t *len)
{
    struct collect c;
    int rc;
    if (!out || !len)
        return NV_E_ARG;
    memset(&c, 0, sizeof c);
    rc = nv_stream_fixnv(p, collect_sink, &c, NULL);
    if (rc != NV_OK && rc != NV_ABNORMAL) {
        free(c.p);
        *out = NULL;
        *len = 0;
        return rc;
    }
    *out = c.p;
    *len = c.len;
    return rc;
}

/* ------------------------------------------------------------- deltanv */
#define DELTA_NAME 0x14   /* name field is 20 bytes, strlcpy => 19 chars max */
#define DELTA_LENOFF 0x16 /* u32 total entry length (header included)        */
#define DELTA_MINHDR 0x1a

void nv_hwname_from_cmdline(const char *cmdline, char out[16])
{
    long v = -1;
    const char *k = cmdline ? strstr(cmdline, "rfboard.id=") : NULL;
    unsigned n;
    if (k)
        v = strtol(k + 11, NULL, 10);   /* atoi() */
    n = (unsigned)(int)v;                /* -1 => 0xffffffff */
    if (n > 0x40)
        n = 0;
    snprintf(out, 16, "hw_ver%02u", n);
}

int nv_build_deltanv(const struct nv_paths *p, uint8_t **out, size_t *len)
{
    size_t region;
    uint8_t *img = NULL, *src = NULL;
    off_t fsz;
    size_t pos = 0, o = 0;
    const char *hw;
    int fd = -1, rc = NV_OK;

    if (!p || !p->deltanv || !out || !len)
        return NV_E_ARG;
    region = p->deltanv_region_size ? p->deltanv_region_size : NV_DEFAULT_DELTANV_REGION;
    hw = p->hwname ? p->hwname : "hw_ver00";

    fd = open(p->deltanv, O_RDONLY);
    if (fd < 0) {
        nvlog(6, "deltanv: failed to open %s: %s", p->deltanv, strerror(errno));
        return NV_E_IO;
    }
    fsz = lseek(fd, 0, SEEK_END);
    if (fsz < 0 || lseek(fd, 0, SEEK_SET) != 0) {
        rc = NV_E_IO;
        goto out;
    }
    /* the original parses the stream in 512 KiB reads; entries never need more
     * than the leading part of the partition, so read at most region*2. */
    if ((uint64_t)fsz > (uint64_t)region * 2 + 0x80000)
        fsz = (off_t)(region * 2 + 0x80000);
    src = malloc((size_t)fsz);
    img = calloc(1, region);
    if (!src || !img) {
        rc = NV_E_MEM;
        goto out;
    }
    if (read_full(fd, src, (size_t)fsz) != 0) {
        rc = NV_E_IO;
        goto out;
    }

    while (pos + DELTA_MINHDR <= (size_t)fsz) {
        char name[DELTA_NAME];
        uint32_t elen;
        size_t nl;

        memcpy(name, src + pos, DELTA_NAME);
        name[DELTA_NAME - 1] = '\0';           /* strlcpy(.., 0x14) */
        nl = strlen(name);
        elen = rd32(src + pos + DELTA_LENOFF);
        if (nl == 0 || elen == 0) {
            nvlog(6, "deltanv:get end of deltanv");
            break;
        }
        nvlog(3, "deltanv info %s / 0x%x", name, elen);
        if (o + elen > region) {
            nvlog(6, "deltanv:oversize, total 0x%zx, offset 0x%zx, name %s, len 0x%x",
                  region, o, name, elen);
            break;
        }
        if (pos + elen > (size_t)fsz) {
            /* entry runs past the end of the data we have: the original would
             * copy what the last read returned and then stop on read()==0. */
            elen = (uint32_t)((size_t)fsz - pos);
            memcpy(img + o, src + pos, elen);
            o += elen;
            break;
        }
        if (strstr(name, "hw_ver") == NULL || strncmp(hw, name, DELTA_NAME) == 0) {
            memcpy(img + o, src + pos, elen);
            o += elen;
        }
        pos += elen;
    }
    *out = img;
    *len = region;
    img = NULL;
out:
    if (fd >= 0)
        close(fd);
    free(src);
    free(img);
    return rc;
}
