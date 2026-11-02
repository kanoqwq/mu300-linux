/* nvglue.c — connects the boot sequence to the NV module (nv.c): partition paths, slot, board id. */
#include "mc.h"
#include "nv.h"

#include <stdio.h>
#include <string.h>

struct paths {
    char fac[192], rt1[192], rt2[192], dl[192], delta[192], hw[16];
    struct nv_paths p;
};

static const struct mc_entry *find(const struct mc_group *g, const char *name)
{
    for (int i = 0; i < g->n; i++)
        if (!strcmp(g->e[i].name, name))
            return &g->e[i];
    return NULL;
}

static void nvlog(int prio, const char *msg)
{
    mc_log(prio >= 6 ? MC_LOG_ERR : MC_LOG_INFO, "nv: %s", msg);
}

static int fill(const struct mc_cfg *cfg, const struct mc_group *cp, struct paths *s)
{
    const struct mc_entry *fix = find(cp, "fixnv"), *delta = find(cp, "deltanv");
    if (!fix || !delta)
        return -1;
    memset(s, 0, sizeof *s);
    mc_partition_path(cfg, "nr_factorynv", s->fac, sizeof s->fac);
    mc_partition_path(cfg, "nr_runtimenv1", s->rt1, sizeof s->rt1);
    mc_partition_path(cfg, "nr_runtimenv2", s->rt2, sizeof s->rt2);
    snprintf(s->delta, sizeof s->delta, "%s", delta->part_path);

    /* "nr_deltanv_a" -> slot "_a" -> "nr_downloadnv_a" */
    const char *slot = strstr(delta->part_name, "nr_deltanv");
    slot = slot ? slot + strlen("nr_deltanv") : "";
    char dn[96];
    snprintf(dn, sizeof dn, "nr_downloadnv%s", slot);
    mc_partition_path(cfg, dn, s->dl, sizeof s->dl);

    char cmd[4096];
    int n = mc_read_file(cfg->cmdline_path ? cfg->cmdline_path : "/proc/cmdline", cmd, sizeof cmd - 1);
    cmd[n > 0 ? n : 0] = 0;
    nv_hwname_from_cmdline(cmd, s->hw);

    s->p.factorynv = s->fac;
    s->p.runtimenv1 = s->rt1;
    s->p.runtimenv2 = s->rt2;
    s->p.downloadnv = s->dl;
    s->p.deltanv = s->delta;
    s->p.hwname = s->hw;
    s->p.fixnv_region_size = fix->size;
    s->p.deltanv_region_size = delta->size;
    s->p.ignore_timestamp = cfg->nv_ignore_timestamp;
    nv_set_logger(nvlog);
    mc_log(MC_LOG_INFO, "nv: fac=%s rt1=%s rt2=%s dl=%s delta=%s hw=%s", s->fac, s->rt1, s->rt2, s->dl,
           s->delta, s->hw);
    return 0;
}

int mc_nv_build_fixnv(const struct mc_cfg *cfg, const struct mc_group *cp, uint8_t **out, size_t *len)
{
    struct paths s;
    if (fill(cfg, cp, &s))
        return -1;
    int rc = nv_build_fixnv(&s.p, out, len);
    if (rc > 0) {                       /* NV_ABNORMAL: damaged NV; the CP would run without calibration */
        mc_log(MC_LOG_ERR, "nv: fixnv is damaged (NV_ABNORMAL), refusing to boot with empty NV");
        return -1;
    }
    return rc;
}

int mc_nv_build_deltanv(const struct mc_cfg *cfg, const struct mc_group *cp, uint8_t **out, size_t *len)
{
    struct paths s;
    if (fill(cfg, cp, &s))
        return -1;
    return nv_build_deltanv(&s.p, out, len);
}
