/*
 * Copyright (C) 2015 Spreadtrum Communications Inc.
 *
 * This software is licensed under the terms of the GNU General Public
 * License version 2, as published by the Free Software Foundation, and
 * may be copied, distributed, and modified under those terms.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 */

#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/types.h>
#include <linux/kdev_t.h>
#include <linux/tty.h>
#include <linux/vt_kern.h>
#include <linux/init.h>
#include <linux/console.h>
#include <linux/delay.h>
#include <linux/semaphore.h>
#ifdef CONFIG_OF
#include <linux/of_device.h>
#endif
#include <linux/compat.h>
#include <linux/tty_flip.h>
#include <linux/kthread.h>
#include <linux/interrupt.h>
#include <linux/workqueue.h>
#include <linux/version.h>

#include <misc/marlin_platform.h>
#include <linux/notifier.h>
#include <misc/wcn_bus.h>
#include "tty.h"
#include "lpm.h"
#include "rfkill.h"

#include "alignment/sitm.h"

#include <linux/dma-direction.h>
#include <linux/dma-mapping.h>


#define MTTY_DEV_MAX_NR     1

#define MTTY_STATE_OPEN     1
#define MTTY_STATE_CLOSE    0

#define SDIOM_WR_DIRECT_MOD
#ifdef SDIOM_WR_DIRECT_MOD
#define SDIOM_WR_DIRECT_MOD_ADDR 0x51004000
#endif

typedef struct mbuf_t mbuf_t;
typedef struct mchn_ops_t mchn_ops_t;
struct semaphore sem_id;

struct rx_data {
    unsigned int channel;
    mbuf_t *head;
    mbuf_t *tail;
    unsigned int num;
    struct list_head entry;
};

struct mtty_device {
    struct mtty_init_data   *pdata;
    struct tty_port *port0;
    struct tty_port *port1;
    struct tty_struct   *tty;
    struct tty_driver   *driver;
    struct platform_device *pdev;
    /* mtty state */
    uint32_t    state;
    struct mutex    stat_mutex;
    struct mutex    rw_mutex;
    struct list_head        rx_head;
    struct work_struct bt_rx_work;
    struct workqueue_struct *bt_rx_workqueue;
};

typedef struct {
    unsigned long vir;
    unsigned long phy;
    int size;
} dm_t;

static struct mtty_device *mtty_dev;
static unsigned int que_task = 1;
static int que_sche = 1;

static unsigned int log_level = MTTY_LOG_LEVEL_NONE;

#define BT_VER(fmt, ...)                        \
    do {                                        \
        if (log_level == MTTY_LOG_LEVEL_VER)    \
            pr_err(fmt, ##__VA_ARGS__);         \
    } while (0)

extern int set_power_ret;
static struct device *dm_rx_t = NULL;
unsigned long dm_rx_phy[BT_RX_MAX_NUM];
unsigned char *(dm_rx_ptr[BT_RX_MAX_NUM]);

struct dma_buf {
	unsigned long vir;
	unsigned long phy;
	int size;
};

int mtty_dmalloc(struct device *priv, struct dma_buf *dm, int size)
{
	struct device *dev = priv;

	if (!dev) {
		pr_err("%s(NULL)\n", __func__);
		return -1;
	}

	if (dma_set_mask(dev, DMA_BIT_MASK(64))) {
		pr_info("dma_set_mask err\n");
		if (dma_set_coherent_mask(dev, DMA_BIT_MASK(64))) {
			pr_err("dma_set_coherent_mask err\n");
			return -1;
		}
	}

	dm->vir =
	    (unsigned long)dma_alloc_coherent(dev, size,
					      (dma_addr_t *)(&(dm->phy)),
					      GFP_DMA);
	if (dm->vir == 0) {
		pr_err("dma_alloc_coherent err\n");
		return -1;
	}
	dm->size = size;
	memset((unsigned char *)(dm->vir), 0x56, size);
	pr_info("dma_alloc_coherent(%d) 0x%lx 0x%lx\n",
		  size, dm->vir, dm->phy);

	return 0;
}

int mtty_dma_buf_alloc(int chn, int size, int num)
{
	int ret, i;
	struct dma_buf temp = {0};
	struct mbuf_t *mbuf, *head, *tail;
    dm_rx_t = &mtty_dev->pdev->dev;

	if (!dm_rx_t) {
		pr_err("%s:PCIE device link error\n", __func__);
		return -1;
	}
	ret = sprdwcn_bus_list_alloc(chn, &head, &tail, &num);
	if (ret != 0)
		return -1;
	for (i = 0, mbuf = head; i < num; i++) {
		ret = mtty_dmalloc(dm_rx_t, &temp, size);
		if (ret != 0)
			return -1;
		mbuf->buf = (unsigned char *)(temp.vir);
        dm_rx_ptr[i] = mbuf->buf;
		mbuf->phy = (unsigned long)(temp.phy);
        dm_rx_phy[i] = mbuf->phy;
		mbuf->len = temp.size;
		memset(mbuf->buf, 0x0, mbuf->len);
		mbuf = mbuf->next;
	}

	ret = sprdwcn_bus_push_list(chn, head, tail, num);

	return ret;
}

int mtty_dma_buf_free(int num) {
    int loop_count = 0;
    for (; loop_count < num; loop_count++) {
        if(!dm_rx_t) {
            pr_err("%s: dm_rx_t or is dm_rx_ptr NULL \n", __func__);
        } else {
            dma_free_coherent(dm_rx_t, BT_RX_DMA_SIZE , (void *)dm_rx_ptr[loop_count], dm_rx_phy[loop_count]);
            pr_err("%s: free  dm_rx_ptr[%d] success \n", __func__, loop_count);
            dm_rx_ptr[loop_count] = NULL;
        }
    }
    return 0;
}

/* static void mtty_rx_task(unsigned long data) */
static void mtty_rx_work_queue(struct work_struct *work)

{
    int i, ret = 0;
    /*struct mtty_device *mtty = (struct mtty_device *)data;*/
    struct mtty_device *mtty;
    struct rx_data *rx = NULL;

    que_task = que_task + 1;
    if (que_task > 65530)
        que_task = 0;
    BT_VER("mtty que_task= %d\n", que_task);
    que_sche = que_sche - 1;

    mtty = container_of(work, struct mtty_device, bt_rx_work);
    if (unlikely(!mtty)) {
        pr_err("mtty_rx_task mtty is NULL\n");
        return;
    }

    mutex_lock(&mtty->stat_mutex);
    if (mtty->state == MTTY_STATE_OPEN) {
        mutex_unlock(&mtty->stat_mutex);

        do {
            mutex_lock(&mtty->rw_mutex);
            if (list_empty_careful(&mtty->rx_head)) {
                BT_VER("mtty over load queue done\n");
                mutex_unlock(&mtty->rw_mutex);
                break;
            }
            rx = list_first_entry_or_null(&mtty->rx_head,
                        struct rx_data, entry);
            if (!rx) {
                pr_err("mtty over load queue abort\n");
                mutex_unlock(&mtty->rw_mutex);
                break;
            }
            list_del(&rx->entry);
            mutex_unlock(&mtty->rw_mutex);

            BT_VER("mtty over load working at channel: %d, len: %d\n",
                        rx->channel, rx->head->len);
            for (i = 0; i < rx->head->len; i++) {
                ret = tty_insert_flip_char(mtty->port0,
                            *(rx->head->buf+i), TTY_NORMAL);
                if (ret != 1) {
                    i--;
                    continue;
                } else {
                    tty_flip_buffer_push(mtty->port0);
                }
            }
            pr_err("mtty over load cut channel: %d\n", rx->channel);
            if (rx->head->buf != NULL) {
                kfree(rx->head->buf);
                rx->head->buf = NULL;
            }
            if (rx != NULL) {
                kfree(rx);
                rx = NULL;
            }

        } while (1);
    } else {
        pr_info("mtty status isn't open, status:%d\n", mtty->state);
        mutex_unlock(&mtty->stat_mutex);
    }
}

static int mtty_rx_cb(int chn, mbuf_t *head, mbuf_t *tail, int num)
{
    int ret = 0, len_send;
    struct rx_data *rx;
    unsigned char *sdio_buf = NULL;
    sdio_buf = (unsigned char *)head->buf;
    bt_wakeup_host();

    len_send = head->len;

    BT_VER("%s() channel: %d, num: %d\n", __func__, chn, num);
    BT_VER("%s() ---mtty receive channel= %d, len_send = %d\n", __func__, chn, len_send);

    if (mtty_dev->state != MTTY_STATE_OPEN) {
        pr_err("%s() mtty bt is closed abnormally\n", __func__);

        sprdwcn_bus_push_list(chn, head, tail, num);
        return -1;
    }

    if (mtty_dev != NULL) {
        if (!work_pending(&mtty_dev->bt_rx_work)) {
            BT_VER("%s() tty_insert_flip_string", __func__);
            ret = tty_insert_flip_string(mtty_dev->port0,
                            (unsigned char *)head->buf+BT_SDIO_HEAD_LEN,
                            len_send);   // -BT_SDIO_HEAD_LEN
            BT_VER("%s() ret=%d, len=%d\n", __func__, ret, len_send);
            if (ret)
                tty_flip_buffer_push(mtty_dev->port0);
            if (ret == (len_send)) {
                BT_VER("%s() send success", __func__);
                sprdwcn_bus_push_list(chn, head, tail, num);
                return 0;
            }
        }

        mutex_lock(&mtty_dev->rw_mutex);
        rx = kmalloc(sizeof(struct rx_data), GFP_KERNEL);
        if (rx == NULL) {
            pr_err("%s() rx == NULL\n", __func__);
            sprdwcn_bus_push_list(chn, head, tail, num);
            mutex_unlock(&mtty_dev->rw_mutex);
            return -ENOMEM;
        }

        rx->head = head;
        rx->tail = tail;
        rx->channel = chn;
        rx->num = num;
        rx->head->len = (len_send) - ret;
        rx->head->buf = kmalloc(rx->head->len, GFP_KERNEL);
        if (rx->head->buf == NULL) {
            pr_err("mtty low memory!\n");
            kfree(rx);
            sprdwcn_bus_push_list(chn, head, tail, num);
            mutex_unlock(&mtty_dev->rw_mutex);
            return -ENOMEM;
        }

        memcpy(rx->head->buf, head->buf, rx->head->len);
        sprdwcn_bus_push_list(chn, head, tail, num);
        //mutex_lock(&mtty_dev->rw_mutex);
        BT_VER("mtty over load push %d -> %d, channel: %d len: %d\n",
                len_send, ret, rx->channel, rx->head->len);
        list_add_tail(&rx->entry, &mtty_dev->rx_head);
        mutex_unlock(&mtty_dev->rw_mutex);
        if (!work_pending(&mtty_dev->bt_rx_work)) {
            BT_VER("work_pending\n");
            queue_work(mtty_dev->bt_rx_workqueue,
                        &mtty_dev->bt_rx_work);
        }
        return 0;
    }
    pr_err("mtty_rx_cb mtty_dev is NULL!!!\n");

    return -1;
}

static int mtty_tx_cb(int chn, mbuf_t *head, mbuf_t *tail, int num)
{
    int i;
    mbuf_t *pos = NULL;
    BT_VER("%s channel: %d, head: %p, tail: %p num: %d mtty_dev %p pdev %p\n",
             __func__, chn, head, tail, num, mtty_dev, mtty_dev->pdev);
    pos = head;
    BT_VER("mtty_close mtty_dev->state = %d !\n", mtty_dev->state);
    for (i = 0; i < num; i++, pos = pos->next) {
        struct device *dm = &mtty_dev->pdev->dev;
        if ((mtty_dev->state == MTTY_STATE_CLOSE) || (pos == NULL)) {
            pr_err("mtty_tx_cb error, return\n");
            up(&sem_id);
            return -1;
        }
        dma_free_coherent(dm, pos->len, (void *)pos->buf, head->phy);
        pos->buf = NULL;
    }
    if ((sprdwcn_bus_list_free(chn, head, tail, num)) == 0)
    {
        BT_VER("%s sprdwcn_bus_list_free() success\n", __func__);
    }
    else
        pr_err("%s sprdwcn_bus_list_free() fail\n", __func__);
    up(&sem_id);
    return 0;
}

static int rx_push(int chn, mbuf_t **head, mbuf_t **tail, int *num) {
    pr_err("%s no buf, rx_push called \n", __func__);
    return 0;
}

mchn_ops_t bt_rx_ops = {
    .channel = BT_RX_CHANNEL,
    .hif_type = HW_TYPE_PCIE,
    .inout = BT_RX_INOUT,
    .pool_size = 1,//BT_RX_POOL_SIZE,
    .pop_link = mtty_rx_cb,
    .push_link = rx_push
};

mchn_ops_t bt_tx_ops0 = {
    .channel = BT_TX_CHANNEL0,
    .hif_type = HW_TYPE_PCIE,
    .inout = BT_TX_INOUT,
    .pool_size = BT_TX_POOL_SIZE0,
    .cb_in_irq = 0,
    .max_pending = 16,
    .pop_link = mtty_tx_cb,
};

mchn_ops_t bt_tx_ops1 = {
    .channel = BT_TX_CHANNEL1,
    .hif_type = HW_TYPE_PCIE,
    .inout = BT_TX_INOUT,
    .pool_size = BT_TX_POOL_SIZE1,
    .pop_link = mtty_tx_cb,
};

static int mtty_open(struct tty_struct *tty, struct file *filp)
{
    struct mtty_device *mtty = NULL;
    struct tty_driver *driver = NULL;
    int ret = -1;
    if (set_power_ret != 0) {
        pr_err("mtty_open : set power failed , return!\n");
        return -1;
    }

    if (tty == NULL) {
        pr_err("mtty open input tty is NULL!\n");
        return -ENOMEM;
    }
    driver = tty->driver;
    mtty = (struct mtty_device *)driver->driver_state;

    if (mtty == NULL) {
        pr_err("mtty open input mtty NULL!\n");
        return -ENOMEM;
    }

    mtty->tty = tty;
    tty->driver_data = (void *)mtty;

    mutex_lock(&mtty->stat_mutex);
    mtty->state = MTTY_STATE_OPEN;
    mutex_unlock(&mtty->stat_mutex);
    que_task = 0;
    que_sche = 0;
    sitm_ini();
    ret = start_marlin(MARLIN_BLUETOOTH);
    pr_info("mtty_open power on state ret = %d!\n", ret);
    sprdwcn_bus_chn_init(&bt_rx_ops);
    sprdwcn_bus_chn_init(&bt_tx_ops0);
    mtty_dma_buf_alloc(BT_RX_CHANNEL, BT_RX_DMA_SIZE, BT_RX_MAX_NUM);
    pr_info("mtty_open device success!\n");

    return 0;
}

static void mtty_close(struct tty_struct *tty, struct file *filp)
{
    struct mtty_device *mtty = NULL;
    mtty_dma_buf_free(BT_RX_MAX_NUM);
    if (tty == NULL) {
        pr_err("mtty close input tty is NULL!\n");
        return;
    }
    mtty = (struct mtty_device *) tty->driver_data;
    if (mtty == NULL) {
        pr_err("mtty close s tty is NULL!\n");
        return;
    }

    mutex_lock(&mtty->stat_mutex);
    mtty->state = MTTY_STATE_CLOSE;
    mutex_unlock(&mtty->stat_mutex);
    sprdwcn_bus_chn_deinit(&bt_rx_ops);
    sprdwcn_bus_chn_deinit(&bt_tx_ops0);
    sitm_cleanup();
    pr_info("mtty_close device success !\n");
}

static int mtty_write(struct tty_struct *tty,
            const unsigned char *buf, int count)
{
    int num = 1;
    mbuf_t *tx_head = NULL;
    mbuf_t *tx_tail = NULL;

    down(&sem_id);
    if (!sprdwcn_bus_list_alloc(BT_TX_CHANNEL0, &tx_head, &tx_tail, &num)) {
        int ret = 0;
        struct device *dm = &mtty_dev->pdev->dev;
        BT_VER("%s() sprdwcn_bus_list_alloc() success tx_head %p tx_tail %p num %d mtty_dev->tty->dev %p\n",
                __func__, tx_head, tx_tail, num, mtty_dev->tty->dev);
        if ((ret = dma_set_mask(dm, DMA_BIT_MASK(64)))) {
            printk(KERN_ERR "dma_set_mask err ret %d\n", ret);
            if ((ret = dma_set_coherent_mask(dm, DMA_BIT_MASK(64)))) {
                printk(KERN_ERR "dma_set_coherent_mask err ret %d\n", ret);
                return -ENOMEM;
            }
        }
        tx_head->buf = (unsigned char *)dma_alloc_coherent(dm, count, (dma_addr_t *)(&(tx_head->phy)), GFP_DMA);

        if(!tx_head->buf)
        {
            pr_err("%s:line:%d dma_alloc_coherent err dev %p count %d phy %p\n",
                    __func__, __LINE__, mtty_dev->tty->dev, count, &(tx_head->phy));
            return -ENOMEM;
        }
        memcpy(tx_head->buf, buf, count);
        tx_head->len = count;
        tx_head->next = NULL;
        /*packer type 0, subtype 0*/
        BT_VER("%s sprdwcn_bus_push_list num: %d ++\n", __func__, num);
        ret = sprdwcn_bus_push_list(BT_TX_CHANNEL0, tx_head, tx_tail, num);
        BT_VER("%s sprdwcn_bus_push_list ret: %d --\n", __func__, ret);
        if (ret)
        {
            dma_free_coherent(dm, count, (void *)tx_head->buf, tx_head->phy);
            tx_head->buf = NULL;
            sprdwcn_bus_list_free(BT_TX_CHANNEL0, tx_head, tx_tail, num);
            up(&sem_id);
            return -EBUSY;
        }
        else
        {
            BT_VER("%s() sprdwcn_bus_push_list() success\n", __func__);
            return count;
        }
    } else {
        pr_err("%s:%d sprdwcn_bus_list_alloc fail\n", __func__, __LINE__);
        up(&sem_id);
        return -ENOMEM;
    }
}


static  int sdio_data_transmit(uint8_t *data, size_t count)
{
	return mtty_write(NULL, data, count);
}


static ssize_t mtty_write_plus(struct tty_struct *tty,
	      const u8 *buf, size_t count)
{
	return sitm_write(buf, count, sdio_data_transmit);
}


static void mtty_flush_chars(struct tty_struct *tty)
{
}

#if(LINUX_VERSION_CODE >= KERNEL_VERSION(5,15,0))
static unsigned int mtty_write_room(struct tty_struct *tty)
{
	return INT_MAX;
}
#else
static int mtty_write_room(struct tty_struct *tty)
{
	return INT_MAX;
}
#endif

static const struct tty_operations mtty_ops = {
    .open  = mtty_open,
    .close = mtty_close,
    .write = mtty_write_plus,
    .flush_chars = mtty_flush_chars,
    .write_room  = mtty_write_room,
};

static struct tty_port *mtty_port_init(void)
{
    struct tty_port *port = NULL;

    port = kzalloc(sizeof(struct tty_port), GFP_KERNEL);
    if (port == NULL)
        return NULL;
    tty_port_init(port);

    return port;
}

static int mtty_tty_driver_init(struct mtty_device *device)
{
    struct tty_driver *driver;
    int ret = 0;

    device->port0 = mtty_port_init();
    if (!device->port0)
        return -ENOMEM;

    device->port1 = mtty_port_init();
    if (!device->port1)
        return -ENOMEM;

    #if(LINUX_VERSION_CODE >= KERNEL_VERSION(5,15,0))
    driver = tty_alloc_driver(MTTY_DEV_MAX_NR,0);
    #else
    driver = alloc_tty_driver(MTTY_DEV_MAX_NR);
    #endif
    if (!driver)
        return -ENOMEM;

    /*
    * Initialize the tty_driver structure
    * Entries in mtty_driver that are NOT initialized:
    * proc_entry, set_termios, flush_buffer, set_ldisc, write_proc
    */
    driver->owner = THIS_MODULE;
    driver->driver_name = device->pdata->name;
    driver->name = device->pdata->name;
    driver->major = 0;
    driver->type = TTY_DRIVER_TYPE_SYSTEM;
    driver->subtype = SYSTEM_TYPE_TTY;
    driver->init_termios = tty_std_termios;
    driver->driver_state = (void *)device;
    device->driver = driver;
    device->driver->flags = TTY_DRIVER_REAL_RAW;
    /* initialize the tty driver */
    tty_set_operations(driver, &mtty_ops);
    tty_port_link_device(device->port0, driver, 0);
    /* the driver has MTTY_DEV_MAX_NR (1) line: linking port1 at index 1 wrote past driver->ports[] (newer kernels
     * refuse it with a warning). port1 never carries data. */
    ret = tty_register_driver(driver);
    if (ret) {
        #if(LINUX_VERSION_CODE >= KERNEL_VERSION(5,15,0))
        tty_driver_kref_put(driver);
        #else
        put_tty_driver(driver);
        #endif
        tty_port_destroy(device->port0);
        tty_port_destroy(device->port1);
        return ret;
    }
    return ret;
}

static void mtty_tty_driver_exit(struct mtty_device *device)
{
    struct tty_driver *driver = device->driver;

    tty_unregister_driver(driver);
    #if(LINUX_VERSION_CODE >= KERNEL_VERSION(5,15,0))
    tty_driver_kref_put(driver);
    #else
    put_tty_driver(driver);
    #endif
    tty_port_destroy(device->port0);
    tty_port_destroy(device->port1);
}

static int mtty_parse_dt(struct mtty_init_data **init, struct device *dev)
{
#ifdef CONFIG_OF
    struct device_node *np = dev->of_node;
    struct mtty_init_data *pdata = NULL;
    int ret;

    pdata = kzalloc(sizeof(struct mtty_init_data), GFP_KERNEL);
    if (!pdata)
        return -ENOMEM;

    ret = of_property_read_string(np,
                    "sprd,name",
                    (const char **)&pdata->name);
    if (ret)
        goto error;
    *init = pdata;

    return 0;
error:
    kfree(pdata);
    *init = NULL;
    return ret;
#else
    return -ENODEV;
#endif
}

static inline void mtty_destroy_pdata(struct mtty_init_data **init)
{
#ifdef CONFIG_OF
    struct mtty_init_data *pdata = *init;

    kfree(pdata);

    *init = NULL;
#else
    return;
#endif
}

#define SPRDWL_MH_ADDRESS_BIT (1UL << 39)

static int bluetooth_reset(struct notifier_block *this, unsigned long ev, void *ptr)
{
#define RESET_BUFSIZE 5

    int ret = 0;
    int block_size = RESET_BUFSIZE;
	unsigned char reset_buf[RESET_BUFSIZE]= {0x04, 0xff, 0x02, 0x57, 0xa5};

	pr_err("%s: reset callback coming\n", __func__);
	if (mtty_dev != NULL) {
		if (!work_pending(&mtty_dev->bt_rx_work)) {
			pr_err("%s tty_insert_flip_string", __func__);

			while(ret < block_size){
				pr_err("%s before tty_insert_flip_string ret: %d, len: %d\n",
						__func__, ret, RESET_BUFSIZE);
				ret = tty_insert_flip_string(mtty_dev->port0,
									(unsigned char *)reset_buf,
									RESET_BUFSIZE);   // -BT_SDIO_HEAD_LEN
			    pr_err("%s ret: %d, len: %d\n", __func__, ret, RESET_BUFSIZE);
				if (ret)
					tty_flip_buffer_push(mtty_dev->port0);
				block_size = block_size - ret;
				ret = 0;
			}
		}
	}
	return NOTIFY_DONE;
}

static struct notifier_block bluetooth_reset_block = {
	.notifier_call = bluetooth_reset,
};

static int mtty_probe(struct platform_device *pdev)
{
    struct mtty_init_data *pdata = (struct mtty_init_data *)
                                pdev->dev.platform_data;
    struct mtty_device *mtty;
    int rval = 0;
    pr_err("mtty start insert mod pdev %p\n", pdev);
    if (pdev->dev.of_node && !pdata) {
        rval = mtty_parse_dt(&pdata, &pdev->dev);
        if (rval) {
            pr_err("failed to parse mtty device tree, ret=%d\n",
                    rval);
            return rval;
        }
    }

    mtty = kzalloc(sizeof(struct mtty_device), GFP_KERNEL);
    if (mtty == NULL) {
        mtty_destroy_pdata(&pdata);
        pr_err("mtty Failed to allocate device!\n");
        return -ENOMEM;
    }
    memset(mtty, 0 ,sizeof(struct mtty_device));
    mtty->pdata = pdata;
    rval = mtty_tty_driver_init(mtty);
    if (rval) {
        mtty_tty_driver_exit(mtty);
        kfree(mtty->port0);
        kfree(mtty->port1);
        kfree(mtty);
        mtty_destroy_pdata(&pdata);
        pr_err("regitster notifier failed (%d)\n", rval);
        return rval;
    }

    pr_info("mtty_probe init device addr: 0x%p\n", mtty);
    platform_set_drvdata(pdev, mtty);
    mtty->pdev = pdev;
    mutex_init(&mtty->stat_mutex);
    sema_init(&sem_id, BT_TX_POOL_SIZE0 - 1);
    mutex_init(&mtty->rw_mutex);
    INIT_LIST_HEAD(&mtty->rx_head);
    mtty->bt_rx_workqueue =
        create_singlethread_workqueue("SPRDBT_RX_QUEUE");
    if (!mtty->bt_rx_workqueue) {
        pr_err("%s SPRDBT_RX_QUEUE create failed", __func__);
        return -ENOMEM;
    }
    INIT_WORK(&mtty->bt_rx_work, mtty_rx_work_queue);

    mtty_dev = mtty;

    rfkill_bluetooth_init(pdev);
    bluesleep_init();
    atomic_notifier_chain_register(&wcn_reset_notifier_list,&bluetooth_reset_block);

    return 0;
}

static void  mtty_remove(struct platform_device *pdev)
{
    struct mtty_device *mtty = platform_get_drvdata(pdev);
    pr_err("mtty remove mod start\n");
    mtty_tty_driver_exit(mtty);
    kfree(mtty->port0);
    kfree(mtty->port1);
    mtty_destroy_pdata(&mtty->pdata);
    flush_workqueue(mtty->bt_rx_workqueue);
    destroy_workqueue(mtty->bt_rx_workqueue);
    kfree(mtty);
    platform_set_drvdata(pdev, NULL);
    bluesleep_exit();

    return;
}

static const struct of_device_id mtty_match_table[] = {
    { .compatible = "sprd,mtty", },
    { },
};

static struct platform_driver mtty_driver = {
    .driver = {
        .owner = THIS_MODULE,
        .name = "mtty",
        .of_match_table = mtty_match_table,
    },
    .probe = mtty_probe,
    .remove = mtty_remove,
};

module_platform_driver(mtty_driver);

MODULE_AUTHOR("Spreadtrum BSP");
MODULE_DESCRIPTION("SPRD marlin tty driver");
