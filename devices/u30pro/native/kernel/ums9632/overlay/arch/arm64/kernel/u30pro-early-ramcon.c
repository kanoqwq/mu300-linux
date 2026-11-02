// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic boot console using only the first page of reserved ramoops console.
 * It has its own permanent fixmap, not the UART earlycon slot. No allocations,
 * storage access, DT mutation, watchdog feeding, or board register access.
 */
#include <linux/console.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/libfdt.h>
#include <linux/of_fdt.h>
#include <linux/printk.h>
#include <linux/string.h>
#include <linux/u30pro_early_ramcon.h>
#include <asm/barrier.h>
#include <asm/fixmap.h>

#define RAMCON_SIG 0x43474244U /* persistent_ram_buffer, same as stock ramoops */
#define RAMCON_HEADER 12U
#define RAMCON_CAPACITY (4096U - RAMCON_HEADER)
static void __iomem *ramcon_mem;
static phys_addr_t ramcon_phys;

static void ramcon_write(struct console *con, const char *text, unsigned int n)
{
	u32 pos, size;
	void __iomem *mem = ramcon_mem;

	if (!mem)
		return;
	pos = readl(mem + 4);
	size = readl(mem + 8);
	if (pos >= RAMCON_CAPACITY || size > RAMCON_CAPACITY)
		return;
	if (n > RAMCON_CAPACITY) {
		text += n - RAMCON_CAPACITY;
		n = RAMCON_CAPACITY;
	}
	while (n--) {
		writeb(*text++, mem + RAMCON_HEADER + pos);
		if (++pos == RAMCON_CAPACITY)
			pos = 0;
		if (size < RAMCON_CAPACITY)
			++size;
	}
	wmb();
	/* Size before start keeps start <= size during partial-buffer commits. */
	writel(size, mem + 8);
	writel(pos, mem + 4);
	dsb(sy);
}

static int ramcon_exit(struct console *con)
{
	if (ramcon_mem) {
		ramcon_mem = NULL;
		clear_fixmap(FIX_U30PRO_RAMCON);
	}
	return 0;
}

static struct console ramcon = {
	.name = "u30ram",
	.write = ramcon_write,
	.exit = ramcon_exit,
	.flags = CON_BOOT | CON_PRINTBUFFER | CON_ANYTIME,
	.index = -1,
};

static bool __init dt_u32_eq(const void *fdt, int node, const char *name, u32 value)
{
	int len;
	const fdt32_t *p = fdt_getprop(fdt, node, name, &len);

	return p && len == sizeof(*p) && fdt32_to_cpu(*p) == value;
}

void __init u30pro_early_ramcon_init(void)
{
	const void *fdt = initial_boot_params;
	const fdt32_t *reg;
	const char *status;
	int node, parent, len;
	u64 base, bytes;
	u32 sig, pos, size;
	bool board, qemu;

	BUILD_BUG_ON(PAGE_SIZE != 4096);
	if (!fdt || fdt_check_header(fdt))
		return;
	board = !fdt_node_check_compatible(fdt, 0, "sprd,ums9632");
	qemu = !fdt_node_check_compatible(fdt, 0, "linux,dummy-virt");
	if (!board && !qemu)
		return;
	node = fdt_node_offset_by_compatible(fdt, -1, "ramoops");
	parent = fdt_path_offset(fdt, "/reserved-memory");
	if (node < 0 || parent < 0 || fdt_parent_offset(fdt, node) != parent ||
	    fdt_node_offset_by_compatible(fdt, node, "ramoops") >= 0 ||
	    !dt_u32_eq(fdt, parent, "#address-cells", 2) ||
	    !dt_u32_eq(fdt, parent, "#size-cells", 2))
		return;
	status = fdt_getprop(fdt, node, "status", &len);
	if (status && (len <= 0 || !memchr(status, '\0', len) ||
		(strcmp(status, "okay") && strcmp(status, "ok"))))
		return;
	if (!dt_u32_eq(fdt, node, "record-size", 0x8000) ||
	    !dt_u32_eq(fdt, node, "console-size", 0x8000) ||
	    !dt_u32_eq(fdt, node, "pmsg-size", 0x8000) ||
	    fdt_getprop(fdt, node, "ftrace-size", NULL) ||
	    fdt_getprop(fdt, node, "ecc-size", NULL))
		return;
	reg = fdt_getprop(fdt, node, "reg", &len);
	if (!reg || len != 4 * sizeof(*reg))
		return;
	base = ((u64)fdt32_to_cpu(reg[0]) << 32) | fdt32_to_cpu(reg[1]);
	bytes = ((u64)fdt32_to_cpu(reg[2]) << 32) | fdt32_to_cpu(reg[3]);
	/* Fixed, independently audited board/QEMU reservations only. */
	if (bytes != 0x40000 || (board && base != 0xfff80000ULL) ||
	    (qemu && base != 0x47f80000ULL))
		return;
	ramcon_phys = base + 0x30000;
	set_fixmap_io(FIX_U30PRO_RAMCON, ramcon_phys);
	ramcon_mem = (void __iomem *)__fix_to_virt(FIX_U30PRO_RAMCON);
	sig = readl(ramcon_mem);
	pos = readl(ramcon_mem + 4);
	size = readl(ramcon_mem + 8);
	/* Keep any valid existing log that cannot fit this one-page writer. */
	if (sig == RAMCON_SIG &&
	    (size > RAMCON_CAPACITY || pos > size ||
	     (size < RAMCON_CAPACITY && pos != size))) {
		ramcon_exit(&ramcon);
		return;
	}
	if (sig != RAMCON_SIG) {
		writel(0, ramcon_mem + 4);
		writel(0, ramcon_mem + 8);
		writel(RAMCON_SIG, ramcon_mem);
	} else if (pos == RAMCON_CAPACITY) {
		writel(0, ramcon_mem + 4);
	}
	dsb(sy);
	register_console(&ramcon);
	pr_info("U30RAM21 START console=%pa capacity=%u before-early-params\n",
		&ramcon_phys, RAMCON_CAPACITY);
}

void u30pro_early_ramcon_checkpoint(const char *stage)
{
	if (ramcon_mem)
		pr_info("U30RAM21 %s\n", stage);
}

void u30pro_early_ramcon_handoff(phys_addr_t console_addr)
{
	if (!ramcon_mem || console_addr != ramcon_phys)
		return;
	pr_info("U30RAM21 handoff-to-ramoops\n");
	unregister_console(&ramcon);
}
