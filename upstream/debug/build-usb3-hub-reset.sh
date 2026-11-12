#!/bin/bash
set -euo pipefail
src=$(cd "$(dirname "$0")" && pwd)/usb3-hub-reset.c
out=${1:?usage: build-usb3-hub-reset.sh OUTPUT}
aarch64-linux-gnu-gcc -Os -Wall -Wextra -Werror -static -s "$src" -o "$out"
sha256sum "$out"
