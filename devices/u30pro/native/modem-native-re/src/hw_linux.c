/* hw_linux.c — the real kernel / Trusty backend for struct mc_hw. */
#define _GNU_SOURCE
#include "mc.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

#define TIPC_IOC_CONNECT 0x40087280UL      /* _IOW('r', 0x80, char *) */

static int hw_ioctl(void *ctx, const char *dev, unsigned long req, void *arg)
{
    (void)ctx;
    int fd = open(dev, O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        mc_log(MC_LOG_ERR, "open %s failed: %s", dev, strerror(errno));
        return -errno;
    }
    int rc = ioctl(fd, req, arg);
    int e = errno;
    close(fd);
    if (rc < 0) {
        mc_log(MC_LOG_ERR, "ioctl 0x%lx on %s failed: %s", req, dev, strerror(e));
        return -e;
    }
    return rc;
}

static ssize_t hw_write_at(void *ctx, const char *dev, uint64_t off, const void *buf, size_t len)
{
    (void)ctx;
    int fd = open(dev, O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        mc_log(MC_LOG_ERR, "failed to open %s, error: %s", dev, strerror(errno));
        return -errno;
    }
    if (lseek(fd, (off_t)off, SEEK_SET) != (off_t)off) {
        int e = errno;
        close(fd);
        mc_log(MC_LOG_ERR, "failed to lseek %llu in %s", (unsigned long long)off, dev);
        return -e;
    }
    size_t done = 0;
    while (done < len) {
        ssize_t w = write(fd, (const char *)buf + done, len - done);
        if (w < 0 && errno == EINTR)
            continue;
        if (w <= 0) {
            int e = errno;
            close(fd);
            if (done)                       /* the node clamps at the end of the region */
                return (ssize_t)done;
            mc_log(MC_LOG_ERR, "failed to write %s: %s", dev, strerror(e));
            return -e;
        }
        done += (size_t)w;
    }
    close(fd);
    return (ssize_t)done;
}

static int hw_tipc_call(void *ctx, uint32_t cmd, const void *req, size_t req_len,
                        void *resp, size_t resp_cap, size_t *resp_len)
{
    const char *dev = ctx ? (const char *)ctx : "/dev/trusty-ipc-dev0";
    static const char port[] = "com.android.trusty.kernelbootcp";
    int fd;
    do { fd = open(dev, O_RDWR | O_CLOEXEC); } while (fd < 0 && errno == EINTR);
    if (fd < 0) {
        mc_log(MC_LOG_ERR, "tipc: cannot open tipc device \"%s\": %s", dev, strerror(errno));
        return -errno;
    }
    int rc;
    do { rc = ioctl(fd, TIPC_IOC_CONNECT, port); } while (rc < 0 && errno == EINTR);
    if (rc < 0) {
        int e = errno;
        mc_log(MC_LOG_ERR, "tipc: can't connect to tipc service \"%s\" (err=%d)", port, e);
        close(fd);
        return -e;
    }
    uint8_t msg[4 + 0x400];
    if (req_len > 0xffc) { close(fd); return -EINVAL; }
    if (req_len > sizeof msg - 4) { close(fd); return -EMSGSIZE; }
    memcpy(msg, &cmd, 4);
    memcpy(msg + 4, req, req_len);
    ssize_t w = write(fd, msg, 4 + req_len);
    if (w < 0) {
        int e = errno;
        mc_log(MC_LOG_ERR, "tipc: failed to send cmd (%u): %s", cmd, strerror(e));
        close(fd);
        return -e;
    }
    ssize_t r = read(fd, resp, resp_cap);
    int e = errno;
    close(fd);
    if (r < 0) {
        mc_log(MC_LOG_ERR, "tipc: failed to retrieve response for cmd (%u): %s", cmd, strerror(e));
        return -e;
    }
    *resp_len = (size_t)r;
    return 0;
}

static ssize_t hw_read_part(void *ctx, const char *path, uint64_t off, void *buf, size_t len)
{
    (void)ctx;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        mc_log(MC_LOG_ERR, "failed to open %s, error: %s", path, strerror(errno));
        return -errno;
    }
    size_t done = 0;
    while (done < len) {
        ssize_t r = pread(fd, (char *)buf + done, len - done, (off_t)(off + done));
        if (r < 0 && errno == EINTR)
            continue;
        if (r < 0) {
            int e = errno;
            close(fd);
            mc_log(MC_LOG_ERR, "failed to read %s: %s", path, strerror(e));
            return -e;
        }
        if (r == 0)
            break;
        done += (size_t)r;
    }
    close(fd);
    return (ssize_t)done;
}

static int64_t hw_part_size(void *ctx, const char *path)
{
    (void)ctx;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return -errno;
    off_t sz = lseek(fd, 0, SEEK_END);
    int e = errno;
    close(fd);
    return sz < 0 ? -e : (int64_t)sz;
}

static int hw_wake_lock(void *ctx, int on)
{
    (void)ctx;
    int fd = open(on ? "/sys/power/wake_lock" : "/sys/power/wake_unlock", O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        mc_log(MC_LOG_WARN, "open %s failed, error: %s", on ? "/sys/power/wake_lock" : "/sys/power/wake_unlock",
               strerror(errno));
        return -errno;
    }
    ssize_t w = write(fd, "modem_control", 14);
    close(fd);
    return w < 0 ? -errno : 0;
}

/* /dev/sprd_bm (bus monitor) and /dev/dmc_mpu (DDR memory protection) are best effort: the
 * stock program silently ignores ENOENT / ENODEV. */
static int hw_bus_protect(void *ctx, int on)
{
    (void)ctx;
    int fd = open("/dev/sprd_bm", O_RDWR | O_CLOEXEC);
    if (fd >= 0) {
        char dummy[16] = { 0 };
        if (ioctl(fd, on ? 0x13 : 0x12, dummy) < 0)
            mc_log(MC_LOG_DBG, "sprd_bm ioctl failed: %s", strerror(errno));
        close(fd);
    } else if (errno != ENOENT && errno != ENODEV) {
        mc_log(MC_LOG_WARN, "open /dev/sprd_bm failed: %s", strerror(errno));
    }
    fd = open("/dev/dmc_mpu", O_RDWR | O_CLOEXEC);
    if (fd >= 0) {
        char dummy[16] = { 0 };
        if (ioctl(fd, on & 1, dummy) < 0)
            mc_log(MC_LOG_DBG, "dmc_mpu ioctl failed: %s", strerror(errno));
        close(fd);
    } else if (errno != ENOENT && errno != ENODEV) {
        mc_log(MC_LOG_WARN, "open /dev/dmc_mpu failed: %s", strerror(errno));
    }
    return 0;
}

static void hw_sleep_ms(void *ctx, unsigned ms) { (void)ctx; usleep(ms * 1000u); }

const struct mc_hw mc_hw_linux = {
    .ioctl = hw_ioctl,
    .write_at = hw_write_at,
    .tipc_call = hw_tipc_call,
    .read_part = hw_read_part,
    .part_size = hw_part_size,
    .wake_lock = hw_wake_lock,
    .bus_protect = hw_bus_protect,
    .sleep_ms = hw_sleep_ms,
    .ctx = NULL,
};
