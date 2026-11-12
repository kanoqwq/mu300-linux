#!/bin/bash
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)/usb3-port-warm-reset.c
out=${1:?usage: build-usb3-port-warm-reset.sh OUTPUT}
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -Werror -static "$src" -o "$out"
aarch64-linux-gnu-strip "$out"
sha256sum "$out"
