#!/bin/sh
# MU300 Linux from this zip, on a macOS or Linux computer, in a window: the graphical installer, with the system and
# the kernel of this zip, from its own files. It needs a Python 3.8 or newer with tkinter; the installer looks for one,
# offers the command that adds tkinter where it is missing, and goes on as the text installer when there is no window
# to be had. START-HERE.md says more.
HERE=$(cd "$(dirname "$0")" && pwd)
# adb from Google's platform-tools unpacked into this folder (platform-tools/adb) needs no PATH entry
if [ -x "$HERE/platform-tools/adb" ]; then PATH="$HERE/platform-tools:$PATH"; export PATH; fi
MU300_PACKAGE=$HERE
MU300_GUI=1
export MU300_PACKAGE MU300_GUI
exec sh "$HERE/computer/install.sh" "$@"
