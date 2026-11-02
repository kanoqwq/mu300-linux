/* secure.c — client of the Trusty "kernelbootcp" TA (libkernelbootcp.trusty.so rewritten).
 *
 * Wire format (reverse engineered, docs/boot-flow.md):
 *   connect : open("/dev/trusty-ipc-dev0", O_RDWR); ioctl(fd, TIPC_IOC_CONNECT, "com.android.trusty.kernelbootcp")
 *   request : write(fd, u32 cmd || 0x1d0-byte table)      cmd 0 = verify_all, cmd 2 = unlock_ddr
 *   response: read(fd, buf, 16) must return a u32 == cmd|1; the stock client reports "ret = <bytes read>" and
 *             every healthy run logs ret = 4 (header only).
 *
 * Table, 0x1d0 bytes:
 *   +0x000 modem  +0x070 ch  +0x0e0 phy  +0x150 pm    4 entries of 0x70 bytes:
 *        +0x00 u64 phys address   +0x08 u32 length   +0x0c u32 map length
 *        +0x10 u8[64] vboot footer (last 0x40 bytes of the partition)
 *        +0x50 char[32] partition name without A/B suffix (31 chars + NUL)
 *   +0x1c0 u32 load_type   +0x1c4 u32 is_packed (always 0)   +0x1c8 u32 image offset in partition (0 here)
 */
#include "mc.h"

#include <errno.h>
#include <string.h>

static const int entry_off[4] = { 0x000, 0x070, 0x0e0, 0x150 };

static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }       /* aarch64 and x86_64 are little endian */
static void put64(uint8_t *p, uint64_t v) { memcpy(p, &v, 8); }

void mc_sec_init(struct mc_sec_table *t) { memset(t->raw, 0, sizeof t->raw); }

void mc_sec_set_flag(struct mc_sec_table *t, uint32_t flag) { put32(t->raw + 0x1c0, flag); }

void mc_sec_set_entry(struct mc_sec_table *t, int which, uint64_t addr, uint32_t len,
                      const char *part_name, const uint8_t footer[64])
{
    uint8_t *e = t->raw + entry_off[which];
    put64(e + 0x00, addr);
    put32(e + 0x08, len);
    put32(e + 0x0c, len);
    if (footer)
        memcpy(e + 0x10, footer, 64);
    strncpy((char *)e + 0x50, part_name, 0x1f);
    e[0x6f] = 0;
}

static int call(const struct mc_hw *hw, uint32_t cmd, const struct mc_sec_table *t, const char *what)
{
    uint8_t resp[16] = { 0 };
    size_t rl = 0;
    int rc = hw->tipc_call(hw->ctx, cmd, t->raw, sizeof t->raw, resp, sizeof resp, &rl);
    if (rc < 0) {
        mc_log(MC_LOG_ERR, "[secure]%s: tipc call failed: %d", what, rc);
        return rc;
    }
    uint32_t echoed;
    memcpy(&echoed, resp, 4);
    if (rl < 4 || echoed != (cmd | 1)) {
        mc_log(MC_LOG_ERR, "[secure]%s: bad response (len %zu, cmd 0x%x)", what, rl, rl >= 4 ? echoed : 0);
        return -EPROTO;
    }
    /* A healthy run answers with the 4-byte header only.  The stock code accepts anything >= 4;
     * we refuse extra payload because its meaning is unknown and might be an error status. */
    if (rl != 4) {
        mc_log(MC_LOG_ERR, "[secure]%s: unexpected %zu-byte response", what, rl);
        return -EPROTO;
    }
    mc_log(MC_LOG_INFO, "[secure]%s: ret = %zu", what, rl);
    return 0;
}

int mc_sec_unlock_ddr(const struct mc_hw *hw, const struct mc_sec_table *t)
{
    return call(hw, 2, t, "secure_boot_unlock_ddr");
}

int mc_sec_verify_all(const struct mc_hw *hw, const struct mc_sec_table *t)
{
    return call(hw, 0, t, "secure_boot_verify_all");
}
