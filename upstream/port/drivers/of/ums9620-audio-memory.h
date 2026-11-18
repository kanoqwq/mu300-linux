/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _UMS9620_AUDIO_MEMORY_H
#define _UMS9620_AUDIO_MEMORY_H

#include <linux/init.h>
#include <linux/libfdt.h>
#include <linux/memblock.h>
#include <linux/of_fdt.h>
#include <linux/sizes.h>

/* The UMS9620 AUDCP image occupies 6 MiB after its DDR32/command pool.
 * Reserve only the memory contract here; drivers and firmware belong to the
 * optional media plugin. Do not power, reset or start the DSP at boot.
 */
static void __init ums9620_reserve_audio_memory(void)
{
	const void *fdt = initial_boot_params;
	const __be32 *ddr, *cmd;
	u64 base, ddr_size, command, command_size, image, total;
	phys_addr_t start, size;
	int node, len;

	if (fdt_node_check_compatible(fdt, 0, "sprd,ums9620"))
		return;
	node = fdt_path_offset(fdt, "/audio-mem-mgr");
	if (node < 0)
		return;
	ddr = fdt_getprop(fdt, node, "sprd,ddr32-dma", &len);
	if (!ddr || len != 2 * sizeof(*ddr))
		goto invalid;
	base = fdt32_to_cpu(ddr[0]);
	ddr_size = fdt32_to_cpu(ddr[1]);
	cmd = fdt_getprop(fdt, node, "sprd,cmdaddr", &len);
	if (!cmd || len != 2 * sizeof(*cmd))
		goto invalid;
	command = fdt32_to_cpu(cmd[0]);
	command_size = fdt32_to_cpu(cmd[1]);
	image = ALIGN(command + command_size, SZ_1M);
	if (!base || !ddr_size || !command_size ||
	    command < base + ddr_size || image <= base ||
	    image - base > SZ_16M || image + 6 * SZ_1M > BIT_ULL(32) ||
	    !IS_ALIGNED(base, PAGE_SIZE))
		goto invalid;
	total = image - base + 6 * SZ_1M;
	if (!memblock_is_region_memory(base, total))
		goto invalid;

	start = base;
	size = total;
	/* memblock merges an existing FDT reservation; never release or move it. */
	if (memblock_reserve(start, size)) {
		pr_err("MU300: could not reserve AUDCP memory\n");
		return;
	}
	pr_info("MU300: reserved AUDCP memory %pa+%pa\n", &start, &size);
	return;
invalid:
	pr_warn("MU300: invalid audio DDR32 layout; AUDCP memory not reserved\n");
}

#endif
