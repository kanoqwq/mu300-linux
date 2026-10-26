// SPDX-License-Identifier: GPL-2.0
/*
 * MU300: the Marlin3 is a PCIe device. SIPC (sbuf/sblock) and the Trusty IPC channel are only used by the
 * integrated WCN variants, whose code is still linked in; these generated stubs satisfy the linker and fail
 * cleanly. Regenerate with the snippet in upstream/README.md if the sources change.
 */
#include <linux/errno.h>
#include <linux/types.h>
#include <linux/err.h>
#include <linux/sipc.h>
#include <linux/trusty/trusty_ipc.h>

int sbuf_write(u8 dst, u8 channel, u32 bufid, void *buf, u32 len, int timeout) { return -ENODEV; }
int sbuf_read(u8 dst, u8 channel, u32 bufid, void *buf, u32 len, int timeout) { return -ENODEV; }
int sbuf_status(u8 dst, u8 channel) { return -ENODEV; }
int sbuf_register_notifier(u8 dst, u8 channel, u32 bufid, void (*handler)(int event, void *data), void *data) { return -ENODEV; }
int sblock_create(u8 dst, u8 channel, u32 txblocknum, u32 txblocksize, u32 rxblocknum, u32 rxblocksize) { return -ENODEV; }
void sblock_destroy(u8 dst, u8 channel) { }
int sblock_register_notifier(u8 dst, u8 channel, void (*handler)(int event, void *data), void *data) { return -ENODEV; }
int sblock_get(u8 dst, u8 channel, struct sblock *blk, int timeout) { return -ENODEV; }
int sblock_send(u8 dst, u8 channel, struct sblock *blk) { return -ENODEV; }
int sblock_receive(u8 dst, u8 channel, struct sblock *blk, int timeout) { return -ENODEV; }
int sblock_release(u8 dst, u8 channel, struct sblock *blk) { return -ENODEV; }
int sblock_get_free_count(u8 dst, u8 channel) { return -ENODEV; }
void sblock_put(u8 dst, u8 channel, struct sblock *blk) { }
struct tipc_chan * tipc_create_channel(struct device *dev, const struct tipc_chan_ops *ops, void *cb_arg) { return ERR_PTR(-ENODEV); }
int tipc_chan_connect(struct tipc_chan *chan, const char *port) { return -ENODEV; }
int tipc_chan_queue_msg(struct tipc_chan *chan, struct tipc_msg_buf *mb) { return -ENODEV; }
int tipc_chan_shutdown(struct tipc_chan *chan) { return -ENODEV; }
void tipc_chan_destroy(struct tipc_chan *chan) { }
struct tipc_msg_buf * tipc_chan_get_rxbuf(struct tipc_chan *chan) { return ERR_PTR(-ENODEV); }
void tipc_chan_put_rxbuf(struct tipc_chan *chan, struct tipc_msg_buf *mb) { }
struct tipc_msg_buf * tipc_chan_get_txbuf_timeout(struct tipc_chan *chan, long timeout) { return ERR_PTR(-ENODEV); }
void tipc_chan_put_txbuf(struct tipc_chan *chan, struct tipc_msg_buf *mb) { }
int sbuf_create_ex(u8 dst, u8 channel, u16 smem, u32 bufnum, u32 txbufsize, u32 rxbufsize) { return -ENODEV; }
