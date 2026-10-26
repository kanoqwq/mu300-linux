# The container of the update experiment (run.sh): Ubuntu's tools for the device path, and for the installers' path
# what Android has: mksh, toybox, the one true awk.
FROM ubuntu:24.04
RUN apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
    python3 dash bash busybox-static mksh toybox original-awk attr acl libcap2-bin e2fsprogs mount gcc libc6-dev \
    && rm -rf /var/lib/apt/lists/*
