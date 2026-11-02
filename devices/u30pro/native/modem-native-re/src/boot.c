/* boot.c — the CP boot sequence of modem_control, rewritten.
 *
 *   load layout -> SET_LOAD_INFO (pmsys, chsys, modem) -> wake lock -> TA unlock_ddr
 *   -> STOP all -> load pmsys / chsys / modem images, nv, cmdline, boot code
 *   -> TA verify_all -> START pmsys, chsys, modem -> release wake lock
 *
 * Every step is checked against evidence/modem_control_boot_trace.txt by tests/boot_test.c.
 */
#include "mc.h"

#include <errno.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>

#define CHUNK (1u << 20)

/* ---------------------------------------------------------------------------- */
/* SET_LOAD_INFO                                                                  */
/* ---------------------------------------------------------------------------- */

static void build_load_info(const struct mc_group *g, struct mc_load_info *li)
{
    memset(li, 0, sizeof *li);
    li->region_cnt = g->partion_cnt > (uint32_t)g->n ? g->partion_cnt : (uint32_t)g->n;
    if (li->region_cnt > MC_REGION_MAX)
        li->region_cnt = MC_REGION_MAX;
    li->mini_base[0] = g->mini_base[0];
    li->mini_base[1] = g->mini_base[1];
    li->mini_size[0] = g->mini_size[0];
    li->mini_size[1] = g->mini_size[1];
    li->modem_base = g->modem_base;
    li->modem_size = g->modem_size;
    li->all_base = g->all_base;
    li->all_size = g->all_size;
    for (int i = 0; i < g->n && i < MC_REGION_MAX; i++) {
        li->regions[i].address = g->e[i].base;
        li->regions[i].size = g->e[i].size;
        snprintf(li->regions[i].name, sizeof li->regions[i].name, "%.20s", g->e[i].name);
        mc_log(MC_LOG_DBG, "convet: %02d. name=%s, addr=0x%llx, size=0x%x, flag=0x%x!", i, g->e[i].name,
               (unsigned long long)g->e[i].base, g->e[i].size, g->e[i].flag);
    }
}

static int set_load_info(const struct mc_hw *hw, const struct mc_group *g)
{
    struct mc_load_info li;
    build_load_info(g, &li);
    mc_log(MC_LOG_INFO, "modem_set_load_info, path=%s!", g->ioctl_path);
    return hw->ioctl(hw->ctx, g->ioctl_path, MC_IOC_SET_LOAD_INFO, &li);
}

/* ---------------------------------------------------------------------------- */
/* region writes                                                                  */
/* ---------------------------------------------------------------------------- */

static int set_region(const struct mc_hw *hw, const struct mc_group *g, int idx)
{
    int v = idx;
    mc_log(MC_LOG_INFO, "modem_set_write_region, region = %d, path=%s!", idx, g->ioctl_path);
    return hw->ioctl(hw->ctx, g->ioctl_path, MC_IOC_SET_WRITE_REG, &v);
}

/* write a whole buffer to the currently selected region starting at offset 0 */
static int region_write(const struct mc_hw *hw, const struct mc_group *g, const struct mc_entry *e,
                        const void *buf, size_t len)
{
    if (len > e->size) {
        mc_log(MC_LOG_ERR, "%s: %zu bytes do not fit region %s (0x%x)", g->ioctl_path, len, e->name, e->size);
        return -EFBIG;
    }
    ssize_t w = hw->write_at(hw->ctx, g->ioctl_path, 0, buf, len);
    if (w != (ssize_t)len) {
        mc_log(MC_LOG_ERR, "failed to write %s [wsize=%zd, rsize=%zu]", g->ioctl_path, w, len);
        return w < 0 ? (int)w : -EIO;
    }
    return 0;
}

static int zero_region(const struct mc_hw *hw, const struct mc_group *g, const struct mc_entry *e)
{
    mc_log(MC_LOG_INFO, "clear region %s!", g->ioctl_path);
    uint8_t *z = calloc(1, CHUNK);
    if (!z)
        return -ENOMEM;
    int rc = 0;
    for (uint64_t off = 0; off < e->size && !rc;) {
        size_t n = e->size - off < CHUNK ? e->size - off : CHUNK;
        ssize_t w = hw->write_at(hw->ctx, g->ioctl_path, off, z, n);
        if (w != (ssize_t)n)
            rc = -EIO;
        off += n;
    }
    free(z);
    return rc;
}

/* modem_load_image(): partition[src_off .. +size) -> region[0 ..), in chunks */
static int load_image(const struct mc_hw *hw, const struct mc_group *g, const struct mc_entry *e,
                      uint32_t src_off, uint32_t size)
{
    mc_log(MC_LOG_INFO, "modem_load_image: (%s(0x%x) ==> %s(0x%x) size=0x%x)", e->part_name, src_off,
           g->ioctl_path, 0, size);
    if (size > e->size) {
        mc_log(MC_LOG_DBG, "load size 0x%x exceeds region 0x%x: the loader node clamps, so do we", size, e->size);
        size = e->size;
    }
    if ((int32_t)e->flag < 0) {
        int rc = zero_region(hw, g, e);
        if (rc)
            return rc;
    }
    uint8_t *buf = malloc(CHUNK);
    if (!buf)
        return -ENOMEM;
    int rc = 0;
    uint64_t done = 0;
    while (done < size) {
        size_t n = size - done < CHUNK ? size - done : CHUNK;
        ssize_t r = hw->read_part(hw->ctx, e->part_path, src_off + done, buf, n);
        if (r < 0) { rc = (int)r; break; }
        if (r == 0) break;                       /* short partition: same as the stock loop's read()==0 */
        ssize_t w = hw->write_at(hw->ctx, g->ioctl_path, done, buf, (size_t)r);
        if (w != r) {
            mc_log(MC_LOG_ERR, "failed to write %s [wsize=%zd, rsize=%zd, remain=%llu]", g->ioctl_path, w, r,
                   (unsigned long long)(size - done));
            rc = w < 0 ? (int)w : -EIO;
            break;
        }
        done += (uint64_t)r;
    }
    free(buf);
    if (!rc && done != size) {
        mc_log(MC_LOG_ERR, "short read from %s: 0x%llx of 0x%x", e->part_path, (unsigned long long)done, size);
        rc = -EIO;
    }
    return rc;
}

/* ---------------------------------------------------------------------------- */
/* secure images: pick the TA slot, record the footer, then load                  */
/* ---------------------------------------------------------------------------- */

static int sec_slot(const char *name)
{
    if (strstr(name, "pm_sys") || strstr(name, "pmsys")) return MC_SEC_PM;
    if (strstr(name, "modem")) return MC_SEC_MODEM;
    if (strstr(name, "gdsp") || strstr(name, "phy")) return MC_SEC_PHY;
    if (strstr(name, "ch") || strstr(name, "ldsp")) return MC_SEC_CH;
    return -1;
}

static void strip_slot(char *s)
{
    char *u = strrchr(s, '_');
    if (u && strlen(u) == 2)
        *u = 0;
}

static int load_secure(const struct mc_hw *hw, const struct mc_group *g, const struct mc_entry *e,
                       struct mc_sec_table *t)
{
    /* non-SCI partitions: image at offset 0; the modem image carries an extra 0x2000 in its load size */
    uint32_t load_size = e->size;
    if (strstr(e->part_name, "modem") && (e->flag & MCF_SEC))
        load_size += 0x2000;
    mc_log(MC_LOG_INFO, "secure_boot_get_patiton_info: image[%s], normal_offset=0x%x, load_offset=0x%x, load_size=0x%x",
           e->name, 0, 0, load_size);

    int slot = sec_slot(e->name);
    if (slot < 0) {
        mc_log(MC_LOG_ERR, "[secure]get_image_footer_byname: name not match: %s", e->name);
        return -EINVAL;
    }
    int64_t psz = hw->part_size(hw->ctx, e->part_path);
    uint8_t footer[64] = { 0 };
    if (psz < 0x40) {
        mc_log(MC_LOG_ERR, "[secure]failed to get partion size %s", e->part_path);
        return -EIO;
    }
    mc_log(MC_LOG_INFO, "[secure]get_verify_img_footer size = %#llx offset = %#llx", (long long)psz,
           (long long)(psz - 0x40));
    if (hw->read_part(hw->ctx, e->part_path, (uint64_t)(psz - 0x40), footer, sizeof footer) != 64) {
        mc_log(MC_LOG_ERR, "[secure]read footer failed on %s", e->part_path);
        return -EIO;
    }
    char pn[96];
    snprintf(pn, sizeof pn, "%s", e->part_name);
    strip_slot(pn);
    mc_sec_set_entry(t, slot, e->base, e->size, pn, footer);
    return load_image(hw, g, e, 0, load_size);
}

/* ---------------------------------------------------------------------------- */
/* boot code, cmdline, nv                                                         */
/* ---------------------------------------------------------------------------- */

static int load_bootcode(const struct mc_hw *hw, const struct mc_group *g, const struct mc_entry *e)
{
    size_t n = (size_t)e->bootcode_words * 4;
    if (n > 0x200)
        n = 0x200;
    mc_log(MC_LOG_INFO, "load_img_from_table: load %s boot code (%zu bytes)", e->name, n);
    if (!n) {
        mc_log(MC_LOG_WARN, "modem_load_cp_boot_code get boot code size is zero");
        return 0;
    }
    return region_write(hw, g, e, e->bootcode, n);
}

/* modem_load_cp_cmdline(): the part of /proc/cmdline after "modem=<state> " up to "cpcmdline=end" */
static int build_cp_cmdline(const struct mc_cfg *cfg, char *out, size_t cap)
{
    static char raw[4096];
    int n = mc_read_file(cfg->cmdline_path ? cfg->cmdline_path : "/proc/cmdline", raw, sizeof raw - 1);
    if (n <= 0)
        return n < 0 ? n : -EIO;
    raw[n] = 0;
    /* Only the trailing newline of /proc/cmdline is a terminator.  The bootloader of this board builds the
     * command line with embedded "\n      " indentation, so truncating at the first '\n' would drop
     * "modem=" and everything the CP needs (crystal, rf board, ...).  Embedded whitespace becomes a space. */
    while (n > 0 && (raw[n - 1] == '\n' || raw[n - 1] == '\r'))
        raw[--n] = 0;
    for (int i = 0; i < n; i++)
        if (raw[i] == '\n' || raw[i] == '\r' || raw[i] == '\t')
            raw[i] = ' ';

    const char *start = raw;
    const char *m = raw;
    int found = 0;
    while ((m = strstr(m, "modem="))) {
        if (m == raw || m[-1] == ' ') { found = 1; break; }
        m += 6;
    }
    char body[4096];
    if (found) {
        const char *sp = strchr(m, ' ');
        start = sp ? sp + 1 : m + strlen(m);
        snprintf(body, sizeof body, "%s", start);
        char *end = strstr(body, "cpcmdline=end");
        if (end) *end = 0;
        else mc_log(MC_LOG_INFO, "cmdline 'cpcmdline=end' is not exist");
    } else {
        mc_log(MC_LOG_INFO, "cmdline 'modem=' is not exist");
        snprintf(body, sizeof body, "%s", raw);
    }
    if (cfg->was_booted && cfg->was_booted() && strlen(body) + 17 < sizeof body)
        strcat(body, " modemboot=reset");
    size_t bl = strlen(body);
    if (cfg->append_log_chan)
        cfg->append_log_chan(body + bl, sizeof body - bl);
    /* lcd_id=xxx is forwarded as an extra token */
    const char *lcd = strstr(raw, "lcd_id=");
    if (lcd) {
        size_t cur = strlen(body);
        size_t k = 0;
        if (cur + 2 < sizeof body) {
            body[cur++] = ' ';
            while (lcd[k] && lcd[k] != ' ' && cur + 1 < sizeof body)
                body[cur++] = lcd[k++];
            body[cur] = 0;
        }
    }
    snprintf(out, cap, "%s", body);
    return 0;
}

static int load_cmdline(const struct mc_cfg *cfg, const struct mc_hw *hw, const struct mc_group *g,
                        const struct mc_entry *e)
{
    char cmd[0x1000];
    int rc = build_cp_cmdline(cfg, cmd, sizeof cmd);
    if (rc)
        return rc;
    mc_log(MC_LOG_INFO, "modem_load_cp_cmdline (/proc/cmdline ==> %s)", g->ioctl_path);
    mc_log(MC_LOG_INFO, "append log cmdline: %s.", cmd);
    mc_log(MC_LOG_INFO, "write cmdline [wsize = %zu]", strlen(cmd) + 1);
    return region_write(hw, g, e, cmd, strlen(cmd) + 1);
}

static int load_nv(const struct mc_cfg *cfg, const struct mc_hw *hw, const struct mc_group *g,
                   const struct mc_entry *e)
{
    int is_delta = !strcmp(e->name, "deltanv");
    int (*build)(const struct mc_cfg *, const struct mc_group *, uint8_t **, size_t *) =
        is_delta ? cfg->nv_build_deltanv : cfg->nv_build_fixnv;
    if (!build) {
        mc_log(MC_LOG_ERR, "no NV provider configured for %s", e->name);
        return -ENOSYS;
    }
    uint8_t *buf = NULL;
    size_t len = 0;
    mc_log(MC_LOG_INFO, "load_img_from_table: load nv (%s)", e->name);
    int rc = build(cfg, g, &buf, &len);
    if (rc) {
        mc_log(MC_LOG_ERR, "nv: building %s failed: %d", e->name, rc);
        return rc;
    }
    rc = region_write(hw, g, e, buf, len);
    free(buf);
    return rc;
}

/* ---------------------------------------------------------------------------- */
/* load_img_from_table + modem_load_run                                           */
/* ---------------------------------------------------------------------------- */

static int load_group(const struct mc_cfg *cfg, const struct mc_hw *hw, const struct mc_group *g,
                      uint32_t flag_mask, struct mc_sec_table *t)
{
    mc_log(MC_LOG_INFO, "load img: load_flag = 0x%x, skip_flag = 0x%x!", flag_mask, 0);
    for (int i = 0; i < g->n; i++) {
        const struct mc_entry *e = &g->e[i];
        if (e->size == 0 || !(e->flag & flag_mask))
            continue;
        int rc = set_region(hw, g, i);
        if (rc)
            return rc;
        if (e->flag & MCF_CMDLINE)       rc = load_cmdline(cfg, hw, g, e);
        else if (e->flag & MCF_NV)       rc = load_nv(cfg, hw, g, e);
        else if (e->flag & MCF_BOOTCODE) rc = load_bootcode(hw, g, e);
        else if (e->flag & MCF_SEC)      rc = load_secure(hw, g, e, t);
        else                             rc = load_image(hw, g, e, 0, e->size);
        if (rc) {
            mc_log(MC_LOG_ERR, "loading %s of %s failed: %d", e->name, g->kind, rc);
            return rc;
        }
    }
    return 0;
}

static int run_group(const struct mc_hw *hw, const struct mc_group *g, int start)
{
    mc_log(MC_LOG_INFO, "modem_load_run: run = %d!", start);
    int rc;
    if (start) {
        mc_log(MC_LOG_INFO, "modem_ioctrl_start, path=%s!", g->ioctl_path);
        rc = hw->ioctl(hw->ctx, g->ioctl_path, MC_IOC_START, NULL);
    } else {
        mc_log(MC_LOG_INFO, "modem_ioctrl_stop, path=%s!", g->ioctl_path);
        rc = hw->ioctl(hw->ctx, g->ioctl_path, MC_IOC_STOP, NULL);
        if (rc == -EINVAL)                 /* already stopped: the loader refuses a no-op stop */
            rc = 0;
        if (!rc && g->sub1_path[0]) {
            mc_log(MC_LOG_INFO, "modem_ioctrl_stop_unconditional, path=%s!", g->sub1_path);
            rc = hw->ioctl(hw->ctx, g->sub1_path, MC_IOC_STOP_UNCOND, NULL);
        }
    }
    return rc;
}

/* ---------------------------------------------------------------------------- */

int mc_load_modem_img(const struct mc_cfg *cfg, const struct mc_hw *hw, uint32_t type)
{
    struct mc_group cp, sp, ch;
    int rc = mc_layout_load(cfg, hw, &cp, &sp, &ch);
    if (rc)
        return rc;

    int do_sp = type & 0x01, do_ch = type & 0x20, do_cp = type & 0x0a;

    /* tell the loader nodes where everything lives (order: pmsys, chsys, modem); only on a full
     * first boot, a partial reload keeps what the kernel already holds */
    if (type == 0x3f &&
        ((rc = set_load_info(hw, &sp)) || (rc = set_load_info(hw, &ch)) || (rc = set_load_info(hw, &cp))))
        return rc;

    struct mc_sec_table t;
    mc_sec_init(&t);
    mc_sec_set_flag(&t, type);

    hw->wake_lock(hw->ctx, 1);
    mc_log(MC_LOG_INFO, "load_modem_img: load_type = 0x%x!", type);

    if (!cfg->skip_verify && (rc = mc_sec_unlock_ddr(hw, &t)))
        goto out;

    if ((do_sp && (rc = run_group(hw, &sp, 0))) || (do_ch && (rc = run_group(hw, &ch, 0))) ||
        (do_cp && (rc = run_group(hw, &cp, 0))))
        goto out;
    if (cfg->set_state)
        cfg->set_state(1);

    hw->bus_protect(hw->ctx, 0);
    rc = 0;
    if (do_sp) { mc_log(MC_LOG_INFO, "start load sp image!"); rc = load_group(cfg, hw, &sp, sp.mask, &t); }
    if (!rc && do_ch) { mc_log(MC_LOG_INFO, "start load ch image!"); rc = load_group(cfg, hw, &ch, ch.mask, &t); }
    if (!rc && do_cp) { mc_log(MC_LOG_INFO, "start load cp image!"); rc = load_group(cfg, hw, &cp, cp.mask, &t); }
    hw->bus_protect(hw->ctx, 1);
    if (rc)
        goto out;

    if (cfg->skip_verify)
        mc_log(MC_LOG_WARN, "skip cp image verify!");
    else if ((rc = mc_sec_verify_all(hw, &t)))
        goto out;

    if (cfg->no_start) {
        mc_log(MC_LOG_INFO, "no_start set: images loaded and verified, CPs left stopped");
        goto out;
    }
    if ((do_sp && (rc = run_group(hw, &sp, 1))) || (do_ch && (rc = run_group(hw, &ch, 1))) ||
        (do_cp && (rc = run_group(hw, &cp, 1))))
        goto out;
    if (cfg->set_state)
        cfg->set_state(2);
    mc_log(MC_LOG_INFO, "modem_ctrl_set_modem_state: g_modem_state = 2");
out:
    hw->wake_lock(hw->ctx, 0);
    return rc;
}

int mc_boot(const struct mc_cfg *cfg, const struct mc_hw *hw)
{
    return mc_load_modem_img(cfg, hw, cfg->load_type ? cfg->load_type : 0x3f);
}
