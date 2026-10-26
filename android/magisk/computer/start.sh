#!/bin/sh
# MU300 Linux from this zip, on a macOS or Linux computer: the installer, with the system and the kernel of this zip,
# from its own files - nothing is downloaded. It asks first whether to open its window (start-gui.sh opens it without
# asking). START-HERE.md says what it needs. Options are install.sh's: sh start.sh --check, sh start.sh --help.
HERE=$(cd "$(dirname "$0")" && pwd)
# adb from Google's platform-tools unpacked into this folder (platform-tools/adb) needs no PATH entry
if [ -x "$HERE/platform-tools/adb" ]; then PATH="$HERE/platform-tools:$PATH"; export PATH; fi
MU300_PACKAGE=$HERE
export MU300_PACKAGE
exec sh "$HERE/computer/install.sh" "$@"
