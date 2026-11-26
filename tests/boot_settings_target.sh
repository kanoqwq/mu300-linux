#!/bin/sh
# The retained prepared root is read-only; /mnt and /dev are private tmpfs mounts.
set -eu
# These tests only redirect output here; a regular scratch sink needs no devices.
: > /target/dev/null
chmod 666 /target/dev/null
exec chroot "$@"
