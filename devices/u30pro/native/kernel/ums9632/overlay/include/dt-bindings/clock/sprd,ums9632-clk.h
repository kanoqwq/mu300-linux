/* SPDX-License-Identifier: (GPL-2.0-only OR MIT) */
/*
 * Unisoc UMS9632 platform clocks
 *
 * Copyright (C) 2023, Unisoc Inc.
 */

#ifndef _DT_BINDINGS_CLK_UMS9632_H_
#define _DT_BINDINGS_CLK_UMS9632_H_

#define CLK_52M			0
#define CLK_13M			1
#define CLK_6M5			2
#define CLK_4M33		3
#define CLK_1M			4
#define CLK_250K		5
#define CLK_RCO_150M_75M	6
#define CLK_RCO_150M_25M	7
#define CLK_RCO_150M_4M05	8
#define CLK_MPLLS_GATE		9
#define CLK_MPLLL_GATE		10
#define CLK_MPLLM_GATE		11
#define CLK_RPLL_GATE		12
#define CLK_DPLL0_GATE		13
#define CLK_DPLL1_GATE		14
#define CLK_DPLL2_GATE		15
#define CLK_GPLL_GATE		16
#define CLK_PHYR8PLL_GATE	17
#define CLK_TGPLL_GATE		18
#define CLK_V4NRPLL_GATE	19
#define CLK_AUDPLL_GATE		20
#define CLK_PIXELPLL_GATE	21
#define CLK_PMU_GATE_NUM	(CLK_PIXELPLL_GATE + 1)

#define CLK_RPLL		0
#define CLK_RPLL_26M		1
#define CLK_RPLL_390M		2
#define CLK_ANLG_PHY_G1_NUM	(CLK_RPLL_390M + 1)

#define CLK_DPLL0		0
#define CLK_DPLL1		1
#define CLK_DPLL2		2
#define CLK_ANLG_PHY_G1L_NUM	(CLK_DPLL2 + 1)

#define CLK_TGPLL		0
#define CLK_TGPLL_48M		1
#define CLK_TGPLL_51M2		2
#define CLK_TGPLL_64M		3
#define CLK_TGPLL_76M8		4
#define CLK_TGPLL_96M		5
#define CLK_TGPLL_128M		6
#define CLK_TGPLL_153M6		7
#define CLK_TGPLL_192M		8
#define CLK_TGPLL_256M		9
#define CLK_TGPLL_307M2		10
#define CLK_TGPLL_384M		11
#define CLK_TGPLL_512M		12
#define CLK_TGPLL_768M		13
#define CLK_V4NRPLL		14
#define CLK_V4NRPLL_409M6	15
#define CLK_V4NRPLL_614M4	16
#define CLK_V4NRPLL_1228M8	17
#define CLK_GPLL		18
#define CLK_GPLL_760M		19
#define CLK_GPLL_950M		20
#define CLK_ANLG_PHY_G5L_NUM	(CLK_GPLL_950M + 1)

#define CLK_AUDPLL		0
#define CLK_PHYR8PLL		1
#define CLK_PIXELPLL		2
#define CLK_PIXELPLL_200M	3
#define CLK_PIXELPLL_400M	4
#define CLK_PIXELPLL_420M	5
#define CLK_ANLG_PHY_G5R_NUM	(CLK_PIXELPLL_420M + 1)

#define CLK_MPLLM		0
#define CLK_ANLG_PHY_G9_NUM	(CLK_MPLLM + 1)

#define CLK_MPLLL		0
#define CLK_MPLLS		1
#define CLK_ANLG_PHY_G10_NUM	(CLK_MPLLS + 1)

#define CLK_IIS0_EB		0
#define CLK_IIS1_EB		1
#define CLK_SPI0_EB		2
#define CLK_SPI1_EB		3
#define CLK_SPI2_EB		4
#define CLK_SPI3_EB		5
#define CLK_UART0_EB		6
#define CLK_UART1_EB		7
#define CLK_UART2_EB		8
#define CLK_UART3_EB		9
#define CLK_SPI0_LFIN_EB	10
#define CLK_SPI1_LFIN_EB	11
#define CLK_SPI2_LFIN_EB	12
#define CLK_SPI3_LFIN_EB	13
#define CLK_CE_SEC_EB		14
#define CLK_CE_PUB_EB		15
#define CLK_AP_APB_GATE_NUM	(CLK_CE_PUB_EB + 1)

#define CLK_SDIO0_EB		0
#define CLK_SDIO1_EB		1
#define CLK_ICE_EB		2
#define CLK_EMMC_EB		3
#define CLK_DMA_PUB_EB		4
#define CLK_DMA_EB		5
#define CLK_UFS_EB		6
#define CLK_CKG_EB		7
#define CLK_BUSMON_CLK_EB	8
#define CLK_AP2EMC_EB		9
#define CLK_I2C0_EB		10
#define CLK_I2C1_EB		11
#define CLK_I2C2_EB		12
#define CLK_I2C3_EB		13
#define CLK_I2C4_EB		14
#define CLK_I2C5_EB		15
#define CLK_I2C6_EB		16
#define CLK_I2C7_EB		17
#define CLK_I2C8_EB		18
#define CLK_I2C9_EB		19
#define CLK_NOC_SER_EB		20
#define CLK_UFS_TX_EB		21
#define CLK_UFS_RX_0_EB		22
#define CLK_UFS_RX_1_EB		23
#define CLK_UFS_AO_APB_EB	24
#define CLK_UFS_UNIPRO_APB_EB	25
#define CLK_UFS_UTP_APB_EB	26
#define CLK_UFS_HCI_APB_EB	27
#define CLK_AP_AHB_GATE_NUM	(CLK_UFS_HCI_APB_EB + 1)

#define CLK_AP_APB		0
#define CLK_AP_AXI		1
#define CLK_AP2EMC		2
#define CLK_AP_UART0		3
#define CLK_AP_UART1		4
#define CLK_AP_UART2		5
#define CLK_AP_UART3		6
#define CLK_AP_I2C0		7
#define CLK_AP_I2C1		8
#define CLK_AP_I2C2		9
#define CLK_AP_I2C3		10
#define CLK_AP_I2C4		11
#define CLK_AP_I2C5		12
#define CLK_AP_I2C6		13
#define CLK_AP_I2C7		14
#define CLK_AP_I2C8		15
#define CLK_AP_I2C9		16
#define CLK_AP_IIS0		17
#define CLK_AP_IIS1		18
#define CLK_AP_CE		19
#define CLK_AP_CE_X1		20
#define CLK_EMMC_2X		21
#define CLK_EMMC_1X		22
#define CLK_UFS_TX_DIV2		23
#define CLK_UFS_TX_DIV2_PAD	24
#define CLK_UFS_TX		25
#define CLK_UFS_RX_0		26
#define CLK_UFS_RX_0_PAD	27
#define CLK_UFS_RX_1		28
#define CLK_UFS_RX_1_PAD	29
#define CLK_UFS_AXI		30
#define CLK_UFS_APB		31
#define CLK_IPA_AXI		32
#define CLK_IPA_APB		33
#define CLK_AP_CLK_NUM		(CLK_IPA_APB + 1)

#define CLK_DJTAG_EB		0
#define CLK_AUX0_EB		1
#define CLK_AUX1_EB		2
#define CLK_AUX2_EB		3
#define CLK_PROBE_EB		4
#define CLK_MM_EB		5
#define CLK_GPU_EB		6
#define CLK_APCPU_DAP_EB	7
#define CLK_AON_CSSYS_EB	8
#define CLK_CSSYS_APB_EB	9
#define CLK_DPU_VSP_EB		10
#define CLK_DSI_CFG_EB		11
#define CLK_EFUSE_EB		12
#define CLK_GPIO_EB		13
#define CLK_MBOX_EB		14
#define CLK_KPD_EB		15
#define CLK_AON_SYST_EB		16
#define CLK_AP_SYST_EB		17
#define CLK_AON_TMR_EB		18
#define CLK_AON_DVFS_TOP_EB	19
#define CLK_AON_USB2_TOP_EB	20
#define CLK_AON_VLT_SENSOR_EB	21
#define CLK_SPLK_EB		22
#define CLK_PIN_EB		23
#define CLK_ANA_EB		24
#define CLK_APCPU_BUSMON_EB	25
#define CLK_APCPU_TS_EB		26
#define CLK_APB_BUSMON_EB	27
#define CLK_AON_ASE_EB		28
#define CLK_AON_SCC_EB		29
#define CLK_AUX3_EB		30
#define CLK_BISR_EB		31
#define CLK_THM0_EB		32
#define CLK_THM1_EB		33
#define CLK_ADI_EB		34
#define CLK_EIC_EB		35
#define CLK_AP_TMR0_EB		36
#define CLK_AP_TMR1_EB		37
#define CLK_AP_TMR2_EB		38
#define CLK_PWM0_EB		39
#define CLK_PWM1_EB		40
#define CLK_PWM2_EB		41
#define CLK_PWM3_EB		42
#define CLK_AP_WDG_EB		43
#define CLK_APCPU_WDG_EB	44
#define CLK_ARCH_RTC_EB		45
#define CLK_KPD_RTC_EB		46
#define CLK_AON_SYST_RTC_EB	47
#define CLK_AP_SYST_RTC_EB	48
#define CLK_AON_TMR_RTC_EB	49
#define CLK_EIC_RTC_EB		50
#define CLK_EIC_RTCDV5_EB	51
#define CLK_AP_WDG_RTC_EB	52
#define CLK_AC_WDG_RTC_EB	53
#define CLK_AP_TMR0_RTC_EB	54
#define CLK_AP_TMR1_RTC_EB	55
#define CLK_AP_TMR2_RTC_EB	56
#define CLK_DCXO_LC_RTC_EB	57
#define CLK_BB_CAL_RTC_EB	58
#define CLK_DJTAG_TCK_EN	59
#define CLK_DMC_REF_EB		60
#define CLK_OTG_REF_EB		61
#define CLK_DEBOUNCE_EB		62
#define CLK_DET_32K_EB		63
#define CLK_DEBUG_TS_EN		64
#define CLK_ACCESS_AUD_EN	65
#define CLK_AUX0		66
#define CLK_AUX1		67
#define CLK_AUX2		68
#define CLK_PROBE		69
#define CLK_AUX3		70
#define CLK_AON_APB_GATE_NUM	(CLK_AUX3 + 1)

#define CLK_AON_APB		0
#define CLK_ADI			1
#define CLK_PWM0		2
#define CLK_PWM1		3
#define CLK_PWM2		4
#define CLK_PWM3		5
#define CLK_EFUSE		6
#define CLK_THM0		7
#define CLK_THM1		8
#define CLK_PRI_SBI		9
#define CLK_AON_TMR		10
#define CLK_TZPC		11
#define CLK_AON_PMU		12
#define CLK_DEBOUNCE		13
#define CLK_APCPU_PMU		14
#define CLK_TOP_DVFS		15
#define CLK_UART0		16
#define CLK_UART1		17
#define CLK_UART2		18
#define CLK_UART3		19
#define CLK_APCPU_DAP		20
#define CLK_APCPU_TS		21
#define CLK_DEBUG_TS		22
#define CLK_DJTAG_TCK		23
#define CLK_DET_REF		24
#define CLK_CSSYS		25
#define CLK_CSSYS_APB		26
#define CLK_SDIO0_2X		27
#define CLK_SDIO0_1X		28
#define CLK_SDIO1_2X		29
#define CLK_SDIO1_1X		30
#define CLK_SPI0		31
#define CLK_SPI1		32
#define CLK_SPI2		33
#define CLK_SPI3		34
#define CLK_ANALOG_IO_APB	35
#define CLK_DMC_REF		36
#define CLK_USB			37
#define CLK_NOC_REF		38
#define CLK_BISR_26M		39
#define CLK_AON_APB_NUM		(CLK_BISR_26M + 1)

#define CLK_CORE0		0
#define CLK_CORE1		1
#define CLK_CORE2		2
#define CLK_CORE3		3
#define CLK_CORE4		4
#define CLK_CORE5		5
#define CLK_CORE6		6
#define CLK_CORE7		7
#define CLK_SCU			8
#define CLK_ACE			9
#define CLK_ATB			10
#define CLK_DEBUG_APB		11
#define CLK_GIC			12
#define CLK_PERIPH		13
#define CLK_TOPDVFS_CLK_NUM	(CLK_PERIPH + 1)

#define CLK_CKG_EN			0
#define CLK_DVFS_EN			1
#define CLK_SYS_MTX_CFG_EN		2
#define CLK_SYS_MTX_AXI_MON_EN		3
#define CLK_SYS_TCK_EN			4
#define CLK_MM_MTX_DATA_EN		5
#define CLK_SNR_EN			6
#define CLK_MM_SYS_CFG_NOC_LL_EN	7
#define CLK_MM_SYS_CFG_NOC_HB_EN	8
#define CLK_MM_S_T_MAINEXTEN		9
#define CLK_NOC_AXI_TRANS_MON_EN	10
#define CLK_NOC_AXI2APB_AUTO_EN		11
#define CLK_ISP_EN			12
#define CLK_CPP_EN			13
#define CLK_ISP_MTX_EN			14
#define CLK_ISP_BLK_CFG_EN		15
#define CLK_ISP_BLK_MTX_AXI_MON_EN	16
#define CLK_ISP_TCK_EN			17
#define CLK_DCAM_IF_EN			18
#define CLK_DCAM_IF_LITE_EN		19
#define CLK_PHY_CFG_EN			20
#define CLK_DCAM_BLK_CFG_EN		21
#define CLK_SENSOR0_EN			22
#define CLK_SENSOR1_EN			23
#define CLK_SENSOR2_EN			24
#define CLK_SENSOR3_EN			25
#define CLK_SENSOR4_EN			26
#define CLK_CSI_SW_EN			27
#define CLK_DCAM_TCK_EN			28
#define CLK_CSI0_EN			29
#define CLK_CSI1_EN			30
#define CLK_CSI2_EN			31
#define CLK_IPA_EN			32
#define CLK_MM_GATE_NUM			(CLK_IPA_EN + 1)

#define CLK_MIPI_CSI0		0
#define CLK_MIPI_CSI1_1		1
#define CLK_MIPI_CSI1_2		2
#define CLK_MIPI_CSI2_1		3
#define CLK_MIPI_CSI2_2		4
#define CLK_DCAM_BLK_CFG	5
#define CLK_MM_SYS_CFG		6
#define CLK_SENSOR0		7
#define CLK_SENSOR1		8
#define CLK_SENSOR2		9
#define CLK_SENSOR3		10
#define CLK_SENSOR4		11
#define CLK_MM_CLK_NUM		(CLK_SENSOR4 + 1)

#define CLK_ISP			0
#define CLK_CPP			1
#define CLK_DCAM0_1		2
#define CLK_DCAM0_1_AXI		3
#define CLK_DCAM2_3		4
#define CLK_DCAM2_3_AXI		5
#define CLK_MM_MTX_DATA		6
#define CLK_MM_DVFS_CLK_NUM	(CLK_MM_MTX_DATA + 1)

#define CLK_DPU_EB		0
#define CLK_DSC0_EB		1
#define CLK_DSI0_EB		2
#define CLK_VPU_ENC0_EB		3
#define CLK_VPU_DEC_EB		4
#define CLK_GSP0_EB		5
#define CLK_JPG_EB		6
#define CLK_DPU_DVFS_EB		7
#define CLK_DPU_CKG_EB		8
#define CLK_DPU_BUSMON_EB	9
#define CLK_NOC_DISP_EB		10
#define CLK_NOC_VPU_EB		11
#define CLK_VOL_SNR_EB		12
#define CLK_MIPI_DPHY_M_ENABLE	13
#define CLK_M_DIV6CLK_GATE_EN	14
#define CLK_DPU_VSP_GATE_NUM	(CLK_M_DIV6CLK_GATE_EN + 1)

#define CLK_DPU_CFG		0
#define CLK_DISPC0_DPI		1
#define CLK_DISPC0_DSC		2
#define CLK_DPU_VSP_CLK_NUM	(CLK_DISPC0_DSC + 1)

#define CLK_VPU_MTX			0
#define CLK_VPU_ENC			1
#define CLK_VPU_DEC			2
#define CLK_GSP0			3
#define CLK_DISPC0			4
#define CLK_JPG				5
#define CLK_DPU_VSP_DVFS_CLK_NUM	(CLK_JPG + 1)

#define CLK_AUDCP_UART_EB		0
#define CLK_AUDCP_TMR0_EB		1
#define CLK_AUDCP_TMR1_EB		2
#define CLK_AUDCP_WDG_EB		3
#define CLK_AUDCP_INTC_EB		4
#define CLK_AUDCP_MCDT_EB		5
#define CLK_AUDCP_HAC_EB		6
#define CLK_AUDCP_VTS_EB		7
#define CLK_AUDCP_DMA_AP_EB		8
#define CLK_AUDCP_DMA_CP_EB		9
#define CLK_AUDCP_SPLK_EB		10
#define CLK_AUDCP_AHBFIFO_VT_EB		11
#define CLK_AUDCP_AHBFIFO_PDM_EB	12
#define CLK_AUDCP_MCDT_CP_EB		13
#define CLK_AUDCP_AHBFIFO_AP_EB		14
#define CLK_AUDCP_MCDT_AP_EB		15
#define CLK_AUDCP_SENSOR_EB		16
#define CLK_AUDCP_GLB_GATE_NUM		(CLK_AUDCP_SENSOR_EB + 1)

#endif/* _DT_BINDINGS_CLK_UMS9632_H_ */
