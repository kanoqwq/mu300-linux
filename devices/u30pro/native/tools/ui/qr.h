/* Minimal QR Code encoder for the screen UI: byte mode, error correction L, versions 1..10 (up to 271 bytes).
 * Follows ISO/IEC 18004 (format/version BCH codes, Reed-Solomon over GF(256)/0x11D, mask penalty scoring). */
#ifndef U30PRO_UI_QR_H
#define U30PRO_UI_QR_H
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

#define QR_MAX_SIZE 57
typedef struct { int size; uint8_t dark[QR_MAX_SIZE * QR_MAX_SIZE]; uint8_t fn[QR_MAX_SIZE * QR_MAX_SIZE]; } QrCode;

typedef struct { int total, ecpb, g1n, g1d, g2n, g2d; } QrBlocks;
static const QrBlocks qr_blocks_l[11] = {
	{0, 0, 0, 0, 0, 0}, {26, 7, 1, 19, 0, 0}, {44, 10, 1, 34, 0, 0}, {70, 15, 1, 55, 0, 0}, {100, 20, 1, 80, 0, 0},
	{134, 26, 1, 108, 0, 0}, {172, 18, 2, 68, 0, 0}, {196, 20, 2, 78, 0, 0}, {242, 24, 2, 97, 0, 0},
	{292, 30, 2, 116, 0, 0}, {346, 18, 2, 68, 2, 69}
};
static const uint8_t qr_align[11][4] = {
	{0}, {0}, {6, 18}, {6, 22}, {6, 26}, {6, 30}, {6, 34}, {6, 22, 38}, {6, 24, 42}, {6, 26, 46}, {6, 28, 50}
};

static uint8_t qr_mul(uint8_t x, uint8_t y)
{
	unsigned z = 0;

	for (int i = 7; i >= 0; i--) {
		z = (z << 1) ^ ((z >> 7) * 0x11D);
		z ^= ((y >> i) & 1) * x;
	}
	return (uint8_t)z;
}

static void qr_rs(const uint8_t *data, int len, int degree, uint8_t *ecc)
{
	uint8_t gen[32] = {0};
	uint8_t root = 1;

	gen[degree - 1] = 1;
	for (int i = 0; i < degree; i++) {
		for (int j = 0; j < degree; j++) {
			gen[j] = qr_mul(gen[j], root);
			if (j + 1 < degree)
				gen[j] ^= gen[j + 1];
		}
		root = qr_mul(root, 2);
	}
	memset(ecc, 0, (size_t)degree);
	for (int k = 0; k < len; k++) {
		uint8_t factor = data[k] ^ ecc[0];

		memmove(ecc, ecc + 1, (size_t)degree - 1);
		ecc[degree - 1] = 0;
		for (int i = 0; i < degree; i++)
			ecc[i] ^= qr_mul(gen[i], factor);
	}
}

static void qr_set(QrCode *q, int x, int y, int dark)
{
	if (x < 0 || y < 0 || x >= q->size || y >= q->size)
		return;
	q->dark[y * q->size + x] = (uint8_t)dark;
	q->fn[y * q->size + x] = 1;
}

static void qr_finder(QrCode *q, int cx, int cy)
{
	for (int dy = -4; dy <= 4; dy++)
		for (int dx = -4; dx <= 4; dx++) {
			int d = abs(dx) > abs(dy) ? abs(dx) : abs(dy);

			qr_set(q, cx + dx, cy + dy, d != 2 && d != 4);
		}
}

static void qr_format(QrCode *q, int mask)
{
	unsigned data = (1u << 3) | (unsigned)mask, rem = data;

	for (int i = 0; i < 10; i++)
		rem = (rem << 1) ^ ((rem >> 9) * 0x537);
	unsigned bits = ((data << 10) | rem) ^ 0x5412;
	int n = q->size;

	for (int i = 0; i <= 5; i++) qr_set(q, 8, i, (bits >> i) & 1);
	qr_set(q, 8, 7, (bits >> 6) & 1);
	qr_set(q, 8, 8, (bits >> 7) & 1);
	qr_set(q, 7, 8, (bits >> 8) & 1);
	for (int i = 9; i < 15; i++) qr_set(q, 14 - i, 8, (bits >> i) & 1);
	for (int i = 0; i < 8; i++) qr_set(q, n - 1 - i, 8, (bits >> i) & 1);
	for (int i = 8; i < 15; i++) qr_set(q, 8, n - 15 + i, (bits >> i) & 1);
	qr_set(q, 8, n - 8, 1);
}

static int qr_penalty(const QrCode *q)
{
	int n = q->size, score = 0, dark = 0;

	for (int pass = 0; pass < 2; pass++) {
		for (int a = 0; a < n; a++) {
			int run = 1;
			unsigned hist = 0;

			for (int b = 0; b < n; b++) {
				int v = pass ? q->dark[b * n + a] : q->dark[a * n + b];
				int pv = b ? (pass ? q->dark[(b - 1) * n + a] : q->dark[a * n + b - 1]) : -1;

				if (b && v == pv) {
					run++;
					if (run == 5) score += 3;
					else if (run > 5) score++;
				} else run = 1;
				hist = ((hist << 1) | (unsigned)v) & 0x7ff;
				if (b >= 10 && (hist == 0x05d || hist == 0x5d0)) score += 40;
			}
		}
	}
	for (int y = 0; y + 1 < n; y++)
		for (int x = 0; x + 1 < n; x++) {
			int v = q->dark[y * n + x];

			if (v == q->dark[y * n + x + 1] && v == q->dark[(y + 1) * n + x] && v == q->dark[(y + 1) * n + x + 1])
				score += 3;
		}
	for (int i = 0; i < n * n; i++) dark += q->dark[i];
	int k = (abs(dark * 20 - n * n * 10) + n * n - 1) / (n * n) - 1;

	return score + (k > 0 ? k : 0) * 10;
}

static int qr_mask_bit(int mask, int x, int y)
{
	switch (mask) {
	case 0: return (x + y) % 2 == 0;
	case 1: return y % 2 == 0;
	case 2: return x % 3 == 0;
	case 3: return (x + y) % 3 == 0;
	case 4: return (x / 3 + y / 2) % 2 == 0;
	case 5: return x * y % 2 + x * y % 3 == 0;
	case 6: return (x * y % 2 + x * y % 3) % 2 == 0;
	default: return ((x + y) % 2 + x * y % 3) % 2 == 0;
	}
}

/* returns the QR version (1..10), or 0 when the text does not fit */
static int qr_encode(QrCode *q, const char *text, int len)
{
	int ver = 0;

	for (int v = 1; v <= 10; v++) {
		const QrBlocks *b = &qr_blocks_l[v];
		int data_cw = b->total - b->ecpb * (b->g1n + b->g2n);

		if (4 + (v < 10 ? 8 : 16) + 8 * len <= data_cw * 8) { ver = v; break; }
	}
	if (!ver || len < 0)
		return 0;
	const QrBlocks *b = &qr_blocks_l[ver];
	int nblocks = b->g1n + b->g2n, data_cw = b->total - b->ecpb * nblocks;
	uint8_t data[400] = {0};
	int nbits = 0;

#define PUT(val, cnt) do { for (int _i = (cnt) - 1; _i >= 0; _i--) { if (((val) >> _i) & 1) data[nbits >> 3] |= (uint8_t)(0x80 >> (nbits & 7)); nbits++; } } while (0)
	PUT(4, 4);
	PUT(len, ver < 10 ? 8 : 16);
	for (int i = 0; i < len; i++) PUT((unsigned char)text[i], 8);
	int term = data_cw * 8 - nbits;

	nbits += term < 4 ? term : 4;
	nbits = (nbits + 7) & ~7;
	for (int pad = 0xEC; nbits < data_cw * 8; pad ^= 0xEC ^ 0x11) { data[nbits >> 3] = (uint8_t)pad; nbits += 8; }
#undef PUT

	/* split into blocks, add Reed-Solomon, interleave */
	uint8_t blocks[8][160], ecc[8][32];
	int blen[8], pos = 0;

	for (int i = 0; i < nblocks; i++) {
		blen[i] = i < b->g1n ? b->g1d : b->g2d;
		memcpy(blocks[i], data + pos, (size_t)blen[i]);
		pos += blen[i];
		qr_rs(blocks[i], blen[i], b->ecpb, ecc[i]);
	}
	uint8_t all[400];
	int n = 0, maxlen = b->g2n ? b->g2d : b->g1d;

	for (int i = 0; i < maxlen; i++)
		for (int k = 0; k < nblocks; k++)
			if (i < blen[k]) all[n++] = blocks[k][i];
	for (int i = 0; i < b->ecpb; i++)
		for (int k = 0; k < nblocks; k++) all[n++] = ecc[k][i];

	/* function patterns */
	memset(q, 0, sizeof *q);
	q->size = ver * 4 + 17;
	int size = q->size;

	for (int i = 0; i < size; i++) {
		qr_set(q, 6, i, i % 2 == 0);
		qr_set(q, i, 6, i % 2 == 0);
	}
	qr_finder(q, 3, 3);
	qr_finder(q, size - 4, 3);
	qr_finder(q, 3, size - 4);
	int na = ver == 1 ? 0 : (ver < 7 ? 2 : 3);

	for (int i = 0; i < na; i++)
		for (int j = 0; j < na; j++) {
			if ((i == 0 && j == 0) || (i == 0 && j == na - 1) || (i == na - 1 && j == 0))
				continue;
			for (int dy = -2; dy <= 2; dy++)
				for (int dx = -2; dx <= 2; dx++) {
					int d = abs(dx) > abs(dy) ? abs(dx) : abs(dy);

					qr_set(q, qr_align[ver][i] + dx, qr_align[ver][j] + dy, d != 1);
				}
		}
	qr_format(q, 0);
	if (ver >= 7) {
		unsigned rem = (unsigned)ver;

		for (int i = 0; i < 12; i++)
			rem = (rem << 1) ^ ((rem >> 11) * 0x1F25);
		unsigned bits = ((unsigned)ver << 12) | rem;

		for (int i = 0; i < 18; i++) {
			int bit = (bits >> i) & 1, a = size - 11 + i % 3, c = i / 3;

			qr_set(q, a, c, bit);
			qr_set(q, c, a, bit);
		}
	}

	/* data modules in the zig-zag order */
	int bit = 0;

	for (int right = size - 1; right >= 1; right -= 2) {
		if (right == 6) right = 5;
		for (int vert = 0; vert < size; vert++)
			for (int j = 0; j < 2; j++) {
				int x = right - j, up = ((right + 1) & 2) == 0, y = up ? size - 1 - vert : vert;

				if (q->fn[y * size + x]) continue;
				q->dark[y * size + x] = bit < n * 8 ? (all[bit >> 3] >> (7 - (bit & 7))) & 1 : 0;
				bit++;
			}
	}

	/* choose the mask with the lowest penalty */
	int best = 0, best_score = 1 << 30;
	QrCode trial;

	for (int m = 0; m < 8; m++) {
		trial = *q;
		for (int y = 0; y < size; y++)
			for (int x = 0; x < size; x++)
				if (!trial.fn[y * size + x] && qr_mask_bit(m, x, y))
					trial.dark[y * size + x] ^= 1;
		qr_format(&trial, m);
		int s = qr_penalty(&trial);

		if (s < best_score) { best_score = s; best = m; }
	}
	for (int y = 0; y < size; y++)
		for (int x = 0; x < size; x++)
			if (!q->fn[y * size + x] && qr_mask_bit(best, x, y))
				q->dark[y * size + x] ^= 1;
	qr_format(q, best);
	return ver;
}
#endif
