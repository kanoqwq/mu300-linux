/*
 *
 * (C) COPYRIGHT ARM Limited. All rights reserved.
 *
 * This program is free software and is provided to you under the terms of the
 * GNU General Public License version 2 as published by the Free Software
 * Foundation, and any use by you of this program is subject to the terms
 * of such GNU licence.
 *
 * A copy of the licence is included with the program, and can also be obtained
 * from Free Software Foundation, Inc., 51 Franklin Street, Fifth Floor,
 * Boston, MA  02110-1301, USA.
 *
 */



#include <linux/ioport.h>
#include <linux/version.h>
#include <linux/delay.h>
#include <linux/clk.h>
#include <linux/hwspinlock.h>
#include <mali_kbase.h>
#include <mali_kbase_defs.h>
#include <mali_kbase_config.h>
#include <mali_kbase_debug.h>
#include <mali_kbase_ioctl.h>
//For fix r24p0 --> r27p0 compile error.
//#include <backend/gpu/mali_kbase_device_internal.h>
#include <linux/mfd/syscon.h>
#include <linux/regmap.h>
#include <linux/clk-provider.h>
#include <linux/io.h>
#include <linux/of_address.h>
#ifdef KBASE_PM_RUNTIME
#include <linux/pm_runtime.h>
#endif
#ifdef CONFIG_OF
#include <linux/of.h>
#endif
#include <linux/regulator/consumer.h>

//#include <linux/smp.h>
//#include <trace/events/power.h>
#include <linux/nvmem-consumer.h>
#include <gpu,qogirl6-regs.h>
#include <gpu,qogirl6-mask.h>

//#define VENDOR_FTRACE_MODULE_NAME    "unisoc-gpu"

#define DTS_CLK_OFFSET      4
#define PM_RUNTIME_DELAY_MS 50
#define UP_THRESHOLD        9/10
#define FREQ_KHZ            1000

#define T606_GPLL_FREQ      650000000
#define T616_GPLL_FREQ      750000000
//#define DEFAULT_GPLL_FREQ   800000000

#define GPU_768M_FREQ       768000000
#define GPU_850M_FREQ       850000000

struct gpu_qos_config {
	u8 arqos;
	u8 awqos;
	u8 arqos_threshold;
	u8 awqos_threshold;
};

struct gpu_freq_info {
	struct clk* clk_src;
	int freq;	//kHz
	int volt;	//uV
	int div;
	int dvfs_index;
};

struct gpu_reg_info {
	struct regmap* regmap_ptr;
	uint32_t args[2];
};

struct gpu_dvfs_context {
	int gpu_clock_on;
	int gpu_power_on;
	int cur_voltage;

	struct clk*  clk_gpu_i;
	struct clk*  clk_gpu_core_eb;
	struct clk*  clk_gpu_mem_eb;
	struct clk*  clk_gpu_sys_eb;
	struct clk** gpu_clk_src;
	int gpu_clk_num;

	struct gpu_freq_info* freq_list;
	int freq_list_len;

	int cur_index;
	const struct gpu_freq_info* freq_cur;
	const struct gpu_freq_info* freq_default;

	struct semaphore* sem;

	struct gpu_reg_info top_force_reg;
	struct gpu_reg_info gpu_top_state_reg;
	struct gpu_reg_info gpu_qos_sel;
	struct gpu_reg_info gpu_qos;
	struct gpu_reg_info dvfs_index_cfg;
	struct gpu_reg_info sw_dvfs_ctrl;
	struct gpu_reg_info freq_upd_cfg;
	struct gpu_reg_info gpu_core0_state_reg;
	struct gpu_reg_info cur_st_st0_reg;
	struct gpu_reg_info core_index0_map;
	struct gpu_reg_info core_index1_map;
	struct gpu_reg_info core_index2_map;
	struct gpu_reg_info core_index3_map;
	struct gpu_reg_info core_index4_map;
	struct gpu_reg_info core_index5_map;
	struct gpu_reg_info core_index6_map;
	struct gpu_reg_info core_index7_map;
	struct gpu_reg_info dvfs_voltage_value1;

	struct regmap* gpu_apb_base_ptr;
	struct regmap* gpu_dvfs_apb_base_ptr;

	u32 gpu_binning;
};

DEFINE_SEMAPHORE(gpu_dfs_sem);
static struct gpu_dvfs_context gpu_dvfs_ctx=
{
	.gpu_clock_on=0,
	.gpu_power_on=0,

	.sem=&gpu_dfs_sem,
};

static struct gpu_qos_config gpu_qos_cfg=
{
	.arqos=0,
	.awqos=0,
	.arqos_threshold=0,
	.awqos_threshold=0,
};

int gpu_boost_level = 0;

#ifdef CONFIG_MALI_DEVFREQ
static void InitFreqStats(struct kbase_device *kbdev)
{
	int i = 0;

	kbdev->enable_freq_stats = 0;
	kbdev->freq_num = gpu_dvfs_ctx.freq_list_len;
	kbdev->freq_stats = vmalloc(sizeof(struct kbase_devfreq_stats) * kbdev->freq_num);
	KBASE_DEBUG_ASSERT(kbdev->freq_stats);

	for (i = 0; i < kbdev->freq_num; i++)
	{
		kbdev->freq_stats[i].freq = gpu_dvfs_ctx.freq_list[i].freq * FREQ_KHZ;
		kbdev->freq_stats[i].busy_time = 0;
		kbdev->freq_stats[i].total_time = 0;
	}
}

static void DeinitFreqStats(struct kbase_device *kbdev)
{
	if (NULL != kbdev->freq_stats)
	{
		vfree(kbdev->freq_stats);
		kbdev->freq_stats = NULL;
	}
}

#endif

static int sprd_gpu_cal_read(struct device_node *np, const char *cell_id, u32 *val)
{
	struct nvmem_cell *cell;
	void *buf;
	size_t len;

	cell = of_nvmem_cell_get(np, cell_id);
	if (IS_ERR(cell))
		return PTR_ERR(cell);

	buf = nvmem_cell_read(cell, &len);
	if (IS_ERR(buf))
	{
		nvmem_cell_put(cell);
		return PTR_ERR(buf);
	}

	memcpy(val, buf, min(len, sizeof(u32)));

	kfree(buf);
	nvmem_cell_put(cell);

	return 0;
}

static inline void mali_freq_init(struct device *dev)
{
	int i = 0, clk_cnt = 0, ret = 0;
	struct device_node *qos_node = NULL;
	struct device_node *hwf;
	const char *auto_efuse = NULL;
	struct regmap* topdvfs_controller_base_ptr = NULL;

	gpu_dvfs_ctx.top_force_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"top-force-shutdown", 2, (uint32_t *)gpu_dvfs_ctx.top_force_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.top_force_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_top_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_top_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr);

	topdvfs_controller_base_ptr = syscon_regmap_lookup_by_phandle(dev->of_node, "topdvfs-controller");
	KBASE_DEBUG_ASSERT(topdvfs_controller_base_ptr);
	gpu_dvfs_ctx.gpu_apb_base_ptr = syscon_regmap_lookup_by_phandle(dev->of_node, "sprd,gpu-apb-syscon");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_apb_base_ptr);
	gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr = syscon_regmap_lookup_by_phandle(dev->of_node, "sprd,gpu-dvfs-apb-syscon");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr);

	//DCDC_MM_DVFS_VOLTAGE_VALEU1
	gpu_dvfs_ctx.dvfs_voltage_value1.regmap_ptr = topdvfs_controller_base_ptr;
	gpu_dvfs_ctx.dvfs_voltage_value1.args[0] = 0x00E8;
	gpu_dvfs_ctx.dvfs_voltage_value1.args[1] = 0x1FF;

	//qos
	gpu_dvfs_ctx.gpu_qos_sel.regmap_ptr = gpu_dvfs_ctx.gpu_apb_base_ptr;
	gpu_dvfs_ctx.gpu_qos_sel.args[0] = REG_GPU_APB_RF_GPU_NIC400_QOS;
	gpu_dvfs_ctx.gpu_qos_sel.args[1] = MASK_GPU_APB_RF_GPU_QOS_SEL;

	gpu_dvfs_ctx.gpu_qos.regmap_ptr = gpu_dvfs_ctx.gpu_apb_base_ptr;
	gpu_dvfs_ctx.gpu_qos.args[0] = REG_GPU_APB_RF_GPU_NIC400_QOS;
	gpu_dvfs_ctx.gpu_qos.args[1] = MASK_GPU_APB_RF_AWQOS_THRESHOLD_GPU | MASK_GPU_APB_RF_ARQOS_THRESHOLD_GPU | MASK_GPU_APB_RF_AWQOS_GPU | MASK_GPU_APB_RF_ARQOS_GPU;

	gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_core0_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_core0_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr);

	gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"cur_st_st0", 2, (uint32_t *)gpu_dvfs_ctx.cur_st_st0_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr);

	/* qos dts parse */
	qos_node = of_parse_phandle(dev->of_node, "sprd,qos", 0);
	if (qos_node)
	{
		if (of_property_read_u8(qos_node, "arqos", &gpu_qos_cfg.arqos)) {
			pr_warn("gpu arqos config reading fail.\n");
		}
		if (of_property_read_u8(qos_node, "awqos", &gpu_qos_cfg.awqos)) {
			pr_warn("gpu awqos config reading fail.\n");
		}
		if (of_property_read_u8(qos_node, "arqos-threshold", &gpu_qos_cfg.arqos_threshold)) {
			pr_warn("gpu arqos_threshold config reading fail.\n");
		}
		if (of_property_read_u8(qos_node, "awqos-threshold", &gpu_qos_cfg.awqos_threshold)) {
			pr_warn("gpu awqos_threshold config reading fail.\n");
		}
	} else {
		pr_warn("can't find gpu qos config node\n");
	}

	//gpu index cfg
	gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.dvfs_index_cfg.args[0] = REG_GPU_DVFS_APB_RF_GPU_DVFS_INDEX_CFG;
	gpu_dvfs_ctx.dvfs_index_cfg.args[1] = MASK_GPU_DVFS_APB_RF_GPU_DVFS_INDEX;

	//sw dvfs ctrl
	gpu_dvfs_ctx.sw_dvfs_ctrl.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.sw_dvfs_ctrl.args[0] = REG_GPU_DVFS_APB_RF_GPU_SW_DVFS_CTRL;
	gpu_dvfs_ctx.sw_dvfs_ctrl.args[1] = MASK_GPU_DVFS_APB_RF_GPU_DVFS_ACK | MASK_GPU_DVFS_APB_RF_GPU_DVFS_VOLTAGE_SW | MASK_GPU_DVFS_APB_RF_GPU_DVFS_REQ_SW;

	//freq update cfg
	gpu_dvfs_ctx.freq_upd_cfg.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.freq_upd_cfg.args[0] = REG_GPU_DVFS_APB_RF_GPU_FREQ_UPD_TYPE_CFG;
	gpu_dvfs_ctx.freq_upd_cfg.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_FREQ_UPD_HDSK_EN | MASK_GPU_DVFS_APB_RF_GPU_CORE_FREQ_UPD_DELAY_EN;

	//core index0 map
	gpu_dvfs_ctx.core_index0_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index0_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX0_MAP;
	gpu_dvfs_ctx.core_index0_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX0;

	//core index1 map
	gpu_dvfs_ctx.core_index1_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index1_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX1_MAP;
	gpu_dvfs_ctx.core_index1_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX1;

	//core index2 map
	gpu_dvfs_ctx.core_index2_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index2_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX2_MAP;
	gpu_dvfs_ctx.core_index2_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX2;

	//core index3 map
	gpu_dvfs_ctx.core_index3_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index3_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX3_MAP;
	gpu_dvfs_ctx.core_index3_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX3;

	//core index4 map
	gpu_dvfs_ctx.core_index4_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index4_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX4_MAP;
	gpu_dvfs_ctx.core_index4_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX4;

	//core index5 map
	gpu_dvfs_ctx.core_index5_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index5_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX5_MAP;
	gpu_dvfs_ctx.core_index5_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX5;

	//core index6 map
	gpu_dvfs_ctx.core_index6_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index6_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX6_MAP;
	gpu_dvfs_ctx.core_index6_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX6;

	//core index7 map
	gpu_dvfs_ctx.core_index7_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index7_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_CORE_INDEX7_MAP;
	gpu_dvfs_ctx.core_index7_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX7;

	//read gpu bin, get T618 1:FF or 2:TT, as well as get T619  1:Bin1  2:bin2
	ret = sprd_gpu_cal_read(dev->of_node, "gpu_bin", &gpu_dvfs_ctx.gpu_binning);
	//printk(KERN_ERR "Jassmine gpu_binning = %d\n", gpu_dvfs_ctx.gpu_binning);
	if (ret)
	{
		pr_warn("gpu binning read fail.\n");
	}

	gpu_dvfs_ctx.clk_gpu_i = of_clk_get(dev->of_node, 0);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_gpu_i);

	gpu_dvfs_ctx.clk_gpu_core_eb = of_clk_get(dev->of_node, 1);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_gpu_core_eb);

	gpu_dvfs_ctx.clk_gpu_mem_eb = of_clk_get(dev->of_node, 2);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_gpu_mem_eb);

	gpu_dvfs_ctx.clk_gpu_sys_eb = of_clk_get(dev->of_node, 3);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_gpu_sys_eb);

	clk_cnt = of_clk_get_parent_count(dev->of_node);
	gpu_dvfs_ctx.gpu_clk_num = clk_cnt - DTS_CLK_OFFSET;

	gpu_dvfs_ctx.gpu_clk_src = vmalloc(sizeof(struct clk*) * gpu_dvfs_ctx.gpu_clk_num);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_clk_src);

	for (i = 0; i < gpu_dvfs_ctx.gpu_clk_num; i++)
	{
		gpu_dvfs_ctx.gpu_clk_src[i] = of_clk_get(dev->of_node, i+DTS_CLK_OFFSET);
		KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_clk_src[i]);
	}

	gpu_dvfs_ctx.freq_list_len = of_property_count_elems_of_size(dev->of_node,"sprd,dvfs-lists",4*sizeof(u32));
	gpu_dvfs_ctx.freq_list = vmalloc(sizeof(struct gpu_freq_info) * gpu_dvfs_ctx.freq_list_len);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_list);

	for(i=0; i<gpu_dvfs_ctx.freq_list_len; i++)
	{
		int clk = 0;

		of_property_read_u32_index(dev->of_node, "sprd,dvfs-lists", 4*i+2, &clk);
		gpu_dvfs_ctx.freq_list[i].clk_src = gpu_dvfs_ctx.gpu_clk_src[clk-DTS_CLK_OFFSET];
		KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_list[i].clk_src);
		of_property_read_u32_index(dev->of_node, "sprd,dvfs-lists", 4*i,   &gpu_dvfs_ctx.freq_list[i].freq);
		of_property_read_u32_index(dev->of_node, "sprd,dvfs-lists", 4*i+1, &gpu_dvfs_ctx.freq_list[i].volt);
		of_property_read_u32_index(dev->of_node, "sprd,dvfs-lists", 4*i+3, &gpu_dvfs_ctx.freq_list[i].div);
		gpu_dvfs_ctx.freq_list[i].dvfs_index = i;
	}

	//adjust freq list
	hwf = of_find_node_by_path("/hwfeature/auto");
	if (hwf) {
		auto_efuse = (char*)of_get_property(hwf, "efuse", NULL);
		//pr_info ("find  in %s was %s\n", __func__, auto_efuse);
	}
	//T606: GPLL max freq is 650M
	//T616: GPLL max freq is 750M
	//T619: GPLL max freq is 850M
	// GPLL max freq is 850M
	if (!strcmp(auto_efuse, "T606") || !strcmp(auto_efuse, "T612"))
	{
		//Modify 850M to 650M
		gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq = T606_GPLL_FREQ / FREQ_KHZ;

		//remove 768M
		memcpy(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2],
			&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1],
			sizeof(struct gpu_freq_info));
		memset(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1], 0, sizeof(struct gpu_freq_info));

		//modify freq list len
		gpu_dvfs_ctx.freq_list_len = gpu_dvfs_ctx.freq_list_len -1;
	}
	else if (!strcmp(auto_efuse, "T616"))
	{
		//Modify 850M to 750M
		gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq = T616_GPLL_FREQ / FREQ_KHZ;

		//remove 768M
		memcpy(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2],
			&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1],
			sizeof(struct gpu_freq_info));
		memset(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1], 0, sizeof(struct gpu_freq_info));

		//modify freq list len
		gpu_dvfs_ctx.freq_list_len = gpu_dvfs_ctx.freq_list_len -1;
	}
	else if (!strcmp(auto_efuse, "T619"))
	{

#define VOLTAGE_OF_GPU_CORE_700000uv 700000
#define VOLTAGE_OF_GPU_CORE_750000uv 750000
#define VOLTAGE_OF_GPU_CORE_800000uv 800000
#define VOLTAGE_OF_GPU_CORE_850000uv 850000

		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			gpu_dvfs_ctx.freq_list[0].volt = VOLTAGE_OF_GPU_CORE_700000uv;
			gpu_dvfs_ctx.freq_list[1].volt = VOLTAGE_OF_GPU_CORE_700000uv;
			gpu_dvfs_ctx.freq_list[2].volt = VOLTAGE_OF_GPU_CORE_750000uv;
			gpu_dvfs_ctx.freq_list[3].volt = VOLTAGE_OF_GPU_CORE_800000uv;
			gpu_dvfs_ctx.freq_list[4].volt = VOLTAGE_OF_GPU_CORE_800000uv;
		}
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			gpu_dvfs_ctx.freq_list[0].volt = VOLTAGE_OF_GPU_CORE_700000uv;
			gpu_dvfs_ctx.freq_list[1].volt = VOLTAGE_OF_GPU_CORE_750000uv;
			gpu_dvfs_ctx.freq_list[2].volt = VOLTAGE_OF_GPU_CORE_750000uv;
			gpu_dvfs_ctx.freq_list[3].volt = VOLTAGE_OF_GPU_CORE_850000uv;
			gpu_dvfs_ctx.freq_list[4].volt = VOLTAGE_OF_GPU_CORE_850000uv;
		}
	}

	if (2 == gpu_dvfs_ctx.gpu_binning)
	{
		//BIN2 T606
		if (!strcmp(auto_efuse, "T606") || !strcmp(auto_efuse, "T612"))
		{
		}
		else if (!strcmp(auto_efuse, "T616"))
		{
			//BIN2 TT 750M:0.8125v-Gear:3
			//0.8125v, 812.5mv/3.125mv/step = 260 steps, 0d260 = 0x104
			regmap_update_bits(gpu_dvfs_ctx.dvfs_voltage_value1.regmap_ptr, gpu_dvfs_ctx.dvfs_voltage_value1.args[0], gpu_dvfs_ctx.dvfs_voltage_value1.args[1], 0x104);
		}
		else if (!strcmp(auto_efuse, "T619"))
		{
			//T619 BIN2 768M\850M: 0.85v-Gear: 3
			//0.85v, 850mv/3.125mv/step = 272 steps, 0d272 = 0x110
			regmap_update_bits(gpu_dvfs_ctx.dvfs_voltage_value1.regmap_ptr, gpu_dvfs_ctx.dvfs_voltage_value1.args[0], gpu_dvfs_ctx.dvfs_voltage_value1.args[1], 0x110);
		}
		else
		{
			//BIN2 TT 850M:0.85v-Gear:3
			//0.85v, 850mv/3.125mv/step = 272 steps, 0d272 = 0x110
			regmap_update_bits(gpu_dvfs_ctx.dvfs_voltage_value1.regmap_ptr, gpu_dvfs_ctx.dvfs_voltage_value1.args[0], gpu_dvfs_ctx.dvfs_voltage_value1.args[1], 0x110);
		}
	}

	of_property_read_u32(dev->of_node, "sprd,dvfs-default", &i);
	gpu_dvfs_ctx.freq_default = &gpu_dvfs_ctx.freq_list[i];
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_default);

	gpu_dvfs_ctx.cur_index = i;
	gpu_dvfs_ctx.freq_cur = gpu_dvfs_ctx.freq_default;
	gpu_dvfs_ctx.cur_voltage = gpu_dvfs_ctx.freq_cur->volt;
}


static bool mali_top_pwr_state_check(void)
{
    u32 top_state = 0xffff;
    const u32 top_mask = 0x1f << 16;
    const u32 top_pwr_on = 0;
    int cnt_1 = 0, cnt_2 = 0;

    while(1) {
        //check if the top_state ready or not powered
        regmap_read(gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_top_state_reg.args[0], &top_state);
        top_state &= top_mask;
        if (top_state == top_pwr_on) {
            if (++cnt_1 >= 5)
                return true;
        } else {
            cnt_1 = 0;
            udelay(50);
            if(++cnt_2 >= 200) {
                printk(KERN_ERR "%s: gpu top power on is timeout !", __func__);
                WARN_ON(1);
                return false;
            }
        }
    }

    return true;
}

static inline void mali_power_on(void)
{
	bool ret = true;

	regmap_update_bits(gpu_dvfs_ctx.top_force_reg.regmap_ptr, gpu_dvfs_ctx.top_force_reg.args[0], gpu_dvfs_ctx.top_force_reg.args[1], ~gpu_dvfs_ctx.top_force_reg.args[1]);

	udelay(100);

	//check if the top_state ready or not
	ret = mali_top_pwr_state_check();
	if (!ret) {
		printk(KERN_ERR "mali %s: gpu top not power on\n", __func__);
		WARN_ON(1);
	}

	gpu_dvfs_ctx.gpu_power_on = 1;
}


static inline void mali_power_off(void)
{
	gpu_dvfs_ctx.gpu_power_on = 0;

	regmap_update_bits(gpu_dvfs_ctx.top_force_reg.regmap_ptr, gpu_dvfs_ctx.top_force_reg.args[0], gpu_dvfs_ctx.top_force_reg.args[1], gpu_dvfs_ctx.top_force_reg.args[1]);
}

static void maliQosConfig(void)
{
	regmap_update_bits(gpu_dvfs_ctx.gpu_qos_sel.regmap_ptr, gpu_dvfs_ctx.gpu_qos_sel.args[0], gpu_dvfs_ctx.gpu_qos_sel.args[1], gpu_dvfs_ctx.gpu_qos_sel.args[1]);
	regmap_update_bits(gpu_dvfs_ctx.gpu_qos.regmap_ptr, gpu_dvfs_ctx.gpu_qos.args[0], gpu_dvfs_ctx.gpu_qos.args[1], ((gpu_qos_cfg.awqos_threshold << 12) | (gpu_qos_cfg.arqos_threshold << 8) | (gpu_qos_cfg.awqos << 4) | gpu_qos_cfg.arqos));
}

static inline void mali_clock_on(void)
{
	int i;
	struct device_node *hwf;
	const char *auto_efuse = NULL;
	bool top_pwr_check_ret = true;
	//enable all clocks
	for(i=0;i<gpu_dvfs_ctx.gpu_clk_num;i++)
	{
		clk_prepare_enable(gpu_dvfs_ctx.gpu_clk_src[i]);
	}
	clk_prepare_enable(gpu_dvfs_ctx.clk_gpu_i);


	//enable gpu clock
	clk_prepare_enable(gpu_dvfs_ctx.clk_gpu_core_eb);
	clk_prepare_enable(gpu_dvfs_ctx.clk_gpu_mem_eb);
	clk_prepare_enable(gpu_dvfs_ctx.clk_gpu_sys_eb);
	udelay(200);

	//check if the top_state ready or not
	top_pwr_check_ret = mali_top_pwr_state_check();
	if (!top_pwr_check_ret) {
		printk(KERN_ERR "mali %s: gpu top not power on\n", __func__);
		WARN_ON(1);
	}

	//set core index map
	hwf = of_find_node_by_path("/hwfeature/auto");
	if (hwf) {
		auto_efuse = (char*)of_get_property(hwf, "efuse", NULL);
		//pr_info ("find  in %s was %s\n", __func__, auto_efuse);
	}

	//default voltage 384M:0.7v 512M:0.75v 614.4M:0.75v 768M:0.8v 850M:0.8v
	//Gear 0: 0.7v 1:0.75v 2:0.8v
	if (!strcmp(auto_efuse, "T606") || !strcmp(auto_efuse, "T612"))
	{
		//T606: GPLL max freq is 650M
		clk_set_rate(gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].clk_src, (unsigned long)gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq * FREQ_KHZ);

		//BIN1 FF 512M:0.7v-Gear:0 650M:0.75v-Gear:1
		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index1_map.regmap_ptr, gpu_dvfs_ctx.core_index1_map.args[0], gpu_dvfs_ctx.core_index1_map.args[1], 0<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 1<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 1<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 1<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0], gpu_dvfs_ctx.core_index7_map.args[1], 1<<14);
		}
		//BIN2 TT 650M:0.8v-Gear:2
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
		}
	}
	else if (!strcmp(auto_efuse, "T616"))
	{
		//T616: GPLL max freq is 750M
		clk_set_rate(gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].clk_src, (unsigned long)gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq * FREQ_KHZ);

		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			//BIN1 FF 512M:0.7v-Gear:0 750M:0.8v-Gear:2
			regmap_update_bits(gpu_dvfs_ctx.core_index1_map.regmap_ptr, gpu_dvfs_ctx.core_index1_map.args[0], gpu_dvfs_ctx.core_index1_map.args[1], 0<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 2<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 2<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 2<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0], gpu_dvfs_ctx.core_index7_map.args[1], 2<<14);
		}
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			//BIN2 TT 750M:0.8125v-Gear:3
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0], gpu_dvfs_ctx.core_index7_map.args[1], 3<<14);
		}
	}
	else if (!strcmp(auto_efuse, "T619"))
	{

#define GPU_CORE_VOL_INDEX_OFFSET 14
#define VOTE_VOLTAGE_OF_GPU_CORE_700mv 0
#define VOTE_VOLTAGE_OF_GPU_CORE_750mv 1
#define VOTE_VOLTAGE_OF_GPU_CORE_800mv 2
#define VOTE_VOLTAGE_OF_GPU_CORE_850mv 3

		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index0_map.regmap_ptr, gpu_dvfs_ctx.core_index0_map.args[0],
								gpu_dvfs_ctx.core_index0_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_700mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index1_map.regmap_ptr, gpu_dvfs_ctx.core_index1_map.args[0],
								gpu_dvfs_ctx.core_index1_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_700mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index2_map.regmap_ptr, gpu_dvfs_ctx.core_index2_map.args[0],
								gpu_dvfs_ctx.core_index2_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_750mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0],
								gpu_dvfs_ctx.core_index3_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_800mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0],
								gpu_dvfs_ctx.core_index4_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_800mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0],
								gpu_dvfs_ctx.core_index5_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_800mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0],
								gpu_dvfs_ctx.core_index6_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_800mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0],
								gpu_dvfs_ctx.core_index7_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_800mv << GPU_CORE_VOL_INDEX_OFFSET);
		}
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index0_map.regmap_ptr, gpu_dvfs_ctx.core_index0_map.args[0],
								gpu_dvfs_ctx.core_index0_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_700mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index1_map.regmap_ptr, gpu_dvfs_ctx.core_index1_map.args[0],
								gpu_dvfs_ctx.core_index1_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_750mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index2_map.regmap_ptr, gpu_dvfs_ctx.core_index2_map.args[0],
								gpu_dvfs_ctx.core_index2_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_750mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0],
								gpu_dvfs_ctx.core_index3_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_850mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0],
								gpu_dvfs_ctx.core_index4_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_850mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0],
								gpu_dvfs_ctx.core_index5_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_850mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0],
								gpu_dvfs_ctx.core_index6_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_850mv << GPU_CORE_VOL_INDEX_OFFSET);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0],
								gpu_dvfs_ctx.core_index7_map.args[1],	VOTE_VOLTAGE_OF_GPU_CORE_850mv << GPU_CORE_VOL_INDEX_OFFSET);
		}
	}
	else
	{
		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			//BIN1 FF 512M:0.7v-Gear:0
			regmap_update_bits(gpu_dvfs_ctx.core_index1_map.regmap_ptr, gpu_dvfs_ctx.core_index1_map.args[0], gpu_dvfs_ctx.core_index1_map.args[1], 0<<14);
		}
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			//BIN2 TT 850M:0.85v-Gear:3
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 3<<14);
			regmap_update_bits(gpu_dvfs_ctx.core_index7_map.regmap_ptr, gpu_dvfs_ctx.core_index7_map.args[0], gpu_dvfs_ctx.core_index7_map.args[1], 3<<14);
		}
	}

	//update freq cfg
	regmap_update_bits(gpu_dvfs_ctx.freq_upd_cfg.regmap_ptr, gpu_dvfs_ctx.freq_upd_cfg.args[0], gpu_dvfs_ctx.freq_upd_cfg.args[1], 1);

	//init freq
	regmap_update_bits(gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr, gpu_dvfs_ctx.dvfs_index_cfg.args[0], gpu_dvfs_ctx.dvfs_index_cfg.args[1], gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.cur_index].dvfs_index);

	//qos
	maliQosConfig();

	gpu_dvfs_ctx.gpu_clock_on = 1;
}

static inline void mali_clock_off(void)
{
	int i;

	gpu_dvfs_ctx.gpu_clock_on = 0;

	//disable gpu clock
	clk_disable_unprepare(gpu_dvfs_ctx.clk_gpu_core_eb);
	clk_disable_unprepare(gpu_dvfs_ctx.clk_gpu_mem_eb);
	clk_disable_unprepare(gpu_dvfs_ctx.clk_gpu_sys_eb);
	clk_disable_unprepare(gpu_dvfs_ctx.clk_gpu_i);

	//disable all clocks
	for(i=0;i<gpu_dvfs_ctx.gpu_clk_num;i++)
	{
		clk_disable_unprepare(gpu_dvfs_ctx.gpu_clk_src[i]);
	}
}

static int mali_platform_init(struct kbase_device *kbdev)
{
	//gpu freq
	mali_freq_init(kbdev->dev);

#ifdef CONFIG_MALI_DEVFREQ
	InitFreqStats(kbdev);
	kbase_pm_statistics_FreqInit(kbdev);
#endif

	mali_power_on();

	//clock on
	mali_clock_on();

	return 0;
}

static void mali_platform_term(struct kbase_device *kbdev)
{
	down(gpu_dvfs_ctx.sem);

	//clock off
	mali_clock_off();

	//power off
	mali_power_off();

#ifdef CONFIG_MALI_DEVFREQ
	kbase_pm_statistics_FreqDeinit(kbdev);
	DeinitFreqStats(kbdev);
#endif

	//free
	vfree(gpu_dvfs_ctx.freq_list);
	vfree(gpu_dvfs_ctx.gpu_clk_src);

	up(gpu_dvfs_ctx.sem);
}

struct kbase_platform_funcs_conf platform_qogirl6_funcs = {
	.platform_init_func = mali_platform_init,
	.platform_term_func = mali_platform_term
};

static void mali_power_mode_change(struct kbase_device *kbdev, int power_mode)
{
	down(gpu_dvfs_ctx.sem);
	//dev_info(kbdev->dev, "mali_power_mode_change: %d, gpu_power_on=%d gpu_clock_on=%d",power_mode,gpu_dvfs_ctx.gpu_power_on,gpu_dvfs_ctx.gpu_clock_on);
	switch (power_mode)
	{
		case 0://power on
			if (!gpu_dvfs_ctx.gpu_power_on)
			{
				mali_power_on();
				mali_clock_on();
			}

			if (!gpu_dvfs_ctx.gpu_clock_on)
			{
				mali_clock_on();
			}
			break;

		case 1://light sleep
		case 2://deep sleep
			if(gpu_dvfs_ctx.gpu_clock_on)
			{
				mali_clock_off();
			}

			if(gpu_dvfs_ctx.gpu_power_on)
			{
				mali_power_off();
			}
			break;

		default:
			break;
	}
	kbase_pm_set_statistics(kbdev, power_mode);
	up(gpu_dvfs_ctx.sem);
}

static void pm_callback_power_off(struct kbase_device *kbdev)
{
#ifdef KBASE_PM_RUNTIME
	int res;

	res = pm_runtime_put_sync(kbdev->dev);
	if (res < 0)
	{
		printk(KERN_ERR "mali----pm_runtime_put_sync return (%d)\n", res);
	}
#endif

	mali_power_mode_change(kbdev, 1);
}

static int pm_callback_power_on(struct kbase_device *kbdev)
{
	mali_power_mode_change(kbdev, 0);

#ifdef KBASE_PM_RUNTIME
	{
		int res;

		res = pm_runtime_get_sync(kbdev->dev);
		if (res < 0)
		{
			printk(KERN_ERR "mali----pm_runtime_get_sync return (%d)\n", res);
		}
	}
#endif

	return 1;
}

static void pm_callback_power_suspend(struct kbase_device *kbdev)
{
	mali_power_mode_change(kbdev, 2);
}

static void pm_callback_power_resume(struct kbase_device *kbdev)
{
	mali_power_mode_change(kbdev, 0);
}

#ifdef KBASE_PM_RUNTIME
static int pm_callback_power_runtime_init(struct kbase_device *kbdev)
{
	pm_runtime_set_active(kbdev->dev);
	pm_suspend_ignore_children(kbdev->dev, true);
	pm_runtime_set_autosuspend_delay(kbdev->dev, PM_RUNTIME_DELAY_MS);
	pm_runtime_use_autosuspend(kbdev->dev);
	pm_runtime_enable(kbdev->dev);

	return 0;
}

static void pm_callback_power_runtime_term(struct kbase_device *kbdev)
{
	pm_runtime_disable(kbdev->dev);
}
#endif/*CONFIG_PM_RUNTIME*/

struct kbase_pm_callback_conf pm_qogirl6_callbacks = {
	.power_off_callback = pm_callback_power_off,
	.power_on_callback = pm_callback_power_on,
	.power_suspend_callback = pm_callback_power_suspend,
	.power_resume_callback = pm_callback_power_resume,
#ifdef KBASE_PM_RUNTIME
	.power_runtime_init_callback = pm_callback_power_runtime_init,
	.power_runtime_term_callback = pm_callback_power_runtime_term,
	.power_runtime_off_callback = NULL,
	.power_runtime_on_callback = NULL
#endif
};


static struct kbase_platform_config versatile_platform_config = {
};

struct kbase_platform_config *kbase_get_platform_config(void)
{
	return &versatile_platform_config;
}

int kbase_platform_early_init(void)
{
	/* Nothing needed at this stage */
	return 0;
}

#if defined(CONFIG_MALI_DEVFREQ)
static int freq_search(struct gpu_freq_info freq_list[], int len, int key)
{
	int low=0, high=len-1, mid;

	if (0 > key)
	{
		return -1;
	}

	while(low <= high)
	{
		mid = (low+high)/2;
		if(key == freq_list[mid].freq)
		{
			return mid;
		}

		if(key < freq_list[mid].freq)
		{
			high = mid-1;
		}
		else
		{
			low = mid+1;
		}
	}
	return -1;
}

int kbase_platform_get_init_freq(void)
{
	return (gpu_dvfs_ctx.freq_default->freq * FREQ_KHZ);
}

int kbase_platform_get_min_freq(void)
{
	return (gpu_dvfs_ctx.freq_list[0].freq * FREQ_KHZ);
}

int kbase_platform_get_max_freq(void)
{
	return (gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len -1].freq * FREQ_KHZ);
}

void kbase_platform_limit_max_freq(struct device *dev)
{
	struct device_node *hwf;
	const char *auto_efuse = NULL;

	hwf = of_find_node_by_path("/hwfeature/auto");
	if (hwf) {
		auto_efuse = (char*)of_get_property(hwf, "efuse", NULL);
		//pr_info ("find  in %s was %s\n", __func__, auto_efuse);
	}

	//T606: GPLL max freq is 650M
	//T616: GPLL max freq is 750M
	// GPLL max freq is 850M
	//printk(KERN_ERR "Jassmine kbase_platform_limit_max_freq auto_efuse, %s", auto_efuse);
	if (!strcmp(auto_efuse, "T606") ||
		!strcmp(auto_efuse, "T612") || !strcmp(auto_efuse, "T616"))
	{
		//remove 768M and 850M
		dev_pm_opp_disable(dev, GPU_768M_FREQ);
		dev_pm_opp_disable(dev, GPU_850M_FREQ);

		//add GPLL max freq
		dev_pm_opp_add(dev, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq * FREQ_KHZ, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].volt);
	}
}

int kbase_platform_set_freq_volt(int freq, int volt)
{
	int index = -1;

	freq = freq/FREQ_KHZ;
	index = freq_search(gpu_dvfs_ctx.freq_list, gpu_dvfs_ctx.freq_list_len, freq);
	printk(KERN_ERR "mali GPU_DVFS %s index=%d cur_freq=%d cur_volt=%d --> freq=%d volt=%d gpu_power_on=%d gpu_clock_on=%d \n",
		__func__, index, gpu_dvfs_ctx.freq_cur->freq, gpu_dvfs_ctx.cur_voltage, freq, volt,
		gpu_dvfs_ctx.gpu_power_on, gpu_dvfs_ctx.gpu_clock_on);
	if (0 <= index)
	{
		down(gpu_dvfs_ctx.sem);

		//set frequency
		if (gpu_dvfs_ctx.gpu_power_on && gpu_dvfs_ctx.gpu_clock_on)
		{
			//set dvfs index, 0: 384M 1:512M 2:614.4M 3:768M 4:800M
			regmap_update_bits(gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr, gpu_dvfs_ctx.dvfs_index_cfg.args[0], gpu_dvfs_ctx.dvfs_index_cfg.args[1], gpu_dvfs_ctx.freq_list[index].dvfs_index);
		}
		gpu_dvfs_ctx.cur_index = index;
		gpu_dvfs_ctx.freq_cur = &gpu_dvfs_ctx.freq_list[index];
		//trace_clock_set_rate(VENDOR_FTRACE_MODULE_NAME, gpu_dvfs_ctx.freq_cur->freq, raw_smp_processor_id());
		up(gpu_dvfs_ctx.sem);
	}

	return 0;
}

#ifdef CONFIG_MALI_BOOST
void kbase_platform_modify_target_freq(struct device *dev, unsigned long *target_freq)
{
	int min_index = -1, max_index = -1, modify_flag = 0;
	struct kbase_device *kbdev = dev_get_drvdata(dev);
	struct gpu_freq_info *freq_max, *freq_min;

	switch(gpu_boost_level)
	{
	case 10:
		freq_max = freq_min = &gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1];
		break;

	case 0:
	default:
		freq_max = &gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1];
		freq_min = &gpu_dvfs_ctx.freq_list[0];
		break;
	}

	//limit min freq
	min_index = freq_search(gpu_dvfs_ctx.freq_list, gpu_dvfs_ctx.freq_list_len, mu300_devfreq_min_freq(kbdev->devfreq)/FREQ_KHZ);
	if ((0 <= min_index) &&
		(freq_min->freq < gpu_dvfs_ctx.freq_list[min_index].freq))
	{
		freq_min = &gpu_dvfs_ctx.freq_list[min_index];
		if (freq_min->freq > freq_max->freq)
		{
			freq_max = freq_min;
		}
	}

	//limit max freq
	max_index = freq_search(gpu_dvfs_ctx.freq_list, gpu_dvfs_ctx.freq_list_len, mu300_devfreq_max_freq(kbdev->devfreq)/FREQ_KHZ);
	if ((0 <= max_index) &&
		(freq_max->freq > gpu_dvfs_ctx.freq_list[max_index].freq))
	{
		freq_max = &gpu_dvfs_ctx.freq_list[max_index];
		if (freq_max->freq < freq_min->freq)
		{
			freq_min = freq_max;
		}
	}

	//gpu_boost_level = 0;

	//set target frequency
	if (*target_freq < (unsigned long)freq_min->freq*FREQ_KHZ)
	{
		*target_freq = (unsigned long)freq_min->freq*FREQ_KHZ;
		modify_flag = 1;
	}
	if (*target_freq > (unsigned long)freq_max->freq*FREQ_KHZ)
	{
		*target_freq = (unsigned long)freq_max->freq*FREQ_KHZ;
		modify_flag = 1;
	}

	if (1 == modify_flag)
	{
		printk(KERN_ERR "GPU_DVFS %s gpu_boost_level:%d min_freq=%dMHz max_freq=%dMHz target_freq=%dMHz \n",
			__func__, gpu_boost_level, freq_min->freq / FREQ_KHZ, freq_max->freq / FREQ_KHZ, *target_freq / (FREQ_KHZ * FREQ_KHZ));
	}
}
#endif
#endif

#ifdef CONFIG_MALI_BOOST

int boost_tgid = 0;
int boost_pid = 0;

void kbase_platform_set_boost(struct kbase_device *kbdev, struct kbase_context *kctx, int boost_level)
{
	if (boost_level == 0 || boost_level == 10)
	{
		if (boost_level == 0 && kctx->tgid == boost_tgid && kctx->pid != boost_pid)
		{
			printk(KERN_INFO "GPU_DVFS %s boost_level = %d, gpu_boost_level = %d, tgid = %d, pid = %d, previous tgid = %d, previous pid = %d",
					__func__, boost_level, gpu_boost_level, kctx->tgid, kctx->pid, boost_tgid, boost_pid);
			return;
		}
		gpu_boost_level = boost_level;
		//printk(KERN_INFO "GPU_DVFS %s gpu_boost_level =%d \n", __func__, gpu_boost_level);
	}
	boost_tgid = kctx->tgid;
	boost_pid = kctx->pid;
}
#endif

#ifdef QOGIRL6_GPU_POLLING
static u32 pd_gpu_co_state;
static u32 PD_GPU_CO_STATE_MASK = 0x1F000000;
static u32 GPU_CO_STATE_ACTIVE = 0x08000000;
static u32 cur_st_st0;
static u32 CUR_ST_ST0_MASK = 0X000000F0;
static u32 CUR_ST_ST0_READY = 0x00000070;
static u32 SHADER_PWRTRANS_LO1,SHADER_PWRTRANS_HI1;
static u32 SHADER_READY_LO1,SHADER_READY_HI1;
extern u32 kbase_reg_read(struct kbase_device *kbdev, u32 offset);

bool is_shader_ready(struct kbase_device *kbdev)
{
	//PMU APB
	//printk(KERN_ERR "SPRDDEBUG read pd_gpu_co_state\n");
	//regmap_read(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core0_state_reg.args[0], &pd_gpu_co_state);
	//pd_gpu_co_state = pd_gpu_co_state & PD_GPU_CO_STATE_MASK;
	pd_gpu_co_state = GPU_CO_STATE_ACTIVE & PD_GPU_CO_STATE_MASK;

	//GPU APB
	//printk(KERN_ERR "SPRDDEBUG read cur_st_st0\n");
	//regmap_read(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st0_reg.args[0], &cur_st_st0);
	//cur_st_st0 = cur_st_st0 & CUR_ST_ST0_MASK;
	cur_st_st0 = CUR_ST_ST0_READY & CUR_ST_ST0_MASK;

	//printk(KERN_ERR "SPRDDEBUG read SHADER_READY\n");
	SHADER_READY_LO1 = kbase_reg_read(kbdev,GPU_CONTROL_REG(SHADER_READY_LO));
	SHADER_READY_HI1 = kbase_reg_read(kbdev,GPU_CONTROL_REG(SHADER_READY_HI));

	//printk(KERN_ERR "SPRDDEBUG read SHADER_PWRTRANS\n");
	SHADER_PWRTRANS_LO1 = kbase_reg_read(kbdev,GPU_CONTROL_REG(SHADER_PWRTRANS_LO));
	SHADER_PWRTRANS_HI1 = kbase_reg_read(kbdev,GPU_CONTROL_REG(SHADER_PWRTRANS_HI));

	return pd_gpu_co_state == GPU_CO_STATE_ACTIVE && cur_st_st0 == CUR_ST_ST0_READY && SHADER_READY_LO1 == 1 &&
		SHADER_READY_HI1 == 0 && SHADER_PWRTRANS_LO1 == 0 && SHADER_PWRTRANS_HI1 == 0;
}

void gpu_polling_power_on(struct kbase_device *kbdev)
{
	int poll=0,timeout=0;

	while(1){
		udelay(5);
		if(is_shader_ready(kbdev)){
			poll++;
			while(poll < 5){
				if(is_shader_ready(kbdev)){
					poll++;
				}else {
					poll = 0;
					break;
				}
			}
			if(poll>=5){//success
				break;
			}
		}

		if(timeout++>2000 && (!is_shader_ready(kbdev))){//10ms
			printk(KERN_ERR "SPRDDEBUG gpu core power on TIMEOUT !!");
			printk(KERN_ERR "SPRDDEBUG pd_gpu_co_state = 0x%x,cur_st_st0 = 0x%x,SHADER_READY_LO1 = 0x%x,SHADER_READY_HI1 = 0x%x,SHADER_PWRTRANS_LO1 = 0x%x,SHADER_PWRTRANS_HI1 = 0x%x, timeout = %d\n ",
				pd_gpu_co_state,cur_st_st0,SHADER_READY_LO1,SHADER_READY_HI1,SHADER_PWRTRANS_LO1,SHADER_PWRTRANS_HI1, timeout);
			WARN_ON(1);
			break;
		}
	}
}
#endif

//fake function for N6pro
int kbase_platform_set_DVFS_table(struct kbase_device *kbdev)
{
	return 0;
}