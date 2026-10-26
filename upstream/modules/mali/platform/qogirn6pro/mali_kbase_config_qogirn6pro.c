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

#include <linux/smp.h>
#include <trace/events/power.h>
#include <linux/nvmem-consumer.h>
#include <linux/thermal.h>
#include <linux/reset.h>
#include <gpu,qogirn6pro-regs.h>
#include <gpu,qogirn6pro-mask.h>
#include "mali_kbase_gpu_sys_qogirn6pro_qos.h"

#define MOVE_BIT_LEFT(x,n) ((unsigned long)x << n)

#define VENDOR_FTRACE_MODULE_NAME    "unisoc-gpu"

#define DTS_CLK_OFFSET      1
#define PM_RUNTIME_DELAY_MS 50
#define UP_THRESHOLD        9/10
#define FREQ_KHZ            1000
#define VOLT_KMV            1000
#define GPU_MIN_FREQ_INDEX  3

#define GPU_26M_FREQ        26000000
#define GPU_76M8_FREQ       76800000
#define GPU_153M6_FREQ      153600000

#define T770_GPLL_780M      780000000
#define T770_GPLL_686M      686400000
#define T760_GPLL_655M      655200000

#define GPU_680M_FREQ       680000000
#define GPU_850M_FREQ       850000000

#define VOLT_650mV 650000
#define VOLT_700mV 700000
#define VOLT_750mV 750000
#define VOLT_800mV 800000
unsigned long gpll_T770_1950M, gpll_T770_1716M, gpll_T760_1638M;

#if 0
struct gpu_qos_config {
	u8 arqos;
	u8 awqos;
	u8 arqos_threshold;
	u8 awqos_threshold;
};
#endif

struct gpu_freq_info {
	struct clk* clk_src;
	int freq;	//kHz
	int volt;	//uV
	int dvfs_index;
};

struct gpu_reg_info {
	struct regmap* regmap_ptr;
	uint32_t args[2];
};

struct gpu_dvfs_context {
	atomic_t gpu_clock_state;
	atomic_t gpu_power_state;
	atomic_t dcdc_gpu_state;
	atomic_t dcdc_gpu_poweron_in_progress;

	int cur_voltage;

	struct clk*  clk_gpu_i;
	struct clk** gpu_clk_src;
	int gpu_clk_num;

	struct gpu_freq_info* freq_list;
	int freq_list_len;

	int cur_index;
	const struct gpu_freq_info* freq_cur;
	const struct gpu_freq_info* freq_default;

	struct semaphore* sem;

	struct gpu_reg_info top_dvfs_cfg_reg;
	struct gpu_reg_info gpu_sw_dvfs_ctrl_reg;
	struct gpu_reg_info top_force_reg;
	struct gpu_reg_info gpu_top_state_reg;
	struct gpu_reg_info gpu_core0_state_reg;
	struct gpu_reg_info gpu_core1_state_reg;
	struct gpu_reg_info gpu_core2_state_reg;
	struct gpu_reg_info gpu_core3_state_reg;
	struct gpu_reg_info clk_core_gpu_eb_reg;
	struct gpu_reg_info cur_st_st0_reg;
	struct gpu_reg_info cur_st_st1_reg;
	struct gpu_reg_info cur_st_st2_reg;
	struct gpu_reg_info cur_st_st3_reg;
	struct gpu_reg_info dvfs_voltage_dbg0;
	struct gpu_reg_info gpll_cfg_force_off_reg;
	struct gpu_reg_info gpll_cfg_force_on_reg;
	struct gpu_reg_info dcdc_gpu_voltage0;
	struct gpu_reg_info dcdc_gpu_voltage1;
	struct gpu_reg_info dcdc_gpu_voltage2;
	struct gpu_reg_info dcdc_gpu_voltage3;
#if 0
	struct gpu_reg_info gpu_qos_sel;
	struct gpu_reg_info gpu_qos;
#endif
	struct gpu_reg_info dvfs_index_cfg;
	struct gpu_reg_info sw_dvfs_ctrl;
	struct gpu_reg_info freq_upd_cfg;
	struct gpu_reg_info core_index0_map;
	struct gpu_reg_info core_index1_map;
	struct gpu_reg_info core_index2_map;
	struct gpu_reg_info core_index3_map;
	struct gpu_reg_info core_index4_map;
	struct gpu_reg_info core_index5_map;
	struct gpu_reg_info core_index6_map;
	struct gpu_reg_info core_index6_map_sel;
	struct gpu_reg_info core_index7_map;

	struct regmap* gpu_apb_base_ptr;
	struct regmap* gpu_dvfs_apb_base_ptr;

	struct reset_control* gpu_soft_rst;

	struct regulator *gpu_reg_ptr;

	const char* auto_efuse;
	u32 gpu_binning;

	int last_gpu_temperature;
	int gpu_temperature;
};

#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 4, 0)

/* MU300: ioremap_nocache() is gone - plain ioremap() has been non-cached since 5.6 */
#if LINUX_VERSION_CODE >= KERNEL_VERSION(5, 6, 0)
#define mu300_ioremap_nocache(a, s) ioremap((a), (s))
#else
#define mu300_ioremap_nocache(a, s) ioremap_nocache((a), (s))
#endif

DEFINE_SEMAPHORE(gpu_dfs_sem, 1);	/* MU300: the count is explicit now */
#else
DEFINE_SEMAPHORE(gpu_dfs_sem);
#endif
static struct gpu_dvfs_context gpu_dvfs_ctx=
{
	.last_gpu_temperature = 0,
	.gpu_temperature = 0,
	.sem = &gpu_dfs_sem,
};

#if 0
static struct gpu_qos_config gpu_qos_cfg=
{
	.arqos = 0,
	.awqos = 0,
	.arqos_threshold = 0,
	.awqos_threshold = 0,
};
#endif

int gpu_boost_level = 0;
void __iomem *mali_qos_reg_base_mtx_m0;
void __iomem *mali_qos_reg_base_mtx_m1;
void __iomem *mali_qos_reg_base_apb_rf;
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
	//struct device_node *qos_node = NULL;
	struct device_node *hwf;

	gpu_dvfs_ctx.gpu_reg_ptr = devm_regulator_get(dev, "gpu");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_reg_ptr);

	gpu_dvfs_ctx.gpu_soft_rst = devm_reset_control_get(dev, "gpu_soft_rst");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_soft_rst);

	gpu_dvfs_ctx.top_dvfs_cfg_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"top_dvfs_cfg", 2, (uint32_t *)gpu_dvfs_ctx.top_dvfs_cfg_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.top_dvfs_cfg_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_sw_dvfs_ctrl", 2, (uint32_t *)gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.regmap_ptr);

	gpu_dvfs_ctx.dcdc_gpu_voltage0.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"dcdc_gpu_voltage0", 2, (uint32_t *)gpu_dvfs_ctx.dcdc_gpu_voltage0.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.dcdc_gpu_voltage0.regmap_ptr);

	gpu_dvfs_ctx.dcdc_gpu_voltage1.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"dcdc_gpu_voltage1", 2, (uint32_t *)gpu_dvfs_ctx.dcdc_gpu_voltage1.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.dcdc_gpu_voltage1.regmap_ptr);

	gpu_dvfs_ctx.dcdc_gpu_voltage2.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"dcdc_gpu_voltage2", 2, (uint32_t *)gpu_dvfs_ctx.dcdc_gpu_voltage2.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.dcdc_gpu_voltage2.regmap_ptr);

	gpu_dvfs_ctx.dcdc_gpu_voltage3.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"dcdc_gpu_voltage3", 2, (uint32_t *)gpu_dvfs_ctx.dcdc_gpu_voltage3.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.dcdc_gpu_voltage3.regmap_ptr);

	//enable gpu hw dvfs
	regmap_update_bits(gpu_dvfs_ctx.top_dvfs_cfg_reg.regmap_ptr, gpu_dvfs_ctx.top_dvfs_cfg_reg.args[0], gpu_dvfs_ctx.top_dvfs_cfg_reg.args[1], ~gpu_dvfs_ctx.top_dvfs_cfg_reg.args[1]);
	regmap_update_bits(gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.regmap_ptr, gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.args[0], gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.args[1], ~gpu_dvfs_ctx.gpu_sw_dvfs_ctrl_reg.args[1]);

	gpu_dvfs_ctx.top_force_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"top_force_shutdown", 2, (uint32_t *)gpu_dvfs_ctx.top_force_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.top_force_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_top_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_top_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_core0_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_core0_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_core1_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_core1_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_core1_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_core1_state_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_core2_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_core2_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_core2_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_core2_state_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_core3_state_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpu_core3_state", 2, (uint32_t *)gpu_dvfs_ctx.gpu_core3_state_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_core3_state_reg.regmap_ptr);

	gpu_dvfs_ctx.clk_core_gpu_eb_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"clk_core_gpu_eb", 2, (uint32_t *)gpu_dvfs_ctx.clk_core_gpu_eb_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_core_gpu_eb_reg.regmap_ptr);

	gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"cur_st_st0", 2, (uint32_t *)gpu_dvfs_ctx.cur_st_st0_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr);

	gpu_dvfs_ctx.cur_st_st1_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"cur_st_st1", 2, (uint32_t *)gpu_dvfs_ctx.cur_st_st1_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.cur_st_st1_reg.regmap_ptr);

	gpu_dvfs_ctx.cur_st_st2_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"cur_st_st2", 2, (uint32_t *)gpu_dvfs_ctx.cur_st_st2_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.cur_st_st2_reg.regmap_ptr);

	gpu_dvfs_ctx.cur_st_st3_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"cur_st_st3", 2, (uint32_t *)gpu_dvfs_ctx.cur_st_st3_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.cur_st_st3_reg.regmap_ptr);

	gpu_dvfs_ctx.gpll_cfg_force_off_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpll_cfg_frc_off", 2, (uint32_t *)gpu_dvfs_ctx.gpll_cfg_force_off_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpll_cfg_force_off_reg.regmap_ptr);

	gpu_dvfs_ctx.gpll_cfg_force_on_reg.regmap_ptr = syscon_regmap_lookup_by_phandle_args(dev->of_node,"gpll_cfg_frc_on", 2, (uint32_t *)gpu_dvfs_ctx.gpll_cfg_force_on_reg.args);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpll_cfg_force_on_reg.regmap_ptr);

	gpu_dvfs_ctx.gpu_apb_base_ptr = syscon_regmap_lookup_by_phandle(dev->of_node, "sprd,gpu-apb-syscon");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_apb_base_ptr);
	gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr = syscon_regmap_lookup_by_phandle(dev->of_node, "sprd,gpu-dvfs-apb-syscon");
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr);

#if 0
	//qos
	gpu_dvfs_ctx.gpu_qos_sel.regmap_ptr = gpu_dvfs_ctx.gpu_apb_base_ptr;
	gpu_dvfs_ctx.gpu_qos_sel.args[0] = REG_GPU_APB_RF_GPU_NIC400_QOS;
	gpu_dvfs_ctx.gpu_qos_sel.args[1] = MASK_GPU_APB_RF_GPU_QOS_SEL;

	gpu_dvfs_ctx.gpu_qos.regmap_ptr = gpu_dvfs_ctx.gpu_apb_base_ptr;
	gpu_dvfs_ctx.gpu_qos.args[0] = REG_GPU_APB_RF_GPU_NIC400_QOS;
	gpu_dvfs_ctx.gpu_qos.args[1] = MASK_GPU_APB_RF_AWQOS_THRESHOLD_GPU | MASK_GPU_APB_RF_ARQOS_THRESHOLD_GPU | MASK_GPU_APB_RF_AWQOS_GPU | MASK_GPU_APB_RF_ARQOS_GPU;

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
#endif

	//gpu index cfg
	gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.dvfs_index_cfg.args[0] = REG_GPU_DVFS_APB_RF_GPU_DVFS_INDEX_CFG;
	gpu_dvfs_ctx.dvfs_index_cfg.args[1] = MASK_GPU_DVFS_APB_RF_GPU_DVFS_INDEX;

	//gpu_dvfs_voltage_dbg0
	gpu_dvfs_ctx.dvfs_voltage_dbg0.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.dvfs_voltage_dbg0.args[0] = REG_GPU_DVFS_APB_RF_GPU_DVFS_VOLTAE_DBG0;
	gpu_dvfs_ctx.dvfs_voltage_dbg0.args[1] = MASK_GPU_DVFS_APB_RF_GPU_DVFS_VOLTAGE_SW;

	//sw dvfs ctrl
	gpu_dvfs_ctx.sw_dvfs_ctrl.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.sw_dvfs_ctrl.args[0] = REG_GPU_DVFS_APB_RF_GPU_SW_DVFS_CTRL;
	gpu_dvfs_ctx.sw_dvfs_ctrl.args[1] = MASK_GPU_DVFS_APB_RF_GPU_DVFS_ACK | MASK_GPU_DVFS_APB_RF_GPU_DVFS_VOLTAGE_SW | MASK_GPU_DVFS_APB_RF_GPU_DVFS_REQ_SW;

	//freq update cfg
	gpu_dvfs_ctx.freq_upd_cfg.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.freq_upd_cfg.args[0] = REG_GPU_DVFS_APB_RF_GPU_FREQ_UPD_TYPE_CFG0;
	gpu_dvfs_ctx.freq_upd_cfg.args[1] = MASK_GPU_DVFS_APB_RF_GPU_FREQ_UPD_HDSK_EN | MASK_GPU_DVFS_APB_RF_GPU_FREQ_UPD_DELAY_EN;

	//core index0 map
	gpu_dvfs_ctx.core_index0_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index0_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX0_MAP;
	gpu_dvfs_ctx.core_index0_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX0;

	//core index1 map
	gpu_dvfs_ctx.core_index1_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index1_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX1_MAP;
	gpu_dvfs_ctx.core_index1_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX1;

	//core index2 map
	gpu_dvfs_ctx.core_index2_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index2_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX2_MAP;
	gpu_dvfs_ctx.core_index2_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX2;

	//core index3 map
	gpu_dvfs_ctx.core_index3_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index3_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX3_MAP;
	gpu_dvfs_ctx.core_index3_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX3;

	//core index4 map
	gpu_dvfs_ctx.core_index4_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index4_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX4_MAP;
	gpu_dvfs_ctx.core_index4_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX4;

	//core index5 map
	gpu_dvfs_ctx.core_index5_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index5_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX5_MAP;
	gpu_dvfs_ctx.core_index5_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX5;

	//core index6 map
	gpu_dvfs_ctx.core_index6_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index6_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX6_MAP;
	gpu_dvfs_ctx.core_index6_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX6;

	//core index6 map sel
	gpu_dvfs_ctx.core_index6_map_sel.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index6_map_sel.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX6_MAP;
	gpu_dvfs_ctx.core_index6_map_sel.args[1] = MASK_GPU_DVFS_APB_RF_GPU_INDEX6_MAP_SEL;

	//core index7 map
	gpu_dvfs_ctx.core_index7_map.regmap_ptr = gpu_dvfs_ctx.gpu_dvfs_apb_base_ptr;
	gpu_dvfs_ctx.core_index7_map.args[0] = REG_GPU_DVFS_APB_RF_GPU_INDEX7_MAP;
	gpu_dvfs_ctx.core_index7_map.args[1] = MASK_GPU_DVFS_APB_RF_GPU_CORE_VOL_INDEX7;

	//read gpu_bin, 1:FF, 2:TT, 3:SS
	ret = sprd_gpu_cal_read(dev->of_node, "gpu_bin", &gpu_dvfs_ctx.gpu_binning);
	if (ret)
	{
		pr_warn("gpu binning read fail.\n");
	}

	gpu_dvfs_ctx.clk_gpu_i = of_clk_get(dev->of_node, 0);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.clk_gpu_i);

	clk_cnt = of_clk_get_parent_count(dev->of_node);
	gpu_dvfs_ctx.gpu_clk_num = clk_cnt - DTS_CLK_OFFSET;

	gpu_dvfs_ctx.gpu_clk_src = vmalloc(sizeof(struct clk*) * gpu_dvfs_ctx.gpu_clk_num);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_clk_src);

	for (i = 0; i < gpu_dvfs_ctx.gpu_clk_num; i++)
	{
		gpu_dvfs_ctx.gpu_clk_src[i] = of_clk_get(dev->of_node, i+DTS_CLK_OFFSET);
		KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.gpu_clk_src[i]);
	}

	gpu_dvfs_ctx.freq_list_len = of_property_count_elems_of_size(dev->of_node,"operating-points",2*sizeof(u32));
	gpu_dvfs_ctx.freq_list = vmalloc(sizeof(struct gpu_freq_info) * gpu_dvfs_ctx.freq_list_len);
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_list);

	for(i = 0; i < gpu_dvfs_ctx.freq_list_len; i++)
	{
		gpu_dvfs_ctx.freq_list[i].clk_src = gpu_dvfs_ctx.gpu_clk_src[i];
		KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_list[i].clk_src);
		of_property_read_u32_index(dev->of_node, "operating-points", 2*i,   &gpu_dvfs_ctx.freq_list[i].freq);
		of_property_read_u32_index(dev->of_node, "operating-points", 2*i+1, &gpu_dvfs_ctx.freq_list[i].volt);
		gpu_dvfs_ctx.freq_list[i].dvfs_index = i;
	}
	//adjust freq list
	hwf = of_find_node_by_path("/hwfeature/auto");
	if (hwf) {
		gpu_dvfs_ctx.auto_efuse = of_get_property(hwf, "efuse", NULL);
		pr_info ("find  in %s was %s\n", __func__, gpu_dvfs_ctx.auto_efuse);
	}

	//T760 table: 384M, 512M, 650M
	//T770 table: 384M, 512M, 680M, 780M
	//T790 table: 384M, 512M, 680M, 850M
	//default table: 384M, 512M, 680M, 850M
	if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T760"))
	{
		//modify 680M to 650M
		gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2].freq = T760_GPLL_655M / FREQ_KHZ;

		//remove 850M
		memset(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1], 0, sizeof(struct gpu_freq_info));

		//modify freq list len
		gpu_dvfs_ctx.freq_list_len = gpu_dvfs_ctx.freq_list_len -1;
		//compute the gpll freq
		gpll_T760_1638M = T760_GPLL_655M * 2.5;

	}
	else if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770"))
	{
		//copy 650M to 780M and remove 850M
		memcpy(&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1],
			&gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2],
			sizeof(struct gpu_freq_info));
		gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq = T770_GPLL_780M / FREQ_KHZ;
		//modify freq index
		gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len -1].dvfs_index = gpu_dvfs_ctx.freq_list_len -1;
		//compute the gpll freq
		gpll_T770_1950M = T770_GPLL_780M * 2.5;
		gpll_T770_1716M = T770_GPLL_686M * 2.5;
	}
	//modify dcdc_voltage
	//0.65v/3.125mv=208->0xD0
	regmap_update_bits(gpu_dvfs_ctx.dcdc_gpu_voltage0.regmap_ptr, gpu_dvfs_ctx.dcdc_gpu_voltage0.args[0], gpu_dvfs_ctx.dcdc_gpu_voltage0.args[1], 0xD0);
	//0.70v/3.125mv=224->0xE0
	regmap_update_bits(gpu_dvfs_ctx.dcdc_gpu_voltage1.regmap_ptr, gpu_dvfs_ctx.dcdc_gpu_voltage1.args[0], gpu_dvfs_ctx.dcdc_gpu_voltage1.args[1], 0xE0<<16);
	//0.75v/3.125mv=240->0xF0
	regmap_update_bits(gpu_dvfs_ctx.dcdc_gpu_voltage2.regmap_ptr, gpu_dvfs_ctx.dcdc_gpu_voltage2.args[0], gpu_dvfs_ctx.dcdc_gpu_voltage2.args[1], 0xF0);
	//0.80v/3.125mv=256->0x100
	regmap_update_bits(gpu_dvfs_ctx.dcdc_gpu_voltage3.regmap_ptr, gpu_dvfs_ctx.dcdc_gpu_voltage3.args[0], gpu_dvfs_ctx.dcdc_gpu_voltage3.args[1], 0x100<<16);

	of_property_read_u32(dev->of_node, "sprd,dvfs-default", &i);
	gpu_dvfs_ctx.freq_default = &gpu_dvfs_ctx.freq_list[i];
	KBASE_DEBUG_ASSERT(gpu_dvfs_ctx.freq_default);

	gpu_dvfs_ctx.cur_index = GPU_MIN_FREQ_INDEX;
	gpu_dvfs_ctx.freq_cur = &gpu_dvfs_ctx.freq_list[GPU_MIN_FREQ_INDEX];
	gpu_dvfs_ctx.cur_voltage = gpu_dvfs_ctx.freq_cur->volt;

	atomic_set(&gpu_dvfs_ctx.dcdc_gpu_state, 0);
	atomic_set(&gpu_dvfs_ctx.gpu_power_state, 0);
	atomic_set(&gpu_dvfs_ctx.gpu_clock_state, 0);
	atomic_set(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress, 0);
}
/* SPRD:gpu_qos_reg_map */
static void gpu_qos_reg_map(void)
{
       u32 qos_offset = 0x100;
       mali_qos_reg_base_mtx_m0 = mu300_ioremap_nocache(nic400_gpu_sys_mtx_m0_qos_list[0].base_addr,qos_offset);
       mali_qos_reg_base_mtx_m1 = mu300_ioremap_nocache(nic400_gpu_sys_mtx_m1_qos_list[0].base_addr,qos_offset);
       mali_qos_reg_base_apb_rf = mu300_ioremap_nocache(gpu_apb_rf_qos_list[0].base_addr,qos_offset);

}
/* SPRD:gpu_qos_reg_unmap */
static void gpu_qos_reg_unmap(void)
{
       iounmap(mali_qos_reg_base_mtx_m0);
       iounmap(mali_qos_reg_base_mtx_m1);
       iounmap(mali_qos_reg_base_apb_rf);
}

//PCIe访问DDR优先级-各个sys modules需要独立管理配置Qos,目前按照要求在各个模块内统一配置
//SPRD note:register 0x23018000 do not support set/clear features, so not use dts reg configuration
#if 1
static void mali_kbase_gpu_sys_qos_reg_rw(struct qos_reg gpu_qos_list_name[], int len)
{
	int i;
	u32 tmp;
        void __iomem *mali_qos_reg_base;

        if(gpu_qos_list_name[0].base_addr == nic400_gpu_sys_mtx_m0_qos_list[0].base_addr)
        {
          mali_qos_reg_base = mali_qos_reg_base_mtx_m0;
        }
        else if(gpu_qos_list_name[0].base_addr == nic400_gpu_sys_mtx_m1_qos_list[0].base_addr)
        {
          mali_qos_reg_base = mali_qos_reg_base_mtx_m1;
        }
        else
        {
          mali_qos_reg_base = mali_qos_reg_base_apb_rf;
        }


	len = len - 1;

	for (i = 0; i < len; i++) {
		tmp = readl_relaxed(mali_qos_reg_base +
				    (gpu_qos_list_name[i].base_addr -
				    gpu_qos_list_name[0].base_addr));
		tmp &= ~(gpu_qos_list_name[i].mask_value);
		tmp |= gpu_qos_list_name[i].set_value;
		writel_relaxed(tmp, mali_qos_reg_base +
			       (gpu_qos_list_name[i].base_addr -
			       gpu_qos_list_name[0].base_addr));
	}
}

static void mali_kbase_gpu_sys_qos_cfg(void)
{
	mali_kbase_gpu_sys_qos_reg_rw(nic400_gpu_sys_mtx_m0_qos_list,
				 sizeof(nic400_gpu_sys_mtx_m0_qos_list) /
				 sizeof(nic400_gpu_sys_mtx_m0_qos_list[0]));
	mali_kbase_gpu_sys_qos_reg_rw(nic400_gpu_sys_mtx_m1_qos_list,
				 sizeof(nic400_gpu_sys_mtx_m1_qos_list) /
				 sizeof(nic400_gpu_sys_mtx_m1_qos_list[0]));
	mali_kbase_gpu_sys_qos_reg_rw(gpu_apb_rf_qos_list,
				 sizeof(gpu_apb_rf_qos_list) /
				 sizeof(gpu_apb_rf_qos_list[0]));
}
#endif

static inline int mali_top_state_check(void)
{
	int cnt_1 = 0, cnt_2 = 0, ret = 0;
	u32 top_pwr = 0x1F;
	const u32 top_pwr_mask = MOVE_BIT_LEFT(0x1f,16);
	const u32 top_pwr_wakeup = MOVE_BIT_LEFT(0,16);

	while (1) {
		//check if the top_state ready or not
		regmap_read(gpu_dvfs_ctx.gpu_top_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_top_state_reg.args[0], &top_pwr);
		top_pwr = top_pwr & top_pwr_mask;

		if (top_pwr == top_pwr_wakeup) {
			if (++cnt_1 > 5)
				return ret;
		} else {
			udelay(50);
			cnt_1 = 0;

			if (cnt_2 % 20 == 0)
				printk(KERN_ERR "%s: gpu_top_pwr:0x%x, counter:%d\n", __func__, top_pwr, cnt_2);

			if (++cnt_2 > 200) {
				printk(KERN_ERR "%s: gpu top power on has timed out", __func__);
				WARN_ON(1);
				return -1;
			}
		}
		top_pwr = 0x1F;
	}

	return ret;
}

int  Get_CurState(char* buf,struct kbase_device *kbdev)
{
	int len = 0;
	int i = 0, FREQ_OFFSET = 3;
	int cur_state =0 ,cur_voltage =0;

	if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) && atomic_read(&gpu_dvfs_ctx.gpu_clock_state)) {
		regmap_read(gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr, gpu_dvfs_ctx.dvfs_index_cfg.args[0], &cur_state);
		cur_state = (int) (cur_state & MASK_GPU_DVFS_APB_RF_GPU_DVFS_INDEX);
		regmap_read(gpu_dvfs_ctx.dvfs_voltage_dbg0.regmap_ptr, gpu_dvfs_ctx.dvfs_voltage_dbg0.args[0], &cur_voltage);
		cur_voltage = (int)( cur_voltage & MASK_GPU_DVFS_APB_RF_GPU_DVFS_VOLTAGE_SW);
		len = sprintf(buf,"cur_state:%d, %6d kHz,cur_voltage: %d \n",cur_state, gpu_dvfs_ctx.freq_list[cur_state].freq,cur_voltage);
	} else {
		len = sprintf(buf,"GPU power is off now!\n");
	}

	for (i = 0; i < gpu_dvfs_ctx.freq_list_len - FREQ_OFFSET; i++) {
		len += sprintf(buf + len,"Available----freq:%d, voltage:%d, index:%d\n",
			gpu_dvfs_ctx.freq_list[i + FREQ_OFFSET].freq,
			gpu_dvfs_ctx.freq_list[i + FREQ_OFFSET].volt,
			gpu_dvfs_ctx.freq_list[i + FREQ_OFFSET].dvfs_index);
	}
	return len;
}

int kbase_platform_set_DVFS_table(struct kbase_device *kbdev)
{
	int ret = 0;
	//get gpu temperature
	ret = thermal_zone_get_temp(kbdev->gpu_tz, &gpu_dvfs_ctx.gpu_temperature);
	if (ret) {
		printk(KERN_ERR "%s:get gpu temperature fail, err:%d.\n", __func__, ret);
		return ret;
	}
	down(gpu_dvfs_ctx.sem);
	if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) && atomic_read(&gpu_dvfs_ctx.gpu_clock_state) && atomic_read(&gpu_dvfs_ctx.dcdc_gpu_state))
	{
		if ((gpu_dvfs_ctx.last_gpu_temperature <= 65000 && gpu_dvfs_ctx.gpu_temperature >= 65000) || (gpu_dvfs_ctx.last_gpu_temperature >= 65000 && gpu_dvfs_ctx.gpu_temperature <= 65000))
		{
			if (mali_top_state_check() != 0)
			{
				printk(KERN_ERR "mali GPU_set_DVFS_table failed.\n");
				up(gpu_dvfs_ctx.sem);
				return -1;
			}
			else
			{
				printk(KERN_ERR "mali GPU_set_DVFS_table %s gpu_power_on = %d gpu_clock_on = %d,gpu_temperature = %d, last_gpu_temperature = %d.\n",
						__func__, atomic_read(&gpu_dvfs_ctx.gpu_power_state), atomic_read(&gpu_dvfs_ctx.gpu_clock_state), gpu_dvfs_ctx.gpu_temperature, gpu_dvfs_ctx.last_gpu_temperature);
			}
			if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770"))
			{
				//T770: GPU max freq is 780M, the corresponding GPLL freq is 1950M
				//modify index6_map sel from 6(gpll_850M) to 5(clk_gpll)
				//T770-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-1(0.70v)
				//T770-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-2(0.75v) 65c
				if (1 == gpu_dvfs_ctx.gpu_binning)
				{
					if (gpu_dvfs_ctx.gpu_temperature < 65000)
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 1 << 6);
						gpu_dvfs_ctx.freq_list[6].volt = VOLT_700mV;
					}
					else
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 2 << 6);
						gpu_dvfs_ctx.freq_list[6].volt = VOLT_750mV;
					}
				}
				//T770-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-2(0.75v)
				//T770-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-2(0.75v), index6-780M-2(0.75v) 65c
				else if (2 == gpu_dvfs_ctx.gpu_binning)
				{
					if (gpu_dvfs_ctx.gpu_temperature < 65000)
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
					}
					else
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
					}
				}
				//T770-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-2(0.75v), index6-780M-3(0.80v)
				//T770-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-3(0.80v), index6-780M-3(0.80v) 65C
				else if (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
				{
					if (gpu_dvfs_ctx.gpu_temperature < 65000)
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
					}
					else
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_800mV;
					}
				}
			}
			else if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T790") || !strcmp(gpu_dvfs_ctx.auto_efuse, "UTS6560S"))
			{
				//T790-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-2(0.75v)
				//T790-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-2(0.75v) 65c
				if (1 == gpu_dvfs_ctx.gpu_binning)
                                {}
				//T790-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-3(0.80v)
				//T790-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-2(0.75v), index6-850M-3(0.80v) 65c
				else if (2 == gpu_dvfs_ctx.gpu_binning)
				{
					if (gpu_dvfs_ctx.gpu_temperature < 65000)
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
					}
					else
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
					}
				}
				//T790-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-2(0.75v), index6-850M-3(0.80v)
				//T790-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-3(0.80v), index6-680M-3(0.80v) 65c
				else if (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
				{
					if (gpu_dvfs_ctx.gpu_temperature < 65000)
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
					}
					else
					{
						regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3 << 6);
						gpu_dvfs_ctx.freq_list[5].volt = VOLT_800mV;
					}
				}
			}
			gpu_dvfs_ctx.last_gpu_temperature = gpu_dvfs_ctx.gpu_temperature;
		}
	}
	up(gpu_dvfs_ctx.sem);
	return 0;
}

/*gpu_soft_reset function*/
static void gpu_reset_assert(struct reset_control* rst_ctrl)
{
	int ret = 0;
	ret = reset_control_assert(rst_ctrl);
	if (ret)
		printk(KERN_ERR "GPU_RESET %s error = %d\n", __func__, ret);

}

static void gpu_reset_deassert(struct reset_control* rst_ctrl)
{
	int ret = 0;
	ret = reset_control_deassert(rst_ctrl);
	if (ret)
		printk(KERN_ERR "GPU_RESET %s error = %d\n", __func__, ret);

}

void mali_poweron_clear_flag(struct kbase_device *kbdev)
{

	down(gpu_dvfs_ctx.sem);
	if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) == 1 || kbase_pm_is_suspending(kbdev))
		atomic_dec(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress);
	else
		dump_stack();
	up(gpu_dvfs_ctx.sem);

}

void mali_poweron_first_part(void)
{
	int ret = 0;
	down(gpu_dvfs_ctx.sem);
	if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) == 0) {
		//gpu regulator enable
		if (!regulator_is_enabled(gpu_dvfs_ctx.gpu_reg_ptr)) {
			ret = regulator_enable(gpu_dvfs_ctx.gpu_reg_ptr);
			if (ret)
				printk(KERN_ERR "%s failed to enable vddgpu, error:%d\n", __func__, ret);
			udelay(10);
			atomic_inc(&gpu_dvfs_ctx.dcdc_gpu_state);
		}
	}
	atomic_inc(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress);

	up(gpu_dvfs_ctx.sem);
}

static inline void mali_power_on(void)
{
	//gpll force off: 0:not force off; 1:force off
	regmap_update_bits(gpu_dvfs_ctx.gpll_cfg_force_off_reg.regmap_ptr, gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[0], gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[1], ~gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[1]);
	udelay(100);
	//gpll force on: 0:not force on; 1:force on
	regmap_update_bits(gpu_dvfs_ctx.gpll_cfg_force_on_reg.regmap_ptr, gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[0], gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[1], gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[1]);
	udelay(150);

	if (atomic_read(&gpu_dvfs_ctx.dcdc_gpu_state) == 0) {
		printk(KERN_ERR "%s vddgpu is not ready! in_progress:%d gpu_power_state:%d\n",
				__func__, atomic_read(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress), atomic_read(&gpu_dvfs_ctx.gpu_power_state));
		WARN_ON(1);
	}

	regmap_update_bits(gpu_dvfs_ctx.top_force_reg.regmap_ptr, gpu_dvfs_ctx.top_force_reg.args[0], gpu_dvfs_ctx.top_force_reg.args[1], ~gpu_dvfs_ctx.top_force_reg.args[1]);

	//reset gpu sys
	gpu_reset_assert(gpu_dvfs_ctx.gpu_soft_rst);
	udelay(10);
	gpu_reset_deassert(gpu_dvfs_ctx.gpu_soft_rst);
	udelay(300);
	//check if the top_state ready or not
	mali_top_state_check();

	atomic_inc(&gpu_dvfs_ctx.gpu_power_state);
}


static inline void mali_power_off(void)
{
	atomic_dec(&gpu_dvfs_ctx.gpu_power_state);

	regmap_update_bits(gpu_dvfs_ctx.top_force_reg.regmap_ptr, gpu_dvfs_ctx.top_force_reg.args[0], gpu_dvfs_ctx.top_force_reg.args[1], gpu_dvfs_ctx.top_force_reg.args[1]);
	udelay(50);
	//gpll force off: 0:not force off; 1:force off
	regmap_update_bits(gpu_dvfs_ctx.gpll_cfg_force_off_reg.regmap_ptr, gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[0], gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[1], gpu_dvfs_ctx.gpll_cfg_force_off_reg.args[1]);
	//gpll force on: 0:not force on; 1:force on
	regmap_update_bits(gpu_dvfs_ctx.gpll_cfg_force_on_reg.regmap_ptr, gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[0], gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[1], ~gpu_dvfs_ctx.gpll_cfg_force_on_reg.args[1]);
}

void mali_poweroff_second_part(struct kbase_device *kbdev)
{
	int ret = 0;
	bool is_suspending = false;

	mutex_lock(&kbdev->pm.lock);
		is_suspending = kbdev->pm.suspending;
	mutex_unlock(&kbdev->pm.lock);

	down(gpu_dvfs_ctx.sem);
	if (is_suspending) {
		if (atomic_read(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress) > 0) {
			WARN_ON(1);
			atomic_set(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress, 0);
		}
	}

	if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) == 0 &&
		atomic_read(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress) == 0) {
		//gpu regulator disable
		if (regulator_is_enabled(gpu_dvfs_ctx.gpu_reg_ptr)) {
			ret = regulator_disable(gpu_dvfs_ctx.gpu_reg_ptr);
			if (ret)
				printk(KERN_ERR "%s failed to disable vddgpu, error:%d\n", __func__, ret);
			udelay(10);
			atomic_dec(&gpu_dvfs_ctx.dcdc_gpu_state);
		}
	}
	up(gpu_dvfs_ctx.sem);
}

#if 0
static void maliQosConfig(void)
{
	regmap_update_bits(gpu_dvfs_ctx.gpu_qos_sel.regmap_ptr, gpu_dvfs_ctx.gpu_qos_sel.args[0], gpu_dvfs_ctx.gpu_qos_sel.args[1], gpu_dvfs_ctx.gpu_qos_sel.args[1]);
	regmap_update_bits(gpu_dvfs_ctx.gpu_qos.regmap_ptr, gpu_dvfs_ctx.gpu_qos.args[0], gpu_dvfs_ctx.gpu_qos.args[1], ((gpu_qos_cfg.awqos_threshold << 12) | (gpu_qos_cfg.arqos_threshold << 8) | (gpu_qos_cfg.awqos << 4) | gpu_qos_cfg.arqos));
}
#endif

static inline void mali_clock_on(void)
{
	int i;
	static int clock_on_count = 0;

	//enable all clocks
	for(i = 0; i < gpu_dvfs_ctx.gpu_clk_num; i++)
	{
		clk_prepare_enable(gpu_dvfs_ctx.gpu_clk_src[i]);
	}
	clk_prepare_enable(gpu_dvfs_ctx.clk_gpu_i);

	//enable gpu clock
	regmap_update_bits(gpu_dvfs_ctx.clk_core_gpu_eb_reg.regmap_ptr, gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[0],
						gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[1], gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[1]);
	udelay(400);
	//check if the top_state ready or not
	mali_top_state_check();

	//set core index map and clk rate
	//GPU_INDEX_MAP [9:6]default index0-0, index1-0, index2-0, index3-1, index4-2, index5-3,index6-4;
	if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T760") || !strcmp(gpu_dvfs_ctx.auto_efuse, "UTS6560"))
	{
		//T760: GPU max freq is 650M, the corresponding GPLL freq is 1638M
		clk_set_rate(gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].clk_src, gpll_T760_1638M);
		//T760-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-650M-0(0.65v)
		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[5].volt = VOLT_650mV;
		}
		//T760-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-650M-1(0.70v)
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
		}
		//T760-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-650M-2(0.75v)
		else if (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_700mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_750mV;
		}
	}
	else if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770"))
	{
		//T770: GPU max freq is 780M, the corresponding GPLL freq is 1950M
		//modify index6_map sel from 6(gpll_850M) to 5(clk_gpll)
		regmap_update_bits(gpu_dvfs_ctx.core_index6_map_sel.regmap_ptr, gpu_dvfs_ctx.core_index6_map_sel.args[0], gpu_dvfs_ctx.core_index6_map_sel.args[1], 0x5);
		//T770-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-1(0.70v)
		//T770-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-2(0.75v) 65c
		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
			if (gpu_dvfs_ctx.gpu_temperature < 65000)
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 1 << 6);
				gpu_dvfs_ctx.freq_list[6].volt = VOLT_700mV;
			}
			else
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 2 << 6);
				gpu_dvfs_ctx.freq_list[6].volt = VOLT_750mV;
			}
		}
		//T770-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-780M-2(0.75v)
		//T770-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-2(0.75v), index6-780M-2(0.75v) 65c
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			if (gpu_dvfs_ctx.gpu_temperature < 65000)
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
			}
			else
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
			}
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 2 << 6);
			gpu_dvfs_ctx.freq_list[6].volt = VOLT_750mV;
		}
		//T770-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-2(0.75v), index6-780M-3(0.80v)
		//T770-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-3(0.80v), index6-780M-3(0.80v) 65C
		else if (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_700mV;
			if (gpu_dvfs_ctx.gpu_temperature < 65000)
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
			}
			else
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_800mV;
			}
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 3 << 6);
			gpu_dvfs_ctx.freq_list[6].volt = VOLT_800mV;
		}
	}
	else if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T790") || !strcmp(gpu_dvfs_ctx.auto_efuse, "UTS6560S"))
	{
		//T790-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-2(0.75v)
		//T790-BIN1 FF index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-2(0.75v) 65c
		if (1 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 2 << 6);
			gpu_dvfs_ctx.freq_list[6].volt = VOLT_750mV;
		}
		//T790-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-1(0.70v), index6-850M-3(0.80v)
		//T790-BIN2 TT index3-384M-0(0.65v), index4-512M-0(0.65v), index5-680M-2(0.75v), index6-850M-3(0.80v) 65c
		else if (2 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_650mV;
			if (gpu_dvfs_ctx.gpu_temperature < 65000)
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 1 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_700mV;
			}
			else
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
			}
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 3 << 6);
			gpu_dvfs_ctx.freq_list[6].volt = VOLT_800mV;
		}
		//T790-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-2(0.75v), index6-850M-3(0.80v)
		//T790-BIN3 SS index3-384M-0(0.65v), index4-512M-1(0.70v), index5-680M-3(0.80v), index6-680M-3(0.80v) 65c
		else if (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
		{
			regmap_update_bits(gpu_dvfs_ctx.core_index3_map.regmap_ptr, gpu_dvfs_ctx.core_index3_map.args[0], gpu_dvfs_ctx.core_index3_map.args[1], 0 << 6);
			gpu_dvfs_ctx.freq_list[3].volt = VOLT_650mV;
			regmap_update_bits(gpu_dvfs_ctx.core_index4_map.regmap_ptr, gpu_dvfs_ctx.core_index4_map.args[0], gpu_dvfs_ctx.core_index4_map.args[1], 1 << 6);
			gpu_dvfs_ctx.freq_list[4].volt = VOLT_700mV;
			if (gpu_dvfs_ctx.gpu_temperature < 65000)
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 2 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_750mV;
			}
			else
			{
				regmap_update_bits(gpu_dvfs_ctx.core_index5_map.regmap_ptr, gpu_dvfs_ctx.core_index5_map.args[0], gpu_dvfs_ctx.core_index5_map.args[1], 3 << 6);
				gpu_dvfs_ctx.freq_list[5].volt = VOLT_800mV;
			}
			regmap_update_bits(gpu_dvfs_ctx.core_index6_map.regmap_ptr, gpu_dvfs_ctx.core_index6_map.args[0], gpu_dvfs_ctx.core_index6_map.args[1], 3 << 6);
			gpu_dvfs_ctx.freq_list[6].volt = VOLT_800mV;
		}
	}

	//update freq cfg
	regmap_update_bits(gpu_dvfs_ctx.freq_upd_cfg.regmap_ptr, gpu_dvfs_ctx.freq_upd_cfg.args[0], gpu_dvfs_ctx.freq_upd_cfg.args[1], 1);

	//init freq
	regmap_update_bits(gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr, gpu_dvfs_ctx.dvfs_index_cfg.args[0], gpu_dvfs_ctx.dvfs_index_cfg.args[1], gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.cur_index].dvfs_index);

#if 0
	//qos
	maliQosConfig();
#endif
#if 1
        /* SPRD:qos_reg_map */
        if(clock_on_count == 0)
        {
        gpu_qos_reg_map();
	clock_on_count++;
        }
        //SPRD:PCIe Qos Config gpu_sys_qos_config
        mali_kbase_gpu_sys_qos_cfg();
#endif
	udelay(200);
	atomic_inc(&gpu_dvfs_ctx.gpu_clock_state);
}

static inline void mali_clock_off(void)
{
	int i;

	atomic_dec(&gpu_dvfs_ctx.gpu_clock_state);

	//disable gpu clock
	regmap_update_bits(gpu_dvfs_ctx.clk_core_gpu_eb_reg.regmap_ptr, gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[0],
						gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[1], ~gpu_dvfs_ctx.clk_core_gpu_eb_reg.args[1]);
	clk_disable_unprepare(gpu_dvfs_ctx.clk_gpu_i);

	//disable all clocks
	for(i = 0; i < gpu_dvfs_ctx.gpu_clk_num; i++)
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

	mali_poweron_first_part();

	mali_power_on();
	atomic_dec(&gpu_dvfs_ctx.dcdc_gpu_poweron_in_progress);
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

	mali_poweroff_second_part(kbdev);

#ifdef CONFIG_MALI_DEVFREQ
	kbase_pm_statistics_FreqDeinit(kbdev);
	DeinitFreqStats(kbdev);
#endif
        /* SPRD:gpu_qos_unmap */
        gpu_qos_reg_unmap();
	//free
	vfree(gpu_dvfs_ctx.freq_list);
	vfree(gpu_dvfs_ctx.gpu_clk_src);

	up(gpu_dvfs_ctx.sem);
}

struct kbase_platform_funcs_conf platform_qogirn6pro_funcs = {
	.platform_init_func = mali_platform_init,
	.platform_term_func = mali_platform_term
};

static void mali_power_mode_change(struct kbase_device *kbdev, int power_mode)
{
	down(gpu_dvfs_ctx.sem);
	//dev_info(kbdev->dev, "mali_power_mode_change: %d, gpu_power_on=%d gpu_clock_on=%d",power_mode,atomic_read(&gpu_dvfs_ctx.gpu_power_state),atomic_read(&gpu_dvfs_ctx.gpu_clock_state));
	switch (power_mode)
	{
		case 0://power on
			if (!atomic_read(&gpu_dvfs_ctx.gpu_power_state))
			{
				mali_power_on();
				mali_clock_on();
			}

			if (!atomic_read(&gpu_dvfs_ctx.gpu_clock_state))
			{
				mali_clock_on();
			}
			break;

		case 1://light sleep
		case 2://deep sleep
			if(atomic_read(&gpu_dvfs_ctx.gpu_clock_state))
			{
				mali_clock_off();
			}

			if(atomic_read(&gpu_dvfs_ctx.gpu_power_state))
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

static void pm_callback_second_part(struct kbase_device *kbdev)
{
	mali_poweroff_second_part(kbdev);
}

static void pm_callback_first_part(struct kbase_device *kbdev)
{
	CSTD_UNUSED(kbdev);
	mali_poweron_first_part();
}

static void pm_callback_clear_flag(struct kbase_device *kbdev)
{
	mali_poweron_clear_flag(kbdev);
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

struct kbase_pm_callback_conf pm_qogirn6pro_callbacks = {
	.power_off_callback = pm_callback_power_off,
	.power_on_callback = pm_callback_power_on,
	.power_suspend_callback = pm_callback_power_suspend,
	.power_resume_callback = pm_callback_power_resume,
	.power_off_second_part_callback = pm_callback_second_part,
	.power_on_first_part_callback = pm_callback_first_part,
	.power_on_clear_flag_callback = pm_callback_clear_flag,
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
	return (gpu_dvfs_ctx.freq_list[GPU_MIN_FREQ_INDEX].freq * FREQ_KHZ);
}

int kbase_platform_get_min_freq(void)
{
	return (gpu_dvfs_ctx.freq_list[GPU_MIN_FREQ_INDEX].freq * FREQ_KHZ);
}

int kbase_platform_get_max_freq(void)
{
	return (gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len -1].freq * FREQ_KHZ);
}

void kbase_platform_limit_max_freq(struct device *dev)
{
	int ret = 0;

	//disable 26M/76.8M/153.6M
	dev_pm_opp_disable(dev, GPU_26M_FREQ);
	dev_pm_opp_disable(dev, GPU_76M8_FREQ);
	dev_pm_opp_disable(dev, GPU_153M6_FREQ);
	//T760: GPU max freq is 650M
	//T770: GPU max freq is 780M
	//T790: GPU max freq is 850M
	//default table:384M, 512M, 680M, 850M
	if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T760"))
	{
		//T760:384M, 512M, 650M
		//remove 680M and 850M
		dev_pm_opp_disable(dev, GPU_680M_FREQ);
		dev_pm_opp_disable(dev, GPU_850M_FREQ);
		//add GPU max freq 650M
		ret = dev_pm_opp_add(dev, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq * FREQ_KHZ, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].volt);
		if (ret != 0)
		{
			printk(KERN_ERR "%s: dev_pm_opp_add return %d\n", __func__, ret);
		}
	}
	else if (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770"))
	{
		//default table:384M, 512M, 680M, 780M
		//remove 850M
		dev_pm_opp_disable(dev, GPU_850M_FREQ);
		//add GPU max freq 780M
		ret = dev_pm_opp_add(dev, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq * FREQ_KHZ, gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].volt);
		if (ret != 0)
		{
			printk(KERN_ERR "%s: dev_pm_opp_add return %d\n", __func__, ret);
		}
	}
}

int kbase_platform_set_freq_volt(int freq, int volt)
{
	int index = -1;
	freq = freq/FREQ_KHZ;
	index = freq_search(gpu_dvfs_ctx.freq_list, gpu_dvfs_ctx.freq_list_len, freq);
	printk(KERN_ERR "mali GPU_DVFS %s index = %d cur_freq = %d MHz cur_volt = %d mV --> freq = %d MHz volt = %d mV gpu_power_on = %d gpu_clock_on = %d dcdc_state:%d, gpu_temperature = %u.%lu, auto_efuse = %s, gpu_binning = %d.\n",
		__func__, index, gpu_dvfs_ctx.freq_cur->freq / FREQ_KHZ, gpu_dvfs_ctx.cur_voltage / VOLT_KMV, freq / FREQ_KHZ, volt / VOLT_KMV,
		atomic_read(&gpu_dvfs_ctx.gpu_power_state), atomic_read(&gpu_dvfs_ctx.gpu_clock_state), atomic_read(&gpu_dvfs_ctx.dcdc_gpu_state), (unsigned)gpu_dvfs_ctx.gpu_temperature / 1000,
		(unsigned long)gpu_dvfs_ctx.gpu_temperature % 1000, gpu_dvfs_ctx.auto_efuse, gpu_dvfs_ctx.gpu_binning);

	if (0 <= index)
	{
		down(gpu_dvfs_ctx.sem);

		//set frequency
		if (atomic_read(&gpu_dvfs_ctx.gpu_power_state) && atomic_read(&gpu_dvfs_ctx.gpu_clock_state) && atomic_read(&gpu_dvfs_ctx.dcdc_gpu_state))
		{
			//T770 the clk src of 680MHz and 780MHz are both clk_gpll
			//set gpll VCO 1950M(780M*2.5)
			if (gpu_dvfs_ctx.freq_list_len-1 == index && (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770")))
			{
				clk_set_rate(gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].clk_src, gpll_T770_1950M);
				udelay(50);
			}
			//set gpll VCO 1716M(686.4M*2.5)
			else if (gpu_dvfs_ctx.freq_list_len-2 == index && (!strcmp(gpu_dvfs_ctx.auto_efuse, "T770")))
			{
				clk_set_rate(gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2].clk_src, gpll_T770_1716M);
				udelay(50);
			}
			//set dvfs index
			regmap_update_bits(gpu_dvfs_ctx.dvfs_index_cfg.regmap_ptr, gpu_dvfs_ctx.dvfs_index_cfg.args[0], gpu_dvfs_ctx.dvfs_index_cfg.args[1], gpu_dvfs_ctx.freq_list[index].dvfs_index);
		}
		gpu_dvfs_ctx.cur_index = index;
		gpu_dvfs_ctx.freq_cur = &gpu_dvfs_ctx.freq_list[index];
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 0, 0)
#if LINUX_VERSION_CODE < KERNEL_VERSION(6, 0, 0)
		trace_clock_set_rate(VENDOR_FTRACE_MODULE_NAME, gpu_dvfs_ctx.freq_cur->freq, raw_smp_processor_id());
#endif	/* MU300: vendor tracepoint */
#endif	/* MU300: this vendor tracepoint does not exist in a mainline kernel */
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
	//T790 SS gpu_temperature >= 65 the max freq of gpu is 680MHz
	if ((*target_freq == (unsigned long)gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-1].freq*FREQ_KHZ ) && (!strcmp(gpu_dvfs_ctx.auto_efuse, "T790") || !strcmp(gpu_dvfs_ctx.auto_efuse, "UTS6560S"))
		&& (3 == gpu_dvfs_ctx.gpu_binning || 0 == gpu_dvfs_ctx.gpu_binning)
		&& gpu_dvfs_ctx.gpu_temperature >= 65000)
	{
		*target_freq = (unsigned long)gpu_dvfs_ctx.freq_list[gpu_dvfs_ctx.freq_list_len-2].freq*FREQ_KHZ;
		modify_flag = 1;
	}
	if(1 == modify_flag)
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
		//printk(KERN_INFO"GPU_DVFS %s gpu_boost_level =%d \n", __func__, gpu_boost_level);
	}
	boost_tgid = kctx->tgid;
	boost_pid = kctx->pid;
}
#endif

#ifdef QOGIRN6P_GPU_POLLING

u32 core0_pwr = 0;
u32 core1_pwr = 0;
u32 core2_pwr = 0;
u32 core3_pwr = 0;
u32 cur_st_st0_pwr = 0;
u32 cur_st_st1_pwr = 0;
u32 cur_st_st2_pwr = 0;
u32 cur_st_st3_pwr = 0;
u32 core0_pwr_mask = MOVE_BIT_LEFT(0x1f,24);
u32 core1_pwr_mask = MOVE_BIT_LEFT(0x1f,0);
u32 core2_pwr_mask = MOVE_BIT_LEFT(0x1f,8);
u32 core3_pwr_mask = MOVE_BIT_LEFT(0x1f,16);
u32 core0_pwr_wakeup = MOVE_BIT_LEFT(8,24);
u32 core1_pwr_wakeup = MOVE_BIT_LEFT(8,0);
u32 core2_pwr_wakeup = MOVE_BIT_LEFT(8,8);
u32 core3_pwr_wakeup = MOVE_BIT_LEFT(8,16);

u32 cur_st_mask = MOVE_BIT_LEFT(0xf0,0);
u32 st_pwr_mode = MOVE_BIT_LEFT(7,4);

//to check if the gpu core state is ready or not
void mali_core_state_check(void)
{
    bool not_ready;
    int pass_retry = 5;//the time of consecutive passes
    int pass_counter = 0;
    int counter = 0;//count the time of check
    //check if the gpu_core_state ready or not
    regmap_read(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core0_state_reg.args[0], &core0_pwr);
    core0_pwr = core0_pwr & core0_pwr_mask ;
    regmap_read(gpu_dvfs_ctx.gpu_core1_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core1_state_reg.args[0], &core1_pwr);
    core1_pwr = core1_pwr & core1_pwr_mask ;
    regmap_read(gpu_dvfs_ctx.gpu_core2_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core2_state_reg.args[0], &core2_pwr);
    core2_pwr = core2_pwr & core2_pwr_mask ;
    regmap_read(gpu_dvfs_ctx.gpu_core3_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core3_state_reg.args[0], &core3_pwr);
    core3_pwr = core3_pwr & core3_pwr_mask ;
    //read the st reg state
    regmap_read(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st0_reg.args[0], &cur_st_st0_pwr);
    cur_st_st0_pwr = cur_st_st0_pwr & cur_st_mask ;
    regmap_read(gpu_dvfs_ctx.cur_st_st1_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st1_reg.args[0], &cur_st_st1_pwr);
    cur_st_st1_pwr = cur_st_st1_pwr & cur_st_mask ;
    regmap_read(gpu_dvfs_ctx.cur_st_st2_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st2_reg.args[0], &cur_st_st2_pwr);
    cur_st_st2_pwr = cur_st_st2_pwr & cur_st_mask ;
    regmap_read(gpu_dvfs_ctx.cur_st_st3_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st3_reg.args[0], &cur_st_st3_pwr);
    cur_st_st3_pwr = cur_st_st3_pwr & cur_st_mask ;

    //printk(KERN_ERR "SPRDDEBUG gpu_core0_pwr = 0x%x,gpu_core1_pwr = 0x%x,gpu_core2_pwr = 0x%x,gpu_core3_pwr = 0x%x,cur_st_st0_pwr = 0x%x,cur_st_st1_pwr = 0x%x,cur_st_st2_pwr = 0x%x,cur_st_st3_pwr= 0x%x, counter = %d\n ",
           //core0_pwr,core1_pwr,core2_pwr,core3_pwr,cur_st_st0_pwr,cur_st_st1_pwr,cur_st_st2_pwr,cur_st_st3_pwr, counter);
    not_ready = ( core0_pwr != core0_pwr_wakeup )||( core1_pwr != core1_pwr_wakeup )||( core2_pwr != core2_pwr_wakeup )
        ||( core3_pwr != core3_pwr_wakeup )||(cur_st_st0_pwr!=st_pwr_mode)||(cur_st_st1_pwr!=st_pwr_mode)
        ||(cur_st_st2_pwr!=st_pwr_mode)||(cur_st_st3_pwr!=st_pwr_mode);
    while(not_ready)
    {
        udelay(5);
        regmap_read(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core0_state_reg.args[0], &core0_pwr);
        core0_pwr = core0_pwr & core0_pwr_mask ;
        regmap_read(gpu_dvfs_ctx.gpu_core1_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core1_state_reg.args[0], &core1_pwr);
        core1_pwr = core1_pwr & core1_pwr_mask ;
        regmap_read(gpu_dvfs_ctx.gpu_core2_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core2_state_reg.args[0], &core2_pwr);
        core2_pwr = core2_pwr & core2_pwr_mask ;
        regmap_read(gpu_dvfs_ctx.gpu_core3_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core3_state_reg.args[0], &core3_pwr);
        core3_pwr = core3_pwr & core3_pwr_mask ;

        regmap_read(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st0_reg.args[0], &cur_st_st0_pwr);
        cur_st_st0_pwr = cur_st_st0_pwr & cur_st_mask ;
        regmap_read(gpu_dvfs_ctx.cur_st_st1_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st1_reg.args[0], &cur_st_st1_pwr);
        cur_st_st1_pwr = cur_st_st1_pwr & cur_st_mask ;
        regmap_read(gpu_dvfs_ctx.cur_st_st2_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st2_reg.args[0], &cur_st_st2_pwr);
        cur_st_st2_pwr = cur_st_st2_pwr & cur_st_mask ;
        regmap_read(gpu_dvfs_ctx.cur_st_st3_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st3_reg.args[0], &cur_st_st3_pwr);
        cur_st_st3_pwr = cur_st_st3_pwr & cur_st_mask ;

        not_ready = ( core0_pwr != core0_pwr_wakeup )||( core1_pwr != core1_pwr_wakeup )||( core2_pwr != core2_pwr_wakeup )
            ||( core3_pwr != core3_pwr_wakeup )||(cur_st_st0_pwr!=st_pwr_mode)||(cur_st_st1_pwr!=st_pwr_mode)
            ||(cur_st_st2_pwr!=st_pwr_mode)||(cur_st_st3_pwr!=st_pwr_mode);
        //printk(KERN_ERR "SPRDDEBUG gpu_core0_pwr = 0x%x,gpu_core1_pwr = 0x%x,gpu_core2_pwr = 0x%x,gpu_core3_pwr = 0x%x,cur_st_st0_pwr = 0x%x,cur_st_st1_pwr = 0x%x,cur_st_st2_pwr = 0x%x,cur_st_st3_pwr= 0x%x, counter = %d\n ",
                       //core0_pwr,core1_pwr,core2_pwr,core3_pwr,cur_st_st0_pwr,cur_st_st1_pwr,cur_st_st2_pwr,cur_st_st3_pwr, counter);
        if(!not_ready){
            pass_counter++;
            while(pass_counter < pass_retry){
                            regmap_read(gpu_dvfs_ctx.gpu_core0_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core0_state_reg.args[0], &core0_pwr);
                                core0_pwr = core0_pwr & core0_pwr_mask ;
                                regmap_read(gpu_dvfs_ctx.gpu_core1_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core1_state_reg.args[0], &core1_pwr);
                                core1_pwr = core1_pwr & core1_pwr_mask ;
                                regmap_read(gpu_dvfs_ctx.gpu_core2_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core2_state_reg.args[0], &core2_pwr);
                                core2_pwr = core2_pwr & core2_pwr_mask ;
                                regmap_read(gpu_dvfs_ctx.gpu_core3_state_reg.regmap_ptr, gpu_dvfs_ctx.gpu_core3_state_reg.args[0], &core3_pwr);
                                core3_pwr = core3_pwr & core3_pwr_mask ;

                                regmap_read(gpu_dvfs_ctx.cur_st_st0_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st0_reg.args[0], &cur_st_st0_pwr);
                                cur_st_st0_pwr = cur_st_st0_pwr & cur_st_mask ;
                                regmap_read(gpu_dvfs_ctx.cur_st_st1_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st1_reg.args[0], &cur_st_st1_pwr);
                                cur_st_st1_pwr = cur_st_st1_pwr & cur_st_mask ;
                                regmap_read(gpu_dvfs_ctx.cur_st_st2_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st2_reg.args[0], &cur_st_st2_pwr);
                                cur_st_st2_pwr = cur_st_st2_pwr & cur_st_mask ;
                                regmap_read(gpu_dvfs_ctx.cur_st_st3_reg.regmap_ptr, gpu_dvfs_ctx.cur_st_st3_reg.args[0], &cur_st_st3_pwr);
                                cur_st_st3_pwr = cur_st_st3_pwr & cur_st_mask ;

                            not_ready = ( core0_pwr != core0_pwr_wakeup )||( core1_pwr != core1_pwr_wakeup )||( core2_pwr != core2_pwr_wakeup )
                ||( core3_pwr != core3_pwr_wakeup )||(cur_st_st0_pwr!=st_pwr_mode)||(cur_st_st1_pwr!=st_pwr_mode)
                ||(cur_st_st2_pwr!=st_pwr_mode)||(cur_st_st3_pwr!=st_pwr_mode);

                            if(not_ready){
                                    break;
                                }else pass_counter++;
                        }
                    if(pass_counter>=pass_retry){
                            printk(KERN_ERR "SPRDDEBUG gpu core power on polling SUCCESS !");
                            break;
                        }
        }
        if(counter++ > 2000 && not_ready )
        {
            printk(KERN_ERR "SPRDDEBUG gpu core power on TIMEOUT !");
        	printk(KERN_ERR "SPRDDEBUG gpu_core0_pwr = 0x%x,gpu_core1_pwr = 0x%x,gpu_core2_pwr = 0x%x,gpu_core3_pwr = 0x%x,cur_st_st0_pwr = 0x%x,cur_st_st1_pwr = 0x%x,cur_st_st2_pwr = 0x%x,cur_st_st3_pwr= 0x%x, counter = %d\n ",
                   core0_pwr,core1_pwr,core2_pwr,core3_pwr,cur_st_st0_pwr,cur_st_st1_pwr,cur_st_st2_pwr,cur_st_st3_pwr, counter);
            WARN_ON(1);
            counter = 0;
            break;
        }
    }
}


#endif
