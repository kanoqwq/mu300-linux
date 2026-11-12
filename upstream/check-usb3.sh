#!/bin/bash
# Compile USB3 changes against the existing prepared kernels, without releasing.
set -euo pipefail
export CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
for version in 6.18.54 7.2.8; do
    source_dir=/src/linux-$version
    objects=/src/out-$version
    test -f "$objects/.config"
    python3 /work/port/install.py "$source_dir"
    make -C "$source_dir" O="$objects" ARCH=arm64 -j4 \
        drivers/usb/phy/phy-sprd-ums9620-ssphy.o drivers/usb/dwc3/core.o \
        drivers/usb/typec/tcpm/sc27xx-ump9620.o drivers/usb/typec/tcpm/tcpm.o
done
