#!/bin/bash
# Build the host simulator of the screen UI (WSL).  usage: bash tests/ui/build_host.sh
cd "$(dirname "$0")/../.."
mkdir -p /opt/u30test /tmp/u30ui-run
gcc -DUI_HOST '-DUI_RUN="/tmp/u30ui-run"' "-DUI_PLUGIN_DIR=\"$PWD/tests/ui/plugins\"" -O2 -Wall -Wextra -Werror -o /opt/u30test/ui-host tools/u30pro_ui.c -lm
