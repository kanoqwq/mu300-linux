#!/bin/sh
# Pull Android's Mali GPU userspace (OpenCL, EGL/GLES, Vulkan ICD) and its library closure from a rooted device.
# The result is merged into the vendor chroot (/opt/mu300/android) by rootfs/assemble.sh. Proprietary: not in this repo.
set -eu
OUT=${1:-android-gpu-subset}
mkdir -p "$OUT"
MU300_CLOSURE_ROOT="$OUT" python3 "$(dirname "$0")/pull_closure.py" \
  /vendor/lib64/libOpenCL.so /vendor/lib64/egl/libGLES_mali.so /vendor/lib64/hw/vulkan.ums9620.so
du -sh "$OUT"
