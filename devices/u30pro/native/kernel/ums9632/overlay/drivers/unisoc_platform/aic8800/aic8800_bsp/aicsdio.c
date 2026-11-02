/**
 * aicwf_sdmmc.c
 *
 * SDIO function declarations
 *
 * Copyright (C) AICSemi 2018-2020
 */
#include <linux/interrupt.h>
#include <linux/completion.h>
#include <linux/mmc/sdio.h>
#include <linux/mmc/sdio_ids.h>
#include <linux/mmc/sdio_func.h>
#include <linux/mmc/card.h>
#include <linux/mmc/host.h>
#include <linux/semaphore.h>
#include <linux/debugfs.h>
#include <linux/kthread.h>
#include "aicsdio_txrxif.h"
#include "aicsdio.h"
#include "aic_bsp_driver.h"
#include <linux/version.h>
#include <linux/delay.h>
#ifdef CONFIG_PLATFORM_ROCKCHIP
#include <linux/rfkill-wlan.h>
#endif /* CONFIG_PLATFORM_ROCKCHIP */
#ifdef CONFIG_PLATFORM_ROCKCHIP2
#include <linux/rfkill-wlan.h>
#endif /* CONFIG_PLATFORM_ROCKCHIP */
#include <linux/gpio.h>
#include <linux/of_gpio.h>
#include <linux/delay.h>
#include <linux/mmc/core.h>
#include <linux/device.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#ifdef CONFIG_POWER_ZTE
#include <linux/gpio.h>
#include <linux/of_gpio.h>
#include <linux/completion.h>
#include <linux/semaphore.h>
#include <linux/atomic.h>
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 2, 0)
#include <linux/pm_wakeirq.h>
#else
#include <linux/pm_wakeup.h>
#endif

#ifdef CONFIG_PLATFORM_ALLWINNER
extern void sunxi_mmc_rescan_card(unsigned ids);
extern void sunxi_wlan_set_power(int on);
extern int sunxi_wlan_get_bus_index(void);
static int aicbsp_bus_index = -1;
#endif

#ifdef CONFIG_PLATFORM_AMLOGIC//for AML
#include <linux/amlogic/aml_gpio_consumer.h>
extern void sdio_reinit(void);
extern void extern_wifi_set_enable(int is_on);
extern void set_power_control_lock(int lock);
#endif//for AML


static int aicbsp_platform_power_on(void);
static void aicbsp_platform_power_off(void);

#ifdef CONFIG_AIC_POWER_RESET
static void do_bsp_sdio_recovery(void);
static bool bsp_sdio_ready = false;
#endif


struct aic_sdio_dev *aicbsp_sdiodev = NULL;
static struct semaphore *aicbsp_notify_semaphore;
extern struct semaphore aicbsp_probe_semaphore;

static const struct sdio_device_id aicbsp_sdmmc_ids[];
static bool aicbsp_load_fw_in_fdrv = false;

//int start_bsp = 0;

#define FW_PATH_MAX 200

//#ifdef CONFIG_PLATFORM_UBUNTU
//static const char* aic_default_fw_path = "/lib/firmware/aic8800_sdio";
//#else
static const char* aic_default_fw_path = CONFIG_AIC_FW_PATH;
//#endif
char aic_fw_path[FW_PATH_MAX];
module_param_string(aic_fw_path, aic_fw_path, FW_PATH_MAX, 0660);
#ifdef CONFIG_M2D_OTA_AUTO_SUPPORT
char saved_sdk_ver[64];
module_param_string(saved_sdk_ver, saved_sdk_ver,64, 0660);
#endif

extern int testmode;
int gpio_ldo;
int gpio_dcdc;
int gpio_power_en;


#define SDIO_DEVICE_ID_AIC8801_FUNC2	0x0146
#define SDIO_DEVICE_ID_AIC8800D80_FUNC2	0x0182

/* SDIO Device ID */
#define SDIO_VENDOR_ID_AIC8801              0x5449
#define SDIO_VENDOR_ID_AIC8800DC            0xc8a1
#define SDIO_VENDOR_ID_AIC8800D80           0xc8a1
#define SDIO_VENDOR_ID_AIC8800D80X2         0xc8a1

#define SDIO_DEVICE_ID_AIC8801				0x0145
#define SDIO_DEVICE_ID_AIC8800DC			0xc08d
#define SDIO_DEVICE_ID_AIC8800D80           0x0082
#define SDIO_DEVICE_ID_AIC8800D80X2         0x2082


static int aicbsp_dummy_probe(struct sdio_func *func, const struct sdio_device_id *id)
{
	if (func && (func->num != 2))
		return 0;

	if(func->vendor != SDIO_VENDOR_ID_AIC8801 &&
		func->device != SDIO_DEVICE_ID_AIC8801 &&
		func->device != SDIO_DEVICE_ID_AIC8801_FUNC2 &&
		func->vendor != SDIO_VENDOR_ID_AIC8800DC &&
		func->device != SDIO_DEVICE_ID_AIC8800DC &&
		func->vendor != SDIO_VENDOR_ID_AIC8800D80 &&
		func->device != SDIO_DEVICE_ID_AIC8800D80 &&
		func->device != SDIO_DEVICE_ID_AIC8800D80_FUNC2){
			printk("VID:%x DID:%X \r\n", func->vendor, func->device);
			aicbsp_load_fw_in_fdrv = true;
    }

	if (aicbsp_notify_semaphore)
		up(aicbsp_notify_semaphore);
	return 0;
}

static void aicbsp_dummy_remove(struct sdio_func *func)
{
}

static struct sdio_driver aicbsp_dummy_sdmmc_driver = {
	.probe		= aicbsp_dummy_probe,
	.remove		= aicbsp_dummy_remove,
	.name		= "aicbsp_dummy_sdmmc",
	.id_table	= aicbsp_sdmmc_ids,
};

static int aicbsp_reg_sdio_notify(void *semaphore)
{
	aicbsp_notify_semaphore = semaphore;
	return sdio_register_driver(&aicbsp_dummy_sdmmc_driver);
}

static void aicbsp_unreg_sdio_notify(void)
{
	mdelay(15);
	sdio_unregister_driver(&aicbsp_dummy_sdmmc_driver);
}

static const char *aicbsp_subsys_name(int subsys)
{
	switch (subsys) {
	case AIC_BLUETOOTH:
		return "AIC_BLUETOOTH";
	case AIC_WIFI:
		return "AIC_WIFI";
	default:
		return "unknown subsys";
	}
}

#ifdef CONFIG_PLATFORM_ROCKCHIP
#if 1//FOR RK SUSPEND
void rfkill_rk_sleep_bt(bool sleep);
#endif
#endif

#ifdef CONFIG_PLATFORM_ROCKCHIP2
#if 1//FOR RK SUSPEND
void rfkill_rk_sleep_bt(bool sleep);
#endif
#endif

static void aic_pwrkey_ctrl(int value)
{
	printk("aic_bsp: %s: %d\n", __func__, value);
	gpio_set_value(gpio_power_en, value);
}


#ifdef CONFIG_AIC_POWER_RESET
bool fw_bt_power_on = false;
bool fw_wifi_power_on = false;
#endif

int aicbsp_set_subsys(int subsys, int state)
{
	static int pre_power_map;
	int cur_power_map;
	int pre_power_state;
	int cur_power_state;

	mutex_lock(&aicbsp_power_lock);
	aicbsp_load_fw_in_fdrv = false;
	cur_power_map = pre_power_map;
	if (state)
		cur_power_map |= (1 << subsys);
	else
		cur_power_map &= ~(1 << subsys);

	pre_power_state = pre_power_map > 0;
	cur_power_state = cur_power_map > 0;

	sdio_dbg("%s, subsys: %s, state to: %d\n", __func__, aicbsp_subsys_name(subsys), state);

	if (cur_power_state != pre_power_state) {
		sdio_dbg("%s, power state change to %d dure to %s\n", __func__, cur_power_state, aicbsp_subsys_name(subsys));
		if (cur_power_state) {
#ifdef CONFIG_AIC_POWER_RESET
			if (bsp_sdio_ready == false) { 
				if (aicbsp_platform_power_on() < 0)
					goto err0;
				if (aicbsp_sdio_init())
					goto err1;
			} else {
#ifdef CONFIG_POWER_ZTE
				sdio_dbg("%s : zte platform power on wifi \n", __func__);
				/* re-powering wifi chip to avoid non-power-down reboot of the device */
				aic_pwrkey_ctrl(0);
				mdelay(20);
				aic_pwrkey_ctrl(1);
				mdelay(200);
#endif
				do_bsp_sdio_recovery();
			}
#else
			if (aicbsp_platform_power_on() < 0)
				goto err0;
			if (aicbsp_sdio_init())
				goto err1;
#endif
			if (aicbsp_driver_fw_init(aicbsp_sdiodev))
				goto err1;
#ifndef CONFIG_FDRV_NO_REG_SDIO
			//aicbsp_sdio_release(aicbsp_sdiodev);
			aicbsp_sdiodev->bus_if->state = BUS_HOLD_ST;
			printk("aicbsp_sdiodev->bus_if->state %d\n",aicbsp_sdiodev->bus_if->state);
#endif

#if defined CONFIG_PLATFORM_ROCKCHIP || defined CONFIG_PLATFORM_ROCKCHIP2
#ifdef CONFIG_GPIO_WAKEUP
			//BT_SLEEP:true,BT_WAKEUP:false
			rfkill_rk_sleep_bt(true);
			printk("%s BT wake default to SLEEP\r\n", __func__);
#endif
#endif

#ifdef CONFIG_AIC_POWER_RESET
			if (subsys == AIC_WIFI) {
				fw_wifi_power_on = true;

			} else if(subsys == AIC_BLUETOOTH) {
				fw_bt_power_on = true;
			}
#endif

//#ifndef CONFIG_PLATFORM_ROCKCHIP
//			aicbsp_sdio_exit();
//#endif
		} else {
		#ifndef CONFIG_PLATFORM_ROCKCHIP
#ifndef CONFIG_AIC_POWER_RESET
			aicbsp_sdio_exit();
#endif
		#endif
#ifdef CONFIG_AIC_POWER_RESET
			if (subsys == AIC_WIFI) {
				fw_wifi_power_on = false;
			} else if(subsys == AIC_BLUETOOTH) {
				fw_bt_power_on = false;
			}
#endif
			aicbsp_platform_power_off();
		}
	} else {
		sdio_dbg("%s, power state no need to change, current: %d\n", __func__, cur_power_state);
#ifdef CONFIG_AIC_POWER_RESET
		if (subsys == AIC_WIFI) {
			if (state == AIC_PWR_ON)
				fw_wifi_power_on = true;
			else if (state == AIC_PWR_OFF)
				fw_wifi_power_on = false;
		} else if(subsys == AIC_BLUETOOTH) {
			if (state == AIC_PWR_ON)
				fw_bt_power_on = true;
			else if (state == AIC_PWR_OFF)
				fw_bt_power_on = false;
		}
#endif
	}
	pre_power_map = cur_power_map;
	mutex_unlock(&aicbsp_power_lock);
	printk("aic_dbg end set_subsys");

	return cur_power_state;

#if 0
err2:
	aicbsp_sdio_release(aicbsp_sdiodev);
	aicbsp_sdio_exit();
#endif
err1:
	aicbsp_platform_power_off();

err0:
	sdio_dbg("%s, fail to set %s power state to %d\n", __func__, aicbsp_subsys_name(subsys), state);
	mutex_unlock(&aicbsp_power_lock);
	return -1;
}
EXPORT_SYMBOL_GPL(aicbsp_set_subsys);
#ifdef CONFIG_AIC_POWER_RESET
EXPORT_SYMBOL_GPL(fw_bt_power_on);
EXPORT_SYMBOL_GPL(fw_wifi_power_on);
#endif

bool aicbsp_get_load_fw_in_fdrv(void){
	return aicbsp_load_fw_in_fdrv;
}

EXPORT_SYMBOL_GPL(aicbsp_get_load_fw_in_fdrv);
#ifdef CONFIG_PLATFORM_ZTE
int wifi_wk_host_gpio = 646;

#endif

#ifdef CONFIG_GPIO_WAKEUP
static int wakeup_enable;
static u32 hostwake_irq_num;
#endif//CONFIG_GPIO_WAKEUP

#ifdef CONFIG_GPIO_WAKEUP
#if 0
void rwnx_set_wifi_suspend(char onoff);
#endif

extern int oob_ok;
static irqreturn_t rwnx_hostwake_irq_handler(int irq, void *para)
{
	static int wake_cnt;
	wake_cnt++;

#if 0
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
	rwnx_wakeup_lock_timeout(g_rwnx_plat->sdiodev->rwnx_hw->ws_rx, 1000);
#else
#ifdef ANDROID_PLATFORM
	wake_lock_timeout(&irq_wakelock, HZ);
#endif //ANDROID_PLATFORM
#endif
#endif
	AICWFDBG(LOGIRQ, "%s(%d): wake_irq_cnt = %d\n", __func__, __LINE__, wake_cnt);
#ifdef CONFIG_OOB_DEBUG
	printk("hw i\n");
#endif
#ifdef CONFIG_OOB
#if 0//old oob feature
    complete(&aicbsp_sdiodev->bus_if->busrx_trgg);
#else//new oob feature
    if(aicbsp_sdiodev->oob_enable){
	#ifdef CONFIG_OOB_DEBUG
		printk("hw i1\n");
	#endif
        complete(&aicbsp_sdiodev->bus_if->busirq_trgg);
    }
#endif//old oob feature
#endif

	return IRQ_HANDLED;
}
#endif//CONFIG_GPIO_WAKEUP


static int rwnx_register_hostwake_irq(struct device *dev)
{
	int ret = 0;//-1;
#ifdef CONFIG_GPIO_WAKEUP
	unsigned long flag_edge;
	//struct aicbsp_feature_t aicwf_feature;
	int irq_flags;

//TODO hostwake_irq_num hostwake_irq_num and wakeup_enable
#ifndef CONFIG_IRQ_FALL
		flag_edge = IRQF_TRIGGER_RISING | IRQF_NO_SUSPEND;
#else
		flag_edge = IRQF_TRIGGER_FALLING | IRQF_NO_SUSPEND;
#endif

#ifdef CONFIG_PLATFORM_ZTE
			hostwake_irq_num = gpio_to_irq(wifi_wk_host_gpio);
			irq_flags = (IORESOURCE_IRQ | IORESOURCE_IRQ_HIGHLEVEL | IORESOURCE_IRQ_SHAREABLE) & IRQF_TRIGGER_MASK;
			printk("hostwake_irq_num %d",hostwake_irq_num);
			wakeup_enable = 1;
#endif

#ifdef CONFIG_PLATFORM_ALLWINNER
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 4, 0)
	hostwake_irq_num = sunxi_wlan_get_oob_irq(&irq_flags, &wakeup_enable);
#else
	hostwake_irq_num = sunxi_wlan_get_oob_irq();
	irq_flags = sunxi_wlan_get_oob_irq_flags();
	wakeup_enable = 1;
#endif
#endif //CONFIG_PLATFORM_ALLWINNER

#ifdef CONFIG_PLATFORM_ASR_DOVE
	hostwake_irq_num = gpio_to_irq(wifi_wk_host_gpio);
	irq_flags = (IORESOURCE_IRQ | IORESOURCE_IRQ_HIGHLEVEL | IORESOURCE_IRQ_SHAREABLE) & IRQF_TRIGGER_MASK;
	wakeup_enable = 1;
#endif


//For Rockchip
#ifdef CONFIG_PLATFORM_ROCKCHIP
		hostwake_irq_num = rockchip_wifi_get_oob_irq();
		printk("%s hostwake_irq_num:%d \r\n", __func__, hostwake_irq_num);
		irq_flags = (IORESOURCE_IRQ | IORESOURCE_IRQ_HIGHLEVEL | IORESOURCE_IRQ_SHAREABLE) & IRQF_TRIGGER_MASK;
		printk("%s irq_flags:%d \r\n", __func__, irq_flags);
		wakeup_enable = 1;
#endif //CONFIG_PLATFORM_ROCKCHIP
    //For Rockchip
#ifdef CONFIG_PLATFORM_ROCKCHIP2
            hostwake_irq_num = rockchip_wifi_get_oob_irq();
            printk("%s hostwake_irq_num:%d \r\n", __func__, hostwake_irq_num);
            irq_flags = (IORESOURCE_IRQ | IORESOURCE_IRQ_HIGHLEVEL | IORESOURCE_IRQ_SHAREABLE) & IRQF_TRIGGER_MASK;
            printk("%s irq_flags:%d \r\n", __func__, irq_flags);
            wakeup_enable = 1;
#endif //CONFIG_PLATFORM_ROCKCHIP



	if (wakeup_enable) {
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
		//ws = wakeup_source_register(dev, "wifisleep");
		//ws_tx_sdio = wakeup_source_register(dev, "wifi_tx_sleep");
		//ws_rx_sdio = wakeup_source_register(dev, "wifi_rx_sleep");
		//ws_sdio_pwrctrl = wakeup_source_register(dev, "sdio_pwrctrl_sleep");
#else
#ifdef ANDROID_PLATFORM
		wake_lock_init(&irq_wakelock, WAKE_LOCK_SUSPEND, "wifisleep");
#endif
#endif
		ret = device_init_wakeup(dev, true);
		if (ret < 0) {
			pr_err("%s(%d): device init wakeup failed!\n", __func__, __LINE__);
			return ret;
		}

#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 2, 0)
		ret = dev_pm_set_wake_irq(dev, hostwake_irq_num);
#endif
		if (ret < 0) {
			pr_err("%s(%d): can't enable wakeup src!\n", __func__, __LINE__);
			goto fail1;
		}

		ret = request_irq(hostwake_irq_num, rwnx_hostwake_irq_handler, flag_edge, "rwnx_hostwake_irq", NULL);

		if (ret < 0) {
			pr_err("%s(%d): request_irq fail! ret = %d\n", __func__, __LINE__, ret);
			goto fail2;
		}

#ifdef CONFIG_PLATFORM_ASR_DOVE
		ret = request_mfp_edge_wakeup(wifi_wk_host_gpio, rwnx_edge_wakeup_handler, NULL, dev);

		if (ret < 0) {
			pr_err("ASR_AIC: %s(%d): request_irq mfp edge fail! ret = %d\n", __func__, __LINE__, ret);
			goto fail2;
		}
#endif
	}
	//disable_irq(hostwake_irq_num);
	rwnx_disable_hostwake_irq();
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 2, 0)
	dev_pm_clear_wake_irq(dev);
#endif
	rwnx_enable_hostwake_irq();
	AICWFDBG(LOGINFO, "%s(%d)\n", __func__, __LINE__);
	return ret;

fail2:
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 2, 0)
	dev_pm_clear_wake_irq(dev);
#endif
fail1:
	device_init_wakeup(dev, false);
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
	//wakeup_source_unregister(ws);
	//wakeup_source_unregister(ws_tx_sdio);
	//wakeup_source_unregister(ws_rx_sdio);
	//wakeup_source_unregister(ws_sdio_pwrctrl);
#else
#ifdef ANDROID_PLATFORM
	wake_lock_destroy(&irq_wakelock);
#endif
#endif
#endif//CONFIG_GPIO_WAKEUP
	return ret;
}

int rwnx_unregister_hostwake_irq(struct device *dev)
{
#ifdef CONFIG_GPIO_WAKEUP
	rwnx_disable_hostwake_irq();
	if (wakeup_enable) {
		device_init_wakeup(dev, false);
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 2, 0)
		dev_pm_clear_wake_irq(dev);
#else
	AICWFDBG(LOGERROR, "%s kernel unsupport this feature!\r\n", __func__);
#endif
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
		//wakeup_source_unregister(ws);
		//wakeup_source_unregister(ws_tx_sdio);
		//wakeup_source_unregister(ws_rx_sdio);
		//wakeup_source_unregister(ws_sdio_pwrctrl);
#else
#ifdef ANDROID_PLATFORM
		wake_lock_destroy(&irq_wakelock);
#endif //ANDROID_PLATFORM
#endif
	}

#ifdef CONFIG_PLATFORM_ASR_DOVE
	remove_mfp_edge_wakeup(wifi_wk_host_gpio);
#endif
	free_irq(hostwake_irq_num, NULL);
#endif//CONFIG_GPIO_WAKEUP
	AICWFDBG(LOGINFO, "%s(%d)\n", __func__, __LINE__);
	return 0;
}

#ifdef CONFIG_GPIO_WAKEUP
int rwnx_enable_hostwake_irq(void)
{
#ifdef CONFIG_GPIO_WAKEUP
	enable_irq(hostwake_irq_num);
	enable_irq_wake(hostwake_irq_num);
#endif//CONFIG_GPIO_WAKEUP
	AICWFDBG(LOGINFO, "%s(%d)\n", __func__, __LINE__);
	return 0;
}

int rwnx_disable_hostwake_irq(void)
{
	AICWFDBG(LOGINFO, "%s(%d)\n", __func__, __LINE__);
#ifdef CONFIG_GPIO_WAKEUP
	disable_irq_nosync(hostwake_irq_num);
	//disable_irq_wake(hostwake_irq_num);
	//disable_irq(hostwake_irq_num);
#endif//CONFIG_GPIO_WAKEUP
	return 0;
}
#endif


static int aicwf_sdio_chipmatch(struct aic_sdio_dev *sdio_dev, uint16_t vid, uint16_t did){

	if(vid == SDIO_VENDOR_ID_AIC8801 && did == SDIO_DEVICE_ID_AIC8801){
		sdio_dev->chipid = PRODUCT_ID_AIC8801;
		AICWFDBG(LOGINFO, "%s USE AIC8801\r\n", __func__);
		return 0;
	}else if(vid == SDIO_VENDOR_ID_AIC8800DC && did == SDIO_DEVICE_ID_AIC8800DC){
		sdio_dev->chipid = PRODUCT_ID_AIC8800DC;
		AICWFDBG(LOGINFO, "%s USE AIC8800DC\r\n", __func__);
		return 0;
	}else if(vid == SDIO_VENDOR_ID_AIC8800D80 && did == SDIO_DEVICE_ID_AIC8800D80){
		sdio_dev->chipid = PRODUCT_ID_AIC8800D80;
		AICWFDBG(LOGINFO, "%s USE AIC8800D80\r\n", __func__);
		return 0;
	}else if(vid == SDIO_VENDOR_ID_AIC8800D80X2 && did == SDIO_DEVICE_ID_AIC8800D80X2){
		sdio_dev->chipid = PRODUCT_ID_AIC8800D80X2;
		AICWFDBG(LOGINFO, "%s USE AIC8800D80X2\r\n", __func__);
		return 0;
	}else{
		return -1;
	}
}

void *aicbsp_get_drvdata(void *args)
{
	(void)args;
	if (aicbsp_sdiodev)
		return aicbsp_sdiodev->bus_if;
	return dev_get_drvdata((const struct device *)args);
}


static int aicbsp_sdio_probe(struct sdio_func *func,
	const struct sdio_device_id *id)
{
	struct mmc_host *host;
	struct aic_sdio_dev *sdiodev;
	struct aicwf_bus *bus_if;
	int err = -ENODEV;

	if (func == NULL) {
		sdio_err("%s func is null\n", __func__);
		return err;
	}

	sdio_dbg("%s:%d vid:0x%04X  did:0x%04X\n", __func__, func->num,
		func->vendor, func->device);

	if(func->vendor != SDIO_VENDOR_ID_AIC8801 &&
		func->device != SDIO_DEVICE_ID_AIC8801 &&
		func->device != SDIO_DEVICE_ID_AIC8801_FUNC2 &&
		func->vendor != SDIO_VENDOR_ID_AIC8800DC &&
		func->device != SDIO_DEVICE_ID_AIC8800DC &&
		func->vendor != SDIO_VENDOR_ID_AIC8800D80 &&
		func->device != SDIO_DEVICE_ID_AIC8800D80 &&
		func->device != SDIO_DEVICE_ID_AIC8800D80_FUNC2){
		aicbsp_load_fw_in_fdrv = true;
		return err;
	}

	if (func->num != 2) {
		return err;
	}

	host = func->card->host;
	host->caps |= MMC_CAP_NONREMOVABLE;

	func = func->card->sdio_func[1 - 1]; //replace 2 with 1

	sdio_dbg("%s after replace:%d\n", __func__, func->num);

	bus_if = kzalloc(sizeof(struct aicwf_bus), GFP_KERNEL);
	if (!bus_if) {
		sdio_err("alloc bus fail\n");
		return -ENOMEM;
	}


	sdiodev = kzalloc(sizeof(struct aic_sdio_dev), GFP_KERNEL);
	if (!sdiodev) {
		sdio_err("alloc sdiodev fail\n");
		kfree(bus_if);
		return -ENOMEM;
	}
    aicbsp_sdiodev = sdiodev;

	err = aicwf_sdio_chipmatch(sdiodev, func->vendor, func->device);

#ifdef CONFIG_OOB
		if(sdiodev->chipid == PRODUCT_ID_AIC8801){
			sdio_err("%s ERROR!!! 8801 not support OOB \r\n", __func__);
			sdiodev->oob_enable = false;
		}else{	  
			sdiodev->oob_enable = true;
		}
#else
		sdiodev->oob_enable = false;
#endif


	sdiodev->func = func;
	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		sdiodev->func_msg = func->card->sdio_func[1];
	}
	sdiodev->bus_if = bus_if;
	bus_if->bus_priv.sdio = sdiodev;
	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		dev_set_drvdata(&sdiodev->func_msg->dev, bus_if);
		printk("the device is PRODUCT_ID_AIC8800DC \n");
	}
	dev_set_drvdata(&func->dev, bus_if);
	sdiodev->dev = &func->dev;

    if (sdiodev->chipid != PRODUCT_ID_AIC8800D80 && sdiodev->chipid != PRODUCT_ID_AIC8800D80X2) {
	    err = aicwf_sdio_func_init(sdiodev);
    } else {
        err = aicwf_sdiov3_func_init(sdiodev);
    }
	if (err < 0) {
		sdio_err("sdio func init fail\n");
		goto fail;
	}

	if (aicwf_sdio_bus_init(sdiodev) == NULL) {
		sdio_err("sdio bus init err\r\n");
		goto fail;
	}

	aicbsp_platform_init(sdiodev);
	err = rwnx_register_hostwake_irq(sdiodev->dev);
	if (err != 0)
		return err;
#ifdef CONFIG_AIC_POWER_RESET
	bsp_sdio_ready = true;
#endif

	up(&aicbsp_probe_semaphore);

	return 0;
fail:
	aicwf_sdio_func_deinit(sdiodev);
	dev_set_drvdata(&func->dev, NULL);
	kfree(sdiodev);
	kfree(bus_if);
	return err;
}


static void aicbsp_sdio_remove(struct sdio_func *func)
{
	struct mmc_host *host;
	struct aicwf_bus *bus_if = NULL;
	struct aic_sdio_dev *sdiodev = NULL;

	AICWFDBG(LOGINFO, "%s\n", __func__);
	if (aicbsp_sdiodev == NULL) {
		AICWFDBG(LOGERROR, "%s: allready unregister\n", __func__);
		goto done;
	}
	if (func == NULL) {
		AICWFDBG(LOGERROR, "%s, sdio func is null\n", __func__);
		goto done;
	}

	host = func->card->host;
	host->caps &= ~MMC_CAP_NONREMOVABLE;

	bus_if = aicbsp_get_drvdata(&func->dev);

	if (!bus_if) {
		AICWFDBG(LOGERROR, "%s bus_if is NULL \r\n", __func__);
		goto done;
	}

	sdiodev = bus_if->bus_priv.sdio;
	if (!sdiodev) {
		AICWFDBG(LOGERROR, "%s sdiodev is NULL \r\n", __func__);
		goto done;
	}

	aicwf_sdio_release(sdiodev);
	aicwf_sdio_func_deinit(sdiodev);
	rwnx_unregister_hostwake_irq(sdiodev->dev);
	
	dev_set_drvdata(&sdiodev->func->dev, NULL);
	kfree(sdiodev);

done:
	if (bus_if)
		kfree(bus_if);
	aicbsp_sdiodev = NULL;
	sdio_dbg("%s done\n", __func__);
}


static int aicbsp_sdio_suspend(struct device *dev)
{
	struct sdio_func *func = dev_to_sdio_func(dev);
	int err;
	mmc_pm_flag_t sdio_flags;

#if defined(CONFIG_PLATFORM_ROCKCHIP) || defined(CONFIG_PLATFORM_ROCKCHIP2)
#ifdef CONFIG_GPIO_WAKEUP
    //BT_SLEEP:true,BT_WAKEUP:false
    rfkill_rk_sleep_bt(false);
#endif
#endif

	sdio_dbg("%s, func->num = %d\n", __func__, func->num);
	if (func->num != 2)
		return 0;

	sdio_flags = sdio_get_host_pm_caps(func);
	if (!(sdio_flags & MMC_PM_KEEP_POWER)) {
		sdio_dbg("%s: can't keep power while host is suspended\n", __func__);
		return  -EINVAL;
	}

	/* keep power while host suspended */
	err = sdio_set_host_pm_flags(func, MMC_PM_KEEP_POWER);
	if (err) {
		sdio_dbg("%s: error while trying to keep power\n", __func__);
		return err;
	}

#if defined(CONFIG_PLATFORM_ROCKCHIP) || defined(CONFIG_PLATFORM_ROCKCHIP2)
#ifdef CONFIG_GPIO_WAKEUP
		//BT_SLEEP:true,BT_WAKEUP:false
		rfkill_rk_sleep_bt(true);
		printk("%s BT wake to SLEEP\r\n", __func__);
#endif
#endif

	return 0;
}

static int aicbsp_sdio_resume(struct device *dev)
{
	sdio_dbg("%s\n", __func__);

#if defined(CONFIG_PLATFORM_ROCKCHIP) || defined(CONFIG_PLATFORM_ROCKCHIP2)
#ifdef CONFIG_GPIO_WAKEUP
		//BT_SLEEP:true,BT_WAKEUP:false
		rfkill_rk_sleep_bt(false);
#endif
#endif

	return 0;
}

static const struct sdio_device_id aicbsp_sdmmc_ids[] = {
	{SDIO_DEVICE_CLASS(SDIO_CLASS_WLAN)},
	{ },
};

MODULE_DEVICE_TABLE(sdio, aicbsp_sdmmc_ids);

static const struct dev_pm_ops aicbsp_sdio_pm_ops = {
	SET_SYSTEM_SLEEP_PM_OPS(aicbsp_sdio_suspend, aicbsp_sdio_resume)
};

static struct sdio_driver aicbsp_sdio_driver = {
	.probe = aicbsp_sdio_probe,
	.remove = aicbsp_sdio_remove,
	.name = AICBSP_SDIO_NAME,
	.id_table = aicbsp_sdmmc_ids,
	.drv = {
		.pm = &aicbsp_sdio_pm_ops,
	},
};

#ifdef CONFIG_POWER_ZTE
struct mmc_host *get_host_mmc(void ){
	struct mmc_host *sdio_dev_host;
	struct platform_device *pdev;
	struct device_node *np;
	struct device_node *sdio_node;

	void* drv_data;

	printk("Starting get_host_mmc");

	//step1 

	np = of_find_node_by_name(NULL,"sprd-marlin3");
	if(np == NULL){
		printk("of_find_node_by_name fail");
		return NULL;
	}
	printk("found node sprd-marlin3 succ %s",np->name);
	
	//step2
	sdio_node = of_parse_phandle(np,"sdhci-name",0);
	if(sdio_node == NULL){
		printk("get sdhci-name failed");
		return NULL;
	}
	printk("parse_phandle succ %s",sdio_node->name);

	//step3
	pdev = of_find_device_by_node(sdio_node);
	if(pdev == NULL) {
		printk("fail to find platform device by node");
		return NULL;
	}
	printk("find platform_dev succ %s",pdev->name);

	//step4
	drv_data = platform_get_drvdata(pdev);

	if(drv_data == NULL){
		printk("get platform drvdata fail");
		return NULL;
	}
	sdio_dev_host = drv_data;
	//printk("Got driver data successfully index: %d",sdio_dev_host->index);
 	printk("host_mmc: %p, parent: %p, pdev->dev %p\n",sdio_dev_host,sdio_dev_host->parent,&pdev->dev);	
	if(sdio_dev_host->parent == &pdev->dev)
		return sdio_dev_host;
	else
		return container_of(drv_data,struct mmc_host, private);

}

static int gpio_request_and_config(int gpio, const char *label)
{
    int ret;
    if (!gpio_is_valid(gpio)) {
        pr_err("[marlin_gpio] Invalid GPIO %s (%d)\n", label, gpio);
        return -EINVAL;
    }
    ret = gpio_request(gpio, label);
    if (ret) {
        pr_err("[marlin_gpio] Failed to request GPIO %s (%d)\n", label, gpio);
        return ret;
    }
	if (label && strcmp(label, "aicldoenabled-gpios")) {
		gpio_direction_output(gpio, 0);
	} else {
		pr_info("skip aicldoenabled-gpios output down \n");
	}
    mdelay(1);
    gpio_direction_output(gpio, 1);
    mdelay(1);
    pr_info("[marlin_gpio] GPIO %s pull up done\n", label);
    return 0;
}
#endif

static int aicbsp_platform_power_on(void)
{
	int ret = 0;
	struct semaphore aic_chipup_sem;
#ifdef CONFIG_POWER_ZTE
	// int rc;
	struct device_node *np;
	struct mmc_host *sdio_dev_host;
	// struct completion rescan_done;
	// struct mmc_host *host;
#endif
	sdio_dbg("%s\n", __func__);

#ifdef CONFIG_POWER_ZTE
	np = of_find_compatible_node(NULL, NULL, "unisoc,marlin3lite_sdio"); 
	if (!np) {
        pr_err("[marlin_gpio] Failed to find device node\n");
        return -ENODEV;
    }

	gpio_dcdc= of_get_named_gpio(np, "aicdcdcenabled-gpios", 0);
    gpio_power_en = of_get_named_gpio(np, "aicpowerkeyenabled-gpios", 0);
    gpio_ldo = of_get_named_gpio(np, "aicldoenabled-gpios", 0);
    ret = gpio_request_and_config(gpio_dcdc, "aicdcdcenabled-gpios");
    if (ret){
        printk("fail set dcdc");
		return -1;}
    ret = gpio_request_and_config(gpio_ldo, "aicldoenabled-gpios");
    if (ret){
        printk("fail set ldo");
		return -1;}
    ret = gpio_request_and_config(gpio_power_en, "aicpowerkeyenabled-gpios");
    if (ret){
        printk("fail set powerkey");
		return -1;}

	/* the Android build hardcodes the global number 646 (= 512 + 134); here the DT is authoritative */
	{
		int wk = of_get_named_gpio(np, "aicwk_wake_host-gpios", 0);

		if (gpio_is_valid(wk)) {
			wifi_wk_host_gpio = wk;
			if (gpio_request(wk, "aicwk_wake_host-gpios"))
				pr_warn("[marlin_gpio] hostwake gpio %d already requested\n", wk);
			gpio_direction_input(wk);
			pr_info("[marlin_gpio] hostwake gpio %d (DT) input\n", wk);
		} else {
			pr_err("[marlin_gpio] no aicwk_wake_host-gpios in DT (%d)\n", wk);
		}
	}

	printk("power_on success");
	sdio_dev_host = get_host_mmc();
	if(sdio_dev_host != NULL){
		printk("Found MMC Host %s",mmc_hostname(sdio_dev_host));
		//init_completion(rescan_done);
		mmc_detect_change(sdio_dev_host,msecs_to_jiffies(200));
		//if(wait_for_completion_timeout(rescan_done,msecs_to_jiffies(2500)) == 0) {
		//	printk("wait scan card time out");
		//	return -1;
		//}
		printk("detect success");
	}
		
#endif

#ifdef CONFIG_PLATFORM_ALLWINNER
	if (aicbsp_bus_index < 0)
		 aicbsp_bus_index = sunxi_wlan_get_bus_index();
	if (aicbsp_bus_index < 0)
		return aicbsp_bus_index;
#endif //CONFIG_PLATFORM_ALLWINNER

#ifdef CONFIG_PLATFORM_AMLOGIC
		extern_wifi_set_enable(0);
		mdelay(200);
		extern_wifi_set_enable(1);
		mdelay(200);
		sdio_reinit();
		set_power_control_lock(1);
#endif

#ifdef CONFIG_PLATFORM_ROCKCHIP2
            rockchip_wifi_power(0);
            mdelay(50);
            rockchip_wifi_power(1);
            mdelay(50);
            rockchip_wifi_set_carddetect(1);
#endif /*CONFIG_PLATFORM_ROCKCHIP2*/

	sema_init(&aic_chipup_sem, 0);
	ret = aicbsp_reg_sdio_notify(&aic_chipup_sem);
	if (ret) {
		sdio_dbg("%s aicbsp_reg_sdio_notify fail(%d)\n", __func__, ret);
			return ret;
	}

#ifdef CONFIG_PLATFORM_ALLWINNER
	sunxi_wlan_set_power(0);
	mdelay(50);
	sunxi_wlan_set_power(1);
	mdelay(50);
	sunxi_mmc_rescan_card(aicbsp_bus_index);
#endif //CONFIG_PLATFORM_ALLWINNER

	if (down_timeout(&aic_chipup_sem, msecs_to_jiffies(2000)) == 0) {
		aicbsp_unreg_sdio_notify();
		if(aicbsp_load_fw_in_fdrv){
			printk("%s load fw in fdrv\r\n", __func__);
			return -1;
		}
		return 0;
	}

	aicbsp_unreg_sdio_notify();
#ifdef CONFIG_PLATFORM_ALLWINNER
	sunxi_wlan_set_power(0);
#endif //CONFIG_PLATFORM_ALLWINNER

#ifdef CONFIG_PLATFORM_AMLOGIC
        extern_wifi_set_enable(0);
#endif


#ifdef CONFIG_PLATFORM_ROCKCHIP2
	rockchip_wifi_power(0);
#endif /*CONFIG_PLATFORM_ROCKCHIP2*/

	return -1;
}

static void aicbsp_platform_power_off(void)
{
//TODO wifi disable and sdio card detection
#ifdef CONFIG_PLATFORM_ALLWINNER
	if (aicbsp_bus_index < 0)
		 aicbsp_bus_index = sunxi_wlan_get_bus_index();
	if (aicbsp_bus_index < 0) {
		sdio_dbg("no aicbsp_bus_index\n");
		return;
	}
	sunxi_wlan_set_power(0);
	mdelay(100);
	sunxi_mmc_rescan_card(aicbsp_bus_index);
#endif //CONFIG_PLATFORM_ALLWINNER

#ifdef CONFIG_PLATFORM_ROCKCHIP2
	rockchip_wifi_set_carddetect(0);
	mdelay(200);
	rockchip_wifi_power(0);
	mdelay(200);
#endif /*CONFIG_PLATFORM_ROCKCHIP*/
#ifdef CONFIG_PLATFORM_AMLOGIC
	extern_wifi_set_enable(0);
#endif

#ifdef CONFIG_POWER_ZTE
	aic_pwrkey_ctrl(0);
#endif

	sdio_dbg("%s\n", __func__);
}


int aicbsp_sdio_init(void)
{

	if (sdio_register_driver(&aicbsp_sdio_driver)) {
		return -1;
	} else {
		//may add mmc_rescan here
	}
	if (down_timeout(&aicbsp_probe_semaphore, msecs_to_jiffies(2000)) != 0){
		printk("%s aicbsp_sdio_probe fail\r\n", __func__);
		return -1;
	}

	
	return 0;
}

void aicbsp_sdio_exit(void)
{
	sdio_unregister_driver(&aicbsp_sdio_driver);
}

void aicbsp_sdio_release(struct aic_sdio_dev *sdiodev)
{
	sdiodev->bus_if->state = BUS_DOWN_ST;
	sdio_claim_host(sdiodev->func);
	sdio_release_irq(sdiodev->func);
	sdio_release_host(sdiodev->func);
	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC  || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		sdio_claim_host(sdiodev->func_msg);
		sdio_release_irq(sdiodev->func_msg);
		sdio_release_host(sdiodev->func_msg);
	}
}

int aicwf_sdio_readb(struct aic_sdio_dev *sdiodev, uint regaddr, u8 *val)
{
	int ret;
	sdio_claim_host(sdiodev->func);
	*val = sdio_readb(sdiodev->func, regaddr, &ret);
	sdio_release_host(sdiodev->func);
	return ret;
}

int aicwf_sdio_readb_func2(struct aic_sdio_dev *sdiodev, uint regaddr, u8 *val)
{
    int ret;
    sdio_claim_host(sdiodev->func_msg);
    *val = sdio_readb(sdiodev->func_msg, regaddr, &ret);
    sdio_release_host(sdiodev->func_msg);
    return ret;
}

int aicwf_sdio_writeb(struct aic_sdio_dev *sdiodev, uint regaddr, u8 val)
{
	int ret;
	sdio_claim_host(sdiodev->func);
	sdio_writeb(sdiodev->func, val, regaddr, &ret);
	sdio_release_host(sdiodev->func);
	return ret;
}

int aicwf_sdio_writeb_func2(struct aic_sdio_dev *sdiodev, uint regaddr, u8 val)
{
    int ret;
    sdio_claim_host(sdiodev->func_msg);
    sdio_writeb(sdiodev->func_msg, val, regaddr, &ret);
    sdio_release_host(sdiodev->func_msg);
    return ret;
}

int aicwf_sdio_flow_ctrl(struct aic_sdio_dev *sdiodev)
{
	int ret = -1;
	u8 fc_reg = 0;
	u32 count = 0;

	while (true) {
		ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.flow_ctrl_reg, &fc_reg);
		if (ret) {
			return -1;
		}

        if (sdiodev->chipid == PRODUCT_ID_AIC8801 || sdiodev->chipid == PRODUCT_ID_AIC8800DC ||
            sdiodev->chipid == PRODUCT_ID_AIC8800DW) {
            fc_reg = fc_reg & SDIOWIFI_FLOWCTRL_MASK_REG;
        }

		if (fc_reg != 0) {
            ret = fc_reg;
			return ret;
		} else {
			if (count >= FLOW_CTRL_RETRY_COUNT) {
				ret = -fc_reg;
				break;
			}
			count++;
			if (count < 30)
				udelay(200);
			else if (count < 40)
				mdelay(1);
			else
				mdelay(10);
		}
	}

	return ret;
}

int aicwf_sdio_send_msg(struct aic_sdio_dev *sdiodev, u8 *buf, uint count)
{
    int ret = 0;

    sdio_claim_host(sdiodev->func_msg);
    ret = sdio_writesb(sdiodev->func_msg, 7, buf, count);
    sdio_release_host(sdiodev->func_msg);

    return ret;
}

int aicwf_sdio_send_pkt(struct aic_sdio_dev *sdiodev, u8 *buf, uint count)
{
	int ret = 0;

	sdio_claim_host(sdiodev->func);
	ret = sdio_writesb(sdiodev->func, sdiodev->sdio_reg.wr_fifo_addr, buf, count);
	sdio_release_host(sdiodev->func);

	return ret;
}

int aicwf_sdio_recv_pkt(struct aic_sdio_dev *sdiodev, struct sk_buff *skbbuf,
	u32 size, u8 msg)
{
	int ret;

	if ((!skbbuf) || (!size)) {
		return -EINVAL;;
	}

    if(!msg) {
        sdio_claim_host(sdiodev->func);
        ret = sdio_readsb(sdiodev->func, skbbuf->data, sdiodev->sdio_reg.rd_fifo_addr, size);
        sdio_release_host(sdiodev->func);
    } else {
        sdio_claim_host(sdiodev->func_msg);
        ret = sdio_readsb(sdiodev->func_msg, skbbuf->data, sdiodev->sdio_reg.rd_fifo_addr, size);
        sdio_release_host(sdiodev->func_msg);
    }


	if (ret < 0) {
		return ret;
	}
	skbbuf->len = size;

	return ret;
}


#if defined(CONFIG_SDIO_PWRCTRL)
int aicwf_sdio_wakeup(struct aic_sdio_dev *sdiodev)
{
	int ret = 0;
	int read_retry;
	int write_retry = 20;
    int wakeup_reg_val = 0;

    if (sdiodev->chipid == PRODUCT_ID_AIC8801 ||
        sdiodev->chipid == PRODUCT_ID_AIC8800DC ||
        sdiodev->chipid == PRODUCT_ID_AIC8800DW) {
        wakeup_reg_val = 1;
    } else if (sdiodev->chipid == PRODUCT_ID_AIC8800D80 || sdiodev->chipid == PRODUCT_ID_AIC8800D80X2) {
        wakeup_reg_val = 0x11;
    }

	if (sdiodev->state == SDIO_SLEEP_ST) {
		//if (sdiodev->rwnx_hw->vif_started) {
			down(&sdiodev->pwrctl_wakeup_sema);
			while (write_retry) {
				ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.wakeup_reg, wakeup_reg_val);
				if (ret) {
					txrx_err("sdio wakeup fail\n");
					ret = -1;
				} else {
					read_retry = 10;
					while (read_retry) {
						u8 val;
						ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.sleep_reg, &val);
						if ((ret == 0) && (val & 0x10)) {
							break;
						}
						read_retry--;
						udelay(200);
					}
					if (read_retry != 0)
						break;
				}
				sdio_dbg("write retry:  %d \n", write_retry);
				write_retry--;
				udelay(100);
			}
			up(&sdiodev->pwrctl_wakeup_sema);
	   // }
	}

	sdiodev->state = SDIO_ACTIVE_ST;
	aicwf_sdio_pwrctl_timer(sdiodev, sdiodev->active_duration);

	return ret;
}

extern u8 dhcped;
int aicwf_sdio_sleep_allow(struct aic_sdio_dev *sdiodev)
{
	int ret = 0;
	struct aicwf_bus *bus_if = sdiodev->bus_if;

	if (bus_if->state != BUS_UP_ST) {
		ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.sleep_reg, 0x10);
		if (ret) {
			sdio_err("Write sleep fail!\n");
	}
		aicwf_sdio_pwrctl_timer(sdiodev, 0);
		return ret;
	}

	if (sdiodev->state == SDIO_ACTIVE_ST) {
		{
			sdio_dbg("s\n");
			ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.sleep_reg, 0x10);
			if (ret)
				sdio_err("Write sleep fail!\n");
		}
		sdiodev->state = SDIO_SLEEP_ST;
		aicwf_sdio_pwrctl_timer(sdiodev, 0);
	} else {
		aicwf_sdio_pwrctl_timer(sdiodev, sdiodev->active_duration);
	}

	return ret;
}

int aicwf_sdio_pwr_stctl(struct aic_sdio_dev *sdiodev, uint target)
{
	int ret = 0;

	if (sdiodev->bus_if->state != BUS_UP_ST) {
		return -1;
	}

	if (sdiodev->state == target) {
		if (target == SDIO_ACTIVE_ST) {
			aicwf_sdio_pwrctl_timer(sdiodev, sdiodev->active_duration);
		}
		return ret;
	}

	switch (target) {
	case SDIO_ACTIVE_ST:
		aicwf_sdio_wakeup(sdiodev);
		break;
	case SDIO_SLEEP_ST:
		aicwf_sdio_sleep_allow(sdiodev);
		break;
	}

	return ret;
}
#endif

int aicwf_sdio_txpkt(struct aic_sdio_dev *sdiodev, struct sk_buff *pkt)
{
	int ret = 0;
	u8 *frame;
	u32 len = 0;
	struct aicwf_bus *bus_if = dev_get_drvdata(sdiodev->dev);

	if (bus_if->state != BUS_UP_ST) {
		sdio_dbg("tx bus is down!\n");
		return -EINVAL;
	}

	frame = (u8 *) (pkt->data);
	len = pkt->len;
	len = (len + SDIOWIFI_FUNC_BLOCKSIZE - 1) / SDIOWIFI_FUNC_BLOCKSIZE * SDIOWIFI_FUNC_BLOCKSIZE;
	ret = aicwf_sdio_send_pkt(sdiodev, pkt->data, len);
	if (ret)
		sdio_err("aicwf_sdio_send_pkt fail%d\n", ret);

	return ret;
}

static int aicwf_sdio_intr_get_len_bytemode(struct aic_sdio_dev *sdiodev, u8 *byte_len)
{
	int ret = 0;

	if (!byte_len)
		return -EBADE;

	if (sdiodev->bus_if->state != BUS_UP_ST) {
		*byte_len = 0;
	} else {
		ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.bytemode_len_reg, byte_len);
		sdiodev->rx_priv->data_len = (*byte_len)*4;
	}

	return ret;
}

static void aicwf_sdio_bus_stop(struct device *dev)
{
	struct aicwf_bus *bus_if = aicbsp_get_drvdata(dev);
	struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;
	int ret;

#if defined(CONFIG_SDIO_PWRCTRL)
	aicwf_sdio_pwrctl_timer(sdiodev, 0);
	sdio_dbg("%s\n", __func__);
	if (sdiodev->pwrctl_tsk) {
		complete_all(&sdiodev->pwrctrl_trgg);
		kthread_stop(sdiodev->pwrctl_tsk);
		sdiodev->pwrctl_tsk = NULL;
	}
#endif
	bus_if->state = BUS_DOWN_ST;
	ret = down_interruptible(&sdiodev->tx_priv->txctl_sema);
	if (ret)
	   sdio_err("down txctl_sema fail\n");

#if defined(CONFIG_SDIO_PWRCTRL)
	aicwf_sdio_pwr_stctl(sdiodev, SDIO_SLEEP_ST);
#endif
	if (!ret)
		up(&sdiodev->tx_priv->txctl_sema);
	aicwf_frame_queue_flush(&sdiodev->tx_priv->txq);
}

struct sk_buff *aicwf_sdio_readframes(struct aic_sdio_dev *sdiodev, u8 msg)
{
	int ret = 0;
	u32 size = 0;
	struct sk_buff *skb = NULL;
	struct aicwf_bus *bus_if = dev_get_drvdata(sdiodev->dev);

	if (bus_if->state != BUS_UP_ST) {
		sdio_dbg("bus down\n");
		return NULL;
	}

	size = sdiodev->rx_priv->data_len;
	skb =  __dev_alloc_skb(size, GFP_KERNEL);
	if (!skb) {
		return NULL;
	}

	ret = aicwf_sdio_recv_pkt(sdiodev, skb, size, msg);
	if (ret) {
		dev_kfree_skb(skb);
		skb = NULL;
	}

	return skb;
}

static int aicwf_sdio_tx_msg(struct aic_sdio_dev *sdiodev)
{
	int err = 0;
	u16 len;
	u8 *payload = sdiodev->tx_priv->cmd_buf;
	u16 payload_len = sdiodev->tx_priv->cmd_len;
	u8 adjust_str[4] = {0, 0, 0, 0};
	int adjust_len = 0;
	int buffer_cnt = 0;
	u8 retry = 0;

	len = payload_len;
	if ((len % TX_ALIGNMENT) != 0) {
		adjust_len = roundup(len, TX_ALIGNMENT);
		memcpy(payload+payload_len, adjust_str, (adjust_len - len));
		payload_len += (adjust_len - len);
	}
	len = payload_len;

	//link tail is necessary
	if ((len % SDIOWIFI_FUNC_BLOCKSIZE) != 0) {
		memset(payload+payload_len, 0, TAIL_LEN);
		payload_len += TAIL_LEN;
		len = (payload_len/SDIOWIFI_FUNC_BLOCKSIZE + 1) * SDIOWIFI_FUNC_BLOCKSIZE;
	} else
		len = payload_len;

	if(sdiodev->chipid == PRODUCT_ID_AIC8801 || sdiodev->chipid == PRODUCT_ID_AIC8800D80 || 
		sdiodev->chipid == PRODUCT_ID_AIC8800D80X2){
		buffer_cnt = aicwf_sdio_flow_ctrl(sdiodev);
		while ((buffer_cnt <= 0 || (buffer_cnt > 0 && len > (buffer_cnt * BUFFER_SIZE))) && retry < 10) {
			retry++;
			buffer_cnt = aicwf_sdio_flow_ctrl(sdiodev);
			printk("buffer_cnt = %d\n", buffer_cnt);
		}
	}
	down(&sdiodev->tx_priv->cmd_txsema);

	if(sdiodev->chipid == PRODUCT_ID_AIC8801 || sdiodev->chipid == PRODUCT_ID_AIC8800D80 ||
		sdiodev->chipid == PRODUCT_ID_AIC8800D80X2){
		if (buffer_cnt > 0 && len < (buffer_cnt * BUFFER_SIZE)) {
			err = aicwf_sdio_send_pkt(sdiodev, payload, len);
			if (err) {
				sdio_err("aicwf_sdio_send_pkt fail%d\n", err);
			}
		} else {
			sdio_err("tx msg fc retry fail:%d, %d\n", buffer_cnt, len);
			up(&sdiodev->tx_priv->cmd_txsema);
			return -1;
		}
	}else if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		err = aicwf_sdio_send_msg(sdiodev, payload, len);
		if (err) {
			sdio_err("aicwf_sdio_send_pkt fail%d\n", err);
		}
	} else {
		sdio_err("tx msg fc retry fail:%d, %d\n", buffer_cnt, len);
		up(&sdiodev->tx_priv->cmd_txsema);
		return -1;
	}

	sdiodev->tx_priv->cmd_txstate = false;
	if (!err)
		sdiodev->tx_priv->cmd_tx_succ = true;
	else
		sdiodev->tx_priv->cmd_tx_succ = false;

	up(&sdiodev->tx_priv->cmd_txsema);

	return err;
}

static void aicwf_sdio_tx_process(struct aic_sdio_dev *sdiodev)
{
	int err = 0;

	if (sdiodev->bus_if->state != BUS_UP_ST) {
		sdio_err("Bus is down\n");
		return;
	}

#if defined(CONFIG_SDIO_PWRCTRL)
	aicwf_sdio_pwr_stctl(sdiodev, SDIO_ACTIVE_ST);
#endif

	//config
	sdio_info("send cmd\n");
	if (sdiodev->tx_priv->cmd_txstate) {
		if (down_interruptible(&sdiodev->tx_priv->txctl_sema)) {
			txrx_err("txctl down bus->txctl_sema fail\n");
			return;
		}
		if (sdiodev->state != SDIO_ACTIVE_ST) {
			txrx_err("state err\n");
			up(&sdiodev->tx_priv->txctl_sema);
			txrx_err("txctl up bus->txctl_sema fail\n");
			return;
		}

		err = aicwf_sdio_tx_msg(sdiodev);
		up(&sdiodev->tx_priv->txctl_sema);
		if (waitqueue_active(&sdiodev->tx_priv->cmd_txdone_wait))
			wake_up(&sdiodev->tx_priv->cmd_txdone_wait);
	}

	//data
	sdio_info("send data\n");
	if (down_interruptible(&sdiodev->tx_priv->txctl_sema)) {
		txrx_err("txdata down bus->txctl_sema\n");
		return;
	}

	if (sdiodev->state != SDIO_ACTIVE_ST) {
		txrx_err("sdio state err\n");
		up(&sdiodev->tx_priv->txctl_sema);
		return;
	}

    if(!aicwf_is_framequeue_empty(&sdiodev->tx_priv->txq))
	    sdiodev->tx_priv->fw_avail_bufcnt = aicwf_sdio_flow_ctrl(sdiodev);
	while (!aicwf_is_framequeue_empty(&sdiodev->tx_priv->txq)) {
		aicwf_sdio_send(sdiodev->tx_priv);
		if (sdiodev->tx_priv->cmd_txstate)
			break;
	}

	up(&sdiodev->tx_priv->txctl_sema);
}

static int aicwf_sdio_bus_txdata(struct device *dev, struct sk_buff *pkt)
{
	uint prio;
	int ret = -EBADE;
	struct aicwf_bus *bus_if = dev_get_drvdata(dev);
	struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;

	prio = (pkt->priority & 0x7);
	spin_lock_bh(&sdiodev->tx_priv->txqlock);
	if (!aicwf_frame_enq(sdiodev->dev, &sdiodev->tx_priv->txq, pkt, prio)) {
		spin_unlock_bh(&sdiodev->tx_priv->txqlock);
		return -ENOSR;
	} else {
		ret = 0;
	}

	if (bus_if->state != BUS_UP_ST) {
		sdio_err("bus_if stopped\n");
		spin_unlock_bh(&sdiodev->tx_priv->txqlock);
		return -1;
	}

	atomic_inc(&sdiodev->tx_priv->tx_pktcnt);
	spin_unlock_bh(&sdiodev->tx_priv->txqlock);
	complete(&bus_if->bustx_trgg);

	return ret;
}

static int aicwf_sdio_bus_txmsg(struct device *dev, u8 *msg, uint msglen)
{
	int ret = -1;
	struct aicwf_bus *bus_if = dev_get_drvdata(dev);
	struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;

	down(&sdiodev->tx_priv->cmd_txsema);
	sdiodev->tx_priv->cmd_txstate = true;
	sdiodev->tx_priv->cmd_tx_succ = false;
	sdiodev->tx_priv->cmd_buf = msg;
	sdiodev->tx_priv->cmd_len = msglen;
	up(&sdiodev->tx_priv->cmd_txsema);

	if (bus_if->state != BUS_UP_ST) {
		sdio_err("bus has stop\n");
		return -1;
	}

	complete(&bus_if->bustx_trgg);

	if (sdiodev->tx_priv->cmd_txstate) {
		int timeout = msecs_to_jiffies(CMD_TX_TIMEOUT);
		ret = wait_event_timeout(sdiodev->tx_priv->cmd_txdone_wait, \
											!(sdiodev->tx_priv->cmd_txstate), timeout);
	}

	if (!sdiodev->tx_priv->cmd_txstate && sdiodev->tx_priv->cmd_tx_succ) {
		ret = 0;
	} else {
		sdio_err("send faild:%d, %d,%x\n", sdiodev->tx_priv->cmd_txstate, sdiodev->tx_priv->cmd_tx_succ, ret);
		ret = -EIO;
	}

	return ret;
}

int aicwf_sdio_send(struct aicwf_tx_priv *tx_priv)
{
	struct sk_buff *pkt;
	struct aic_sdio_dev *sdiodev = tx_priv->sdiodev;
	u16 aggr_len = 0;
	int retry_times = 0;
	int max_retry_times = 5;

	aggr_len = (tx_priv->tail - tx_priv->head);
	if (((atomic_read(&tx_priv->aggr_count) == 0) && (aggr_len != 0))
		|| ((atomic_read(&tx_priv->aggr_count) != 0) && (aggr_len == 0))) {
		if (aggr_len > 0)
			aicwf_sdio_aggrbuf_reset(tx_priv);
		goto done;
	}

	if (tx_priv->fw_avail_bufcnt <= 0) { //flow control failed
		tx_priv->fw_avail_bufcnt = aicwf_sdio_flow_ctrl(sdiodev);
		while (tx_priv->fw_avail_bufcnt <= 0 && retry_times < max_retry_times) {
			retry_times++;
			tx_priv->fw_avail_bufcnt = aicwf_sdio_flow_ctrl(sdiodev);
		}
		if (tx_priv->fw_avail_bufcnt <= 0) {
			sdio_err("fc retry %d fail\n", tx_priv->fw_avail_bufcnt);
			goto done;
		}
	}

	if (atomic_read(&tx_priv->aggr_count) == tx_priv->fw_avail_bufcnt) {
		if (atomic_read(&tx_priv->aggr_count) > 0) {
			tx_priv->fw_avail_bufcnt -= atomic_read(&tx_priv->aggr_count);
			aicwf_sdio_aggr_send(tx_priv); //send and check the next pkt;
		}
	} else {
		spin_lock_bh(&sdiodev->tx_priv->txqlock);
		pkt = aicwf_frame_dequeue(&sdiodev->tx_priv->txq);
		if (pkt == NULL) {
			sdio_err("txq no pkt\n");
			spin_unlock_bh(&sdiodev->tx_priv->txqlock);
			goto done;
		}
		atomic_dec(&sdiodev->tx_priv->tx_pktcnt);
		spin_unlock_bh(&sdiodev->tx_priv->txqlock);

		if (tx_priv == NULL || tx_priv->tail == NULL || pkt == NULL)
			txrx_err("null error\n");
		if (aicwf_sdio_aggr(tx_priv, pkt)) {
			aicwf_sdio_aggrbuf_reset(tx_priv);
			sdio_err("add aggr pkts failed!\n");
			goto done;
		}

		//when aggr finish or there is cmd to send, just send this aggr pkt to fw
		if ((int)atomic_read(&sdiodev->tx_priv->tx_pktcnt) == 0 || sdiodev->tx_priv->cmd_txstate) { //no more pkt send it!
			tx_priv->fw_avail_bufcnt -= atomic_read(&tx_priv->aggr_count);
			aicwf_sdio_aggr_send(tx_priv);
		} else
			goto done;
	}

done:
	return 0;
}

int aicwf_sdio_aggr(struct aicwf_tx_priv *tx_priv, struct sk_buff *pkt)
{
	//struct rwnx_txhdr *txhdr = (struct rwnx_txhdr *)pkt->data;
	u8 *start_ptr = tx_priv->tail;
	u8 sdio_header[4];
	u8 adjust_str[4] = {0, 0, 0, 0};
	u16 curr_len = 0;
	int allign_len = 0;

	//sdio_header[0] =((pkt->len - sizeof(struct rwnx_txhdr) + sizeof(struct txdesc_api)) & 0xff);
	//sdio_header[1] =(((pkt->len - sizeof(struct rwnx_txhdr) + sizeof(struct txdesc_api)) >> 8)&0x0f);
	sdio_header[2] = 0x01; //data
	if (tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8801 || tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800DC ||
        tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800DW)
        sdio_header[3] = 0; //reserved
    else if (tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800D80  || tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800D80X2)
	    sdio_header[3] = crc8_ponl_107(&sdio_header[0], 3); // crc8

	memcpy(tx_priv->tail, (u8 *)&sdio_header, sizeof(sdio_header));
	tx_priv->tail += sizeof(sdio_header);
	//payload
	//memcpy(tx_priv->tail, (u8 *)(long)&txhdr->sw_hdr->desc, sizeof(struct txdesc_api));
	//tx_priv->tail += sizeof(struct txdesc_api); //hostdesc
	//memcpy(tx_priv->tail, (u8 *)((u8 *)txhdr + txhdr->sw_hdr->headroom), pkt->len-txhdr->sw_hdr->headroom);
	//tx_priv->tail += (pkt->len - txhdr->sw_hdr->headroom);

	//word alignment
	curr_len = tx_priv->tail - tx_priv->head;
	if (curr_len & (TX_ALIGNMENT - 1)) {
		allign_len = roundup(curr_len, TX_ALIGNMENT)-curr_len;
		memcpy(tx_priv->tail, adjust_str, allign_len);
		tx_priv->tail += allign_len;
	}

    if (tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8801 || tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800DC ||
        tx_priv->sdiodev->chipid == PRODUCT_ID_AIC8800DW) {
    	start_ptr[0] = ((tx_priv->tail - start_ptr - 4) & 0xff);
    	start_ptr[1] = (((tx_priv->tail - start_ptr - 4)>>8) & 0x0f);
    }

	tx_priv->aggr_buf->dev = pkt->dev;

	#if 0
	if (!txhdr->sw_hdr->need_cfm) {
		kmem_cache_free(txhdr->sw_hdr->rwnx_vif->rwnx_hw->sw_txhdr_cache, txhdr->sw_hdr);
		skb_pull(pkt, txhdr->sw_hdr->headroom);
		consume_skb(pkt);
	}
	#endif

	consume_skb(pkt);
	atomic_inc(&tx_priv->aggr_count);
	return 0;
}

void aicwf_sdio_aggr_send(struct aicwf_tx_priv *tx_priv)
{
	struct sk_buff *tx_buf = tx_priv->aggr_buf;
	int ret = 0;
	int curr_len = 0;

	//link tail is necessary
	curr_len = tx_priv->tail - tx_priv->head;
	if ((curr_len % TXPKT_BLOCKSIZE) != 0) {
		memset(tx_priv->tail, 0, TAIL_LEN);
		tx_priv->tail += TAIL_LEN;
	}

	tx_buf->len = tx_priv->tail - tx_priv->head;
	ret = aicwf_sdio_txpkt(tx_priv->sdiodev, tx_buf);
	if (ret < 0) {
		sdio_err("fail to send aggr pkt!\n");
	}

	aicwf_sdio_aggrbuf_reset(tx_priv);
}

void aicwf_sdio_aggrbuf_reset(struct aicwf_tx_priv *tx_priv)
{
	struct sk_buff *aggr_buf = tx_priv->aggr_buf;

	tx_priv->tail = tx_priv->head;
	aggr_buf->len = 0;
	atomic_set(&tx_priv->aggr_count, 0);
}

static int aicwf_sdio_bus_start(struct device *dev)
{
	struct aicwf_bus *bus_if = dev_get_drvdata(dev);
	struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;
	int ret = 0;

	if(sdiodev->chipid == PRODUCT_ID_AIC8801){
		sdio_claim_host(sdiodev->func);
		sdio_claim_irq(sdiodev->func, aicwf_sdio_hal_irqhandler);
		//enable sdio interrupt
		ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x07);
		if (ret != 0)
			sdio_err("intr register failed:%d\n", ret);
		sdio_release_host(sdiodev->func);
	}else if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		sdio_claim_host(sdiodev->func);

		//since we have func2 we don't register irq handler
		sdio_claim_irq(sdiodev->func, NULL);
		sdio_claim_irq(sdiodev->func_msg, NULL);

		sdiodev->func->irq_handler = (sdio_irq_handler_t *)aicwf_sdio_hal_irqhandler;
		sdiodev->func_msg->irq_handler = (sdio_irq_handler_t *)aicwf_sdio_hal_irqhandler_func2;
		sdio_release_host(sdiodev->func);

		//enable sdio interrupt
		ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x07);

		if (ret != 0)
			sdio_err("intr register failed:%d\n", ret);

		//enable sdio interrupt
		ret = aicwf_sdio_writeb_func2(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x07);

		if (ret != 0)
			sdio_err("func2 intr register failed:%d\n", ret);
	}else if(sdiodev->chipid == PRODUCT_ID_AIC8800D80 || sdiodev->chipid == PRODUCT_ID_AIC8800D80X2){
		sdio_claim_host(sdiodev->func);
		sdio_claim_irq(sdiodev->func, aicwf_sdio_hal_irqhandler);

        sdio_f0_writeb(sdiodev->func, 0x07, 0x04, &ret);
        if (ret) {
            sdio_err("set func0 int en fail %d\n", ret);
        }
        sdio_release_host(sdiodev->func);
		//enable sdio interrupt
		ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x07);
		if (ret != 0)
			sdio_err("intr register failed:%d\n", ret);
	}

	bus_if->state = BUS_UP_ST;
	return ret;
}
#ifdef CONFIG_OOB
int rx_thread_wait_to = 1000;
module_param_named(rx_thread_wait_to, rx_thread_wait_to, int, 0644);

int aicwf_sdio_busirq_thread(void *data){
    struct aicwf_rx_priv *rx_priv = (struct aicwf_rx_priv *)data;
    struct aicwf_bus *bus_if = rx_priv->sdiodev->bus_if;

    while (1) {
#if 1
            if (kthread_should_stop()) {
                AICWFDBG(LOGERROR, "sdio busirq thread stop\n");
                break;
            }
#endif
			//if(!wait_for_completion_interruptible(&bus_if->busirq_trgg)){
            /* U30 Pro: an interruptible wait, the same for a kernel thread (no signals), but the idle thread no longer counts
             * towards the load average (two of these threads kept it near 2 on an idle device) */
            if (wait_for_completion_interruptible_timeout(&bus_if->busirq_trgg, msecs_to_jiffies(rx_thread_wait_to)) <= 0) {
                AICWFDBG(LOGRXPOLL, "%s wait for completion timout \r\n", __func__);
				if(bus_if->state == BUS_HOLD_ST){
					continue;
				}
            }
       
            if (bus_if->state == BUS_DOWN_ST){
                AICWFDBG(LOGERROR, "%s bus down thread exit \r\n", __func__);
                break;
            }
                //continue;
#if 0
#ifdef CONFIG_SDIO_PWRCTRL
            while(atomic_read(&bus_if->bus_priv.sdio->is_bus_suspend) == 1){
                AICWFDBG(LOGDEBUG, "%s waiting for sdio bus resume \r\n", __func__);
                msleep(100);
            }
            aicwf_sdio_pwr_stctl(bus_if->bus_priv.sdio, SDIO_ACTIVE_ST);
#endif//CONFIG_SDIO_PWRCTRL
#endif
#ifdef CONFIG_OOB_DEBUG
			printk("be irqh\n");
#endif
            aicwf_sdio_hal_irqhandler(bus_if->bus_priv.sdio->func);
        }
    
    //aic_thread_wait_stop();

    return 0;
}

#endif//CONFIG_OOB


int aicwf_sdio_bustx_thread(void *data)
{
	struct aicwf_bus *bus = (struct aicwf_bus *) data;
	struct aic_sdio_dev *sdiodev = bus->bus_priv.sdio;

	sched_set_fifo(current);

	while (1) {
		if (kthread_should_stop()) {
			sdio_err("sdio bustx thread stop\n");
			break;
		}
		if (!wait_for_completion_interruptible(&bus->bustx_trgg)) {
			if ((int)(atomic_read(&sdiodev->tx_priv->tx_pktcnt) > 0) || (sdiodev->tx_priv->cmd_txstate == true))
				aicwf_sdio_tx_process(sdiodev);
		}
	}

	return 0;
}

int aicwf_sdio_busrx_thread(void *data)
{
	struct aicwf_rx_priv *rx_priv = (struct aicwf_rx_priv *)data;
	struct aicwf_bus *bus_if = rx_priv->sdiodev->bus_if;

	while (1) {
		if (kthread_should_stop()) {
			sdio_err("sdio busrx thread stop\n");
			break;
		}
		if (!wait_for_completion_interruptible(&bus_if->busrx_trgg)) {
			aicwf_process_rxframes(rx_priv);
		}
	}

	return 0;
}

#if defined(CONFIG_SDIO_PWRCTRL)
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
static void aicwf_sdio_bus_pwrctl(struct timer_list *t)
{
	struct aic_sdio_dev *sdiodev = from_timer(sdiodev, t, timer);
#else
static void aicwf_sdio_bus_pwrctl(ulong data)
{
	struct aic_sdio_dev *sdiodev = (struct aic_sdio_dev *) data;
#endif
	if (sdiodev->bus_if->state == BUS_DOWN_ST) {
		sdio_err("bus down\n");
		return;
	}

	if (sdiodev->pwrctl_tsk) {
		complete(&sdiodev->pwrctrl_trgg);
	}
}
#endif

 static void aicwf_sdio_enq_rxpkt(struct aic_sdio_dev *sdiodev, struct sk_buff *pkt)
{
	struct aicwf_rx_priv *rx_priv = sdiodev->rx_priv;
	unsigned long flags = 0;

	spin_lock_irqsave(&rx_priv->rxqlock, flags);
	if (!aicwf_rxframe_enqueue(sdiodev->dev, &rx_priv->rxq, pkt)) {
		spin_unlock_irqrestore(&rx_priv->rxqlock, flags);
		aicwf_dev_skb_free(pkt);
		return;
	}
	spin_unlock_irqrestore(&rx_priv->rxqlock, flags);

	atomic_inc(&rx_priv->rx_cnt);
}

#define SDIO_OTHER_INTERRUPT (0x1ul << 7)

void aicwf_sdio_hal_irqhandler(struct sdio_func *func)
{
	struct aicwf_bus *bus_if = dev_get_drvdata(&func->dev);
	struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;
	u8 intstatus = 0;
	u8 byte_len = 0;
	struct sk_buff *pkt = NULL;
	int ret;
	int retry = 10;
	//static unsigned long cnt = 0;
   // printk("%s bsp enter \r\n", __func__);

#if 0
    if(aicbsp_sdiodev->sdio_hal_irqhandler){
        aicbsp_sdiodev->sdio_hal_irqhandler(func);
        return;
    }
#endif

	if (!bus_if || bus_if->state != BUS_UP_ST) {
		sdio_err("bus err\n");
		return;
	}

    if (sdiodev->chipid  == PRODUCT_ID_AIC8801 || sdiodev->chipid  == PRODUCT_ID_AIC8800DC ||
        sdiodev->chipid  == PRODUCT_ID_AIC8800DW) {
    	ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.block_cnt_reg, &intstatus);

    	while(intstatus){
    	    sdiodev->rx_priv->data_len = intstatus * SDIOWIFI_FUNC_BLOCKSIZE;
    	    if (intstatus > 0) {
    	        if(intstatus < 64) {
    	            pkt = aicwf_sdio_readframes(sdiodev, 0);
    	        } else {
    	            aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len);//byte_len must<= 128
    	            sdio_info("byte mode len=%d\r\n", byte_len);
    	            pkt = aicwf_sdio_readframes(sdiodev, 0);
    	        }
    	    } else {
    	#ifndef CONFIG_PLATFORM_ALLWINNER
    	        sdio_err("Interrupt but no data\n");
    	#endif
    	    }

    	    if (pkt)
    	        aicwf_sdio_enq_rxpkt(sdiodev, pkt);

    	    ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.block_cnt_reg, &intstatus);
    	}
    }else if (sdiodev->chipid  == PRODUCT_ID_AIC8800D80 || sdiodev->chipid  == PRODUCT_ID_AIC8800D80X2) {
        do {
            ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.misc_int_status_reg, &intstatus);
			//printk("r");
            if (!ret) {
                break;
            }
            sdio_err("ret=%d, intstatus=%x\r\n",ret, intstatus);
				if (retry-- <= 0)
					break;
        } while (1);
        if (intstatus & SDIO_OTHER_INTERRUPT) {
            u8 int_pending;
            ret = aicwf_sdio_readb(sdiodev, sdiodev->sdio_reg.sleep_reg, &int_pending);
            if (ret < 0) {
                sdio_err("reg:%d read failed!\n", sdiodev->sdio_reg.sleep_reg);
            }
            int_pending &= ~0x01; // dev to host soft irq
            ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.sleep_reg, int_pending);
            if (ret < 0) {
                sdio_err("reg:%d write failed!\n", sdiodev->sdio_reg.sleep_reg);
            }
        }

        if (intstatus > 0) {
            uint8_t intmaskf2 = intstatus | (0x1UL << 3);
            if (intmaskf2 > 120U) { // func2
                if (intmaskf2 == 127U) { // byte mode
                    //aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len, 1);//byte_len must<= 128
                    aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len);//byte_len must<= 128
                    sdio_info("byte mode len=%d\r\n", byte_len);
                    pkt = aicwf_sdio_readframes(sdiodev, 1);
                } else { // block mode
                    sdiodev->rx_priv->data_len = (intstatus & 0x7U) * SDIOWIFI_FUNC_BLOCKSIZE;
                    pkt = aicwf_sdio_readframes(sdiodev, 1);
                }
            } else { // func1
                if (intstatus == 120U) { // byte mode
                    //aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len, 0);//byte_len must<= 128
                    aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len);//byte_len must<= 128
                    sdio_info("byte mode len=%d\r\n", byte_len);
                    pkt = aicwf_sdio_readframes(sdiodev, 0);
                } else { // block mode
                    sdiodev->rx_priv->data_len = (intstatus & 0x7FU) * SDIOWIFI_FUNC_BLOCKSIZE;
                    pkt = aicwf_sdio_readframes(sdiodev, 0);
                }
    		}
    } else {
    	#ifndef CONFIG_PLATFORM_ALLWINNER
    //        sdio_err("Interrupt but no data\n");
    	#endif
        }

        if (pkt)
            aicwf_sdio_enq_rxpkt(sdiodev, pkt);
    }

	complete(&bus_if->busrx_trgg);
}

void aicwf_sdio_hal_irqhandler_func2(struct sdio_func *func)
{
    struct aicwf_bus *bus_if = dev_get_drvdata(&func->dev);
    struct aic_sdio_dev *sdiodev = bus_if->bus_priv.sdio;
    u8 intstatus = 0;
    u8 byte_len = 0;
    #ifdef CONFIG_PREALLOC_RX_SKB
	struct rx_buff *pkt = NULL;
	#else
	struct sk_buff *pkt = NULL;
	#endif
    int ret;

    if (!bus_if || bus_if->state != BUS_UP_ST) {
        sdio_err("bus err\n");
        return;
    }

	#ifdef CONFIG_PREALLOC_RX_SKB
	if (list_empty(&aic_rx_buff_list.rxbuff_list)) {
        printk("%s %d, rxbuff list is empty\n", __func__, __LINE__);
        return;
    }
	#endif

    ret = aicwf_sdio_readb_func2(sdiodev, sdiodev->sdio_reg.block_cnt_reg, &intstatus);

	while(intstatus) {
	    sdiodev->rx_priv->data_len = intstatus * SDIOWIFI_FUNC_BLOCKSIZE;
	    if (intstatus > 0) {
	        if(intstatus < 64) {
	            pkt = aicwf_sdio_readframes(sdiodev,1);
	        } else {
	            sdio_info("byte mode len=%d\r\n", byte_len);

	            aicwf_sdio_intr_get_len_bytemode(sdiodev, &byte_len);//byte_len must<= 128
	            pkt = aicwf_sdio_readframes(sdiodev,1);
	        }
	    } else {
		#ifndef CONFIG_PLATFORM_ALLWINNER
	        sdio_err("Interrupt but no data\n");
		#endif
	    }

	    if (pkt){
	        aicwf_sdio_enq_rxpkt(sdiodev, pkt);
	    }
		ret = aicwf_sdio_readb_func2(sdiodev, sdiodev->sdio_reg.block_cnt_reg, &intstatus);
	}

	complete(&bus_if->busrx_trgg);
}

#if defined(CONFIG_SDIO_PWRCTRL)
void aicwf_sdio_pwrctl_timer(struct aic_sdio_dev *sdiodev, uint duration)
{
	uint timeout;

	if (sdiodev->bus_if->state != BUS_UP_ST && duration)
		return;

	spin_lock_bh(&sdiodev->pwrctl_lock);
	if (!duration) {
		if (timer_pending(&sdiodev->timer))
			del_timer_sync(&sdiodev->timer);
	} else {
		sdiodev->active_duration = duration;
		timeout = msecs_to_jiffies(sdiodev->active_duration);
		mod_timer(&sdiodev->timer, jiffies + timeout);
	}
	spin_unlock_bh(&sdiodev->pwrctl_lock);
}
#endif

static struct aicwf_bus_ops aicwf_sdio_bus_ops = {
	.stop = aicwf_sdio_bus_stop,
	.start = aicwf_sdio_bus_start,
	.txdata = aicwf_sdio_bus_txdata,
	.txmsg = aicwf_sdio_bus_txmsg,
};

void aicwf_sdio_release_func2(struct aic_sdio_dev *sdiodev)
{
	int ret = 0;
	sdio_dbg("%s\n", __func__);
	sdio_claim_host(sdiodev->func_msg);
	//disable sdio interrupt
	ret = aicwf_sdio_writeb_func2(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x0);
	if (ret < 0) {
		sdio_err("reg:%d write failed!\n", sdiodev->sdio_reg.intr_config_reg);
	}
	sdio_release_irq(sdiodev->func_msg);
	sdio_release_host(sdiodev->func_msg);

}

void aicwf_sdio_release(struct aic_sdio_dev *sdiodev)
{
	struct aicwf_bus *bus_if = NULL;
	struct aicwf_bus *bus_if_t = NULL;
	int ret = 0;

	sdio_dbg("%s\n", __func__);
	if (sdiodev->func == NULL) {
		printk("%s, NULL sdio func\n", __func__);
		return;
	}

	bus_if = aicbsp_get_drvdata(sdiodev->dev);
	if (bus_if)
		bus_if->state = BUS_DOWN_ST;

	bus_if_t = dev_get_drvdata(sdiodev->dev);

	if ((bus_if_t != NULL) && (sdiodev->bus_if == bus_if_t)) {
		sdio_dbg("%s bsp release\n", __func__);
		sdio_claim_host(sdiodev->func);
		//disable sdio interrupt
		ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x0);
		if (ret < 0) {
			sdio_err("reg:%d write failed!, ret=%d\n", sdiodev->sdio_reg.intr_config_reg, ret);
		}
		sdio_release_irq(sdiodev->func);
		sdio_release_host(sdiodev->func);
	}

	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		aicwf_sdio_release_func2(sdiodev);
	}

	if (sdiodev->dev)
		aicwf_bus_deinit(sdiodev->dev);

	if (sdiodev->tx_priv)
		aicwf_tx_deinit(sdiodev->tx_priv);

	if (sdiodev->rx_priv)
		aicwf_rx_deinit(sdiodev->rx_priv);

	rwnx_cmd_mgr_deinit(&sdiodev->cmd_mgr);
}



void aicwf_sdio_reg_init(struct aic_sdio_dev *sdiodev)
{
    sdio_dbg("%s\n", __func__);
//	start_bsp = 1;

    if(sdiodev->chipid == PRODUCT_ID_AIC8801 || sdiodev->chipid == PRODUCT_ID_AIC8800DC ||
       sdiodev->chipid == PRODUCT_ID_AIC8800DW){
        sdiodev->sdio_reg.bytemode_len_reg =       SDIOWIFI_BYTEMODE_LEN_REG;
        sdiodev->sdio_reg.intr_config_reg =        SDIOWIFI_INTR_CONFIG_REG;
        sdiodev->sdio_reg.sleep_reg =              SDIOWIFI_SLEEP_REG;
        sdiodev->sdio_reg.wakeup_reg =             SDIOWIFI_WAKEUP_REG;
        sdiodev->sdio_reg.flow_ctrl_reg =          SDIOWIFI_FLOW_CTRL_REG;
        sdiodev->sdio_reg.register_block =         SDIOWIFI_REGISTER_BLOCK;
        sdiodev->sdio_reg.bytemode_enable_reg =    SDIOWIFI_BYTEMODE_ENABLE_REG;
        sdiodev->sdio_reg.block_cnt_reg =          SDIOWIFI_BLOCK_CNT_REG;
        sdiodev->sdio_reg.rd_fifo_addr =           SDIOWIFI_RD_FIFO_ADDR;
        sdiodev->sdio_reg.wr_fifo_addr =           SDIOWIFI_WR_FIFO_ADDR;
	} else if (sdiodev->chipid == PRODUCT_ID_AIC8800D80 || sdiodev->chipid == PRODUCT_ID_AIC8800D80X2){
        sdiodev->sdio_reg.bytemode_len_reg =       SDIOWIFI_BYTEMODE_LEN_REG_V3;
        sdiodev->sdio_reg.intr_config_reg =        SDIOWIFI_INTR_ENABLE_REG_V3;
        sdiodev->sdio_reg.sleep_reg =              SDIOWIFI_INTR_PENDING_REG_V3;
        sdiodev->sdio_reg.wakeup_reg =             SDIOWIFI_INTR_TO_DEVICE_REG_V3;
        sdiodev->sdio_reg.flow_ctrl_reg =          SDIOWIFI_FLOW_CTRL_Q1_REG_V3;
        sdiodev->sdio_reg.bytemode_enable_reg =    SDIOWIFI_BYTEMODE_ENABLE_REG_V3;
        sdiodev->sdio_reg.misc_int_status_reg =    SDIOWIFI_MISC_INT_STATUS_REG_V3;
        sdiodev->sdio_reg.rd_fifo_addr =           SDIOWIFI_RD_FIFO_ADDR_V3;
        sdiodev->sdio_reg.wr_fifo_addr =           SDIOWIFI_WR_FIFO_ADDR_V3;
    }
}

int aicwf_sdio_func_init(struct aic_sdio_dev *sdiodev)
{
	struct mmc_host *host;
	u8 block_bit0 = 0x1;
	u8 byte_mode_disable = 0x1;//1: no byte mode
	int ret = 0;
	struct aicbsp_feature_t feature;

	aicbsp_get_feature(&feature, NULL);
    aicwf_sdio_reg_init(sdiodev);

	host = sdiodev->func->card->host;

	sdio_claim_host(sdiodev->func);
#if 0//SDIO PHASE SETTING
	sdiodev->func->card->quirks |= MMC_QUIRK_LENIENT_FN0;
	sdio_f0_writeb(sdiodev->func, feature.sdio_phase, 0x13, &ret);
	if (ret < 0) {
		sdio_err("write func0 fail %d\n", ret);
        sdio_release_host(sdiodev->func);
		return ret;
	}
#endif

	ret = sdio_set_block_size(sdiodev->func, SDIOWIFI_FUNC_BLOCKSIZE);
	if (ret < 0) {
		sdio_err("set blocksize fail %d\n", ret);
		sdio_release_host(sdiodev->func);
		return ret;
	}
	ret = sdio_enable_func(sdiodev->func);
	if (ret < 0) {
        sdio_err("enable func fail %d.\n", ret);
		sdio_release_host(sdiodev->func);
		return ret;
	}
    udelay(100);
#if 1//SDIO CLOCK SETTING
	if (feature.sdio_clock > 0) {
		host->ios.clock = feature.sdio_clock;
		host->ops->set_ios(host, &host->ios);
		sdio_dbg("Set SDIO Clock %d MHz\n", host->ios.clock/1000000);
	}
#endif
	sdio_release_host(sdiodev->func);

	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){

	    sdio_claim_host(sdiodev->func_msg);

		//set sdio blocksize
	    ret = sdio_set_block_size(sdiodev->func_msg, SDIOWIFI_FUNC_BLOCKSIZE);
	    if (ret < 0) {
	        AICWFDBG(LOGERROR, "set func2 blocksize fail %d\n", ret);
	        sdio_release_host(sdiodev->func_msg);
	        return ret;
	    }

		//set sdio enable func
	    ret = sdio_enable_func(sdiodev->func_msg);
	    if (ret < 0) {
	        AICWFDBG(LOGERROR, "enable func2 fail %d.\n", ret);
	    }

	    sdio_release_host(sdiodev->func_msg);

	    ret = aicwf_sdio_writeb_func2(sdiodev, sdiodev->sdio_reg.register_block, block_bit0);
	    if (ret < 0) {
	        AICWFDBG(LOGERROR, "reg:%d write failed!\n", sdiodev->sdio_reg.register_block);
	        return ret;
	    }

	    //1: no byte mode
	    ret = aicwf_sdio_writeb_func2(sdiodev, sdiodev->sdio_reg.bytemode_enable_reg, byte_mode_disable);
	    if (ret < 0) {
	        AICWFDBG(LOGERROR, "reg:%d write failed!\n", sdiodev->sdio_reg.bytemode_enable_reg);
	        return ret;
	    }
	}

	ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.register_block, block_bit0);
	if (ret < 0) {
		sdio_err("reg:%d write failed!\n", sdiodev->sdio_reg.register_block);
		return ret;
	}

	//1: no byte mode
	ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.bytemode_enable_reg, byte_mode_disable);
	if (ret < 0) {
		sdio_err("reg:%d write failed!\n", sdiodev->sdio_reg.bytemode_enable_reg);
		return ret;
	}

	return ret;
}

int aicwf_sdiov3_func_init(struct aic_sdio_dev *sdiodev)
{
	struct mmc_host *host;
	u8 byte_mode_disable = 0x1;//1: no byte mode
	int ret = 0;
    //u8 val;
	struct aicbsp_feature_t feature;

	aicbsp_get_feature(&feature, NULL);
    aicwf_sdio_reg_init(sdiodev);

	host = sdiodev->func->card->host;

	sdio_claim_host(sdiodev->func);
	sdiodev->func->card->quirks |= MMC_QUIRK_LENIENT_FN0;

	ret = sdio_set_block_size(sdiodev->func, SDIOWIFI_FUNC_BLOCKSIZE);
	if (ret < 0) {
		sdio_err("set blocksize fail %d\n", ret);
		sdio_release_host(sdiodev->func);
		return ret;
	}
	ret = sdio_enable_func(sdiodev->func);
	if (ret < 0) {
        sdio_err("enable func fail %d.\n", ret);
		sdio_release_host(sdiodev->func);
		return ret;
	}

    sdio_f0_writeb(sdiodev->func, 0x7F, 0xF2, &ret);
    if (ret) {
        sdio_err("set fn0 0xF2 fail %d\n", ret);
        sdio_release_host(sdiodev->func);
        return ret;
    }
#if 0
    if (host->ios.timing == MMC_TIMING_UHS_DDR50) {
        val = 0x21;//0x1D;//0x5;
    } else {
        val = 0x01;//0x19;//0x1;
    }
    val |= SDIOCLK_FREE_RUNNING_BIT;
    sdio_f0_writeb(sdiodev->func, val, 0xF0, &ret);
    if (ret) {
        sdio_err("set iopad ctrl fail %d\n", ret);
        sdio_release_host(sdiodev->func);
        return ret;
    }
    sdio_f0_writeb(sdiodev->func, 0x0, 0xF8, &ret);
    if (ret) {
        sdio_err("set iopad delay2 fail %d\n", ret);
        sdio_release_host(sdiodev->func);
        return ret;
    }
    sdio_f0_writeb(sdiodev->func, 0x40, 0xF1, &ret);
    if (ret) {
        sdio_err("set iopad delay1 fail %d\n", ret);
        sdio_release_host(sdiodev->func);
        return ret;
    }
    msleep(1);
	
#if 1//SDIO CLOCK SETTING
	/* U30 Pro: not in the UHS-I modes, where the core picked the clock and tuned the bus for it (sdhci_sprd.sdio_mode) */
	if ((feature.sdio_clock > 0) && (host->ios.timing < MMC_TIMING_UHS_SDR50)) {
		host->ios.clock = feature.sdio_clock;
		host->ops->set_ios(host, &host->ios);
		sdio_dbg("Set SDIO Clock %d MHz\n", host->ios.clock/1000000);
	}

#endif
#endif

	sdio_release_host(sdiodev->func);

	//1: no byte mode
	ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.bytemode_enable_reg, byte_mode_disable);
	if (ret < 0) {
		sdio_err("reg:%d write failed!\n", sdiodev->sdio_reg.bytemode_enable_reg);
		return ret;
	}

	return ret;
}

void aicwf_sdio_func_deinit(struct aic_sdio_dev *sdiodev)
{
	struct aicwf_bus *bus_if = NULL;

	if (sdiodev->func == NULL) {
		sdio_err("%s, NULL sdio func\n", __func__);
		return;
	}

	bus_if = dev_get_drvdata(sdiodev->dev);
	if (bus_if == NULL) {
		sdio_err("%s, bus_if is null\n", __func__);
		return;
	}

	if (sdiodev->bus_if == bus_if) {
		sdio_dbg("%s bsp disable\n", __func__);
		sdio_claim_host(sdiodev->func);
		sdio_disable_func(sdiodev->func);
		sdio_release_host(sdiodev->func);
	}

	if(sdiodev->chipid == PRODUCT_ID_AIC8800DC || sdiodev->chipid == PRODUCT_ID_AIC8800DW){
		sdio_claim_host(sdiodev->func_msg);
		sdio_disable_func(sdiodev->func_msg);
		sdio_release_host(sdiodev->func_msg);
	}

}


void *aicwf_sdio_bus_init(struct aic_sdio_dev *sdiodev)
{
	int ret;
	struct aicwf_bus *bus_if;
	struct aicwf_rx_priv *rx_priv;
	struct aicwf_tx_priv *tx_priv;

#if defined(CONFIG_SDIO_PWRCTRL)
	spin_lock_init(&sdiodev->pwrctl_lock);
	sema_init(&sdiodev->pwrctl_wakeup_sema, 1);
#endif

	bus_if = sdiodev->bus_if;
	bus_if->dev = sdiodev->dev;
	bus_if->ops = &aicwf_sdio_bus_ops;
	bus_if->state = BUS_DOWN_ST;
#if defined(CONFIG_SDIO_PWRCTRL)
	sdiodev->state = SDIO_SLEEP_ST;
	sdiodev->active_duration = SDIOWIFI_PWR_CTRL_INTERVAL;
#else
	sdiodev->state = SDIO_ACTIVE_ST;
#endif

	rx_priv = aicwf_rx_init(sdiodev);
	if (!rx_priv) {
		sdio_err("rx init fail\n");
		goto fail;
	}
	sdiodev->rx_priv = rx_priv;

	tx_priv = aicwf_tx_init(sdiodev);
	if (!tx_priv) {
		sdio_err("tx init fail\n");
		goto fail;
	}
	sdiodev->tx_priv = tx_priv;
	aicwf_frame_queue_init(&tx_priv->txq, 8, TXQLEN);
	spin_lock_init(&tx_priv->txqlock);
	sema_init(&tx_priv->txctl_sema, 1);
	sema_init(&tx_priv->cmd_txsema, 1);
	init_waitqueue_head(&tx_priv->cmd_txdone_wait);
	atomic_set(&tx_priv->tx_pktcnt, 0);

#if defined(CONFIG_SDIO_PWRCTRL)
#if LINUX_VERSION_CODE >= KERNEL_VERSION(4, 14, 0)
	timer_setup(&sdiodev->timer, aicwf_sdio_bus_pwrctl, 0);
#else
	init_timer(&sdiodev->timer);
	sdiodev->timer.data = (ulong) sdiodev;
	sdiodev->timer.function = aicwf_sdio_bus_pwrctl;
#endif
	init_completion(&sdiodev->pwrctrl_trgg);
#endif
	ret = aicwf_bus_init(0, sdiodev->dev);
	if (ret < 0) {
		sdio_err("bus init fail\n");
		goto fail;
	}

	ret  = aicwf_bus_start(bus_if);
	if (ret != 0) {
		sdio_err("bus start fail\n");
		goto fail;
	}

	return sdiodev;

fail:
	aicwf_sdio_release(sdiodev);
	return NULL;
}

void get_fw_path(char* fw_path){
	if (strlen(aic_fw_path) > 0) {
		memcpy(fw_path, aic_fw_path, strlen(aic_fw_path));
	}else{
		memcpy(fw_path, aic_default_fw_path, strlen(aic_default_fw_path));
	}
}

int get_testmode(void){
	return testmode;
}

struct sdio_func *get_sdio_func(void){
    return aicbsp_sdiodev->func;
}

void set_irq_handler(void *fn){
    aicbsp_sdiodev->sdio_hal_irqhandler = (sdio_irq_handler_t *)fn;
}

uint8_t crc8_ponl_107(uint8_t *p_buffer, uint16_t cal_size)
{
    uint8_t i;
    uint8_t crc = 0;
    if (cal_size==0) {
        return crc;
    }
    while (cal_size--) {
        for (i = 0x80; i > 0; i /= 2) {
            if (crc & 0x80)  {
                crc *= 2;
                crc ^= 0x07; //polynomial X8 + X2 + X + 1,(0x107)
            } else {
                crc *= 2;
            }
            if ((*p_buffer) & i) {
                crc ^= 0x07;
            }
        }
        p_buffer++;
    }
    return crc;
}

#ifdef CONFIG_AIC_POWER_RESET
#define MMC_GO_IDLE_STATE 0
#define SD_SEND_IF_COND 8
#define SD_IO_SEND_OP_COND 5
#define SD_SEND_RELATIVE_ADDR 3
#define MMC_SELECT_CARD 7

#define MMC_CARD_BUSY 0X80000000
#define MMC_CMD_RETRIES 3
static inline void mmc_delay(unsigned int ms)
{
    if (ms <= 20)
            usleep_range(ms * 1000, ms * 1250);
    else
            msleep(ms);
}

#if 0
static int mmc_go_idle(struct mmc_host *host)
{
        int err;
        struct mmc_command cmd = {};

        /*
         * Non-SPI hosts need to prevent chipselect going active during
         * GO_IDLE; that would put chips into SPI mode.  Remind them of
         * that in case of hardware that won't pull up DAT3/nCS otherwise.
         *
         * SPI hosts ignore ios.chip_select; it's managed according to
         * rules that must accommodate non-MMC slaves which this layer
         * won't even know about.
         */
#if 0
        if (!mmc_host_is_spi(host)) {
                mmc_set_chip_select(host, MMC_CS_HIGH);
                mmc_delay(1);
        }
#endif
        cmd.opcode = MMC_GO_IDLE_STATE;
        cmd.arg = 0;
        cmd.flags = MMC_RSP_SPI_R1 | MMC_RSP_NONE | MMC_CMD_BC;

        err = mmc_wait_for_cmd(host, &cmd, 0);
        mmc_delay(1);

#if 0
        if (!mmc_host_is_spi(host)) {
                mmc_set_chip_select(host, MMC_CS_DONTCARE);
                mmc_delay(1);
        }
#endif
        host->use_spi_crc = 0;

        return err;
}
#endif

static int __mmc_send_if_cond(struct mmc_host *host, u32 ocr, u8 pcie_bits,
                              u32 *resp)
{
        struct mmc_command cmd = {};
        int err;
        static const u8 test_pattern = 0xAA;
        u8 result_pattern;

        /*
         * To support SD 2.0 cards, we must always invoke SD_SEND_IF_COND
         * before SD_APP_OP_COND. This command will harmlessly fail for
         * SD 1.0 cards.
         */
        cmd.opcode = SD_SEND_IF_COND;
        cmd.arg = ((ocr & 0xFF8000) != 0) << 8 | pcie_bits << 8 | test_pattern;
        cmd.flags = MMC_RSP_SPI_R7 | MMC_RSP_R7 | MMC_CMD_BCR;

        err = mmc_wait_for_cmd(host, &cmd, 0);
        if (err)
                return err;

        //if (mmc_host_is_spi(host))
         //       result_pattern = cmd.resp[1] & 0xFF;
       // else
        result_pattern = cmd.resp[0] & 0xFF;

        if (result_pattern != test_pattern)
                return -EIO;

        if (resp)
                *resp = cmd.resp[0];

        return 0;
}

int mmc_send_if_cond(struct mmc_host *host, u32 ocr)
{
        return __mmc_send_if_cond(host, ocr, 0, NULL);
}

static int mmc_send_io_op_cond(struct mmc_host *host, u32 ocr, u32 *rocr)
{
        struct mmc_command cmd = {};
        int i, err = 0;

        cmd.opcode = SD_IO_SEND_OP_COND;
        cmd.arg = ocr;
        cmd.flags = MMC_RSP_SPI_R4 | MMC_RSP_R4 | MMC_CMD_BCR;

        for (i = 100; i; i--) {
                err = mmc_wait_for_cmd(host, &cmd, MMC_CMD_RETRIES);
                if (err)
                        break;

                /* if we're just probing, do a single pass */
                if (ocr == 0)
                        break;

                /* otherwise wait until reset completes */
                if (mmc_host_is_spi(host)) {
                        /*
                         * Both R1_SPI_IDLE and MMC_CARD_BUSY indicate
                         * an initialized card under SPI, but some cards
                         * (Marvell's) only behave when looking at this
                         * one.
                         */
                        if (cmd.resp[1] & MMC_CARD_BUSY)
                                break;
                } else {
                        if (cmd.resp[0] & MMC_CARD_BUSY)
                                break;
                }

                err = -ETIMEDOUT;

                mmc_delay(10);
        }

        if (rocr)
                *rocr = cmd.resp[mmc_host_is_spi(host) ? 1 : 0];

        return err;
}

int mmc_send_relative_addr(struct mmc_host *host, unsigned int *rca)
{
        int err;
        struct mmc_command cmd = {};

        cmd.opcode = SD_SEND_RELATIVE_ADDR;
        cmd.arg = 0;
        cmd.flags = MMC_RSP_R6 | MMC_CMD_BCR;

        err = mmc_wait_for_cmd(host, &cmd, MMC_CMD_RETRIES);
        if (err)
                return err;

        *rca = cmd.resp[0] >> 16;

        return 0;
}

static int _mmc_select_card(struct mmc_host *host, struct mmc_card *card)
{
        struct mmc_command cmd = {};

        cmd.opcode = MMC_SELECT_CARD;

        if (card) {
                cmd.arg = card->rca << 16;
                cmd.flags = MMC_RSP_R1 | MMC_CMD_AC;
        } else {
                cmd.arg = 0;
                cmd.flags = MMC_RSP_NONE | MMC_CMD_AC;
        }

        return mmc_wait_for_cmd(host, &cmd, MMC_CMD_RETRIES);
}

int mmc_select_card(struct mmc_card *card)
{

        return _mmc_select_card(card->host, card);
}

#define SD_IO_RW_DIRECT 52
static int mmc_io_rw_direct_host(struct mmc_host *host, int write, unsigned fn,
        unsigned addr, u8 in, u8 *out)
{
        struct mmc_command cmd = {};
        int err;

        if (fn > 7)
                return -EINVAL;

        /* sanity check */
        if (addr & ~0x1FFFF)
                return -EINVAL;

        cmd.opcode = SD_IO_RW_DIRECT;
        cmd.arg = write ? 0x80000000 : 0x00000000;
        cmd.arg |= fn << 28; 
        cmd.arg |= (write && out) ? 0x08000000 : 0x00000000;
        cmd.arg |= addr << 9;
        cmd.arg |= in;
        cmd.flags = MMC_RSP_SPI_R5 | MMC_RSP_R5 | MMC_CMD_AC;

        err = mmc_wait_for_cmd(host, &cmd, 0);
        if (err)
                return err;

        if (mmc_host_is_spi(host)) {
                /* host driver already reported errors */
        } else {
                if (cmd.resp[0] & R5_ERROR)
                        return -EIO;
                if (cmd.resp[0] & R5_FUNCTION_NUMBER)
                        return -EINVAL;
                if (cmd.resp[0] & R5_OUT_OF_RANGE)
                        return -ERANGE;
        }

        if (out) { 
                if (mmc_host_is_spi(host))
                        *out = (cmd.resp[0] >> 8) & 0xFF;
                else
                        *out = cmd.resp[0] & 0xFF;
        }

        return 0;
}

int mmc_io_rw_direct(struct mmc_card *card, int write, unsigned fn,
        unsigned addr, u8 in, u8 *out)
{
        return mmc_io_rw_direct_host(card->host, write, fn, addr, in, out);
}
		
#if 0
#define MMC_CAP_4_BIT_DATA (1 << 0)
#define SDIO_CCCR_IF  0X7
#define SDIO_BUS_WIDTH_MASK 0x03
#define SDIO_BUS_WIDTH_4BIT 0x2
#endif
static int sdio_enable_wide(struct mmc_card *card)
{
        int ret;
        u8 ctrl;

        if (!(card->host->caps & MMC_CAP_4_BIT_DATA))
                return 0;

        if (card->cccr.low_speed && !card->cccr.wide_bus)
                return 0;

        ret = mmc_io_rw_direct(card, 0, 0, SDIO_CCCR_IF, 0, &ctrl);
        if (ret)
                return ret;

        if ((ctrl & SDIO_BUS_WIDTH_MASK) == SDIO_BUS_WIDTH_RESERVED)
                pr_warn("%s: SDIO_CCCR_IF is invalid: 0x%02x\n",
                        mmc_hostname(card->host), ctrl);

        /* set as 4-bit bus width */
        ctrl &= ~SDIO_BUS_WIDTH_MASK;
        ctrl |= SDIO_BUS_WIDTH_4BIT;

        ret = mmc_io_rw_direct(card, 1, 0, SDIO_CCCR_IF, ctrl, NULL);
        if (ret)
                return ret;

        return 1;
}

#define MMC_SEND_TUNING_BLOCK    19   /* adtc                    R1  */
#define MMC_SEND_TUNING_BLOCK_HS200	21	/* adtc R1  */
static inline void mmc_retune_clear(struct mmc_host *host)
{
	host->retune_now = 0;
	host->need_retune = 0;
}
static void mmc_retune_enable(struct mmc_host *host)
{
	host->can_retune = 1;
	if (host->retune_period)
		mod_timer(&host->retune_timer,
			  jiffies + host->retune_period * HZ);
}

static int mmc_execute_tuning(struct mmc_card *card)
{
	struct mmc_host *host = card->host;
	u32 opcode;
	int err;

	if (!host->ops->execute_tuning)
		return 0;

	if (host->cqe_on)
		host->cqe_ops->cqe_off(host);

	if (mmc_card_mmc(card))
		opcode = MMC_SEND_TUNING_BLOCK_HS200;
	else
		opcode = MMC_SEND_TUNING_BLOCK;

	err = host->ops->execute_tuning(host, opcode);
	if (!err) {
		mmc_retune_clear(host);
		mmc_retune_enable(host);
		return 0;
	}

	/* Only print error when we don't check for card removal */
	if (!host->detect_change)
		pr_err("%s: tuning execution failed: %d\n",
			mmc_hostname(host), err);

	return err;
}
static void do_bsp_sdio_recovery(void)
{
	printk("%s, enter1\n", __func__);
	u32 ocr;
	struct aic_sdio_dev *sdiodev = aicbsp_sdiodev;
	struct mmc_host *host = sdiodev->func->card->host;
	int ret;
	int err;

	printk("%s, enter2\n", __func__);
	sdio_claim_host(sdiodev->func);
#if 0
	//disable sdio interrupt
	ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x0);
	if (ret < 0) {
		printk("reg:%d write failed!\n", sdiodev->sdio_reg.intr_config_reg);
	}
#endif
	sdio_release_irq(sdiodev->func);

	//    mmc_go_idle(host);
	//    mmc_send_if_cond(host, host->ocr_avail);
	mmc_send_io_op_cond(host, 0, &ocr);

	ret = mmc_send_relative_addr(host, &sdiodev->func->card->rca);
	if(ret) {
	    printk("read rca fail\n");
	}

	ret = mmc_select_card(sdiodev->func->card);
	if(ret) {
	        printk("select card fail\n");
	    }

	sdio_enable_wide(sdiodev->func->card);

	ret = mmc_execute_tuning(sdiodev->func->card);
	if (ret) {
		printk("mmc_execute_tuning fail!\n");
	} else {
		printk("mmc_execute_tuning success!\n");
	}

	sdio_release_host(sdiodev->func);

	dev_set_drvdata(&sdiodev->func->dev, sdiodev->bus_if);

	aicwf_sdiov3_func_init(sdiodev);

	sdio_claim_host(sdiodev->func);
	sdio_claim_irq(sdiodev->func, aicwf_sdio_hal_irqhandler);

	sdio_f0_writeb(sdiodev->func, 0x07, 0x04, &ret);
	if (ret) {
	    printk("set func0 int en fail %d\n", ret);
	}
	sdio_release_host(sdiodev->func);
	//enable sdio interrupt
	ret = aicwf_sdio_writeb(sdiodev, sdiodev->sdio_reg.intr_config_reg, 0x07);
	if (ret != 0)
		printk("intr register failed:%d\n", ret);

	err = rwnx_register_hostwake_irq(sdiodev->dev);

	if(err != 0)
	printk("do_bsp_sdio_recovery, bus_if:%p\n", sdiodev->bus_if);
	
	sdiodev->bus_if->state = BUS_UP_ST;

	printk("%s, end\n", __func__);
}
#endif

EXPORT_SYMBOL(get_fw_path);
EXPORT_SYMBOL(get_testmode);
EXPORT_SYMBOL(get_sdio_func);
EXPORT_SYMBOL(set_irq_handler);

