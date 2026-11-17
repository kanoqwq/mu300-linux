#!/bin/sh
set -eu
TOP=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:?usage: build.sh REGISTERED_INPUTS/tools/bt-init}
sh "$TOP/tools/build-static-helper.sh" "$TOP/tools/bt-init/mu300-bt-init.c" "$OUT/mu300-bt-init"
python3 "$TOP/tools/atd-io/artifact.py" stamp "$OUT/mu300-bt-init" "$TOP/tools/bt-init/mu300-bt-init.c"
