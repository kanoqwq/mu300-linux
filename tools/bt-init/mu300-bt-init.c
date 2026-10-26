/*
 * mu300-bt-init: Unisoc Marlin3 (SC2355) Bluetooth vendor initialisation for BlueZ.
 *
 * Android's libbt-sprd_suite uploads two parameter blocks from /vendor/etc before the
 * stack sends its first standard HCI command, then enables the controller:
 *   0xFCA0  PSKey   (bt_configure_pskey.ini, 160 bytes, includes the BD address)
 *   0xFCA2  RF      (bt_configure_rf.ini, 252 bytes)
 *   0xFCA1  enable  (mode 0 = dual, 1 = enable)
 * Without this the firmware answers only a subset of HCI commands and BlueZ fails to
 * power the adapter. Run this on the H4 tty before `btattach -P h4`.
 *
 * usage: mu300-bt-init [-d /dev/ttyBT0] [-p pskey.ini] [-r rf.ini] [-a AA:BB:CC:DD:EE:FF] [-v]
 *                      [-x OPCODE:HEXPARAMS]...   (extra raw commands after init, for debugging)
 * SPDX-License-Identifier: MIT
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <unistd.h>

struct field { const char *name; int size; int count; };

/* order and sizes follow pskey_config_t / rf_config_t of the Marlin3 vendor library */
static const struct field pskey_fields[] = {
	{"device_class", 4, 1}, {"feature_set", 1, 16}, {"device_addr", 1, 6}, {"comp_id", 2, 1},
	{"g_sys_uart0_communication_supported", 1, 1}, {"cp2_log_mode", 1, 1}, {"LogLevel", 1, 1},
	{"g_central_or_perpheral", 1, 1}, {"Log_BitMask", 2, 1}, {"super_ssp_enable", 1, 1},
	{"common_rfu_b3", 1, 1}, {"common_rfu_w", 4, 2}, {"le_rfu_w", 4, 2}, {"lmp_rfu_w", 4, 2},
	{"lc_rfu_w", 4, 2}, {"g_wbs_nv_117", 2, 1}, {"g_wbs_nv_118", 2, 1}, {"g_nbv_nv_117", 2, 1},
	{"g_nbv_nv_118", 2, 1}, {"g_sys_sco_transmit_mode", 1, 1}, {"audio_rfu_b1", 1, 1},
	{"audio_rfu_b2", 1, 1}, {"audio_rfu_b3", 1, 1}, {"audio_rfu_w", 4, 2},
	{"g_sys_sleep_in_standby_supported", 1, 1}, {"g_sys_sleep_master_supported", 1, 1},
	{"g_sys_sleep_slave_supported", 1, 1}, {"power_rfu_b1", 1, 1}, {"power_rfu_w", 4, 2},
	{"win_ext", 4, 1}, {"edr_tx_edr_delay", 1, 1}, {"edr_rx_edr_delay", 1, 1}, {"tx_delay", 1, 1},
	{"rx_delay", 1, 1}, {"bb_rfu_w", 4, 2}, {"agc_mode", 1, 1}, {"diff_or_eq", 1, 1},
	{"ramp_mode", 1, 1}, {"modem_rfu_b1", 1, 1}, {"modem_rfu_w", 4, 2}, {"BQB_BitMask_1", 4, 1},
	{"BQB_BitMask_2", 4, 1}, {"bt_coex_threshold", 2, 8}, {"other_rfu_w", 4, 6}, {NULL, 0, 0}
};
static const struct field rf_fields[] = {
	{"g_GainValue_A", 2, 6}, {"g_ClassicPowerValue_A", 2, 10}, {"g_LEPowerValue_A", 2, 16},
	{"g_BRChannelpwrvalue_A", 2, 8}, {"g_EDRChannelpwrvalue_A", 2, 8}, {"g_LEChannelpwrvalue_A", 2, 8},
	{"g_GainValue_B", 2, 6}, {"g_ClassicPowerValue_B", 2, 10}, {"g_LEPowerValue_B", 2, 16},
	{"g_BRChannelpwrvalue_B", 2, 8}, {"g_EDRChannelpwrvalue_B", 2, 8}, {"g_LEChannelpwrvalue_B", 2, 8},
	{"LE_fix_powerword", 2, 1}, {"Classic_pc_by_channel", 1, 1}, {"LE_pc_by_channel", 1, 1},
	{"RF_switch_mode", 1, 1}, {"Data_Capture_Mode", 1, 1}, {"Analog_IQ_Debug_Mode", 1, 1},
	{"RF_common_rfu_b3", 1, 1}, {"RF_common_rfu_w", 4, 5}, {NULL, 0, 0}
};

static int verbose;

/* values[field][i]; unset entries stay 0 like the vendor library's memset */
static int load_ini(const char *path, const struct field *f, uint32_t vals[][16])
{
	char line[1024];
	FILE *fp = fopen(path, "r");
	if (!fp) { fprintf(stderr, "%s: %s\n", path, strerror(errno)); return -1; }
	while (fgets(line, sizeof(line), fp)) {
		char *eq, *name, *end, *tok, *save;
		if (line[0] == '#' || !(eq = strchr(line, '='))) continue;
		*eq = 0;
		for (name = line; *name == ' ' || *name == '\t'; name++);
		for (end = eq; end > name && (end[-1] == ' ' || end[-1] == '\t'); *--end = 0);
		for (int i = 0; f[i].name; i++) {
			if (strcmp(f[i].name, name)) continue;
			int n = 0;
			for (tok = strtok_r(eq + 1, ", \t\r\n#", &save); tok && n < f[i].count;
			     tok = strtok_r(NULL, ", \t\r\n#", &save))
				vals[i][n++] = strtoul(tok, NULL, 0);
		}
	}
	fclose(fp);
	return 0;
}

static int field_index(const struct field *f, const char *name)
{
	for (int i = 0; f[i].name; i++) if (!strcmp(f[i].name, name)) return i;
	return -1;
}

static int serialize(const struct field *f, uint32_t vals[][16], uint8_t *out)
{
	uint8_t *p = out;
	for (int i = 0; f[i].name; i++)
		for (int n = 0; n < f[i].count; n++)
			for (int b = 0; b < f[i].size; b++) *p++ = vals[i][n] >> (8 * b);
	return p - out;
}

static void hexdump(const char *pfx, const uint8_t *b, int n)
{
	if (!verbose) return;
	fprintf(stderr, "%s", pfx);
	for (int i = 0; i < n; i++) fprintf(stderr, " %02x", b[i]);
	fprintf(stderr, "\n");
}

static int read_full(int fd, uint8_t *b, int n, int timeout_ms)
{
	int got = 0;
	while (got < n) {
		struct pollfd pfd = { fd, POLLIN, 0 };
		int r = poll(&pfd, 1, timeout_ms);
		if (r <= 0) return -1;
		r = read(fd, b + got, n - got);
		if (r < 0 && errno == EINTR) continue;
		if (r <= 0) return -1;
		got += r;
	}
	return got;
}

/* send one command and wait for its Command Complete; returns the status byte or -1 */
static int hci_cmd(int fd, uint16_t op, const uint8_t *param, int len)
{
	uint8_t pkt[260] = { 0x01, op & 0xff, op >> 8, len };
	memcpy(pkt + 4, param, len);
	hexdump("<", pkt, len + 4);
	if (write(fd, pkt, len + 4) != len + 4) { perror("write"); return -1; }
	for (int tries = 0; tries < 8; tries++) {
		uint8_t h[3], ev[256];
		if (read_full(fd, h, 1, 3000) < 0) break;
		if (h[0] != 0x04) { hexdump("? skip", h, 1); continue; }
		if (read_full(fd, h + 1, 2, 1000) < 0 || read_full(fd, ev, h[2], 1000) < 0) break;
		hexdump(">", ev, h[2]);
		if (h[1] == 0x0e && h[2] >= 4 && (ev[1] | ev[2] << 8) == op) return ev[3];
		if (h[1] == 0x0f && h[2] >= 4 && (ev[2] | ev[3] << 8) == op && ev[0]) return ev[0];
	}
	fprintf(stderr, "no Command Complete for 0x%04x\n", op);
	return -1;
}

int main(int argc, char **argv)
{
	const char *dev = "/dev/ttyBT0", *pskey = "/usr/lib/firmware/bt_configure_pskey.ini";
	const char *rf = "/usr/lib/firmware/bt_configure_rf.ini", *addr = NULL;
	char *extra[16];
	int nextra = 0;
	static uint32_t pv[64][16], rv[32][16];
	uint8_t buf[256];
	int c, n, st;

	while ((c = getopt(argc, argv, "d:p:r:a:vx:")) != -1) {
		switch (c) {
		case 'd': dev = optarg; break;
		case 'p': pskey = optarg; break;
		case 'r': rf = optarg; break;
		case 'a': addr = optarg; break;
		case 'v': verbose = 1; break;
		case 'x': if (nextra < 16) extra[nextra++] = optarg; break;
		default:
			fprintf(stderr, "usage: %s [-d tty] [-p pskey.ini] [-r rf.ini] [-a bdaddr] [-v]\n", argv[0]);
			return 2;
		}
	}
	if (load_ini(pskey, pskey_fields, pv) || load_ini(rf, rf_fields, rv)) return 1;
	if (addr) {
		unsigned int m[6];
		int ai = field_index(pskey_fields, "device_addr");
		if (sscanf(addr, "%x:%x:%x:%x:%x:%x", &m[0], &m[1], &m[2], &m[3], &m[4], &m[5]) != 6) {
			fprintf(stderr, "bad address %s\n", addr);
			return 2;
		}
		for (int i = 0; i < 6; i++) pv[ai][5 - i] = m[i];   /* little endian on the wire */
	}

	int fd = open(dev, O_RDWR | O_NOCTTY);
	if (fd < 0) { perror(dev); return 1; }
	struct termios t;
	if (tcgetattr(fd, &t) == 0) {
		cfmakeraw(&t);
		t.c_cflag |= CLOCAL | CREAD;
		tcsetattr(fd, TCSANOW, &t);
	}
	tcflush(fd, TCIOFLUSH);

	n = serialize(pskey_fields, pv, buf);
	if ((st = hci_cmd(fd, 0xfca0, buf, n)) != 0) { fprintf(stderr, "PSKey upload failed (%d)\n", st); return 1; }
	n = serialize(rf_fields, rv, buf);
	if ((st = hci_cmd(fd, 0xfca2, buf, n)) != 0) { fprintf(stderr, "RF parameters failed (%d)\n", st); return 1; }
	/* the enable reply carries mode (2 bytes) before the status, so report it only */
	st = hci_cmd(fd, 0xfca1, (const uint8_t[]){ 0x00, 0x00, 0x01 }, 3);
	if (st < 0) { fprintf(stderr, "enable failed\n"); return 1; }
	for (int i = 0; i < nextra; i++) {
		uint8_t pb[255];
		int pl = 0;
		unsigned op = strtoul(extra[i], NULL, 16);
		char *h = strchr(extra[i], ':');
		for (h = h ? h + 1 : ""; h[0] && h[1] && pl < 255; h += 2) {
			char byte[3] = { h[0], h[1], 0 };
			pb[pl++] = strtoul(byte, NULL, 16);
		}
		printf("0x%04x -> status %d\n", op, hci_cmd(fd, op, pb, pl));
	}
	printf("Marlin3 Bluetooth initialised on %s (pskey %d bytes, rf %d bytes)\n", dev,
	       serialize(pskey_fields, pv, buf), serialize(rf_fields, rv, buf));
	close(fd);
	return 0;
}
