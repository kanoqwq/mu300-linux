/* layout.c — region tables: XML parsing, partition path resolution, cp image header ("MECP").
 *
 * Mirrors modem_xml_init / modem_xml_to_load_info / modem_add_vab_suffix_to_path /
 * modem_head_correct_load_info of the stock program (see docs/boot-flow.md).
 */
#include "mc.h"

#include <ctype.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---------------------------------------------------------------------------- */
/* minimal XML scanner: tags with attributes only, comments skipped              */
/* ---------------------------------------------------------------------------- */

struct attr { char k[24]; char v[256]; };

static const char *attr_get(const struct attr *a, int n, const char *k)
{
    for (int i = 0; i < n; i++)
        if (!strcmp(a[i].k, k))
            return a[i].v;
    return NULL;
}

static uint64_t num(const char *s) { return s ? strtoull(s, NULL, 0) : 0; }

static void copy(char *dst, size_t n, const char *src)
{
    if (!src) { dst[0] = 0; return; }
    snprintf(dst, n, "%s", src);
}

static void parse_bootcode(struct mc_entry *e, const char *s)
{
    e->bootcode_words = 0;
    while (s && *s && e->bootcode_words < MC_MAX_BOOTCODE) {
        char *end;
        unsigned long v = strtoul(s, &end, 0);
        if (end == s)
            break;
        e->bootcode[e->bootcode_words++] = (uint32_t)v;
        s = end;
        while (*s == ' ')
            s++;
    }
}

static void handle_tag(struct mc_group *g, const char *tag, const struct attr *a, int na)
{
    const char *val = attr_get(a, na, "val");
    if (!strcmp(tag, "product"))            copy(g->product, sizeof g->product, val);
    else if (!strcmp(tag, "partion_cnt"))   g->partion_cnt = (uint32_t)num(val);
    else if (!strcmp(tag, "ioctl_path"))    copy(g->ioctl_path, sizeof g->ioctl_path, val);
    else if (!strcmp(tag, "sub1_ioctl"))    copy(g->sub1_path, sizeof g->sub1_path, val);
    else if (!strcmp(tag, "src_path"))      copy(g->src_path, sizeof g->src_path, val);
    else if (!strcmp(tag, "modem_range")) {
        g->modem_base = num(attr_get(a, na, "base"));
        g->modem_size = (uint32_t)num(attr_get(a, na, "size"));
    } else if (!strcmp(tag, "all_range")) {
        g->all_base = num(attr_get(a, na, "base"));
        g->all_size = (uint32_t)num(attr_get(a, na, "size"));
    } else if (!strncmp(tag, "partition", 9) && isdigit((unsigned char)tag[9])) {
        if (g->n >= MC_MAX_ENTRIES)
            return;
        struct mc_entry *e = &g->e[g->n++];
        memset(e, 0, sizeof *e);
        copy(e->name, sizeof e->name, attr_get(a, na, "name"));
        copy(e->src_file, sizeof e->src_file, attr_get(a, na, "src_file"));
        copy(e->dst_file, sizeof e->dst_file, attr_get(a, na, "dst_file"));
        e->base = num(attr_get(a, na, "base"));
        e->size = (uint32_t)num(attr_get(a, na, "size"));
        e->flag = (uint32_t)num(attr_get(a, na, "flag"));
        parse_bootcode(e, attr_get(a, na, "bootcode"));
    }
    /* modem_name is informational; the group kind comes from the file we were asked to load */
}

int mc_layout_parse_xml(const char *path, struct mc_group *g)
{
    static char buf[16384];
    int n = mc_read_file(path, buf, sizeof buf - 1);
    if (n < 0)
        return n;
    buf[n] = 0;

    const char *p = buf;
    while ((p = strchr(p, '<'))) {
        if (!strncmp(p, "<!--", 4)) {
            const char *e = strstr(p + 4, "-->");
            if (!e) break;
            p = e + 3;
            continue;
        }
        if (p[1] == '?' || p[1] == '/' || p[1] == '!') {
            p++;
            continue;
        }
        p++;
        char tag[32];
        size_t tl = 0;
        while (*p && !isspace((unsigned char)*p) && *p != '>' && *p != '/' && tl < sizeof tag - 1)
            tag[tl++] = *p++;
        tag[tl] = 0;

        struct attr a[16];
        int na = 0;
        while (*p && *p != '>') {
            while (isspace((unsigned char)*p) || *p == '/')
                p++;
            if (*p == '>' || !*p)
                break;
            struct attr cur = { { 0 }, { 0 } };
            size_t kl = 0;
            while (*p && *p != '=' && *p != '>' && !isspace((unsigned char)*p)) {
                if (kl < sizeof cur.k - 1)
                    cur.k[kl++] = *p;
                p++;
            }
            if (*p != '=' || p[1] != '"')
                continue;                           /* not a key="value" pair; skip */
            p += 2;
            size_t vl = 0;
            while (*p && *p != '"') {
                if (vl < sizeof cur.v - 1)
                    cur.v[vl++] = *p;
                p++;
            }
            if (*p == '"')
                p++;
            if (na < 16)
                a[na++] = cur;
        }
        handle_tag(g, tag, a, na);
    }
    if (g->n == 0)
        return -EINVAL;
    return 0;
}

/* ---------------------------------------------------------------------------- */
/* partition path resolution                                                      */
/* ---------------------------------------------------------------------------- */

struct pmap { char name[96]; char path[160]; };
static struct pmap g_map[64];
static int g_nmap;

static int map_load(const char *file)
{
    g_nmap = 0;
    if (!file)
        return 0;
    FILE *f = fopen(file, "r");
    if (!f)
        return -errno;
    while (g_nmap < 64 && fscanf(f, "%95s %159s", g_map[g_nmap].name, g_map[g_nmap].path) == 2)
        g_nmap++;
    fclose(f);
    return 0;
}

static const char *map_lookup(const char *name)
{
    for (int i = 0; i < g_nmap; i++)
        if (!strcmp(g_map[i].name, name))
            return g_map[i].path;
    return NULL;
}

const char *mc_partition_path(const struct mc_cfg *cfg, const char *name, char *out, size_t cap)
{
    (void)cfg;
    const char *m = map_lookup(name);
    if (m)
        snprintf(out, cap, "%s", m);
    else
        snprintf(out, cap, "/dev/block/by-name/%s", name);
    return out;
}

/* The stock program appends the A/B suffix to every readable path except these. */
static int wants_suffix(const char *src)
{
    return !(strstr(src, "NULL") || strstr(src, "fixnv") || strstr(src, "runtimenv") ||
             strstr(src, "cmdline") || strstr(src, "downloadnv"));
}

int mc_resolve_paths(const struct mc_cfg *cfg, struct mc_group *g, const char *slot)
{
    (void)cfg;
    for (int i = 0; i < g->n; i++) {
        struct mc_entry *e = &g->e[i];
        e->part_name[0] = e->part_path[0] = 0;
        if (!e->src_file[0] || strstr(e->src_file, "NULL") || (e->flag & MCF_CMDLINE))
            continue;
        if (strchr(e->src_file, '/')) {            /* absolute path given in the XML */
            char abs[64];
            snprintf(abs, sizeof abs, "%s", e->src_file);
            snprintf(e->part_path, sizeof e->part_path, "%s", abs);
            continue;
        }
        char srcf[64];
        snprintf(srcf, sizeof srcf, "%s", e->src_file);
        snprintf(e->part_name, sizeof e->part_name, "%s%s", srcf, wants_suffix(srcf) ? slot : "");
        const char *m = map_lookup(e->part_name);
        char pn[96], sp[64];
        snprintf(pn, sizeof pn, "%s", e->part_name);
        snprintf(sp, sizeof sp, "%s", g->src_path);
        if (m)
            snprintf(e->part_path, sizeof e->part_path, "%s", m);
        else
            snprintf(e->part_path, sizeof e->part_path, "%s%s", sp, pn);
    }
    return 0;
}

/* ---------------------------------------------------------------------------- */
/* MECP header                                                                    */
/* ---------------------------------------------------------------------------- */

static uint32_t le32(const uint8_t *p) { return p[0] | p[1] << 8 | p[2] << 16 | (uint32_t)p[3] << 24; }
static uint64_t le64(const uint8_t *p) { return le32(p) | (uint64_t)le32(p + 4) << 32; }

static int item(const uint8_t *p, struct mc_mecp_item *it)
{
    if (!p[0])
        return 0;
    memcpy(it->name, p, 20);
    it->name[20] = 0;
    it->base = le64(p + 0x18);
    it->size = le32(p + 0x20);
    return 1;
}

int mc_mecp_parse(const uint8_t h[MC_MECP_HDR_SIZE], struct mc_mecp *o)
{
    memset(o, 0, sizeof *o);
    uint32_t magic = le32(h);
    if (magic == MC_CPHD_MAGIC) {
        mc_log(MC_LOG_ERR, "cp image uses the CPHD container, not implemented (not needed on this firmware)");
        return -ENOTSUP;
    }
    if (magic != MC_MECP_MAGIC) {
        mc_log(MC_LOG_ERR, "cp image header magic 0x%08x is not MECP", magic);
        return -EPROTO;
    }
    for (int i = 0; i < MC_MECP_MAX_REG; i++)
        if (item(h + 0x1a0 + i * 0x30, &o->region[o->nregion]))
            o->nregion++;
        else
            break;
    for (int i = 0; i < MC_MECP_MAX_RES; i++)
        if (item(h + 0x320 + i * 0x28, &o->res[o->nres]))
            o->nres++;
        else
            break;
    uint32_t words = le32(h + 0xd0);
    if (words > (0x1a0 - 0xd4) / 4)
        words = (0x1a0 - 0xd4) / 4;
    for (uint32_t i = 0; i < words && i < MC_MAX_BOOTCODE; i++)
        o->bootcode[o->bootcode_words++] = le32(h + 0xd4 + i * 4);
    return 0;
}

static struct mc_entry *find_entry(struct mc_group *g, const char *name)
{
    for (int i = 0; i < g->n; i++)
        if (!strcmp(g->e[i].name, name))
            return &g->e[i];
    return NULL;
}

static int read_head(const struct mc_hw *hw, const struct mc_entry *e, struct mc_mecp *m)
{
    uint8_t h[MC_MECP_HDR_SIZE];
    if (!e->part_path[0])
        return -ENOENT;
    ssize_t r = hw->read_part(hw->ctx, e->part_path, 0, h, sizeof h);
    if (r != (ssize_t)sizeof h) {
        mc_log(MC_LOG_ERR, "failed to read %zu in %s (got %zd)", sizeof h, e->part_path, r);
        return r < 0 ? (int)r : -EIO;
    }
    return mc_mecp_parse(h, m);
}

static const struct mc_mecp_item *find_res(const struct mc_mecp *m, const char *name)
{
    for (int i = 0; i < m->nres; i++)
        if (!strcmp(m->res[i].name, name))
            return &m->res[i];
    return NULL;
}

/* modem_head_correct_load_info(): the phy header only contributes the PHY mini-dump slot and
 * the phy boot code; the modem header supplies the region list that rewrites the XML defaults. */
static int correct_cp(const struct mc_hw *hw, struct mc_group *cp)
{
    struct mc_entry *ephy = find_entry(cp, "phy"), *emod = find_entry(cp, "modem");
    if (!ephy || !emod) {
        mc_log(MC_LOG_ERR, "modem_head_correct_load_info: can't get modem img!");
        return -ENOENT;
    }
    struct mc_mecp ph, md;
    int rc = read_head(hw, ephy, &ph);
    if (rc)
        return rc;
    const struct mc_mecp_item *mini = find_res(&ph, "mini-dump");
    if (mini) { cp->mini_base[1] = mini->base; cp->mini_size[1] = mini->size; }
    struct mc_entry *bc = find_entry(cp, "phy_bootcode");
    if (bc) {
        memcpy(bc->bootcode, ph.bootcode, sizeof bc->bootcode);
        bc->bootcode_words = ph.bootcode_words;
    }

    rc = read_head(hw, emod, &md);
    if (rc)
        return rc;
    mini = find_res(&md, "mini-dump");
    if (mini) { cp->mini_base[0] = mini->base; cp->mini_size[0] = mini->size; }
    bc = find_entry(cp, "modem_bootcode");
    if (bc) {
        memcpy(bc->bootcode, md.bootcode, sizeof bc->bootcode);
        bc->bootcode_words = md.bootcode_words;
    }
    if (md.nregion == 0)
        return -EPROTO;
    for (int i = 0; i < cp->n; i++) {
        struct mc_entry *e = &cp->e[i];
        int found = 0;
        for (int j = 0; j < md.nregion; j++) {
            if (!strcmp(e->name, md.region[j].name)) {
                mc_log(MC_LOG_DBG, "modem_head_convert_img: find, region[%d].name = %s! %s: 0x%llx/0x%x -> 0x%llx/0x%x",
                       j, e->name, e->name, (unsigned long long)e->base, e->size,
                       (unsigned long long)md.region[j].base, md.region[j].size);
                e->base = md.region[j].base;
                e->size = md.region[j].size;
                found = 1;
                break;
            }
        }
        if (!found && (((e->flag >> 8) & 0xe) != 0))
            e->size = 0;                    /* modem-image style entry with no region: unused */
    }
    return 0;
}

/* ---------------------------------------------------------------------------- */

static int load_group(const struct mc_cfg *cfg, const char *file, const char *kind, uint32_t mask,
                      struct mc_group *g)
{
    char path[256];
    snprintf(path, sizeof path, "%s/%s", cfg->conf_dir, file);
    memset(g, 0, sizeof *g);
    snprintf(g->kind, sizeof g->kind, "%s", kind);
    g->mask = mask;
    int rc = mc_layout_parse_xml(path, g);
    if (rc) {
        mc_log(MC_LOG_ERR, "parse xml %s failed: %d", path, rc);
        return rc;
    }
    mc_log(MC_LOG_INFO, "parse xml, path is %s: %s partitions=%d ioctl=%s", path, kind, g->n, g->ioctl_path);
    return 0;
}

int mc_layout_load(const struct mc_cfg *cfg, const struct mc_hw *hw,
                   struct mc_group *cp, struct mc_group *sp, struct mc_group *ch)
{
    char slot[16] = "";
    if (cfg->slot_suffix) {
        snprintf(slot, sizeof slot, "%s", cfg->slot_suffix);
    } else {
        static char txt[8192];
        int n = mc_read_file(cfg->bootconfig_path ? cfg->bootconfig_path : "/proc/bootconfig", txt, sizeof txt - 1);
        int ok = 0;
        if (n > 0) { txt[n] = 0; ok = !mc_bootconfig_get(txt, "androidboot.slot_suffix", slot, sizeof slot); }
        if (!ok) {
            n = mc_read_file(cfg->cmdline_path ? cfg->cmdline_path : "/proc/cmdline", txt, sizeof txt - 1);
            if (n > 0) {
                txt[n] = 0;
                ok = !mc_cmdline_get(txt, "androidboot.slot_suffix", slot, sizeof slot) ||
                     !mc_cmdline_get(txt, "sprdboot.slot_suffix", slot, sizeof slot);
            }
        }
        if (!ok)
            mc_log(MC_LOG_WARN, "no slot suffix found, using none");
    }
    mc_log(MC_LOG_INFO, "slot suffix '%s'", slot);

    int rc = map_load(cfg->map_file);
    if (rc) {
        mc_log(MC_LOG_ERR, "cannot read partition map %s: %d", cfg->map_file, rc);
        return rc;
    }
    if ((rc = load_group(cfg, "modem_cp_info.xml", "cp", 0x00000f00, cp)) ||
        (rc = load_group(cfg, "modem_sp_info.xml", "sp", 0x0000f000, sp)) ||
        (rc = load_group(cfg, "modem_ch_info.xml", "ch", 0x00f00000, ch)))
        return rc;
    mc_resolve_paths(cfg, cp, slot);
    mc_resolve_paths(cfg, sp, slot);
    mc_resolve_paths(cfg, ch, slot);
    return correct_cp(hw, cp);
}
