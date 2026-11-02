/* mc.h — native (musl) rewrite of the Unisoc modem_control boot path for UMS9632/MU5358.
 *
 * Shared types and the hardware abstraction.  Everything that touches the kernel
 * or the TEE goes through struct mc_hw so the whole boot sequence can be replayed
 * against real partition images with a fake kernel in tests/.
 *
 * Spec: docs/boot-flow.md.  Evidence: evidence/modem_control_boot_trace.txt.
 */
#ifndef MC_H
#define MC_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

/* ---- logging --------------------------------------------------------------- */
enum { MC_LOG_ERR = 3, MC_LOG_WARN = 4, MC_LOG_INFO = 6, MC_LOG_DBG = 7 };
void mc_log(int prio, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
void mc_set_log_level(int prio);

/* ---- kernel loader UAPI (drivers/unisoc_platform/modem/modem_loader) -------- */
#define MC_REGION_NAME_LEN 20
#define MC_REGION_MAX      20

struct mc_region_info {            /* struct modem_region_info, 40 bytes */
    uint64_t address;
    uint32_t size;
    char     name[MC_REGION_NAME_LEN + 1];
};

struct mc_load_info {              /* struct modem_load_info, 864 bytes */
    uint32_t region_cnt;
    uint64_t mini_base[2];
    uint32_t mini_size[2];
    uint64_t modem_base;
    uint32_t modem_size;
    uint64_t all_base;
    uint32_t all_size;
    struct mc_region_info regions[MC_REGION_MAX];
};

/* ioctl numbers: _IO('M', n) etc. */
#define MC_IOC_SET_LOAD_INFO   0x43604d06UL   /* _IOW('M', 6, struct modem_load_info) */
#define MC_IOC_GET_LOAD_INFO   0x83604d05UL   /* _IOR('M', 5, struct modem_load_info) */
#define MC_IOC_SET_WRITE_REG   0x40044d08UL   /* _IOW('M', 8, int) */
#define MC_IOC_STOP            0x4d0cUL
#define MC_IOC_START           0x4d0dUL
#define MC_IOC_STOP_UNCOND     0x4d12UL
#define MC_IOC_ASSERT          0x4d0eUL

/* ---- layout (from /vendor/etc/modem_{cp,sp,ch}_info.xml) --------------------- */
#define MC_MAX_ENTRIES   16
#define MC_MAX_BOOTCODE  64

/* flag bits, see the comment block inside the XML files */
#define MCF_SEC       0x00000001u
#define MCF_NV        0x00000002u
#define MCF_BOOTCODE  0x00000004u
#define MCF_CMDLINE   0x00000008u
#define MCF_MODEM_ANY 0x00000f00u

struct mc_entry {
    char     name[32];             /* "modem", "fixnv", ... (<= 20 significant) */
    char     src_file[64];         /* "nr_modem" or "NULL" */
    char     dst_file[32];
    char     part_name[96];        /* resolved partition name incl. slot suffix, "" if none */
    char     part_path[160];       /* resolved device path ("" for NULL/cmdline) */
    uint64_t base;
    uint32_t size;
    uint32_t flag;
    uint32_t bootcode[MC_MAX_BOOTCODE];
    uint32_t bootcode_words;
};

struct mc_group {
    char     kind[3];              /* "cp", "sp", "ch" */
    char     product[16];
    char     ioctl_path[64];       /* where regions are written / start/stop issued */
    char     sub1_path[64];        /* e.g. /dev/phycp (unconditional stop only) */
    char     src_path[64];         /* /dev/block/by-name/ */
    uint64_t modem_base, all_base;
    uint32_t modem_size, all_size;
    uint64_t mini_base[2];
    uint32_t mini_size[2];
    uint32_t partion_cnt;          /* value of <partion_cnt>, may exceed n */
    uint32_t mask;                 /* flag mask this group loads (0xf000 sp / 0xf00000 ch / 0xf00 cp) */
    int      n;
    struct mc_entry e[MC_MAX_ENTRIES];
};

/* ---- cp image header ("MECPV1.0", 0x600 bytes at partition offset 0) ---------- */
#define MC_MECP_MAGIC     0x5043454du      /* 'M''E''C''P' little endian */
#define MC_CPHD_MAGIC     0x44485043u      /* newer container, not used by this firmware */
#define MC_MECP_HDR_SIZE  0x600
#define MC_MECP_MAX_REG   8
#define MC_MECP_MAX_RES   4

struct mc_mecp_item { char name[21]; uint64_t base; uint32_t size; };

struct mc_mecp {
    int      nregion;
    struct mc_mecp_item region[MC_MECP_MAX_REG];
    int      nres;
    struct mc_mecp_item res[MC_MECP_MAX_RES];
    uint32_t bootcode[MC_MAX_BOOTCODE];
    uint32_t bootcode_words;
};

int mc_mecp_parse(const uint8_t hdr[MC_MECP_HDR_SIZE], struct mc_mecp *out);

/* ---- tiny helpers ------------------------------------------------------------ */
int  mc_cmdline_get(const char *text, const char *key, char *val, size_t n);     /* "key=value" token */
int  mc_bootconfig_get(const char *text, const char *key, char *val, size_t n);  /* key = "value" */
int  mc_read_file(const char *path, void *buf, size_t cap);                      /* bytes read or -errno */

/* ---- configuration ----------------------------------------------------------- */
struct mc_cfg {
    const char *conf_dir;          /* directory holding modem_{cp,sp,ch}_info.xml */
    const char *map_file;          /* optional TSV "partition-name device-path" (loop aliases) */
    const char *slot_suffix;       /* "_a"; NULL = detect from bootconfig/cmdline */
    const char *cmdline_path;      /* default /proc/cmdline */
    const char *bootconfig_path;   /* default /proc/bootconfig */
    const char *trusty_dev;        /* default /dev/trusty-ipc-dev0 */
    uint32_t    load_type;         /* default 0x3f */
    int         no_start;          /* load + verify but do not start the CPs */
    int         skip_verify;       /* DEBUG ONLY: never set in production */

    /* optional providers supplied by the nv / mon modules */
    int (*nv_build_fixnv)(const struct mc_cfg *, const struct mc_group *cp, uint8_t **out, size_t *len);
    int (*nv_build_deltanv)(const struct mc_cfg *, const struct mc_group *cp, uint8_t **out, size_t *len);
    int (*append_log_chan)(char *buf, size_t room);        /* appends " log_chan=..." tokens */
    void (*set_state)(int state);                          /* g_modem_state: 1 loading, 2 started */
    int  (*was_booted)(void);                              /* CP was alive before: add " modemboot=reset" */
    int  nv_ignore_timestamp;                              /* do not fail on downloadnv/inherit timestamp diff */
    void *user;
};

/* ---- hardware abstraction (real impl in hw_linux.c, fake in tests) ------------- */
struct mc_hw {
    /* ioctl on a loader node; opens/closes the node around the call */
    int (*ioctl)(void *ctx, const char *dev, unsigned long req, void *arg);
    /* open(dev,O_WRONLY), lseek(off), write all; returns bytes accepted by the node (may be < len) */
    ssize_t (*write_at)(void *ctx, const char *dev, uint64_t off, const void *buf, size_t len);
    /* TIPC request/response to the kernelbootcp TA */
    int (*tipc_call)(void *ctx, uint32_t cmd, const void *req, size_t req_len,
                     void *resp, size_t resp_cap, size_t *resp_len);
    /* read from a partition path */
    ssize_t (*read_part)(void *ctx, const char *path, uint64_t off, void *buf, size_t len);
    int64_t (*part_size)(void *ctx, const char *path);
    int (*wake_lock)(void *ctx, int on);
    int (*bus_protect)(void *ctx, int on);       /* sprd_bm + dmc_mpu, best effort */
    void (*sleep_ms)(void *ctx, unsigned ms);
    void *ctx;
};

extern const struct mc_hw mc_hw_linux;           /* real kernel / Trusty */

/* ---- layout, secure, boot ---------------------------------------------------- */
int  mc_layout_load(const struct mc_cfg *cfg, const struct mc_hw *hw,
                    struct mc_group *cp, struct mc_group *sp, struct mc_group *ch);
int  mc_layout_parse_xml(const char *path, struct mc_group *g);
int  mc_resolve_paths(const struct mc_cfg *cfg, struct mc_group *g, const char *slot);

#define MC_SEC_ENTRY_SZ   0x70
#define MC_SEC_TABLE_SZ   0x1d0
enum { MC_SEC_MODEM = 0, MC_SEC_CH = 1, MC_SEC_PHY = 2, MC_SEC_PM = 3 };   /* order in the TA table */

struct mc_sec_table { uint8_t raw[MC_SEC_TABLE_SZ]; };
void mc_sec_init(struct mc_sec_table *t);
void mc_sec_set_flag(struct mc_sec_table *t, uint32_t flag);
void mc_sec_set_entry(struct mc_sec_table *t, int which, uint64_t addr, uint32_t len,
                      const char *part_name, const uint8_t footer[64]);
int  mc_sec_unlock_ddr(const struct mc_hw *hw, const struct mc_sec_table *t);
int  mc_sec_verify_all(const struct mc_hw *hw, const struct mc_sec_table *t);

/* Stop, (re)load, verify and start the parts of the CP selected by `type` (bit0 pmsys, bit5 chsys,
 * bits 1|3 modem+phy).  0x3f is the first boot and also programs the loader nodes with SET_LOAD_INFO. */
int  mc_load_modem_img(const struct mc_cfg *cfg, const struct mc_hw *hw, uint32_t type);
int  mc_boot(const struct mc_cfg *cfg, const struct mc_hw *hw);

/* resolve a partition name through the --map file, else /dev/block/by-name/<name> */
const char *mc_partition_path(const struct mc_cfg *cfg, const char *name, char *out, size_t cap);
int  mc_nv_build_fixnv(const struct mc_cfg *cfg, const struct mc_group *cp, uint8_t **out, size_t *len);
int  mc_nv_build_deltanv(const struct mc_cfg *cfg, const struct mc_group *cp, uint8_t **out, size_t *len);

#endif
