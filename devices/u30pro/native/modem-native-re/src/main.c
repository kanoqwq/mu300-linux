/* main.c — u30-modemd: native replacement for /vendor/bin/modem_control on U30 Pro (UMS9632).
 *
 *   u30-modemd [--conf-dir DIR] [--map FILE] [--slot _a] [--trusty-dev DEV]
 *              [--no-start] [--no-monitor] [--force-load] [--check]
 *              [--nv-ignore-timestamp] [--reboot-on-failure] [-v]
 *
 * The kernel loader accepts SET_LOAD_INFO and ASSERT only from a task whose comm is exactly
 * "modem_control", so we rename ourselves before touching any node (and before creating threads).
 * Flow: load + verify + start the CPs (boot.c), then supervise them (mon.c) until killed.
 */
#define _GNU_SOURCE
#include "mc.h"
#include "mon.h"

#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <unistd.h>

static struct mc_cfg g_cfg;
static struct mc_hw g_hw;
static int g_allow_reboot;

static void usage(void)
{
    fputs("usage: u30-modemd [--conf-dir DIR] [--map FILE] [--slot _a] [--trusty-dev DEV]\n"
          "                  [--no-start] [--no-monitor] [--force-load] [--check]\n"
          "                  [--nv-ignore-timestamp] [--reboot-on-failure] [-v]\n", stderr);
}

/* ---- glue to the supervisor ------------------------------------------------------------- */

static int op_reload(int type, void *ctx)
{
    (void)ctx;
    mc_log(MC_LOG_INFO, "reload modem: type 0x%x", type);
    return mc_load_modem_img(&g_cfg, &g_hw, (uint32_t)type);
}

/* the monitor's debug lines stay debug (-v): its listen loop writes one every POLL_MS, which filled the 16 MiB /run of the
 * OpenWrt port in about 14 hours when they were passed on as info */
static void op_log(int prio, const char *msg, void *ctx)
{
    (void)ctx;
    mc_log(prio >= MON_LOG_ERROR ? MC_LOG_ERR : prio >= MON_LOG_INFO ? MC_LOG_INFO : MC_LOG_DBG, "%s", msg);
}

/* The stock program reboots the whole phone through sysrq when the CP cannot be revived.  On a
 * router in bring-up that turns a modem bug into a boot loop, so it is opt-in. */
static void op_reboot(int normal, void *ctx)
{
    (void)ctx;
    if (!g_allow_reboot) {
        mc_log(MC_LOG_ERR, "system %s requested by the supervisor; ignored (use --reboot-on-failure)",
               normal ? "reboot" : "crash-reboot");
        return;
    }
    int fd = open("/proc/sysrq-trigger", O_WRONLY | O_CLOEXEC);
    if (fd >= 0) {
        if (write(fd, normal ? "b" : "c", 1) < 0)
            mc_log(MC_LOG_ERR, "sysrq write failed: %s", strerror(errno));
        close(fd);
    }
    for (;;)
        sleep(60);
}

static int append_log_chan(char *buf, size_t room) { return mon_append_log_chan("", buf, room); }
static void set_state(int st) { mon_set_state(st); }

/* PM SYS may already be alive in charger boot while CH/NR are shut down.
 * Opening sctl_pm proves only that auxiliary core's SBUF is ready. Require
 * the control transport of every core before skipping the full load. These
 * probes neither read a notification nor compete for the AT rings. */
static int cp_already_booted(void)
{
    static const char *const controls[] = {
        "/dev/sctl_pm", "/dev/sctl_ch", "/dev/spipe_nr2"
    };
    for (size_t i = 0; i < sizeof controls / sizeof controls[0]; i++) {
        int fd = open(controls[i], O_RDWR | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) {
            mc_log(MC_LOG_INFO, "CP control transport not ready: %s (%s)",
                   controls[i], strerror(errno));
            return 0;
        }
        close(fd);
    }
    return 1;
}

static void on_term(int sig) { (void)sig; _exit(0); }

/* SIGUSR1 = the host supervisor asks for a CP-only subsystem restart (SSR): reset+reload the
 * modem (modem+phy) without rebooting the whole device, exactly as on a CP assert.  Used by
 * u30pro-celld when AT+CFUN=1 hangs, before escalating to a full reboot. */
static volatile sig_atomic_t g_ssr_req;
static void on_ssr(int sig) { (void)sig; g_ssr_req = 1; }

int main(int argc, char **argv)
{
    memset(&g_cfg, 0, sizeof g_cfg);
    g_cfg.conf_dir = "/etc/u30pro/modem";
    g_cfg.trusty_dev = "/dev/trusty-ipc-dev0";
    int force = 0, check = 0, monitor = 1;

    static const struct option opts[] = {
        { "conf-dir", 1, 0, 'c' }, { "map", 1, 0, 'm' }, { "slot", 1, 0, 's' },
        { "trusty-dev", 1, 0, 't' }, { "no-start", 0, 0, 'n' }, { "no-monitor", 0, 0, 'M' },
        { "force-load", 0, 0, 'f' }, { "check", 0, 0, 'k' }, { "nv-ignore-timestamp", 0, 0, 'T' },
        { "reboot-on-failure", 0, 0, 'R' }, { "verbose", 0, 0, 'v' }, { "help", 0, 0, 'h' }, { 0, 0, 0, 0 },
    };
    int o;
    while ((o = getopt_long(argc, argv, "c:m:s:t:nMfkTRvh", opts, NULL)) != -1) {
        switch (o) {
        case 'c': g_cfg.conf_dir = optarg; break;
        case 'm': g_cfg.map_file = optarg; break;
        case 's': g_cfg.slot_suffix = optarg; break;
        case 't': g_cfg.trusty_dev = optarg; break;
        case 'n': g_cfg.no_start = 1; break;
        case 'M': monitor = 0; break;
        case 'f': force = 1; break;
        case 'k': check = 1; break;
        case 'T': g_cfg.nv_ignore_timestamp = 1; break;
        case 'R': g_allow_reboot = 1; break;
        case 'v': mc_set_log_level(MC_LOG_DBG); break;
        default: usage(); return 2;
        }
    }

    if (prctl(PR_SET_NAME, "modem_control", 0, 0, 0))
        mc_log(MC_LOG_WARN, "cannot set task name: %s (loader will refuse SET_LOAD_INFO)", strerror(errno));
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGUSR1, on_ssr);

    g_hw = mc_hw_linux;
    g_hw.ctx = (void *)g_cfg.trusty_dev;

    g_cfg.nv_build_fixnv = mc_nv_build_fixnv;
    g_cfg.nv_build_deltanv = mc_nv_build_deltanv;
    g_cfg.append_log_chan = append_log_chan;
    g_cfg.set_state = set_state;
    g_cfg.was_booted = mon_was_booted;

    if (check) {                              /* parse + resolve everything, touch nothing */
        struct mc_group cp, sp, ch;
        int rc = mc_layout_load(&g_cfg, &g_hw, &cp, &sp, &ch);
        mc_log(MC_LOG_INFO, "layout check: %d", rc);
        return rc ? 1 : 0;
    }

    char cmdline[4096];
    int n = mc_read_file("/proc/cmdline", cmdline, sizeof cmdline - 1);
    char state[32] = "";
    if (n > 0) { cmdline[n] = 0; mc_cmdline_get(cmdline, "modem", state, sizeof state); }
    mc_log(MC_LOG_INFO, "modem state on the kernel cmdline: '%s'", state);
    int skip_load = !force && (strcmp(state, "shutdown") || cp_already_booted());

    struct mon_cfg mcfg;
    mon_cfg_default(&mcfg);
    mcfg.reset_opt = 1;                       /* persist.vendor.sys.modemreset = 1 : reload the CP */
    mcfg.save_dump = 0;                       /* no dump consumer on a router */
    mcfg.dump_wait_s = 0;
    mcfg.debuggable = 1;                      /* ro.debuggable = 1 in the stock property set */
    mcfg.reboot_on_alive_timeout = g_allow_reboot;
    struct mon_ops ops;
    memset(&ops, 0, sizeof ops);
    ops.reload = op_reload;
    ops.log = op_log;
    ops.reboot = op_reboot;

    if (skip_load) {
        mc_log(MC_LOG_INFO, "CP is already booted (or not in shutdown): nothing to load");
    } else {
        int rc = mc_boot(&g_cfg, &g_hw);
        if (rc) {
            mc_log(MC_LOG_ERR, "boot modem fail! (%d)", rc);
            return 1;
        }
        mc_log(MC_LOG_INFO, "boot sequence done");
        if (g_cfg.no_start)
            return 0;
    }

    if (!monitor)
        return 0;
    if (mon_start(&mcfg, &ops)) {
        mc_log(MC_LOG_ERR, "cannot start the supervisor");
        return 1;
    }
    if (!skip_load && !mon_wait_alive(0))
        mc_log(MC_LOG_ERR, "wait_for_modem_alive: modem did not come alive");
    for (;;) {                                 /* supervise; SIGUSR1 => host-requested CP subsystem restart */
        pause();
        if (g_ssr_req) {
            g_ssr_req = 0;
            mc_log(MC_LOG_INFO, "host-requested CP subsystem restart (SIGUSR1): reloading CP");
            mon_report_blocked();
        }
    }
}
