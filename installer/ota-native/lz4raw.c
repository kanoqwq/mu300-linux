/* Bounded raw LZ4 block decoder for the standalone ARM64 OTA interpreter.
 * No libc, syscalls, allocation or host ABI dependencies. Return -1 on errors.
 * The caller owns a <= 8 MiB output buffer. Handles overlapping matches.
 */
__attribute__((visibility("default")))
int unisoc_lz4_decode(const unsigned char *src, unsigned int size,
                     unsigned char *dst, unsigned int capacity)
{
    unsigned int at = 0, out = 0;
    while (at < size) {
        unsigned int token = src[at++], literals = token >> 4, match, offset, n;
        if (literals == 15) {
            do {
                if (at == size) return -1;
                n = src[at++];
                if (literals > capacity || n > capacity - literals) return -1;
                literals += n;
            } while (n == 255);
        }
        if (literals > size - at || literals > capacity - out) return -1;
        for (n = 0; n < literals; ++n) dst[out++] = src[at++];
        if (at == size) return (int)out;
        if (size - at < 2) return -1;
        offset = src[at] | ((unsigned int)src[at + 1] << 8); at += 2;
        if (!offset || offset > out) return -1;
        match = (token & 15) + 4;
        if ((token & 15) == 15) {
            do {
                if (at == size) return -1;
                n = src[at++];
                if (match > capacity || n > capacity - match) return -1;
                match += n;
            } while (n == 255);
        }
        if (match > capacity - out) return -1;
        for (n = 0; n < match; ++n) { dst[out] = dst[out-offset]; ++out; }
    }
    return (int)out;
}
