/*
 * nv.h - read-only NV loader for the Unisoc UMS9632 modem (rewrite of the NV
 * path of the vendor modem_control + libnv_utils).  See docs/nv-format.md.
 *
 * Pure C99, no threads, no Android dependencies.  Never writes any NV
 * partition: upgrade / recover / overwrite-write-back paths of the original
 * are intentionally NOT implemented; damaged data is reported as an error.
 */
#ifndef NV_H
#define NV_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Sizes taken from the original program. */
#define NV_HDR_SIZE        0x200u     /* fragment header                      */
#define NV_MAGIC           0x4e56u    /* "VN" little endian                   */
#define NV_MAX_CORES       20u        /* records must fit inside the header   */
#define NV_CORE_RES_SIZE   0x10000u   /* 0xFF block appended after every core */
#define NV_XFER_BUF        0x400000u  /* original flushes in 4 MiB blocks     */
#define NV_DEFAULT_FIXNV_REGION   0x2100000u  /* fixnv region of the CP layout */
#define NV_DEFAULT_DELTANV_REGION 0x200000u   /* deltanv region                */

/* Return codes. 0 = ok, <0 = error, >0 = "abnormal" (see below). */
enum {
    NV_OK              = 0,
    NV_ABNORMAL        = 1,   /* a fragment is damaged: the original writes 0x400
                                 zero bytes to the DDR region and gives up. The
                                 sink/out buffer then holds exactly those bytes. */
    NV_E_ARG           = -1,
    NV_E_IO            = -2,  /* open/read/seek failure                          */
    NV_E_MEM           = -3,
    NV_E_FORMAT        = -4,  /* malformed header/record/bounds                  */
    NV_E_TOOBIG        = -5,  /* result does not fit the CP region               */
    NV_E_STALE         = -6,  /* downloadnv timestamp != inheritnv timestamp: the
                                 original would run its (write) upgrade flow      */
    NV_E_SINK          = -7   /* the sink callback failed                        */
};

/*
 * Paths of the partitions (block devices or regular files).  The original
 * builds them as "<partitionpath><nvp><name>" = "/dev/block/by-name/nr_<name>".
 */
struct nv_paths {
    const char *factorynv;     /* nr_factorynv                                   */
    const char *runtimenv1;    /* nr_runtimenv1 (fallback copy)                  */
    const char *runtimenv2;    /* nr_runtimenv2 (preferred copy)                 */
    const char *downloadnv;    /* nr_downloadnv_<slot>; optional (timestamp only)*/
    const char *deltanv;       /* nr_deltanv_<slot>                              */
    const char *hwname;        /* "hw_verNN"; NULL => "hw_ver00"                 */
    size_t fixnv_region_size;  /* 0 => NV_DEFAULT_FIXNV_REGION                   */
    size_t deltanv_region_size;/* 0 => NV_DEFAULT_DELTANV_REGION                 */
    int ignore_timestamp;      /* 1 => do not fail on downloadnv/inherit ts diff */
};

/* Optional diagnostics filled by nv_stream_fixnv(); may be NULL. */
struct nv_info {
    int      runtime_used;             /* 2 = runtimenv2, 1 = runtimenv1            */
    uint32_t timestamp;                /* first u32 of the inherit data             */
    uint32_t downloadnv_timestamp;     /* 0 if downloadnv not given/readable        */
    unsigned ncores;
    uint32_t ow_cksum, inh_cksum, fac_cksum;  /* 16-bit header checksums (as found) */
    uint32_t core_payload[NV_MAX_CORES];      /* ow+inh+fac bytes per core          */
    uint32_t core_pad[NV_MAX_CORES];          /* padding item length per core       */
    uint32_t ddr_offset[NV_MAX_CORES];        /* dataOffset written into DDR header */
    uint32_t ddr_size[NV_MAX_CORES];          /* dataSize written into DDR header   */
    size_t   total_len;                       /* bytes sent to the sink             */
};

/* Sink for streamed output. Return 0 on success, nonzero on failure. */
typedef int (*nv_sink_fn)(void *ctx, const uint8_t *data, size_t len);

/* Logging hook (default: stderr). prio: 3 = debug/info, 6 = error (Android-like). */
typedef void (*nv_log_fn)(int prio, const char *msg);
void nv_set_logger(nv_log_fn fn);

/*
 * Stream the bytes that the original writes into the CP "fixnv" region
 * (write region 2 of /dev/modem): 0x200 header, then for every core
 * [overwrite][inherit][factory][padding item] flushed in <= 4 MiB blocks,
 * then 0x10000 bytes of 0xFF.  Sequential, offset 0 of the region.
 */
int nv_stream_fixnv(const struct nv_paths *p, nv_sink_fn sink, void *ctx,
                    struct nv_info *info);

/* Same bytes collected into one malloc'ed buffer (caller frees). */
int nv_build_fixnv(const struct nv_paths *p, uint8_t **out, size_t *len);

/*
 * deltanv region image (write region 6): the whole region_size bytes
 * (default 0x200000): selected entries copied back-to-back, rest zero.
 * Entries whose name contains "hw_ver" are kept only if the name equals hwname.
 */
int nv_build_deltanv(const struct nv_paths *p, uint8_t **out, size_t *len);

/* "hw_ver%02d" from an AP kernel cmdline ("rfboard.id=N"; unknown/>64 => 0). */
void nv_hwname_from_cmdline(const char *cmdline, char out[16]);

/* --- helpers exposed for tests ------------------------------------------ */
/* Internet-style checksum over little-endian 16-bit words of the original:
 * wide=0: 32-bit accumulator (calc_checksum_nv), wide=1: 64-bit
 * (calc_checksum64_nv).  Returned value is the low 16 bits (what is stored). */
uint32_t nv_checksum(const uint8_t *p, size_t n, int wide);
/* generate_padding_nv: padding length for a core payload of `total` bytes
 * (1..0x200, always > 4) and the 32-bit pad item header word. */
uint32_t nv_padding_len(uint32_t total);
uint32_t nv_padding_item_header(uint32_t padlen);

#ifdef __cplusplus
}
#endif
#endif /* NV_H */
