#!/usr/bin/env python3
"""The graphical installer of MU300 Linux: a window that drives install.sh / install.ps1 (and the uninstallers) in
their answers mode. Standard library only; Python 3.8 or newer with tkinter.

    python3 gui/mu300-gui.py            the window
    python3 gui/mu300-gui.py --check    can it start here? (Python, tkinter, a display); prints why not

install.sh and install.ps1 offer it when they are started by hand (tools/gui-launch.sh, tools/gui-launch.ps1).
Exit status 3: the window could not be opened (no tkinter, no display) - the caller goes on with the text installer.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.version_info < (3, 8):
    sys.stderr.write('The graphical installer needs Python 3.8 or newer (this is %d.%d).\n' % sys.version_info[:2])
    sys.exit(3)

from mu300gui.app import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
