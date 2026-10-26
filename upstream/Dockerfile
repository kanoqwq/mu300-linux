# Native arm64 build environment for mainline kernels (gcc from Ubuntu 26.04); pahole (dwarves) turns the DWARF
# into the BTF that CONFIG_DEBUG_INFO_BTF puts into the kernel and its modules (eBPF programs such as dae need it)
# gpgv checks the kernel source's signatures (verify-kernel-src.sh; listed although apt already depends on it)
FROM ubuntu:26.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential bc bison flex libssl-dev libelf-dev lz4 cpio kmod python3 curl device-tree-compiler xz-utils ca-certificates dwarves gpgv \
 && rm -rf /var/lib/apt/lists/*
