#!/bin/sh
# Remove MU300 Linux from the device again, from a macOS or Linux computer: the project's uninstaller (it asks before
# anything is written; START-HERE.md). Options are uninstall.sh's: sh uninstall-linux.sh --help.
HERE=$(cd "$(dirname "$0")" && pwd)
if [ -x "$HERE/platform-tools/adb" ]; then PATH="$HERE/platform-tools:$PATH"; export PATH; fi
exec sh "$HERE/computer/uninstall.sh" "$@"
