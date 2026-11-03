/* Host-only I2C device: compile the real CLI against a private memory file. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/i2c.h>
#include <linux/i2c-dev.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int test_open(const char *path, int flags, ...)
{
    if (strcmp(path, "/dev/i2c-4")) { errno = ENODEV; return -1; }
    return open(getenv("NFC_TEST_IMAGE"), flags);
}

static int test_ioctl(int fd, unsigned long request, ...)
{
    va_list ap;
    va_start(ap, request);
    struct i2c_rdwr_ioctl_data *d = va_arg(ap, struct i2c_rdwr_ioctl_data *);
    va_end(ap);
    if (request != I2C_RDWR || !d || !d->nmsgs) return -1;
    struct i2c_msg *m = d->msgs;
    unsigned offset = (m[0].buf[0] << 8) | m[0].buf[1];
    if (m[0].addr != 0x57 || m[0].len < 2) return -1;
    if (d->nmsgs == 2 && (m[1].flags & I2C_M_RD))
        return pread(fd, m[1].buf, m[1].len, offset) == m[1].len ? 2 : -1;
    if (d->nmsgs != 1 || m[0].len < 3) return -1;
    const char *fail = getenv("NFC_TEST_FAIL_OFFSET");
    if (fail && offset == strtoul(fail, NULL, 0)) { errno = EIO; return -1; }
    FILE *log = fopen(getenv("NFC_TEST_WRITES"), "a");
    if (!log) return -1;
    fprintf(log, "%u %u\n", offset, m[0].len - 2);
    fclose(log);
    return pwrite(fd, m[0].buf + 2, m[0].len - 2, offset) == m[0].len - 2 ? 1 : -1;
}

#define open test_open
#define ioctl test_ioctl
#include "../../devices/u30pro/native/tools/u30pro_nfc.c"
