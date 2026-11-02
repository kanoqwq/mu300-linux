/* SPDX-License-Identifier: MIT
 * u30pro-nfc: the NFC tag of the U30 Pro as a tap-to-connect token for the hotspot.
 *
 * The tag is a Fudan FM11NT08x on I2C bus 4, address 0x57 (stock device tree node ntag_fm11_gpio; the stock kernel driver only
 * exposes its reset and interrupt lines).  It is an NFC Forum Type 2 tag whose memory the I2C side addresses byte for byte with a
 * 16-bit address: UID and lock bytes at 0x00-0x0B, the capability container at 0x0C (E1 10 6D 00 on the device: NDEF, 0x6D * 8 =
 * 872 bytes, read/write), the data area from 0x10.  The stock firmware kept a Wi-Fi Simple Configuration credential there (an NDEF
 * record of type application/vnd.wfa.wsc, what phones act on when they touch the device); this tool writes the current hotspot the
 * same way.  Writes go in 4-byte pages (the Type 2 page), only the pages that differ, each followed by acknowledge polling for the
 * EEPROM write cycle, and the whole message is read back.
 * The SAK the RF side answers with comes from EEPROM byte 0x3BF: the factory value 0x20 claims ISO-DEP, phones then talk Type 4 to
 * it and see nothing.  The stock firmware wrote 0x00 (a Type 2 tag) when the hotspot came up, then RF_TXEN (0xFFF4) = 0x77 and a
 * soft reset (0xFFE6 = 0x55) so the chip takes it; wifi and clear do the same when the byte is not 0x00 yet.
 *   u30pro-nfc show                            the capability container and the SSID and authentication of the credential (no key)
 *   u30pro-nfc wifi <ssid> open|wpa2           write the hotspot; the key of wpa2 comes on stdin (8-63 characters or 64 hex digits)
 *   u30pro-nfc clear                           an empty NDEF message
 *   u30pro-nfc encode <ssid> open|wpa2 [key]   print the data area that "wifi" writes, in hex (no I2C; for tests)
 * Prints "written" or "unchanged" after wifi/clear. */
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

#define BUS "/dev/i2c-4"
#define ADDR 0x57
#define CC 0x0c
#define SAK_CTL 0x3bf
#define RF_TXEN 0xfff4
#define RESET_SILENCE 0xffe6
#define DATA 0x10
#define PAGE 4
#define MAXMSG 512

static const char WSC_TYPE[] = "application/vnd.wfa.wsc";
static int fd = -1;

static int xfer(struct i2c_msg *m, int n)
{
	struct i2c_rdwr_ioctl_data d = { m, n };

	return ioctl(fd, I2C_RDWR, &d) == n ? 0 : -1;
}

static int tag_read(unsigned a, unsigned char *buf, unsigned n)
{
	unsigned char ad[2] = { a >> 8, a & 0xff };
	struct i2c_msg m[2] = { { ADDR, 0, 2, ad }, { ADDR, I2C_M_RD, n, buf } };

	return xfer(m, 2);
}

static void msleep(int ms)
{
	struct timespec t = { 0, ms * 1000000L };

	nanosleep(&t, NULL);
}

/* bytes within one page, then acknowledge polling: the chip does not answer while it writes its EEPROM */
static int tag_write(unsigned a, const unsigned char *p, unsigned n)
{
	unsigned char b[2 + PAGE] = { a >> 8, a & 0xff };
	struct i2c_msg m = { ADDR, 0, 2 + n, b };
	unsigned char probe;
	int i;

	memcpy(b + 2, p, n);
	if (xfer(&m, 1))
		return -1;
	for (i = 0; i < 50; i++) {
		msleep(1);
		if (!tag_read(a, &probe, 1))
			return 0;
	}
	return -1;
}

/* a register write (no EEPROM cycle) */
static int tag_reg(unsigned a, unsigned char v)
{
	unsigned char b[3] = { a >> 8, a & 0xff, v };
	struct i2c_msg m = { ADDR, 0, 3, b };

	return xfer(&m, 1);
}

/* make the RF side a Type 2 tag (SAK 0x00), as the stock firmware did */
static int rf_type2(void)
{
	unsigned char sak, zero = 0;
	int i;

	if (tag_read(SAK_CTL, &sak, 1))
		return -1;
	if (!sak)
		return 0;
	if (tag_write(SAK_CTL, &zero, 1) || tag_reg(RF_TXEN, 0x77) || tag_reg(RESET_SILENCE, 0x55))
		goto fail;
	for (i = 0; i < 50; i++) {
		msleep(2);
		if (!tag_read(SAK_CTL, &sak, 1))
			break;
	}
	if (i < 50 && !sak) {
		fprintf(stderr, "u30pro-nfc: SAK set to 0x00 (Type 2 tag)\n");
		return 0;
	}
fail:
	fprintf(stderr, "u30pro-nfc: could not set the SAK at 0x%03x: %s\n", SAK_CTL, strerror(errno));
	return -1;
}

static unsigned put16(unsigned char *p, unsigned v)
{
	p[0] = v >> 8;
	p[1] = v & 0xff;
	return 2;
}

static unsigned tlv(unsigned char *p, unsigned type, const void *v, unsigned len)
{
	put16(p, type);
	put16(p + 2, len);
	memcpy(p + 4, v, len);
	return 4 + len;
}

/* the data area for a hotspot: NDEF TLV with one WSC record, then the terminator TLV.  Returns its length, 0 when it does not fit */
static unsigned encode(unsigned char *out, const char *ssid, int wpa2, const char *key)
{
	unsigned char cred[200], wsc[256];
	unsigned c = 0, w = 0, n = 0, auth = wpa2 ? 0x0020 : 0x0001, enc = wpa2 ? 0x0008 : 0x0001;
	unsigned char v16[2], idx = 1, ver = 0x10, mac[6] = { 0xff, 0xff, 0xff, 0xff, 0xff, 0xff };
	static const unsigned char wfa_ext[6] = { 0x00, 0x37, 0x2a, 0x00, 0x01, 0x20 };	/* WFA vendor id, Version2 2.0 */
	size_t sl = strlen(ssid), kl = wpa2 ? strlen(key) : 0;

	if (!sl || sl > 32 || kl > 64)
		return 0;
	c += tlv(cred + c, 0x1026, &idx, 1);			/* Network Index */
	c += tlv(cred + c, 0x1045, ssid, sl);			/* SSID */
	put16(v16, auth);
	c += tlv(cred + c, 0x1003, v16, 2);			/* Authentication Type: open / WPA2-Personal */
	put16(v16, enc);
	c += tlv(cred + c, 0x100f, v16, 2);			/* Encryption Type: none / AES */
	c += tlv(cred + c, 0x1027, key ? key : "", kl);		/* Network Key */
	c += tlv(cred + c, 0x1020, mac, 6);			/* MAC Address: any */
	w += tlv(wsc + w, 0x104a, &ver, 1);			/* Version 1.0 */
	w += tlv(wsc + w, 0x100e, cred, c);			/* Credential */
	w += tlv(wsc + w, 0x1049, wfa_ext, sizeof wfa_ext);	/* Vendor Extension */
	if (w > 255 || 3 + sizeof WSC_TYPE - 1 + w > 254)
		return 0;
	out[n++] = 0x03;					/* NDEF message TLV */
	out[n++] = 3 + sizeof WSC_TYPE - 1 + w;
	out[n++] = 0xd2;					/* MB ME SR, TNF media type */
	out[n++] = sizeof WSC_TYPE - 1;
	out[n++] = w;
	memcpy(out + n, WSC_TYPE, sizeof WSC_TYPE - 1);
	n += sizeof WSC_TYPE - 1;
	memcpy(out + n, wsc, w);
	n += w;
	out[n++] = 0xfe;					/* terminator TLV */
	return n;
}

static int open_tag(unsigned *size)
{
	unsigned char cc[4];

	fd = open(BUS, O_RDWR | O_CLOEXEC);
	if (fd < 0 || tag_read(CC, cc, 4)) {
		fprintf(stderr, "u30pro-nfc: no tag on %s at 0x%02x: %s\n", BUS, ADDR, strerror(errno));
		return -1;
	}
	if (cc[0] != 0xe1 || (cc[3] & 0xf0)) {
		fprintf(stderr, "u30pro-nfc: capability container %02x %02x %02x %02x: not a writable NDEF tag\n", cc[0], cc[1], cc[2], cc[3]);
		return -1;
	}
	*size = cc[2] * 8;
	return 0;
}

/* write what differs, page by page, and read the whole message back */
static int store(const unsigned char *msg, unsigned n, unsigned size)
{
	unsigned char cur[MAXMSG + PAGE], page[PAGE];
	unsigned len = (n + PAGE - 1) / PAGE * PAGE, a, changed = 0;

	if (len > size || len > MAXMSG) {
		fprintf(stderr, "u30pro-nfc: %u bytes do not fit the tag (%u)\n", n, size);
		return -1;
	}
	if (tag_read(DATA, cur, len))
		return -1;
	for (a = 0; a < len; a += PAGE) {
		memset(page, 0, PAGE);
		memcpy(page, msg + a, a + PAGE <= n ? PAGE : n - a);
		if (!memcmp(page, cur + a, PAGE))
			continue;
		if (tag_write(DATA + a, page, PAGE)) {
			fprintf(stderr, "u30pro-nfc: write at 0x%03x failed: %s\n", DATA + a, strerror(errno));
			return -1;
		}
		changed = 1;
	}
	if (tag_read(DATA, cur, n) || memcmp(cur, msg, n)) {
		fprintf(stderr, "u30pro-nfc: the tag does not read back what was written\n");
		return -1;
	}
	puts(changed ? "written" : "unchanged");
	return 0;
}

static unsigned get16(const unsigned char *p)
{
	return p[0] << 8 | p[1];
}

static int show(unsigned size)
{
	unsigned char m[MAXMSG], sak;
	unsigned n = size < MAXMSG ? size : MAXMSG, len, tl, pl, i;
	const unsigned char *w, *e;

	if (tag_read(DATA, m, n))
		return -1;
	printf("tag %s 0x%02x, data area %u bytes", BUS, ADDR, size);
	if (!tag_read(SAK_CTL, &sak, 1))
		printf(", SAK 0x%02x%s", sak, sak ? " (phones do not read it)" : "");
	putchar('\n');
	if (m[0] != 0x03) {
		printf("no NDEF message (first TLV 0x%02x)\n", m[0]);
		return 0;
	}
	len = m[1];
	if (!len) {
		puts("empty NDEF message");
		return 0;
	}
	tl = m[3];
	pl = m[4];
	if (!(m[2] & 0x10) || 5 + tl + pl > n || tl != sizeof WSC_TYPE - 1 || memcmp(m + 5, WSC_TYPE, tl)) {
		printf("NDEF message of %u bytes, not a Wi-Fi credential\n", len);
		return 0;
	}
	for (w = m + 5 + tl, e = w + pl; w + 4 <= e; w += 4 + get16(w + 2)) {
		if (get16(w) != 0x100e)
			continue;
		for (const unsigned char *c = w + 4, *ce = c + get16(w + 2); c + 4 <= ce && c + 4 + get16(c + 2) <= ce; c += 4 + get16(c + 2)) {
			unsigned t = get16(c), l = get16(c + 2);

			if (t == 0x1045) {
				printf("ssid ");
				for (i = 0; i < l; i++)
					putchar(c[4 + i] >= 0x20 && c[4 + i] < 0x7f ? c[4 + i] : '?');
				putchar('\n');
			} else if (t == 0x1003 && l == 2) {
				printf("auth 0x%04x\n", get16(c + 4));
			} else if (t == 0x1027) {
				printf("key %u characters\n", l);
			}
		}
	}
	return 0;
}

static int auth_arg(const char *a)
{
	if (!strcmp(a, "open"))
		return 0;
	if (!strcmp(a, "wpa2"))
		return 1;
	return -1;
}

static int key_ok(const char *k)
{
	size_t l = strlen(k);

	if (l == 64)
		return strspn(k, "0123456789abcdefABCDEF") == 64;
	return l >= 8 && l <= 63;
}

int main(int argc, char **argv)
{
	unsigned char msg[MAXMSG];
	char key[80] = "";
	unsigned size, n;
	int wpa2;

	if (argc >= 4 && !strcmp(argv[1], "encode") && (wpa2 = auth_arg(argv[3])) >= 0) {
		n = encode(msg, argv[2], wpa2, argc > 4 ? argv[4] : "");
		if (!n)
			return 1;
		for (unsigned i = 0; i < n; i++)
			printf("%02x", msg[i]);
		putchar('\n');
		return 0;
	}
	if (argc == 2 && !strcmp(argv[1], "show"))
		return open_tag(&size) || show(size) ? 1 : 0;
	if (argc == 2 && !strcmp(argv[1], "clear")) {
		static const unsigned char empty[3] = { 0x03, 0x00, 0xfe };

		return open_tag(&size) || rf_type2() || store(empty, sizeof empty, size) ? 1 : 0;
	}
	if (argc == 4 && !strcmp(argv[1], "wifi") && (wpa2 = auth_arg(argv[3])) >= 0) {
		if (wpa2) {
			if (!fgets(key, sizeof key, stdin))
				key[0] = 0;
			key[strcspn(key, "\r\n")] = 0;
			if (!key_ok(key)) {
				fprintf(stderr, "u30pro-nfc: the key must have 8-63 characters or 64 hex digits\n");
				return 2;
			}
		}
		n = encode(msg, argv[2], wpa2, key);
		if (!n) {
			fprintf(stderr, "u30pro-nfc: the SSID must have 1-32 bytes\n");
			return 2;
		}
		return open_tag(&size) || rf_type2() || store(msg, n, size) ? 1 : 0;
	}
	fprintf(stderr, "usage: u30pro-nfc show | clear | wifi <ssid> open|wpa2 (key on stdin) | encode <ssid> open|wpa2 [key]\n");
	return 2;
}
