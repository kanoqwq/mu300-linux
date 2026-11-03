#!/bin/sh
# Host-only integration checks. All modem bytes go to synthetic PTYs.
set -eu
[ "${U30PRO_BUILD_LOCKED:-}" = 1 ] || { echo 'Use tools/build-release.py run' >&2; exit 2; }
top=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
native=$top/devices/u30pro/native
testdir=$(mktemp -d "${TMPDIR:?runner supplies owned temporary storage}/u30pro-native.XXXXXX")
trap 'rm -rf -- "$testdir"' EXIT HUP INT TERM
python3 "$top/tools/u30pro-build.py" check --sources-only
python3 "$top/tests/u30pro_port.py"
python3 "$top/tests/u30pro_navigation.py"
python3 "$top/tests/u30pro_power_events.py"
python3 "$top/tests/u30pro_charge_sources.py"
python3 "$top/tests/u30pro_usb_roles.py"
python3 -m unittest discover -s "$top/tests" -p test_u30pro_nfc.py
python3 -m unittest discover -s "$top/tests" -p test_nfc_dashboard.py
python3 -m unittest discover -s "$top/tests" -p test_v50_sim.py
python3 -m unittest discover -s "$top/tests" -p test_u30pro_cards.py
# The production native tools use Zig/Clang, not GCC-specific diagnostics.
cc=${CC:-clang}
"$cc" -std=gnu11 -O2 -Wall -Wextra -Werror -DATD_TEST "$native/tools/u30pro_atd.c" -o "$testdir/atd"
"$cc" -std=gnu11 -O2 -Wall -Wextra -Werror "$native/tools/u30pro_atc.c" -o "$testdir/atc"
"$cc" -std=gnu11 -O2 -Wall -Wextra -Werror -DCELL_TEST "$native/tools/u30pro_celld.c" -o "$testdir/celld"
python3 "$native/tests/test_atd.py" "$testdir/atd" "$testdir/atc"
python3 "$native/tests/test_cellular_native.py" "$testdir/celld"
python3 "$native/tests/test_charger.py"
