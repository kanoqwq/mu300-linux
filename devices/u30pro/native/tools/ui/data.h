/* Data collection of the screen UI: kernel files read directly every second, richer state from the feeder's key=value file. */
#ifndef U30PRO_UI_DATA_H
#define U30PRO_UI_DATA_H
#include <ctype.h>
#include <sys/stat.h>
#include <sys/utsname.h>
#include "common.h"

#ifndef UI_RUN
#define UI_RUN "/run/u30pro-ui"
#endif
#ifndef UI_SYSFS
#define UI_SYSFS ""
#endif

static Data D;
static char bat_dir[96];

static char *rd_text(const char *path, char *buf, size_t cap)
{
	FILE *f = fopen(path, "r");
	size_t n;

	if (!f) return NULL;
	n = fread(buf, 1, cap - 1, f);
	fclose(f);
	buf[n] = 0;
	while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r' || buf[n - 1] == ' ')) buf[--n] = 0;
	return buf;
}

static long rd_long(const char *path, long def)
{
	char b[64];

	if (!rd_text(path, b, sizeof b) || !*b) return def;
	char *e;
	long v = strtol(b, &e, 10);

	return e == b ? def : v;
}

static void battery_find(void)
{
	static const char *names[] = { "u30pro-battery", "battery", "sc27xx-fgu", "ump965x-fgu" };

	for (unsigned i = 0; i < sizeof names / sizeof *names; i++) {
		char p[160], t[32];

		snprintf(p, sizeof p, UI_SYSFS "/sys/class/power_supply/%s/type", names[i]);
		if (rd_text(p, t, sizeof t) && !strcmp(t, "Battery")) {
			snprintf(bat_dir, sizeof bat_dir, UI_SYSFS "/sys/class/power_supply/%s", names[i]);
			return;
		}
	}
	bat_dir[0] = 0;
}

static void collect_battery(void)
{
	char p[160], s[64];

	D.bat_ok = bat_dir[0] != 0;
	if (!D.bat_ok) return;
	snprintf(p, sizeof p, "%s/capacity", bat_dir);
	long cap = rd_long(p, -1);
	D.bat_pct = cap >= 0 && cap <= 100 ? (int)cap : -1;
	snprintf(p, sizeof p, "%s/status", bat_dir);
	if (!rd_text(p, D.bat_status, sizeof D.bat_status)) D.bat_status[0] = 0;
	/* the native fuel gauge has no status: the charger's (Charging, Full, Not charging when unplugged or inhibited) stands in for it */
	if (!D.bat_status[0] && !rd_text(UI_SYSFS "/sys/class/power_supply/u30pro-charger-master/status", D.bat_status, sizeof D.bat_status))
		D.bat_status[0] = 0;
	D.charging = !strcmp(D.bat_status, "Charging");
	D.full = !strcmp(D.bat_status, "Full");
	snprintf(p, sizeof p, "%s/voltage_now", bat_dir);
	long v = rd_long(p, -1);
	D.bat_v = v > 0 ? (float)v / 1e6f : 0;
	snprintf(p, sizeof p, "%s/current_now", bat_dir);
	v = rd_long(p, 0);
	D.bat_a = (float)(v < 0 ? -v : v) / 1e6f;
	D.bat_i = (float)v / 1e6f;
	snprintf(p, sizeof p, "%s/temp", bat_dir);
	v = rd_long(p, -9999);
	D.bat_temp = v == -9999 ? -99.f : (float)v / 10.f;
	snprintf(p, sizeof p, "%s/charge_full", bat_dir);
	v = rd_long(p, 0);
	D.bat_full_mah = (float)v / 1000.f;
	(void)s;
}

/* The charger (SGM41513, which measures nothing itself) and the USB input voltage from the PMIC ADC: channel 14 of the second PMIC's ADC
 * ("charge-vol" in the vendor's device tree, scaled to mV by the driver).  Read only while the power page is open (want_charger, set by it). */
static int want_charger;
static char vbus_path[128];

static void collect_charger(void)
{
	const char *base = UI_SYSFS "/sys/class/power_supply/u30pro-charger-master";
	char p[160], b[1024];

	snprintf(p, sizeof p, "%s/online", base);
	D.chg_online = rd_long(p, 0) == 1;
	snprintf(p, sizeof p, "%s/input_current_limit", base);
	D.chg_ilim = (float)rd_long(p, 0) / 1e6f;
	snprintf(p, sizeof p, "%s/charge_behaviour", base);
	D.chg_bypass = rd_text(p, b, sizeof b) && strstr(b, "[inhibit-charge]");
	if (!vbus_path[0])
		for (int i = 0; i < 8; i++) {
			snprintf(p, sizeof p, UI_SYSFS "/sys/bus/iio/devices/iio:device%d/name", i);
			if (rd_text(p, b, 128) && strstr(b, "pmic@1:adc")) {
				snprintf(vbus_path, sizeof vbus_path, UI_SYSFS "/sys/bus/iio/devices/iio:device%d/in_voltage14_input", i);
				break;
			}
		}
	D.chg_vbus = vbus_path[0] ? (float)rd_long(vbus_path, 0) / 1000.f : 0.f;
}

/* /proc/net/dev counters of one interface */
static int net_counters(const char *iface, uint64_t *rx, uint64_t *tx)
{
	FILE *f = fopen(UI_SYSFS "/proc/net/dev", "r");
	char line[512];
	size_t il = strlen(iface);

	if (!f) return 0;
	while (fgets(line, sizeof line, f)) {
		char *p = line;

		while (*p == ' ') p++;
		if (strncmp(p, iface, il) || p[il] != ':') continue;
		unsigned long long v[16] = {0};

		if (sscanf(p + il + 1, "%llu %*u %*u %*u %*u %*u %*u %*u %llu", &v[0], &v[8]) < 2) break;
		*rx = v[0];
		*tx = v[8];
		fclose(f);
		return 1;
	}
	fclose(f);
	return 0;
}

static void collect_traffic(long ms_now)
{
	static uint64_t prx, ptx;
	static long pms;
	static int have;
	uint64_t rx, tx;
	const char *ifn = D.iface[0] ? D.iface : "sipa_eth0";

	if (!net_counters(ifn, &rx, &tx)) { have = 0; D.rx_bps = D.tx_bps = 0; return; }
	if (have && ms_now > pms && rx >= prx && tx >= ptx) {
		double dt = (double)(ms_now - pms) / 1000.0;

		D.rx_bps = (double)(rx - prx) / dt;
		D.tx_bps = (double)(tx - ptx) / dt;
		memmove(D.hist_rx, D.hist_rx + 1, sizeof D.hist_rx - sizeof D.hist_rx[0]);
		memmove(D.hist_tx, D.hist_tx + 1, sizeof D.hist_tx - sizeof D.hist_tx[0]);
		D.hist_rx[59] = (float)D.rx_bps;
		D.hist_tx[59] = (float)D.tx_bps;
		if (D.hist_n < 60) D.hist_n++;
	}
	D.rx_total = rx;
	D.tx_total = tx;
	prx = rx; ptx = tx; pms = ms_now; have = 1;
}

static void collect_system(void)
{
	static unsigned long long pt, pi;
	char b[256];
	FILE *f = fopen(UI_SYSFS "/proc/stat", "r");
	unsigned long long u, n, s, idle, io, irq, sirq, st;

	if (f) {
		if (fgets(b, sizeof b, f) && sscanf(b, "cpu %llu %llu %llu %llu %llu %llu %llu %llu", &u, &n, &s, &idle, &io, &irq, &sirq, &st) >= 4) {
			unsigned long long total = u + n + s + idle + io + irq + sirq + st, id = idle + io;

			if (pt && total > pt) D.cpu_pct = 100.f * (1.f - (float)(id - pi) / (float)(total - pt));
			pt = total; pi = id;
		}
		fclose(f);
	}
	f = fopen(UI_SYSFS "/proc/meminfo", "r");
	if (f) {
		unsigned long long total = 0, avail = 0;

		while (fgets(b, sizeof b, f)) {
			sscanf(b, "MemTotal: %llu kB", &total);
			sscanf(b, "MemAvailable: %llu kB", &avail);
		}
		fclose(f);
		if (total) { D.mem_pct = 100.f * (1.f - (float)avail / (float)total); D.mem_total_mb = (int)(total / 1024); }
	}
	if (rd_text(UI_SYSFS "/proc/uptime", b, sizeof b)) D.uptime = (long)atof(b);
	if (rd_text(UI_SYSFS "/proc/loadavg", b, sizeof b)) D.load1 = (float)atof(b);
	D.temp_c = -99.f;
	/* prefer the SoC zone; otherwise the hottest valid zone */
	for (int z = 0; z < 48; z++) {
		char p[96], ty[48];
		long t;

		snprintf(p, sizeof p, UI_SYSFS "/sys/class/thermal/thermal_zone%d/temp", z);
		t = rd_long(p, -1);
		if (t <= 1000 || t >= 150000) continue;
		snprintf(p, sizeof p, UI_SYSFS "/sys/class/thermal/thermal_zone%d/type", z);
		if (rd_text(p, ty, sizeof ty) && !strcmp(ty, "soc-thmzone")) { D.temp_c = (float)t / 1000.f; break; }
		if ((float)t / 1000.f > D.temp_c) D.temp_c = (float)t / 1000.f;
	}
}

/* feeder file: "key=value" lines; list entries use "key.N=field<TAB>field...". */
static void feed_set(const char *k, char *v)
{
#define S(name, dst) do { if (!strcmp(k, name)) { snprintf(dst, sizeof dst, "%s", v); return; } } while (0)
#define I(name, dst) do { if (!strcmp(k, name)) { dst = atoi(v); return; } } while (0)
#define F(name, dst) do { if (!strcmp(k, name)) { dst = (float)atof(v); return; } } while (0)
	/* an empty access technology means "not reported this time" (modem busy or restarting): the last known one stays */
	if (!strcmp(k, "rat")) { if (*v) snprintf(D.rat, sizeof D.rat, "%s", v); return; }
	if (!strcmp(k, "sinr")) { D.sinr = (float)atof(v); D.sinr_ok = 1; return; }
	if (!strcmp(k, "nr_rsrp")) { D.nr_rsrp = (float)atof(v); D.nr_rsrp_ok = 1; return; }
	if (!strcmp(k, "nr_sinr")) { D.nr_sinr = (float)atof(v); D.nr_sinr_ok = 1; return; }
	S("lte_bw", D.lte_bw);
	S("op", D.op); S("cell_state", D.cell_state); S("ip", D.ip); S("iface", D.iface);
	S("relay_ssid", D.relay_ssid); I("relay_state", D.relay_state); S("relay_cfg", D.relay_cfg); I("relay_on", D.relay_on);
	S("ssid", D.ssid); S("psk", D.psk); S("wifi_band", D.wifi_band); S("kernel", D.kernel); S("slot", D.slot); S("version", D.version);
	I("cell_connected", D.cell_connected); I("csq", D.csq); I("have_sig", D.have_sig); I("band", D.band); I("pci", D.pci);
	I("earfcn", D.earfcn); I("bw_idx", D.bw_idx); I("nr", D.nr); I("nr_idle", D.nr_idle); I("nr_band", D.nr_band); I("nr_pci", D.nr_pci); I("nr_arfcn", D.nr_arfcn); I("nr_bw", D.nr_bw); I("wifi_enabled", D.wifi_enabled); I("wifi_active", D.wifi_active);
	I("admin_wifi", D.admin_wifi); I("chg_paused", D.chg_paused); I("endc", D.endc); I("channel", D.channel); I("nclients", D.nclients); I("usb_up", D.usb_up); I("sms_unread", D.sms_unread); I("sms_total", D.sms_total);
	F("rsrp", D.rsrp); F("rsrq", D.rsrq);
#undef S
#undef I
#undef F
	if (!strncmp(k, "cl.", 3)) {
		int i = atoi(k + 3);

		if (i >= 0 && i < 6) {
			char *t1 = strchr(v, '\t');

			snprintf(D.cl[i].name, sizeof D.cl[i].name, "%.*s", t1 ? (int)(t1 - v) : 31, v);
			if (t1) {
				char *t2 = strchr(t1 + 1, '\t');

				snprintf(D.cl[i].ip, sizeof D.cl[i].ip, "%.*s", t2 ? (int)(t2 - t1 - 1) : 23, t1 + 1);
				D.cl[i].rssi = t2 ? atoi(t2 + 1) : 0;
			}
		}
	} else if (!strncmp(k, "sms.", 4)) {
		int i = atoi(k + 4);

		if (i >= 0 && i < 6) {
			char *t1 = strchr(v, '\t'), *t2 = t1 ? strchr(t1 + 1, '\t') : NULL, *t3 = t2 ? strchr(t2 + 1, '\t') : NULL;

			if (t3) {
				snprintf(D.sms[i].addr, sizeof D.sms[i].addr, "%.*s", (int)(t1 - v), v);
				snprintf(D.sms[i].ts, sizeof D.sms[i].ts, "%.*s", (int)(t2 - t1 - 1), t1 + 1);
				D.sms[i].unread = atoi(t2 + 1);
				snprintf(D.sms[i].text, sizeof D.sms[i].text, "%s", t3 + 1);
				if (i + 1 > D.nsms) D.nsms = i + 1;
			}
		}
	}
}

/* The battery history of u30pro-battlog (/usr/share/u30pro/battery-log.uc): per range (since the last full charge, since the last charge,
 * 24 h, 7 days) its start, the percentage now, the points used, the time on battery and its average power, and 60 steps of percentage,
 * power drawn from the battery (mW) and state (D on battery, C charging, F full, P plugged without charging, '.' no records).  Read while the
 * battery page is open, again when the file changed (once a minute). */
#ifdef UI_HOST
#define BH_PATH UI_RUN "/battery-screen"
#else
#define BH_PATH "/run/u30pro-battery/screen"
#endif
#define BH_N 60
enum { BH_FULL, BH_CHARGE, BH_DAY, BH_WEEK, NBH };
typedef struct {
	int found, n, pct_now, used, onbat_s, avg_mw;
	long t0, t1;
	int pct[BH_N], mw[BH_N];
	char st[BH_N + 1];
} BattRange;
static BattRange bh[NBH];
static int bh_ok;

static void batlog_load(void)
{
	static const char *const keys[NBH] = { "full", "charge", "24h", "7d" };
	static time_t last_mtime;
	static long last_nsec = -1;
	struct stat st;
	char line[1024];
	FILE *f;
	int cur = -1;

	if (stat(BH_PATH, &st) != 0) { bh_ok = 0; return; }
	if (st.st_mtime == last_mtime && st.st_mtim.tv_nsec == last_nsec) return;
	last_mtime = st.st_mtime;
	last_nsec = st.st_mtim.tv_nsec;
	if (!(f = fopen(BH_PATH, "r"))) { bh_ok = 0; return; }
	memset(bh, 0, sizeof bh);
	bh_ok = 0;
	while (fgets(line, sizeof line, f)) {
		char key[16], *p = line + 2, *e;
		BattRange *r;

		line[strcspn(line, "\n")] = 0;
		if (line[0] == 'R' && line[1] == ' ') {
			BattRange t = { 0 };

			cur = -1;
			if (sscanf(p, "%15s %d %ld %ld %d %d %d %d", key, &t.found, &t.t0, &t.t1, &t.pct_now, &t.used, &t.onbat_s, &t.avg_mw) != 8) continue;
			for (int i = 0; i < NBH; i++) if (!strcmp(key, keys[i])) { cur = i; bh[i] = t; bh_ok = 1; }
			continue;
		}
		if (cur < 0 || line[1] != ' ') continue;
		r = &bh[cur];
		if (line[0] == 'S') {
			snprintf(r->st, sizeof r->st, "%s", p);
			continue;
		}
		if (line[0] != 'L' && line[0] != 'W') continue;
		int n = 0, *dst = line[0] == 'L' ? r->pct : r->mw;

		while (n < BH_N) {
			long v = strtol(p, &e, 10);

			if (e == p) break;
			dst[n++] = (int)v;
			p = e;
		}
		if (line[0] == 'L') r->n = n;
	}
	fclose(f);
}

/* reads feed.kv when it changed since the last call; returns whether it did (the host simulation: on the device the feed comes over
   ubus, tools/ui/bus.h) */
static int feed_load(void)
{
#ifndef UI_HOST
	return 0;
#endif
	static time_t last_mtime;
	static long last_nsec, last_size = -1;
	struct stat st;
	char path[96];

	snprintf(path, sizeof path, "%s/feed.kv", UI_RUN);
	if (stat(path, &st)) return 0;
	if (st.st_mtime == last_mtime && st.st_mtim.tv_nsec == last_nsec && st.st_size == last_size) return 0;
	last_mtime = st.st_mtime;
	last_nsec = st.st_mtim.tv_nsec;
	last_size = st.st_size;
	FILE *f = fopen(path, "r");
	char line[1024];

	if (!f) return 0;
	D.nsms = 0;
	D.sinr_ok = D.nr = D.nr_idle = D.nr_rsrp_ok = D.nr_sinr_ok = 0;
	D.bw_idx = -1;
	memset(D.cl, 0, sizeof D.cl);
	while (fgets(line, sizeof line, f)) {
		char *eq = strchr(line, '=');

		line[strcspn(line, "\r\n")] = 0;
		if (!eq) continue;
		*eq = 0;
		feed_set(line, eq + 1);
	}
	fclose(f);
	D.feed_ok = 1;
	return 1;
}

static void collect_all(long ms_now)
{
	struct utsname u;

	D.now = time(NULL);
	D.clock_ok = D.now > 1735689600;
	if (!bat_dir[0]) battery_find();
	collect_battery();
	if (want_charger) collect_charger();
	feed_load();
	collect_traffic(ms_now);
	collect_system();
	if (!D.kernel[0] && !uname(&u)) snprintf(D.kernel, sizeof D.kernel, "%.39s", u.release);
}
#endif
