#!/bin/sh
# Target userspace fixture only: no host hardware/partitions or services mounted.
set -eu
apk update
apk add --no-scripts --allow-untrusted python3 python3-openssl /lpac/lpac-2.3.0-r1001.apk
export PYTHONDONTWRITEBYTECODE=1 UNISOC_LPAC_REFERENCE=/usr/lib/lpac
python3 /src/openwrt/tests/euicc.py
LPAC_APDU=stdio LPAC_HTTP=stdio /usr/lib/lpac driver list
