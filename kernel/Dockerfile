# Native arm64 build environment for the Unisoc 5.4 kernel (device kernel used Android clang 12.0.5)
FROM ubuntu:20.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    clang-12 lld-12 llvm-12 build-essential bc bison flex libssl-dev libelf-dev \
    python3 python-is-python3 cpio git kmod ca-certificates rsync lz4 xz-utils file \
 && rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/lib/llvm-12/bin:$PATH
WORKDIR /src
