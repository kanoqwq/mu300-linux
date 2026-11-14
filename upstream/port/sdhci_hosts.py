"""Do not probe the unused Wi-Fi SDIO controller on PCIe-WLAN UMS9620."""
from pathlib import Path


def apply(tree):
    path = Path(tree) / 'drivers/mmc/host/sdhci-sprd.c'
    text = path.read_text()
    marker = 'MU300: WLAN uses PCIe, not the spare SDIO host'
    if marker in text:
        return
    start = text.index('static int sdhci_sprd_probe(')
    anchor = '\thost = sdhci_pltfm_init('
    pos = text.index(anchor, start)
    text = text[:pos] + '''\t/* MU300: WLAN uses PCIe, not the spare SDIO host.
\t * Keep eMMC, TF and other platforms (including AIC SDIO) unchanged. */
\tif (of_machine_is_compatible("sprd,ums9620") &&
\t    of_property_match_string(pdev->dev.of_node, "sprd,name", "sdio_wifi") >= 0)
\t\treturn -ENODEV;

''' + text[pos:]
    path.write_text(text)
