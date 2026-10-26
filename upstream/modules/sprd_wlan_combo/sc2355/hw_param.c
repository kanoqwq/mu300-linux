/*
* SPDX-FileCopyrightText: 2021-2022 Unisoc (Shanghai) Technologies Co., Ltd
* SPDX-License-Identifier: GPL-2.0
*
* Copyright 2021-2022 Unisoc (Shanghai) Technologies Co., Ltd
*
* This program is free software; you can redistribute it and/or modify it
* under the terms of version 2 of the GNU General Public License
* as published by the Free Software Foundation.
*/

#include <linux/of.h>
#include <linux/device.h>
#include <linux/file.h>
#include <linux/kdev_t.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/printk.h>
#include <linux/proc_fs.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/types.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include <misc/marlin_platform.h>
#include <misc/wcn_bus.h>
#include <linux/of_gpio.h>

#include "common/common.h"
#include "hw_param.h"

#define SYSTEM_WIFI_CONFIG_FILE "wifi_board_config.ini"
#define SYSTEM_WIFI_CONFIG_FILE_HULK "wifi_board_config_hulk.ini"
#define SYSTEM_WIFI_CONFIG_FILE_HULKBC "wifi_board_config_hulkbc.ini"
#define SYSTEM_WIFI_AB_CONFIG_FILE "wifi_board_config_ab.ini"
#define SYSTEM_WIFI_AC_CONFIG_FILE "wifi_board_config_ac.ini"
#define SYSTEM_WIFI_AA_CONFIG_FILE "wifi_board_config_aa.ini"

#define CF_TAB(NAME, MEM_OFFSET, TYPE) \
	{ NAME, (size_t)(&(((struct wifi_conf_t *)(0))->MEM_OFFSET)), TYPE}

#define OFS_MARK_STRING \
	"#-----------------------------------------------------------------\r\n"

static struct nvm_name_table sc2355_nvm_table[] = {
	/* [Section 1: Version] */
	CF_TAB("Major", version.major, 2),
	CF_TAB("Minor", version.minor, 2),

	/* [SECTION 2]Board Config: board_config_t */
	CF_TAB("Calib_Bypass", board_config.calib_bypass, 2),
	CF_TAB("TxChain_Mask", board_config.txchain_mask, 1),
	CF_TAB("RxChain_Mask", board_config.rxchain_mask, 1),

	/* [SECTION 3]Board Config TPC: board_config_tpc_t */
	CF_TAB("DPD_LUT_idx", board_config_tpc.dpd_lut_idx[0], 1),
	CF_TAB("TPC_Goal_Chain0", board_config_tpc.tpc_goal_chain0[0], 2),
	CF_TAB("TPC_Goal_Chain1", board_config_tpc.tpc_goal_chain1[0], 2),
	/* [SECTION 4]TPC-LUT: tpc_lut_t */
	CF_TAB("Chain0_LUT_0", tpc_lut.chain0_lut[0], 1),
	CF_TAB("Chain0_LUT_1", tpc_lut.chain0_lut[1], 1),
	CF_TAB("Chain0_LUT_2", tpc_lut.chain0_lut[2], 1),
	CF_TAB("Chain0_LUT_3", tpc_lut.chain0_lut[3], 1),
	CF_TAB("Chain0_LUT_4", tpc_lut.chain0_lut[4], 1),
	CF_TAB("Chain0_LUT_5", tpc_lut.chain0_lut[5], 1),
	CF_TAB("Chain0_LUT_6", tpc_lut.chain0_lut[6], 1),
	CF_TAB("Chain0_LUT_7", tpc_lut.chain0_lut[7], 1),
	CF_TAB("Chain1_LUT_0", tpc_lut.chain1_lut[0], 1),
	CF_TAB("Chain1_LUT_1", tpc_lut.chain1_lut[1], 1),
	CF_TAB("Chain1_LUT_2", tpc_lut.chain1_lut[2], 1),
	CF_TAB("Chain1_LUT_3", tpc_lut.chain1_lut[3], 1),
	CF_TAB("Chain1_LUT_4", tpc_lut.chain1_lut[4], 1),
	CF_TAB("Chain1_LUT_5", tpc_lut.chain1_lut[5], 1),
	CF_TAB("Chain1_LUT_6", tpc_lut.chain1_lut[6], 1),
	CF_TAB("Chain1_LUT_7", tpc_lut.chain1_lut[7], 1),

	/* [SECTION 5]Board Config Frequency Compensation:
	 * board_conf_freq_comp_t
	 */
	CF_TAB("2G_Channel_Chain0",
	       board_conf_freq_comp.channel_2g_chain0[0], 1),
	CF_TAB("2G_Channel_Chain1",
	       board_conf_freq_comp.channel_2g_chain1[0], 1),
	CF_TAB("5G_Channel_Chain0",
	       board_conf_freq_comp.channel_5g_chain0[0], 1),
	CF_TAB("5G_Channel_Chain1",
	       board_conf_freq_comp.channel_5g_chain1[0], 1),

	/* [SECTION 6]Rate To Power with BW 20M: power_20m_t */
	CF_TAB("11b_Power", power_20m.power_11b[0], 1),
	CF_TAB("11ag_Power", power_20m.power_11ag[0], 1),
	CF_TAB("11n_Power", power_20m.power_11n[0], 1),
	CF_TAB("11ac_Power", power_20m.power_11ac[0], 1),

	/* [SECTION 7]Power Backoff:power_backoff_t */
	CF_TAB("Green_WIFI_offset", power_backoff.green_wifi_offset, 1),
	CF_TAB("HT40_Power_offset", power_backoff.ht40_power_offset, 1),
	CF_TAB("VHT40_Power_offset", power_backoff.vht40_power_offset, 1),
	CF_TAB("VHT80_Power_offset", power_backoff.vht80_power_offset, 1),
	CF_TAB("SAR_Power_offset", power_backoff.sar_power_offset, 1),
	CF_TAB("Mean_Power_offset", power_backoff.mean_power_offset, 1),

	/* [SECTION 8]Reg Domain:reg_domain_t */
	CF_TAB("reg_domain1", reg_domain.reg_domain1, 4),
	CF_TAB("reg_domain2", reg_domain.reg_domain2, 4),

	/* [SECTION 9]Band Edge Power offset(MKK, FCC, ETSI):
	 * band_edge_power_offset_t
	 */
	CF_TAB("BW20M", band_edge_power_offset.bw20m[0], 1),
	CF_TAB("BW40M", band_edge_power_offset.bw40m[0], 1),
	CF_TAB("BW80M", band_edge_power_offset.bw80m[0], 1),

	/* [SECTION 10]TX Scale:tx_scale_t */
	CF_TAB("Chain0_1", tx_scale.chain0[0][0], 1),
	CF_TAB("Chain1_1", tx_scale.chain1[0][0], 1),
	CF_TAB("Chain0_2", tx_scale.chain0[1][0], 1),
	CF_TAB("Chain1_2", tx_scale.chain1[1][0], 1),
	CF_TAB("Chain0_3", tx_scale.chain0[2][0], 1),
	CF_TAB("Chain1_3", tx_scale.chain1[2][0], 1),
	CF_TAB("Chain0_4", tx_scale.chain0[3][0], 1),
	CF_TAB("Chain1_4", tx_scale.chain1[3][0], 1),
	CF_TAB("Chain0_5", tx_scale.chain0[4][0], 1),
	CF_TAB("Chain1_5", tx_scale.chain1[4][0], 1),
	CF_TAB("Chain0_6", tx_scale.chain0[5][0], 1),
	CF_TAB("Chain1_6", tx_scale.chain1[5][0], 1),
	CF_TAB("Chain0_7", tx_scale.chain0[6][0], 1),
	CF_TAB("Chain1_7", tx_scale.chain1[6][0], 1),
	CF_TAB("Chain0_8", tx_scale.chain0[7][0], 1),
	CF_TAB("Chain1_8", tx_scale.chain1[7][0], 1),
	CF_TAB("Chain0_9", tx_scale.chain0[8][0], 1),
	CF_TAB("Chain1_9", tx_scale.chain1[8][0], 1),
	CF_TAB("Chain0_10", tx_scale.chain0[9][0], 1),
	CF_TAB("Chain1_10", tx_scale.chain1[9][0], 1),
	CF_TAB("Chain0_11", tx_scale.chain0[10][0], 1),
	CF_TAB("Chain1_11", tx_scale.chain1[10][0], 1),
	CF_TAB("Chain0_12", tx_scale.chain0[11][0], 1),
	CF_TAB("Chain1_12", tx_scale.chain1[11][0], 1),
	CF_TAB("Chain0_13", tx_scale.chain0[12][0], 1),
	CF_TAB("Chain1_13", tx_scale.chain1[12][0], 1),
	CF_TAB("Chain0_14", tx_scale.chain0[13][0], 1),
	CF_TAB("Chain1_14", tx_scale.chain1[13][0], 1),
	CF_TAB("Chain0_36", tx_scale.chain0[14][0], 1),
	CF_TAB("Chain1_36", tx_scale.chain1[14][0], 1),
	CF_TAB("Chain0_40", tx_scale.chain0[15][0], 1),
	CF_TAB("Chain1_40", tx_scale.chain1[15][0], 1),
	CF_TAB("Chain0_44", tx_scale.chain0[16][0], 1),
	CF_TAB("Chain1_44", tx_scale.chain1[16][0], 1),
	CF_TAB("Chain0_48", tx_scale.chain0[17][0], 1),
	CF_TAB("Chain1_48", tx_scale.chain1[17][0], 1),
	CF_TAB("Chain0_52", tx_scale.chain0[18][0], 1),
	CF_TAB("Chain1_52", tx_scale.chain1[18][0], 1),
	CF_TAB("Chain0_56", tx_scale.chain0[19][0], 1),
	CF_TAB("Chain1_56", tx_scale.chain1[19][0], 1),
	CF_TAB("Chain0_60", tx_scale.chain0[20][0], 1),
	CF_TAB("Chain1_60", tx_scale.chain1[20][0], 1),
	CF_TAB("Chain0_64", tx_scale.chain0[21][0], 1),
	CF_TAB("Chain1_64", tx_scale.chain1[21][0], 1),
	CF_TAB("Chain0_100", tx_scale.chain0[22][0], 1),
	CF_TAB("Chain1_100", tx_scale.chain1[22][0], 1),
	CF_TAB("Chain0_104", tx_scale.chain0[23][0], 1),
	CF_TAB("Chain1_104", tx_scale.chain1[23][0], 1),
	CF_TAB("Chain0_108", tx_scale.chain0[24][0], 1),
	CF_TAB("Chain1_108", tx_scale.chain1[24][0], 1),
	CF_TAB("Chain0_112", tx_scale.chain0[25][0], 1),
	CF_TAB("Chain1_112", tx_scale.chain1[25][0], 1),
	CF_TAB("Chain0_116", tx_scale.chain0[26][0], 1),
	CF_TAB("Chain1_116", tx_scale.chain1[26][0], 1),
	CF_TAB("Chain0_120", tx_scale.chain0[27][0], 1),
	CF_TAB("Chain1_120", tx_scale.chain1[27][0], 1),
	CF_TAB("Chain0_124", tx_scale.chain0[28][0], 1),
	CF_TAB("Chain1_124", tx_scale.chain1[28][0], 1),
	CF_TAB("Chain0_128", tx_scale.chain0[29][0], 1),
	CF_TAB("Chain1_128", tx_scale.chain1[29][0], 1),
	CF_TAB("Chain0_132", tx_scale.chain0[30][0], 1),
	CF_TAB("Chain1_132", tx_scale.chain1[30][0], 1),
	CF_TAB("Chain0_136", tx_scale.chain0[31][0], 1),
	CF_TAB("Chain1_136", tx_scale.chain1[31][0], 1),
	CF_TAB("Chain0_140", tx_scale.chain0[32][0], 1),
	CF_TAB("Chain1_140", tx_scale.chain1[32][0], 1),
	CF_TAB("Chain0_144", tx_scale.chain0[33][0], 1),
	CF_TAB("Chain1_144", tx_scale.chain1[33][0], 1),
	CF_TAB("Chain0_149", tx_scale.chain0[34][0], 1),
	CF_TAB("Chain1_149", tx_scale.chain1[34][0], 1),
	CF_TAB("Chain0_153", tx_scale.chain0[35][0], 1),
	CF_TAB("Chain1_153", tx_scale.chain0[35][0], 1),
	CF_TAB("Chain0_157", tx_scale.chain0[36][0], 1),
	CF_TAB("Chain1_157", tx_scale.chain0[36][0], 1),
	CF_TAB("Chain0_161", tx_scale.chain0[37][0], 1),
	CF_TAB("Chain1_161", tx_scale.chain1[37][0], 1),
	CF_TAB("Chain0_165", tx_scale.chain0[38][0], 1),
	CF_TAB("Chain1_165", tx_scale.chain1[38][0], 1),

	/* [SECTION 11]misc:misc_t */
	CF_TAB("DFS_switch", misc.dfs_switch, 1),
	CF_TAB("power_save_switch", misc.power_save_switch, 1),
	CF_TAB("ex-Fem_and_ex-LNA_param_setup", misc.fem_lan_param_setup, 1),
	CF_TAB("rssi_report_diff", misc.rssi_report_diff, 1),

	/* [SECTION 12]debug reg:debug_reg_t */
	CF_TAB("address", debug_reg.address[0], 4),
	CF_TAB("value", debug_reg.value[0], 4),

	/* [SECTION 13]coex_config:coex_config_t */
	CF_TAB("bt_performance_cfg0", coex_config.bt_performance_cfg0, 4),
	CF_TAB("bt_performance_cfg1", coex_config.bt_performance_cfg1, 4),
	CF_TAB("wifi_performance_cfg0", coex_config.wifi_performance_cfg0, 4),
	CF_TAB("wifi_performance_cfg2", coex_config.wifi_performance_cfg2, 4),
	CF_TAB("strategy_cfg0", coex_config.strategy_cfg0, 4),
	CF_TAB("strategy_cfg1", coex_config.strategy_cfg1, 4),
	CF_TAB("strategy_cfg2", coex_config.strategy_cfg2, 4),
	CF_TAB("compatibility_cfg0", coex_config.compatibility_cfg0, 4),
	CF_TAB("compatibility_cfg1", coex_config.compatibility_cfg1, 4),
	CF_TAB("ant_cfg0", coex_config.ant_cfg0, 4),
	CF_TAB("ant_cfg1", coex_config.ant_cfg1, 4),
	CF_TAB("isolation_cfg0", coex_config.isolation_cfg0, 4),
	CF_TAB("isolation_cfg1", coex_config.isolation_cfg1, 4),
	CF_TAB("reserved_cfg0", coex_config.reserved_cfg0, 4),
	CF_TAB("reserved_cfg1", coex_config.reserved_cfg1, 4),
	CF_TAB("reserved_cfg2", coex_config.reserved_cfg2, 4),
	CF_TAB("reserved_cfg3", coex_config.reserved_cfg3, 4),
	CF_TAB("reserved_cfg4", coex_config.reserved_cfg4, 4),
	CF_TAB("reserved_cfg5", coex_config.reserved_cfg5, 4),
	CF_TAB("reserved_cfg6", coex_config.reserved_cfg6, 4),
	CF_TAB("reserved_cfg7", coex_config.reserved_cfg7, 4),

	/* [SECTION 14] rf_config:rf_config_t */
	CF_TAB("rf_config", rf_config.rf_data, 1),

	/* [SECTION 15] wifi_param:wifi_config_param_t */
	CF_TAB("roaming_trigger", wifi_param.roaming_param.trigger, 1),
	CF_TAB("roaming_delta", wifi_param.roaming_param.delta, 1),
	CF_TAB("roaming_5g_prefer", wifi_param.roaming_param.band_5g_prefer, 1),
};

static int hw_param_nvm_find_type(char key)
{
	if ((key >= 'a' && key <= 'w') ||
	    (key >= 'y' && key <= 'z') ||
	    (key >= 'A' && key <= 'W') ||
	    (key >= 'Y' && key <= 'Z') ||
	    key == '_')
		return 1;
	if ((key >= '0' && key <= '9') || key == '-')
		return 2;
	if (key == 'x' || key == 'X' || key == '.')
		return 3;
	if (key == '\0' || key == '\r' || key == '\n' || key == '#')
		return 4;
	return 0;
}

static int hw_param_nvm_set_cmd(struct nvm_name_table *ptable,
				struct nvm_cali_cmd *cmd, void *p_data)
{
	int i;
	unsigned char *p;

	if (ptable->type != 1 && ptable->type != 2 && ptable->type != 4)
		return -1;

	p = (unsigned char *)(p_data) + ptable->mem_offset;

	pr_info("[g_table]%s, offset:%u, num:%u, value:"
		"%d %d %d %d %d %d %d %d %d %d\n",
		ptable->itm, ptable->mem_offset, cmd->num,
		cmd->par[0], cmd->par[1], cmd->par[2],
		cmd->par[3], cmd->par[4], cmd->par[5],
		cmd->par[6], cmd->par[7], cmd->par[8],
		cmd->par[9]);

	for (i = 0; i < cmd->num; i++) {
		if (ptable->type == 1)
			*((unsigned char *)p + i) =
			    (unsigned char)(cmd->par[i]);
		else if (ptable->type == 2)
			*((unsigned short *)p + i) =
			    (unsigned short)(cmd->par[i]);
		else if (ptable->type == 4)
			*((unsigned int *)p + i) =
			    (unsigned int)(cmd->par[i]);
		else
			pr_info("%s, type err\n", __func__);
	}
	return 0;
}

static void hw_param_nvm_get_cmd_par(char *str, struct nvm_cali_cmd *cmd)
{
	int i, j, buftype, ctype, flag;
	unsigned int m_cmd_num = ARRAY_SIZE(cmd->par);
	char tmp[64];
	char c;
	long val;

	buftype = -1;
	ctype = 0;
	flag = 0;
	memset(cmd, 0, sizeof(struct nvm_cali_cmd));

	for (i = 0, j = 0; j < sizeof(tmp); i++) {
		c = str[i];
		ctype = hw_param_nvm_find_type(c);
		if (ctype == 1 || ctype == 2 || ctype == 3) {
			tmp[j] = c;
			j++;
			if (buftype == -1) {
				if (ctype == 2)
					buftype = 2;
				else
					buftype = 1;
			} else if (buftype == 2) {
				if (ctype == 1)
					buftype = 1;
			}
			continue;
		}
		if (-1 != buftype) {
			tmp[j] = '\0';

			if (buftype == 1 && !flag) {
				strcpy(cmd->itm, tmp);
				flag = 1;
			} else {
				if (kstrtol(tmp, 0, &val))
					pr_info(" %s ", tmp);
				/* pr_err("kstrtol %s: error\n", tmp); */
				if (cmd->num >= m_cmd_num) {
					pr_err("cmd_num(%d) exceed max_num(%d)", cmd->num + 1, m_cmd_num);
					return;
				}
				cmd->par[cmd->num] = val & 0xFFFFFFFF;
				cmd->num++;
			}
			buftype = -1;
			j = 0;
		}
		if (!ctype)
			continue;
		if (ctype == 4)
			return;
	}
	tmp[j - 1] = '\0';
	pr_err("too long str : %s..., max strlen is %d\n", tmp, sizeof(tmp) - 1);
}

static struct nvm_name_table *hw_param_nvm_cf_table_match(struct nvm_cali_cmd *cmd)
{
	int i;
	struct nvm_name_table *ptable = NULL;
	int len = sizeof(sc2355_nvm_table) / sizeof(struct nvm_name_table);

	if (!cmd)
		return NULL;
	for (i = 0; i < len; i++) {
		if (!sc2355_nvm_table[i].itm)
			continue;
		if (strcmp(sc2355_nvm_table[i].itm, cmd->itm))
			continue;
		ptable = &sc2355_nvm_table[i];
		break;
	}
	return ptable;
}

static int hw_param_nvm_buf_operate(char *pbuf, int file_len, void *p_data)
{
	int i, p;
	struct nvm_cali_cmd *cmd;
	struct wifi_conf_t *conf;
	struct nvm_name_table *ptable = NULL;

	if (!pbuf || !file_len)
		return -1;

	cmd = kzalloc(sizeof(*cmd), GFP_KERNEL);
	for (i = 0, p = 0; i < file_len; i++) {
		if (('\n' == *(pbuf + i)) ||
		    ('\r' == *(pbuf + i)) || ('\0' == *(pbuf + i))) {
			if (5 <= (i - p)) {
				hw_param_nvm_get_cmd_par((pbuf + p), cmd);
				ptable = hw_param_nvm_cf_table_match(cmd);

				if (ptable) {
					hw_param_nvm_set_cmd(ptable, cmd, p_data);
					if (strcmp(ptable->itm, "rf_config") == 0) {
						conf = (struct wifi_conf_t *)p_data;
						conf->rf_config.rf_data_len = cmd->num;
					}
					if (strcmp(ptable->itm, "value") == 0) {
						spin_lock_bh(&adap_info.adap_lock);
						adap_info.special_data_flag = cmd->par[4];
						pr_info("%s special_data_flag: %d\n",
							__func__, adap_info.special_data_flag);
						spin_unlock_bh(&adap_info.adap_lock);
					}
				}
			}
			p = i + 1;
		}
	}

	kfree(cmd);
	return 0;
}

static int hw_param_nvm_parse(struct sprd_priv *priv, const char *path, void *p_data)
{
	const struct firmware *fw = NULL;
	unsigned char *p_buf = NULL;
	unsigned int buffer_len;
	char *buffer = NULL;
	int ret = 0;

	pr_info("%s()...\n", __func__);
	ret = request_firmware(&fw, path, wiphy_dev(priv->wiphy));
	if (ret) {
		pr_err("open file %s error\n", path);
		return -1;
	}

	if (!fw || !fw->data || fw->size <= 0) {
		pr_err("%s invalid firmware file\n", __func__);
		release_firmware(fw);
		return -EINVAL;
	}

	buffer_len = fw->size;
	buffer = vmalloc(fw->size);
	if (!buffer) {
		pr_err("%s no memory\n", __func__);
		release_firmware(fw);
		return -1;
	}

	memcpy(buffer, fw->data, fw->size);
	release_firmware(fw);
	p_buf = buffer;

	pr_info("%s read %s data_len:0x%x\n", __func__, path, buffer_len);
	ret = hw_param_nvm_buf_operate(buffer, buffer_len, p_data);
	vfree(buffer);
	pr_info("%s(), parsing ini data result=%d\n", __func__, ret);
	return ret;
}

static char *get_project_name()
{
	struct device_node *cmdline_node;
	const char *cmd_line = 0;
	char *temp_name = 0;
	int ret = 0;
	cmdline_node = of_find_node_by_path("/chosen");
	ret = of_property_read_string(cmdline_node,"bootargs",&cmd_line);
	if(!ret){
		temp_name = strstr(cmd_line,"prj_name=");
		if(temp_name != NULL){
		temp_name += strlen("prj_name=");
		pr_info("prj_name_hw_param=%s\n",temp_name);
	    }else{
		    pr_err("read prj_name_hw_param err");
	    }
	}
	pr_info("temp_name=%s\n",temp_name);
	return temp_name;
}

int sc2355_get_nvm_table(struct sprd_priv *priv, struct wifi_conf_t *p)
{
	char *prj_name = get_project_name();
	if (wcn_get_chip_type() == WCN_CHIP_ID_INVALID) {
		pr_err("%s, marlin chip ID is invalid\n", __func__);
		return -1;
	} else if (wcn_get_chip_type() == WCN_CHIP_ID_AA) {
		pr_info("%s, chip id of marlin3 lite is %d, open %s\n",
			__func__, wcn_get_chip_type(),
			SYSTEM_WIFI_AA_CONFIG_FILE);
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_AA_CONFIG_FILE, (void *)p);
	}
	if (prj_name !=NULL && (strncmp(prj_name,"22716",5) ==0 )) {
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULK 22716");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULK, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22717",5) ==0 )) {
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULK 22717");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULK, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22718",5) ==0 )) {
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULK 22718");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULK, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22722",5) ==0 )){
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULKBC 22722");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULKBC, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22723",5) ==0 )){
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULKBC 22723");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULKBC, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22724",5) ==0 )){
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULKBC 22724");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULKBC, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22726",5) ==0 )){
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULKBC 22726");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULKBC, (void *)p);
	}else if(prj_name !=NULL && (strncmp(prj_name,"22734",5) ==0 )){
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE_HULKBC 22734");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE_HULKBC, (void *)p);
	}else{
		printk("[kernel] SYSTEM_WIFI_CONFIG_FILE");
		return hw_param_nvm_parse(priv, SYSTEM_WIFI_CONFIG_FILE, (void *)p);
	}
}
