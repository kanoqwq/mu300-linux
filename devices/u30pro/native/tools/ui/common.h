/* Shared definitions of the screen UI: palette, data model, small formatting helpers. */
#ifndef U30PRO_UI_COMMON_H
#define U30PRO_UI_COMMON_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define SCREEN 240
#define MAXHITS 48

/* ------------------------------------------------------------------ palette */
typedef struct { const char *name; uint32_t a, b; } Theme;
static const Theme themes[] = {
	{ "海洋", 0x38D0FF, 0xFF7AC8 }, { "极光", 0x4ADE80, 0xFFC857 }, { "日落", 0xFF9F43, 0x6C8CFF }, { "紫罗兰", 0xA78BFA, 0x2DD4BF },
};
#define NTHEMES ((int)(sizeof themes / sizeof *themes))
#define C_TXT 0xF2F6FA
#define C_DIM 0x93A4B8
#define C_FAINT 0x56677A
#define C_OK 0x4ADE80
#define C_WARN 0xFBBF24
#define C_BAD 0xF87171
#define C_CARD 0xFFFFFF

/* ------------------------------------------------------------------ data model */
typedef struct { char name[32]; char ip[24]; int rssi; } Client;
typedef struct { char addr[28]; char ts[28]; int unread; char text[420]; } Sms;

typedef struct {
	time_t now;
	int clock_ok;
	/* battery */
	int bat_ok, bat_pct, charging, full;
	char bat_status[20];
	float bat_v, bat_a, bat_temp, bat_full_mah, bat_now_mah;
	float bat_i;		/* battery current with its sign: + charging, - discharging (A) */
	/* charger: online, direct supply (charging inhibited), charging paused by the temperature protection, USB voltage, input current limit */
	int chg_online, chg_bypass, chg_paused;
	float chg_vbus, chg_ilim;
	/* cellular */
	char cell_state[24], op[32], rat[8], ip[48], iface[16];
	int cell_connected, csq, have_sig, band, pci, earfcn;
	char lte_bw[48];	/* MU300 parsed downlink component widths, with unit */
	int bw_idx;		/* downlink bandwidth index 0..5 = 1.4/3/5/10/15/20 MHz, -1 unknown */
	int sinr_ok;		/* sinr is only fed while the signal page is on screen */
	float rsrp, rsrq, sinr;
	/* the NR leg of EN-DC, while it is added: band, PCI, NR-ARFCN, bandwidth in MHz; RSRP and SINR when the modem reports them */
	int nr, nr_band, nr_pci, nr_arfcn, nr_bw, nr_rsrp_ok, nr_sinr_ok;
	int nr_idle;		/* no NR leg now (an idle link): the fields above are the last one seen */
	float nr_rsrp, nr_sinr;
	/* wifi */
	int wifi_enabled, wifi_active, channel, nclients;
	char ssid[40], psk[72], wifi_band[12];
	int relay_state;	/* Wi-Fi relay: 0 off, 1 connecting, 2 connected with an address */
	char relay_ssid[40];
	int relay_on;		/* the relay is switched on in the settings */
	char relay_cfg[40];	/* the network it is set to join ("" until one is set in the web interface) */
	Client cl[6];
	int usb_up, admin_wifi, endc;
	/* sms */
	Sms sms[6];
	int nsms, sms_unread, sms_total;
	/* system */
	float cpu_pct, mem_pct, load1, temp_c;
	long uptime;
	char kernel[40], slot[4], version[40];
	int mem_total_mb;
	/* traffic */
	double rx_bps, tx_bps;
	uint64_t rx_total, tx_total;
	float hist_rx[60], hist_tx[60];
	int hist_n;
	/* feed freshness */
	int feed_ok;
} Data;

/* ------------------------------------------------------------------ formatting */
static void fmt_rate(double bps, char *num, size_t nc, const char **unit)
{
	if (bps < 1000.0) { snprintf(num, nc, "%d", (int)(bps + .5)); *unit = "B/s"; }
	else if (bps < 1e6) { double v = bps / 1000.0; snprintf(num, nc, v < 10 ? "%.1f" : "%.0f", v); *unit = "KB/s"; }
	else if (bps < 1e9) { double v = bps / 1e6; snprintf(num, nc, v < 10 ? "%.1f" : "%.0f", v); *unit = "MB/s"; }
	else { snprintf(num, nc, "%.1f", bps / 1e9); *unit = "GB/s"; }
}

static void fmt_bytes(uint64_t b, char *out, size_t cap)
{
	double v = (double)b;

	if (v < 1024.0) snprintf(out, cap, "%d B", (int)v);
	else if (v < 1024.0 * 1024) snprintf(out, cap, "%.0f KB", v / 1024);
	else if (v < 1024.0 * 1024 * 1024) snprintf(out, cap, "%.1f MB", v / 1048576.0);
	else snprintf(out, cap, "%.2f GB", v / 1073741824.0);
}

static void fmt_duration(long s, char *out, size_t cap)
{
	if (s < 0) s = 0;
	if (s >= 86400) snprintf(out, cap, "%ld天%ld时", s / 86400, (s % 86400) / 3600);
	else if (s >= 3600) snprintf(out, cap, "%ld时%ld分", s / 3600, (s % 3600) / 60);
	else snprintf(out, cap, "%ld分%ld秒", s / 60, s % 60);
}

static const char *wday_name(int w)
{
	static const char *n[] = { "周日", "周一", "周二", "周三", "周四", "周五", "周六" };

	return n[w % 7];
}

/* number of decimal places that keep three significant digits */
static float rsrp_level(float rsrp)
{
	/* 0..1 over -125 .. -70 dBm */
	float v = (rsrp + 125.f) / 55.f;

	return v < 0 ? 0 : (v > 1 ? 1 : v);
}

static int signal_bars_from_rsrp(float rsrp, int have)
{
	if (!have) return 0;
	if (rsrp >= -85) return 4;
	if (rsrp >= -95) return 3;
	if (rsrp >= -105) return 2;
	if (rsrp >= -115) return 1;
	return 0;
}
#endif
