#!/bin/sh
# Offline only: fake sysfs, synthetic AT owners, no real modem or GPIO access.
set -eu
[ "${U30PRO_BUILD_LOCKED:-}" = 1 ] || { echo 'Use tools/build-release.py run' >&2; exit 2; }
top=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
for name in test_v50_sim test_u30pro_cards test_data_sim test_dual_radio test_sim_scope test_sim_radio; do
    python3 -m unittest discover -s "$top/tests" -p "$name.py" -v
done
node "$top/tests/dashboard_sim.js"
