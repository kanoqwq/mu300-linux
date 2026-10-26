"""The graphical installer of MU300 Linux (see ../mu300-gui.py).

    i18n         the catalogs of i18n/<lang>.tsv, shared with the text installers
    masking      what may not leave the computer in a log: serial numbers, IMEI, IMSI, ICCID, passwords
    adb          the adb devices, as the installers find and tell them apart; the device in Linux
    releases     the newest release and the beta, decided by tools/release_pick.py
    model        the facts of a device, the choices, the answers they become, progress - no window needed
    runner       one run of an installer in answers mode: its events, its text, an answer on standard input
    controller   what the window does, without the window: checks, runs, the device list
    theme, app   the window

The install logic is not here: the window asks the questions the text installer asks, hands the answers to the same
scripts (docs/INSTALLER-AUTOMATION.md) and shows what they report.
"""
import os

TOP = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

WIKI = 'https://github.com/dikeckaan/mu300-linux/wiki'
RELEASES = 'https://github.com/dikeckaan/mu300-linux/releases'
ISSUES = 'https://github.com/dikeckaan/mu300-linux/issues'
TELEGRAM = 'https://t.me/mu300project'
SITE = 'https://kaandikec.com/mu300-linux/'
REPO = 'dikeckaan/mu300-linux'
