#!/bin/bash
# Build the Unisoc display stack for USB-C DisplayPort (DPU1 + DPTX) and the PMIC USB-PD driver as external
# modules against out-linux (5.4). The F50/MU300 ships without display, so ZTE's config leaves them out.
# Run inside mu300-kbuild with the mu300-kernel volume on /src.
set -e
K=/src/zte-u30air
M="make O=/src/out-linux ARCH=arm64 LLVM=1 LLVM_IAS=1 CC=clang LD=ld.lld -j$(nproc)"

# sprd-drm: DPU1 (dpu_lite_r3p0) + DP; DPU0/DSI are linked because the common code uses them (no panel node probes)
D=/src/ext-sprd-drm
rm -rf $D && cp -r $K/drivers/gpu/drm/sprd $D
sed -i "s#-I \$(srctree)/\$(src)/../../../devfreq/apsys/#-I $K/drivers/devfreq/apsys/#; s#-I \$(srctree)/\$(src)\$#-I $D#" $D/Makefile
cd $K
$M M=$D \
  CONFIG_DRM_SPRD=m CONFIG_DRM_SPRD_DPU0=y CONFIG_DRM_SPRD_DSI=y CONFIG_DRM_SPRD_DPU1=y CONFIG_DRM_SPRD_DP=y CONFIG_DRM_SPRD_GSP= \
  KCFLAGS="-DCONFIG_DRM_SPRD_MODULE=1 -DCONFIG_DRM_SPRD_DPU0=1 -DCONFIG_DRM_SPRD_DSI=1 -DCONFIG_DRM_SPRD_DPU1=1 -DCONFIG_DRM_SPRD_DP=1 -I$D" modules

# sc27xx_pd: PMIC USB Power Delivery PHY for the TCPM (needed to enter DisplayPort alt mode)
P=/src/ext-sc27xx-pd
rm -rf $P && mkdir -p $P && cp $K/drivers/usb/typec/tcpm/sc27xx_pd.c $P/
# the DT has no "connector" node under pd@e00: describe a dual-role port (prefers sink) with a software node
python3 - $P/sc27xx_pd.c <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
s = s.replace('\tpd->tcpc.fwnode = device_get_named_child_node(&pdev->dev, "connector");\n',
 '\tpd->tcpc.fwnode = device_get_named_child_node(&pdev->dev, "connector");\n'
 '\tif (!pd->tcpc.fwnode) {\n'
 '\t\tpd->tcpc.fwnode = fwnode_create_software_node(mu300_connector_props, NULL);\n'
 '\t\tif (IS_ERR(pd->tcpc.fwnode))\n\t\t\treturn PTR_ERR(pd->tcpc.fwnode);\n'
 '\t\tdev_info(&pdev->dev, "MU300: no connector node, using a dual-role software node\\n");\n'
 '\t}\n', 1)
s = s.replace('static const struct tcpc_config sc27xx_pd_config = {',
 '/* MU300: Type-C connector description (dual role, prefers sink, 5 V only) */\n'
 'static const u32 mu300_src_pdo[] = { SPRD_PDO_FIXED(5000, 500, SPRD_PDO_FIXED_DUAL_ROLE | SPRD_PDO_FIXED_DATA_SWAP | SPRD_PDO_FIXED_USB_COMM) };\n'
 'static const u32 mu300_snk_pdo[] = { SPRD_PDO_FIXED(5000, 3000, SPRD_PDO_FIXED_DUAL_ROLE | SPRD_PDO_FIXED_DATA_SWAP | SPRD_PDO_FIXED_USB_COMM) };\n'
 'static const struct property_entry mu300_connector_props[] = {\n'
 '\tPROPERTY_ENTRY_STRING("data-role", "dual"),\n'
 '\tPROPERTY_ENTRY_STRING("power-role", "dual"),\n'
 '\tPROPERTY_ENTRY_STRING("try-power-role", "sink"),\n'
 '\tPROPERTY_ENTRY_U32_ARRAY("source-pdos", mu300_src_pdo),\n'
 '\tPROPERTY_ENTRY_U32_ARRAY("sink-pdos", mu300_snk_pdo),\n'
 '\tPROPERTY_ENTRY_U32("op-sink-microwatt", 10000000),\n'
 '\t{ }\n};\n\n'
 'static const struct tcpc_config sc27xx_pd_config = {', 1)
if '#include <linux/property.h>' not in s:
    s = s.replace('#include <linux/module.h>\n', '#include <linux/module.h>\n#include <linux/property.h>\n', 1)
s = s.replace("""	} else {
		pd->aon_apb = NULL;
	}""", """	} else {
		/* MU300: the pd@e00 node has no sprd,syscon-aon-apb; the PD PHY needs the AON 26M sine driver enabled */
		struct device_node *aon = of_find_node_by_path("/soc/syscon@64900000");

		pd->aon_apb = aon ? syscon_node_to_regmap(aon) : NULL;
		of_node_put(aon);
		if (IS_ERR(pd->aon_apb))
			pd->aon_apb = NULL;
		dev_info(pd->dev, "MU300: aon apb %s\\n", pd->aon_apb ? "from /soc/syscon@64900000" : "missing");
	}""", 1)
s = s.replace("""static int sc27xx_pd_send_hardreset(struct sc27xx_pd *pd)
{
	int ret, state;
""", """/*
 * MU300: the device has no battery. A hard reset makes a PD power source (dock, charger) drop VBUS, which powers the
 * device off. Until PD messaging is verified, only log hard resets (TCPM then falls back to Type-C current).
 */
static bool mu300_allow_hard_reset;
module_param(mu300_allow_hard_reset, bool, 0644);

static int sc27xx_pd_send_hardreset(struct sc27xx_pd *pd)
{
	int ret, state;

	if (!mu300_allow_hard_reset) {
		dev_warn(pd->dev, "MU300: hard reset suppressed (no battery)\\n");
		return 0;
	}
""", 1)
assert 'mu300_allow_hard_reset' in s
assert 'syscon@64900000' in s
assert 'mu300_connector_props' in s and 'fwnode_create_software_node' in s
open(p, 'w').write(s)
PY
echo 'obj-m += sc27xx_pd.o' > $P/Makefile
$M M=$P KCFLAGS="-DCONFIG_SC27XX_PD_MODULE=1" modules

mkdir -p /src/out-display
for f in $D/sprd-drm.ko $P/sc27xx_pd.ko; do llvm-strip --strip-debug -o /src/out-display/$(basename $f) $f; done
ls -la /src/out-display

# mu300-dp-enable: turns the disabled PD and DPTX nodes on at runtime
E=/src/ext-mu300-dp-enable
rm -rf $E && cp -r /work/mu300-dp-enable $E
cd $K && $M M=$E modules
llvm-strip --strip-debug -o /src/out-display/mu300-dp-enable.ko $E/mu300-dp-enable.ko
ls -la /src/out-display
