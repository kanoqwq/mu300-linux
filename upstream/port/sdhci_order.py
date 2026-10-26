"""sdhci-sprd's register accessors with the ordering of the generic SDHCI ones (docs/FINDINGS.md 93).

Mainline sdhci-sprd overrides readl/writel/writew/writeb to handle its reserved registers, and does the access
itself with the *_relaxed forms. The SDHCI core writes the ADMA descriptor table to DMA memory and then starts the
transfer with a register write (SDHCI_COMMAND through writew): a relaxed MMIO write is not ordered after those
memory stores, so the controller can fetch a descriptor the CPU has not yet made visible. The generic accessors
(readl/writel...) carry the barrier that orders them. The vendor special cases (MAX_CURRENT, BLOCK_COUNT, the
interrupt mask, the reset bits) are kept as they are: only the final access changes.

After kanoqwq's clean-tf-7.2 0438ab01 (his upstream/port/emmc.py; his measurement was on a different board, an
E5, see FINDINGS 93). Used by install.py; files directly in port/ are not copied into the kernel tree.
"""
import re

START = 'static inline u32 sdhci_sprd_readl('
END = 'static inline void sdhci_sprd_sd_clk_off('
MARKER = 'MU300: ordered MMIO, as the generic SDHCI accessors (FINDINGS 93)'
SWAPS = (('\treturn readl_relaxed(host->ioaddr + reg);', '\t/* ' + MARKER + ' */\n\treturn readl(host->ioaddr + reg);'),
         ('\twritel_relaxed(val, host->ioaddr + reg);', '\twritel(val, host->ioaddr + reg);'),
         ('\twritew_relaxed(val, host->ioaddr + reg);', '\twritew(val, host->ioaddr + reg);'),
         ('\twriteb_relaxed(val, host->ioaddr + reg);', '\twriteb(val, host->ioaddr + reg);'))


def ordered(text):
    """TEXT (sdhci-sprd.c) with the four accessors ordered; the same text when it already is. Raises ValueError
    when the accessors are not the ones this edit knows (a kernel that changed them must be looked at again)."""
    try:
        start = text.index(START)
        end = text.index(END, start)
    except ValueError:
        raise ValueError('sdhci-sprd accessor anchors changed')
    block = text[start:end]
    if MARKER not in block:
        for old, new in SWAPS:
            if block.count(old) != 1:
                raise ValueError('sdhci-sprd accessor anchor changed: ' + old.strip())
            block = block.replace(old, new)
    if re.search(r'\bwrite[bwl]_relaxed\(|return readl_relaxed\(', block) or any(new.split('\n')[-1] not in block for _, new in SWAPS):
        raise ValueError('sdhci-sprd accessors not as expected after the edit')
    return text[:start] + block + text[end:]
