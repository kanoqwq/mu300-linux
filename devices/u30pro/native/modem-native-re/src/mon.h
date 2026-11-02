/*
 * mon.h - runtime supervision of the Unisoc CP (modem) after it has been started.
 *
 * Reimplements the post-boot part of the stock vendor/bin/modem_control
 * (modem_ctrl_listen_sp/ch/modem/clients, wait_for_modem_alive, process_message,
 * prepare_reset_modem, sipa notification, log_chan cmdline parameters).
 * See docs/mon-flow.md for the reverse-engineered behaviour and docs/mon-status.md
 * for what has / has not been verified.
 *
 * Plain C99 + POSIX + pthreads. No Android API is used: everything Android
 * specific (property_get/set, logging, reboot, reload of the images) goes
 * through struct mon_ops and struct mon_cfg so it can be replaced or faked.
 *
 * INTERFACE CHANGES relative to README.md (parent agreed to these in advance):
 *   - mon_start(cfg, ops) takes a configuration struct as well.
 *   - mon_ops.reload() takes the load type (MON_LOAD_CP / MON_LOAD_CH) because
 *     the stock program reloads only the CP (type 2) or only the CH (type 0x20).
 *   - mon_ops.log() receives an already formatted string.
 */
#ifndef MON_H
#define MON_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* load_type bits understood by the stock load_modem_img(): */
#define MON_LOAD_SP 0x1u    /* pm_sys (/dev/pmsys)                           */
#define MON_LOAD_CP 0x2u    /* modem + phy (/dev/modem, /dev/phycp)          */
#define MON_LOAD_CH 0x20u   /* ch_sys (/dev/chsys)                           */
#define MON_LOAD_ALL 0x3fu  /* what the first boot uses                      */

/* g_modem_state in the stock program (DAT_0011d6b0). */
enum mon_state {
	MON_ST_OFF       = 0,
	MON_ST_LOADING   = 1,  /* images being written (set by load_modem_img)   */
	MON_ST_RUNNING   = 2,  /* START ioctl done, waiting for "Modem Alive"    */
	MON_ST_ALIVE     = 3,
	MON_ST_ASSERT    = 4,  /* "Modem Assert" read from the assert pipe       */
	MON_ST_BLOCKED   = 5,  /* "Modem Blocked" reported by a watchdog/client  */
	MON_ST_RESET     = 6,  /* CP announced "Modem Reset"                     */
	MON_ST_REBOOTING = 8,  /* whole system reboot requested                  */
	MON_ST_NOT_ALIVE = 9,  /* timeout waiting for "Modem Alive"              */
};

/* s_chState in the stock program (DAT_0011c9c4). */
enum mon_ch_state {
	MON_CH_OFFLINE  = 0,
	MON_CH_ALIVE    = 1,
	MON_CH_ASSERT   = 2,
	MON_CH_RESET    = 3,
	MON_CH_WATCHDOG = 4,   /* "CH Watchdog Timeout" read on /dev/sctl_pm     */
};

/* syslog-like priorities (the stock program uses android 3=debug, 4=info, 6=error). */
#define MON_LOG_DEBUG 3
#define MON_LOG_INFO  4
#define MON_LOG_ERROR 6

/* Replaceable environment. Any member may be NULL (a default is used). */
struct mon_ops {
	void *ctx;
	/* Rewrite the images and restart the given part of the CP.
	 * Called from the supervisor thread only, never concurrently.
	 * Must perform what the stock load_modem_img(load_type) does.
	 * Return 0 on success. */
	int  (*reload)(int load_type, void *ctx);
	/* Pre-formatted message; default: stderr. */
	void (*log)(int prio, const char *msg, void *ctx);
	/* Whole-system reboot. normal!=0 -> sysrq 'b', otherwise 'c' (crash for
	 * a ramdump) like the stock program. Default writes /proc/sysrq-trigger
	 * and then sleeps forever. */
	void (*reboot)(int normal, void *ctx);
	/* Android property_set() equivalent. The stock program only calls
	 * ("vendor.modem.sp-boot","1"), ("ctl.start"/"ctl.stop","vendor.cp_diskserver").
	 * Default: ignore. */
	void (*prop_set)(const char *key, const char *val, void *ctx);
	/* Every message that stock modem_write_data_to_clients() would have sent to
	 * the "modemd" socket clients. Default: ignore. */
	void (*on_message)(const char *msg, int len, void *ctx);
	/* Wake lock "modem_control". Default: write to the cfg wake_lock paths. */
	void (*wake_lock)(int on, void *ctx);
};

/* Static configuration (values the stock program fetches from Android properties
 * or hard codes). Use mon_cfg_default() and then override. */
struct mon_cfg {
	const char *root;             /* prefix for every path below (tests); "" */
	const char *sctl_pm;          /* /dev/sctl_pm       SP (pm_sys) events  */
	const char *sctl_ch;          /* /dev/sctl_ch       CH events           */
	const char *assert_dev;       /* /dev/spipe_nr2     ro.vendor.modem.assert */
	const char *alive_dev;        /* /dev/spipe_nr2     ro.vendor.modem.alive; NULL = use tty_dev */
	const char *wdt_dev;          /* /dev/modemwdtirq   ro.vendor.modem.dev+"wdtirq"; NULL = none */
	const char *tty_dev;          /* /dev/stty_nr       ro.vendor.modem.tty */
	const char *modem_dev;        /* /dev/modem         ioctl: assert info / force assert */
	const char *log_dev;          /* /dev/slog_nr       ro.vendor.modem.log  */
	const char *diag_dev;         /* /dev/sdiag_nr      ro.vendor.modem.diag */
	const char *loop_dev;         /* /dev/spipe_nr0     ro.vendor.modem.loop */
	const char *sipa_reset_path;  /* sysfs sipa_dele_reset; NULL/missing = skip */
	const char *wake_lock_path;   /* /sys/power/wake_lock                   */
	const char *wake_unlock_path; /* /sys/power/wake_unlock                 */
	const char *sysrq_path;       /* /proc/sysrq-trigger                    */

	int reset_opt;   /* persist.vendor.sys.modemreset: 0 off, 1 reload CP, 2 reboot whole system */
	int save_dump;   /* persist.vendor.sys.modem.save_dump                    */
	int dump_wait_s; /* max wait for a dump client ("SLOGMODEM DUMP COMPLETE"), stock = 300;
	                    0 = do not wait (router: nobody sends it)             */
	int debuggable;  /* ro.debuggable: 0 -> tell CH to enable its watchdog ("watchdog on") */
	int boot_mode_cali; /* stock boot mode == 1 (calibration): do not forward messages */
	int reboot_on_alive_timeout; /* stock: reset_opt != 0 -> reboot when "Modem Alive" never arrives */
	int reboot_delay_s;    /* sleep before sysrq, stock 5                      */
	int alive_timeout_s;   /* stock 60                                         */
	int reset_wait_s;      /* wait for CP to answer the diag reset cmd, stock 5 */
	int drain_timeout_ms;  /* log drain idle timeout in prepare_reset, stock 1000 */
};

struct mon_log_chan {      /* one "log_chan=sblock,<dst>,<chan>,<smem>" entry */
	unsigned dst;
	unsigned chan;
	uint32_t smem;
};

void mon_cfg_default(struct mon_cfg *cfg);

/* Start the supervisor threads (sp / ch / modem / control). Idempotent per process.
 * Returns 0 on success. cfg and ops are copied. */
int  mon_start(const struct mon_cfg *cfg, const struct mon_ops *ops);
/* Ask threads to finish and join them (tests; the daemon never calls this). */
void mon_stop(void);

/* Wait for "Modem Alive" after a start. Stock wait_for_modem_alive():
 * timeout_s<=0 -> cfg.alive_timeout_s. Returns 1 when alive, 0 otherwise.
 * On timeout it classifies the failure (state ASSERT or NOT_ALIVE), forwards the
 * message and, if cfg.reboot_on_alive_timeout, reboots the system. */
int  mon_wait_alive(int timeout_s);

/* State access / injection. */
int  mon_get_state(void);
int  mon_get_ch_state(void);
void mon_set_state(int state);          /* stock modem_ctrl_set_modem_state (3 also raises the alive flag) */
void mon_set_ch_state(int state);
/* Non-zero once the CP has been alive at least once: the boot code appends
 * " modemboot=reset" to the CP cmdline when this is set (stock modem_ctrl_is_modem_reset). */
int  mon_was_booted(void);

/* Feed a message exactly as if it had been read from one of the nodes
 * (stock modem_write_data_to_clients / process_message). */
void mon_inject(const char *msg);
/* A health check (or a human) decided the CP is stuck: stock "Modem Blocked" path. */
void mon_report_blocked(void);
/* A dump consumer finished ("SLOGMODEM DUMP COMPLETE"): releases the post-assert wait. */
void mon_notify_dump_complete(void);

/* ---- CP cmdline log_chan parameters (stock get_log_chan_param/append_log_chan_param) ---- */

/* Find the sipc channel whose DT label starts with `label` ("slog_nr" / "slog_phy")
 * below <sysroot>/sys/devices/platform and parse its base_addr file
 * ("<dst> <chan> 0x<smem> ..."). Returns 0 on success, -1 on failure. */
int  mon_get_log_chan(const char *sysroot, const char *label, struct mon_log_chan *out);
/* Append " log_chan=sblock,%u,%u,%08X" for slog_nr and slog_phy to `buf` (size `size`,
 * NUL terminated, buf must already hold a C string or be empty... the string is appended
 * at strlen(buf)). Returns the number of bytes appended (without NUL), or -1 when either
 * channel cannot be found or does not fit (buf is left unchanged in that case). */
int  mon_append_log_chan(const char *sysroot, char *buf, size_t size);

#ifdef __cplusplus
}
#endif
#endif /* MON_H */
