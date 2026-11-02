#include "aic8800d80_compat.h"
#include "aic_bsp_driver.h"
#include <linux/mmc/sdio.h>
#include <linux/mmc/sdio_ids.h>
#include <linux/mmc/sdio_func.h>
#include <linux/mmc/card.h>
#include <linux/mmc/host.h>


extern struct aicbsp_info_t aicbsp_info;
extern int adap_test;

typedef u32 (*array2_tbl_t)[2];

#define AIC_PATCH_MAGIG_NUM     0x48435450 // "PTCH"
#define AIC_PATCH_MAGIG_NUM_2   0x50544348 // "HCTP"
#define AIC_PATCH_BLOCK_MAX     4

typedef struct {
    uint32_t magic_num;
    uint32_t pair_start;
    uint32_t magic_num_2;
    uint32_t pair_count;
    uint32_t block_dst[AIC_PATCH_BLOCK_MAX];
    uint32_t block_src[AIC_PATCH_BLOCK_MAX];
    uint32_t block_size[AIC_PATCH_BLOCK_MAX]; // word count
} aic_patch_t;

#define AIC_PATCH_OFST(mem) ((size_t) &((aic_patch_t *)0)->mem)
#define AIC_PATCH_ADDR(mem) ((u32)(aic_patch_str_base + AIC_PATCH_OFST(mem)))

u32 aicbsp_syscfg_tbl_8800d80[][2] = {
};

int aicbsp_system_config_8800d80(struct aic_sdio_dev *sdiodev)
{
	int syscfg_num = sizeof(aicbsp_syscfg_tbl_8800d80) / sizeof(u32) / 2;
	int ret, cnt;
	for (cnt = 0; cnt < syscfg_num; cnt++) {
		ret = rwnx_send_dbg_mem_write_req(sdiodev, aicbsp_syscfg_tbl_8800d80[cnt][0], aicbsp_syscfg_tbl_8800d80[cnt][1]);
		if (ret) {
			printk("%x write fail: %d\n", aicbsp_syscfg_tbl_8800d80[cnt][0], ret);
			return ret;
		}
	}
	return 0;
}


u32 adaptivity_patch_tbl_8800d80[][2] = {
	{0x000C, 0x0000320A}, //linkloss_thd
	{0x009C, 0x00000000}, //ac_param_conf
	{0x0168, 0x00010000}, //tx_adaptivity_en
};

#define USER_PWROFST_COVER_CALIB_FLAG	0x01U
#define USER_CHAN_MAX_TXPWR_EN_FLAG     (0x01U << 1)
#define USER_TX_USE_ANA_F_FLAG          (0x01U << 2)
#define USER_APM_PRBRSP_OFFLOAD_DISABLE_FLAG	(0x01U << 3)
#define USER_HE_MU_EDCA_UPDATE_DISABLE_FLAG     (0x01U << 4)
// bit5~9 reserved
#define USER_RF_WITH_SAW_EN_FLAG        (0x01U << 10)


#define CFG_PWROFST_COVER_CALIB     1
#define CFG_USER_CHAN_MAX_TXPWR_EN  1
#define CFG_USER_TX_USE_ANA_F       0
#define CFG_USER_APM_PRBRSP_OFFLOAD_DISABLE	0


#define CFG_USER_EXT_FLAGS_EN   (CFG_PWROFST_COVER_CALIB || CFG_USER_CHAN_MAX_TXPWR_EN || CFG_USER_TX_USE_ANA_F || CFG_USER_APM_PRBRSP_OFFLOAD_DISABLE)

u32 patch_tbl_8800d80[][2] = {
	#ifdef USE_5G
	{0x00b4, 0xf3010001},
	#else
	{0x00b4, 0xf3010000},
	#endif
#if defined(CONFIG_AMSDU_RX)
        {0x170, 0x0100000a},
#endif
#ifdef CONFIG_IRQ_FALL
	{0x00000170, 0x0000010a}, //irqf
#endif

	#if CFG_USER_EXT_FLAGS_EN
	{0x0188, 0x00000000
	#if CFG_PWROFST_COVER_CALIB
		| USER_PWROFST_COVER_CALIB_FLAG
	#endif
	#if CFG_USER_CHAN_MAX_TXPWR_EN
		| USER_CHAN_MAX_TXPWR_EN_FLAG
	#endif
	#if CFG_USER_TX_USE_ANA_F
		| USER_TX_USE_ANA_F_FLAG
	#endif
	#if CFG_USER_APM_PRBRSP_OFFLOAD_DISABLE
		| USER_APM_PRBRSP_OFFLOAD_DISABLE_FLAG
	#endif
	}, // user_ext_flags
	#endif

#ifdef CONFIG_RADAR_OR_IR_DETECT
	{0x019c, 0x00000100}, //enable radar detect
#endif
};

u32 adaptivity_patch_tbl_8800d80x2[][2] = {
    {0x000C, 0x0000320A}, //linkloss_thd
    {0x009C, 0x00000000}, //ac_param_conf
    {0x01D0, 0x00010000}, //tx_adaptivity_en
};


u32 patch_tbl_8800d80x2[][2] = {
    {0x01f0, 0x00000001
        #if CFG_USER_CHAN_MAX_TXPWR_EN
        | USER_CHAN_MAX_TXPWR_EN_FLAG
        #endif
        #if CFG_USER_TX_USE_ANA_F
        | USER_TX_USE_ANA_F_FLAG
        #endif
        #if CFG_USER_APM_PRBRSP_OFFLOAD_DISABLE
        | USER_APM_PRBRSP_OFFLOAD_DISABLE_FLAG
        #endif
		#ifdef CONFIG_RF_WHTH_SAW
		| USER_RF_WITH_SAW_EN_FLAG
		#endif		
    }, // user_ext_flags
#ifdef CONFIG_LOWPOWER
#ifdef CONFIG_DELAYED_BA
    { 0x22c, 0x08010101}, //08-24Mbps 06-12Mbps 04-6Mbps 00-1Mbps
#else
    { 0x22c, 0x00000101},
#endif
#else
    { 0x22c, 0},
#endif
    #ifdef CONFIG_FLASH_CALRES
    {0x0234, 0x0000004F}, // cal_res_stored_in_flash_flags
    #endif

	#ifdef CONFIG_RADAR_OR_IR_DETECT
    {0x0204, 0x01010100},//radar
	#endif
	#ifdef CONFIG_GPIO_SET
	{0x238, 0x0001000F}
	#endif
};


#ifdef CONFIG_OOB
// for 8800d40/d80     map data1 isr to gpiob1
u32 gpio_cfg_tbl_8800d40d80[][2] = {
    {0x40504084, 0x00000006},
    {0x40500040, 0x00000000},
    {0x40100030, 0x00000001},
    {0x40241020, 0x00000001},
    {0x40240030, 0x00000004},
    {0x40240020, 0x03020700},
};
#endif

#ifdef CONFIG_OOB
u32 gpio_cfg_tbl_8800d80x2[][2] = {
    {0x40500040, 0x00000000},
    {0x40100030, 0x00000001},
    {0x40241038, 0x00000001},
    {0x40240030, 0x00000400},
    {0x40240028, 0x0b0a0f08},
	{0x40504044, 0x00000007},
};
#endif


int aicwifi_sys_config_8800d80(struct aic_sdio_dev *sdiodev)
{
#ifdef CONFIG_OOB
    int ret, cnt;
	int gpiocfg_num = sizeof(gpio_cfg_tbl_8800d40d80) / sizeof(u32) / 2;
	for (cnt = 0; cnt < gpiocfg_num; cnt++) {
		ret = rwnx_send_dbg_mem_write_req(sdiodev, gpio_cfg_tbl_8800d40d80[cnt][0], gpio_cfg_tbl_8800d40d80[cnt][1]);
		if (ret) {
			printk("%x write fail: %d\n", gpio_cfg_tbl_8800d40d80[cnt][0], ret);
			return ret;
		}
	}
#endif

	return 0;
}

int aicwifi_sys_config_8800d80x2(struct aic_sdio_dev *sdiodev)
{
#ifdef CONFIG_OOB
    int ret, cnt;
	int gpiocfg_num = sizeof(gpio_cfg_tbl_8800d80x2) / sizeof(u32) / 2;
	printk("start config d80x2_oob");
	for (cnt = 0; cnt < gpiocfg_num; cnt++) {
		if(gpiocfg_num - 1 == cnt) {
			sdio_claim_host(sdiodev->func);
			sdio_release_irq(sdiodev->func);
			sdio_release_host(sdiodev->func);
			printk("aic_dbg release_irq");

		}
		ret = rwnx_send_dbg_mem_write_req(sdiodev, gpio_cfg_tbl_8800d80x2[cnt][0], gpio_cfg_tbl_8800d80x2[cnt][1]);
		if (ret) {
			printk("%x write fail: %d\n", gpio_cfg_tbl_8800d80x2[cnt][0], ret);
			return ret;
		}
	}
#endif

	return 0;
}


#define NEW_PATCH_BUFFER_MAP    1

int aicwifi_patch_config_8800d80(struct aic_sdio_dev *sdiodev,int ofset)
{
	
	const u32 rd_patch_addr = RAM_FMAC_FW_ADDR + ofset;
	u32 aic_patch_addr;
	u32 config_base, aic_patch_str_base;
	#if (NEW_PATCH_BUFFER_MAP)
	u32 patch_buff_addr, patch_buff_base, rd_version_addr, rd_version_val;
	#endif
	uint32_t start_addr = 0x0016F800;
	u32 patch_addr = start_addr;
	u32 patch_cnt = sizeof(patch_tbl_8800d80)/sizeof(u32)/2;
	struct dbg_mem_read_cfm rd_patch_addr_cfm;
	int ret = 0;
	int cnt = 0;
	//adap test
	int adap_patch_cnt = 0;

	if (adap_test) {
        printk("%s for adaptivity test \r\n", __func__);
		adap_patch_cnt = sizeof(adaptivity_patch_tbl_8800d80)/sizeof(u32)/2;
	}

	aic_patch_addr = rd_patch_addr + 8;

	ret = rwnx_send_dbg_mem_read_req(sdiodev, rd_patch_addr, &rd_patch_addr_cfm);
	if (ret) {
		printk("patch rd fail\n");
		return ret;
	}

	config_base = rd_patch_addr_cfm.memdata;

	ret = rwnx_send_dbg_mem_read_req(sdiodev, aic_patch_addr, &rd_patch_addr_cfm);
	if (ret) {
		printk("patch str rd fail\n");
		return ret;
	}
	aic_patch_str_base = rd_patch_addr_cfm.memdata;

	#if (NEW_PATCH_BUFFER_MAP)
	rd_version_addr = RAM_FMAC_FW_ADDR + 0x01C;
	if ((ret = rwnx_send_dbg_mem_read_req(sdiodev, rd_version_addr, &rd_patch_addr_cfm))) {
		printk("version val[0x%x] rd fail: %d\n", rd_version_addr, ret);
		return ret;
	}
	rd_version_val = rd_patch_addr_cfm.memdata;
	printk("rd_version_val=%08X\n", rd_version_val);
	sdiodev->fw_version_uint = rd_version_val;
	if (rd_version_val > 0x06090100) {
		patch_buff_addr = rd_patch_addr + 12;
		ret = rwnx_send_dbg_mem_read_req(sdiodev, patch_buff_addr, &rd_patch_addr_cfm);
		if (ret) {
			printk("patch buf rd fail\n");
			return ret;
		}
		patch_buff_base = rd_patch_addr_cfm.memdata;
		patch_addr = start_addr = patch_buff_base;
	}
	#endif

	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num), AIC_PATCH_MAGIG_NUM);
	if (ret) {
		printk("0x%x write fail\n", AIC_PATCH_ADDR(magic_num));
		return ret;
	}

	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num_2), AIC_PATCH_MAGIG_NUM_2);
	if (ret) {
		printk("0x%x write fail\n", AIC_PATCH_ADDR(magic_num_2));
		return ret;
	}

	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(pair_start), patch_addr);
	if (ret) {
		printk("0x%x write fail\n", AIC_PATCH_ADDR(pair_start));
		return ret;
	}

	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(pair_count), patch_cnt + adap_patch_cnt);
	if (ret) {
		printk("0x%x write fail\n", AIC_PATCH_ADDR(pair_count));
		return ret;
	}

	for (cnt = 0; cnt < patch_cnt; cnt++) {
		ret = rwnx_send_dbg_mem_write_req(sdiodev, start_addr+8*cnt, patch_tbl_8800d80[cnt][0]+config_base);
		if (ret) {
			printk("%x write fail\n", start_addr+8*cnt);
			return ret;
		}
		ret = rwnx_send_dbg_mem_write_req(sdiodev, start_addr+8*cnt+4, patch_tbl_8800d80[cnt][1]);
		if (ret) {
			printk("%x write fail\n", start_addr+8*cnt+4);
			return ret;
		}
	}

	if (adap_test){
		int tmp_cnt = patch_cnt + adap_patch_cnt;
		for (cnt = patch_cnt; cnt < tmp_cnt; cnt++) {
			int tbl_idx = cnt - patch_cnt;
			ret = rwnx_send_dbg_mem_write_req(sdiodev, start_addr+8*cnt, adaptivity_patch_tbl_8800d80[tbl_idx][0]+config_base);
			if(ret) {
				printk("%x write fail\n", start_addr+8*cnt);
				return ret;
			}
			ret = rwnx_send_dbg_mem_write_req(sdiodev, start_addr+8*cnt+4, adaptivity_patch_tbl_8800d80[tbl_idx][1]);
			if(ret) {
				printk("%x write fail\n", start_addr+8*cnt+4);
				return ret;
			}
		}
	}

	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(block_size[0]), 0);
	if (ret) {
		printk("block_size[0x%x] write fail: %d\n", AIC_PATCH_ADDR(block_size[0]), ret);
		return ret;
	}
	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(block_size[1]), 0);
	if (ret) {
		printk("block_size[0x%x] write fail: %d\n", AIC_PATCH_ADDR(block_size[1]), ret);
		return ret;
	}
	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(block_size[2]), 0);
	if (ret) {
		printk("block_size[0x%x] write fail: %d\n", AIC_PATCH_ADDR(block_size[2]), ret);
		return ret;
	}
	ret = rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(block_size[3]), 0);
	if (ret) {
		printk("block_size[0x%x] write fail: %d\n", AIC_PATCH_ADDR(block_size[3]), ret);
		return ret;
	}

	return 0;
}
int aicwifi_patch_config_8800d80x2(struct aic_sdio_dev *sdiodev){

	const u32 rd_patch_addr = RAM_FMAC_FW_ADDR + 0x1a8;//0x0198; g_wifi_settings
	u32 aic_patch_addr;
	u32 patch_buff_addr;
	uint32_t start_addr;
	u32 patch_addr;
	u32 patch_cnt = sizeof(patch_tbl_8800d80x2)/sizeof(u32)/2;
	int cnt = 0;
	//adap test
	int adap_patch_cnt = 0;
	int tmp_cnt = 0;
	int tbl_idx = 0;
    u32 zero = 0;
    u32 temp;
	u32 aic_patch_str_base;
	struct dbg_mem_read_cfm rd_mem_addr_cfm;
	struct dbg_mem_read_cfm patch_str_base;
	struct dbg_mem_read_cfm read_patch_buff_base;

	AICWFDBG(LOGINFO,"start aicwifi_patch_config_8800d80x2");
	if (adap_test) {
		adap_patch_cnt = sizeof(adaptivity_patch_tbl_8800d80x2)/sizeof(u32)/2;
	}

	aic_patch_addr = rd_patch_addr + 8;//g_aic_patch
	//aicwf_pcie_tran(rwnx_hw->pcidev, (void *)rd_patch_addr, &config_base, 4, AIC_TRAN_EMB2DRV, 1);
	rwnx_send_dbg_mem_read_req(sdiodev, rd_patch_addr, &rd_mem_addr_cfm);
	//aicwf_pcie_tran(rwnx_hw->pcidev, (void *)aic_patch_addr, &aic_patch_str_base, 4, AIC_TRAN_EMB2DRV, 1);
	rwnx_send_dbg_mem_read_req(sdiodev, aic_patch_addr,&patch_str_base);
	//AICWFDBG(LOGINFO, "%s: cfg_base:%x,patch_str_base:%x,adap_test=%d\n", __func__, config_base, aic_patch_str_base, adap_test);
	AICWFDBG(LOGINFO, "%s: cfg_base:%x,patch_str_base:%x,adap_test=%d\n", __func__, rd_mem_addr_cfm.memdata, patch_str_base.memdata, adap_test);
	aic_patch_str_base =  patch_str_base.memdata;
	if (1) {
		patch_buff_addr = rd_patch_addr + 12; //2k start addr
        //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)patch_buff_addr, &patch_buff_base, 4, AIC_TRAN_EMB2DRV, 1);
		rwnx_send_dbg_mem_read_req(sdiodev,patch_buff_addr, &read_patch_buff_base);
		patch_addr = start_addr = read_patch_buff_base.memdata;
		AICWFDBG(LOGINFO, "%s: patch_buff_base:%x\n", __func__, read_patch_buff_base.memdata);
	}

	if ((patch_cnt + adap_patch_cnt) == 0) {
        //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(magic_num), &zero, 4, AIC_TRAN_DRV2EMB, 1);
		rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num), zero);
		AICWFDBG(LOGTRACE,"w addr:%d,val:%d",AIC_PATCH_ADDR(magic_num),zero);
		//aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(magic_num_2), &zero, 4, AIC_TRAN_DRV2EMB, 1);
		rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num_2), zero);
		AICWFDBG(LOGTRACE,"w addr:%d,val:%d",AIC_PATCH_ADDR(magic_num_2),zero);

	return 0;
	}

	temp = AIC_PATCH_MAGIG_NUM;
	//aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(magic_num), &temp, 4, AIC_TRAN_DRV2EMB, 1);
	rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num), temp);
	AICWFDBG(LOGTRACE,"w addr:%d,val:%d",AIC_PATCH_ADDR(magic_num),temp);
	temp = AIC_PATCH_MAGIG_NUM_2;
	//aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(magic_num_2), &temp, 4, AIC_TRAN_DRV2EMB, 1);
	rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(magic_num_2), temp);
	AICWFDBG(LOGTRACE,"w addr:%d,val:%d",AIC_PATCH_ADDR(magic_num_2),temp);

	temp = patch_addr;
   // aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(pair_start), &temp, 4, AIC_TRAN_DRV2EMB, 1);
   rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(pair_start), temp);
   AICWFDBG(LOGTRACE,"w addr:%d,val:%d", AIC_PATCH_ADDR(pair_start),temp);

   	temp = patch_cnt + adap_patch_cnt;;
    //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)AIC_PATCH_ADDR(pair_count), &temp, 4, AIC_TRAN_DRV2EMB, 1);
	rwnx_send_dbg_mem_write_req(sdiodev, AIC_PATCH_ADDR(pair_count), temp);
	AICWFDBG(LOGTRACE,"w addr:%d,val:%d", AIC_PATCH_ADDR(pair_count),temp);

	for (cnt = 0; cnt < patch_cnt; cnt++) {
        temp = patch_tbl_8800d80x2[cnt][0]+rd_mem_addr_cfm.memdata;
       // aicwf_pcie_tran(rwnx_hw->pcidev, (void *)(start_addr+8*cnt), &temp, 4, AIC_TRAN_DRV2EMB, 1);
		rwnx_send_dbg_mem_write_req(sdiodev, (start_addr+8*cnt), temp);
		AICWFDBG(LOGTRACE,"w addr:%d,val:%d", (start_addr+8*cnt),temp);
		temp = patch_tbl_8800d80x2[cnt][1];
        //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)(start_addr+8*cnt + 4), &temp, 4, AIC_TRAN_DRV2EMB, 1);
		rwnx_send_dbg_mem_write_req(sdiodev, (start_addr+8*cnt + 4), temp);
		AICWFDBG(LOGTRACE,"w addr:%d,val:%d", (start_addr+8*cnt + 4),temp);
		AICWFDBG(LOGTRACE, "0x%x: 0x%x\n",start_addr+8*cnt, patch_tbl_8800d80x2[cnt][0]+rd_mem_addr_cfm.memdata);
	}

	if (adap_test){
		tmp_cnt = patch_cnt + adap_patch_cnt;
		for (cnt = patch_cnt; cnt < tmp_cnt; cnt++) {
			tbl_idx = cnt - patch_cnt;
            temp = adaptivity_patch_tbl_8800d80x2[tbl_idx][0]+rd_mem_addr_cfm.memdata;
            //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)(start_addr+8*cnt), &temp, 4, AIC_TRAN_DRV2EMB, 1);
			rwnx_send_dbg_mem_write_req(sdiodev,(start_addr+8*cnt), temp);
			temp = adaptivity_patch_tbl_8800d80x2[tbl_idx][1];
            //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)(start_addr+8*cnt + 4), &temp, 4, AIC_TRAN_DRV2EMB, 1);
			rwnx_send_dbg_mem_write_req(sdiodev,(start_addr+8*cnt + 4), temp);
		}
	}

	for (cnt = 0; cnt < 4; cnt++) {
        //aicwf_pcie_tran(rwnx_hw->pcidev, (void *)(AIC_PATCH_ADDR(block_size[cnt])), &zero, 4, AIC_TRAN_DRV2EMB, 1);
		rwnx_send_dbg_mem_write_req(sdiodev,(AIC_PATCH_ADDR(block_size[cnt])), zero);
		AICWFDBG(LOGTRACE,"w addr:%d,val:%d", (AIC_PATCH_ADDR(block_size[cnt])),zero);
	}
	AICWFDBG(LOGINFO,"end aicwifi_patch_config_8800d80x2");
	return 0;
	
}


