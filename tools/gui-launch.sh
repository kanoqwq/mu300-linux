# The graphical installer (gui/): the question that offers it, and the Python that can run it. Sourced by install.sh
# (t, ask, say and ANSWERS_MODE are its own; needs $TOP). The window drives this same script in answers mode
# (tools/answers.sh), so nothing is installed differently from there.
#   MU300_GUI=0   never ask: the text installer
#   MU300_GUI=1   do not ask: the graphical installer (the text installer when it cannot start)
# Asked once, and only of a person: both ends a terminal, no answers mode, not when the window itself runs this
# script (MU300_FROM_GUI). Nothing is installed here without a yes to a question that shows the command first, and
# every failure and every "no" leads to the text installer, which goes on as if nothing had been asked.

GUI_MIN_MINOR=8     # Python 3.8: the oldest the window is written for
# ... and Tk 8.6: the Tk 8.5 that macOS's own Python (the Command Line Tools') brings opens a window that stays black
# on current macOS, so a Python whose tkinter is that old counts as one without tkinter
GUI_TK_PROBE='import sys, tkinter; sys.exit(0 if tkinter.TkVersion >= 8.6 else 4)'

# gui_asked: whether the question is put at all
gui_asked() {
    [ "${MU300_GUI:-}" != 0 ] || return 1
    [ -z "${MU300_FROM_GUI:-}" ] || return 1
    [ "${ANSWERS_MODE:-0}" = 0 ] || return 1
    [ "${MU300_GUI:-}" = 1 ] && return 0
    [ -t 0 ] && [ -t 1 ]
}

# gui_display: is there a screen for a window? Not in an SSH session, and not on Linux without X11 or Wayland.
gui_display() {
    [ -z "${SSH_CONNECTION:-}${SSH_CLIENT:-}${SSH_TTY:-}" ] || return 1
    case $(gui_os) in
        Darwin) return 0 ;;
        *) [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] ;;
    esac
}
# (MU300_GUI_OS and MU300_GUI_ROOT are for tests/: another system's name, and a directory that stands for /)
gui_os() { echo "${MU300_GUI_OS:-$(uname -s)}"; }

# gui_py_candidates: every place a Python 3 may be, one path per line, the likeliest first; a path is listed once.
# PATH first (python3, python, then python3.14 .. python3.8), then where each system's installers put one: on
# macOS Homebrew (/opt/homebrew/bin, /usr/local/bin), the python.org framework builds and /usr/bin/python3; on
# Linux /usr/bin and /usr/local/bin.
gui_py_candidates() {
    _gr=${MU300_GUI_ROOT:-}
    _gv="python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8"
    {
        for _gn in python3 python $_gv; do command -v "$_gn" 2>/dev/null || true; done
        case $(gui_os) in
            Darwin)
                for _gd in /opt/homebrew/bin /usr/local/bin; do
                    for _gn in python3 $_gv; do echo "$_gr$_gd/$_gn"; done
                done
                for _gf in "$_gr"/Library/Frameworks/Python.framework/Versions/*/bin/python3; do echo "$_gf"; done
                echo "$_gr/usr/bin/python3" ;;
            *)
                for _gd in /usr/bin /usr/local/bin; do
                    for _gn in python3 $_gv; do echo "$_gr$_gd/$_gn"; done
                done ;;
        esac
    } | awk '!seen[$0]++' | while IFS= read -r _gp; do
        case $_gp in /*) [ -x "$_gp" ] && [ ! -d "$_gp" ] && echo "$_gp" ;; esac
    done
}

# gui_py_is_clt_stub PATH: macOS's /usr/bin/python3 is a stub of the Command Line Tools. With the tools installed it
# is their Python (which has tkinter); without them, running it only opens the dialog that offers to install them.
# So it is run only when xcode-select says the tools are there, and the dialog is never triggered from here.
gui_py_is_clt_stub() {
    [ "$(gui_os)" = Darwin ] || return 1
    case $1 in /usr/bin/python3|/usr/bin/python|"${MU300_GUI_ROOT:-}/usr/bin/python3"|"${MU300_GUI_ROOT:-}/usr/bin/python") ;; *) return 1 ;; esac
    ! xcode-select -p >/dev/null 2>&1
}

# gui_py_probe PATH: what this Python is. PY_VERSION is "3.12" (empty when it does not run or is no Python), PY_OLD
# is 1 when it is older than 3.8, PY_TK is 1 when it can import a tkinter with Tk 8.6 or newer (PY_TK_OLD is 1 when
# its Tk is older). The version and tkinter are asked separately, so the messages can say which is missing. Status
# 0 only for a Python new enough (with or without tkinter).
gui_py_probe() {
    PY_VERSION=; PY_OLD=0; PY_TK=0; PY_TK_OLD=0
    gui_py_is_clt_stub "$1" && return 1
    _gv=$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null </dev/null) || return 1
    case $_gv in [0-9]*.[0-9]*) ;; *) return 1 ;; esac
    case $_gv in *[!0-9.]*) return 1 ;; esac
    PY_VERSION=$_gv
    if [ "${_gv%%.*}" -ne 3 ] || [ "${_gv#*.}" -lt $GUI_MIN_MINOR ]; then PY_OLD=1; return 1; fi
    _gt=0; "$1" -c "$GUI_TK_PROBE" >/dev/null 2>&1 </dev/null || _gt=$?
    case $_gt in 0) PY_TK=1 ;; 4) PY_TK_OLD=1 ;; esac
    return 0
}

# gui_py_find: GUI_PY, the first Python that is new enough and has tkinter (empty: none). When there is none,
# GUI_PY_NOTK / GUI_PY_NOTK_VERSION are a Python that is new enough but has no usable tkinter - the first one that
# gui_tk_command knows a command for, else the first (GUI_PY_NOTK_OLDTK=1: it has one, with a Tk older than 8.6) -
# and GUI_PY_OLD / GUI_PY_OLD_VERSION the first that is too old.
gui_py_find() {
    GUI_PY=; GUI_PY_VERSION=; GUI_PY_NOTK=; GUI_PY_NOTK_VERSION=; GUI_PY_NOTK_OLDTK=0; GUI_PY_OLD=; GUI_PY_OLD_VERSION=
    _gfix=0
    _gl=$(gui_py_candidates)
    _gifs=$IFS; IFS='
'
    for _gc in $_gl; do
        IFS=$_gifs
        if gui_py_probe "$_gc"; then
            if [ $PY_TK = 1 ]; then
                GUI_PY=$_gc; GUI_PY_VERSION=$PY_VERSION
                GUI_PY_NOTK=; GUI_PY_NOTK_VERSION=; GUI_PY_NOTK_OLDTK=0; GUI_PY_OLD=; GUI_PY_OLD_VERSION=
                return 0
            fi
            if [ $_gfix = 0 ]; then
                [ -z "$(gui_tk_command "$_gc" "$PY_VERSION")" ] || _gfix=1
                if [ -z "$GUI_PY_NOTK" ] || [ $_gfix = 1 ]; then
                    GUI_PY_NOTK=$_gc; GUI_PY_NOTK_VERSION=$PY_VERSION; GUI_PY_NOTK_OLDTK=$PY_TK_OLD
                fi
            fi
        elif [ $PY_OLD = 1 ]; then
            [ -n "$GUI_PY_OLD" ] || { GUI_PY_OLD=$_gc; GUI_PY_OLD_VERSION=$PY_VERSION; }
        fi
    done
    IFS=$_gifs
    return 1
}

# gui_tk_command PYTHON VERSION: the command that adds tkinter to this system, for the Python found without it
# (empty: none known). Debian/Ubuntu, Fedora, Arch, openSUSE by their package tools; on macOS a Homebrew Python gets
# Homebrew's python-tk of the same version (python.org's and the Command Line Tools' Pythons have tkinter already).
gui_tk_command() {
    case $(gui_os) in
        Darwin)
            case $1 in
                "${MU300_GUI_ROOT:-}"/opt/homebrew/*|"${MU300_GUI_ROOT:-}"/usr/local/*)
                    command -v brew >/dev/null 2>&1 && echo "brew install python-tk@$2" ;;
            esac ;;
        *)
            if command -v apt-get >/dev/null 2>&1; then echo "sudo apt-get install -y python3-tk"
            elif command -v dnf >/dev/null 2>&1; then echo "sudo dnf install -y python3-tkinter"
            elif command -v pacman >/dev/null 2>&1; then echo "sudo pacman -S --noconfirm tk"
            elif command -v zypper >/dev/null 2>&1; then echo "sudo zypper install -y python3-tk"
            fi ;;
    esac
    return 0
}

# gui_offer: the question, the search and the start. Returns when the text installer should go on (a "no", no
# Python, a window that could not start); exits when the graphical installer ran and ended by itself.
gui_offer() {
    gui_asked || return 0
    if [ "${MU300_GUI:-}" != 1 ]; then
        _gd=yes; gui_display || _gd=no
        ask _ga "$(t 'Use the graphical installer (a window with the same steps)? (yes/no)')" $_gd MU300_GUI
        [ "$_ga" = yes ] || return 0
    fi
    if ! gui_py_find; then
        if [ -n "$GUI_PY_NOTK" ]; then
            if [ "$GUI_PY_NOTK_OLDTK" = 1 ]; then
                echo "  $(t 'Python {1} is there ({2}), but its Tk is older than 8.6 and cannot draw the window.' "$GUI_PY_NOTK_VERSION" "$GUI_PY_NOTK")"
            else
                echo "  $(t 'Python {1} is there ({2}), but without tkinter, which the window is made with.' "$GUI_PY_NOTK_VERSION" "$GUI_PY_NOTK")"
            fi
            _gc=$(gui_tk_command "$GUI_PY_NOTK" "$GUI_PY_NOTK_VERSION")
            if [ -n "$_gc" ]; then
                echo "  $(t 'This command installs it:')"
                echo "    $_gc"
                ask _ga "$(t 'Run that command now? (yes/no)')" no MU300_GUI_INSTALL_TK
                if [ "$_ga" = yes ]; then
                    sh -c "$_gc" || echo "  $(t 'the command failed')"
                    gui_py_find || true
                fi
            else
                case $(gui_os) in
                    Darwin) echo "  $(t 'Get it from python.org, or with Homebrew: brew install python-tk')" ;;
                    *) echo "  $(t 'Install tkinter for it with the package tool of this system (the package is usually called python3-tk).')" ;;
                esac
            fi
        elif [ -n "$GUI_PY_OLD" ]; then
            echo "  $(t 'Python {1} is there ({2}), but the window needs Python 3.8 or newer.' "$GUI_PY_OLD_VERSION" "$GUI_PY_OLD")"
        else
            echo "  $(t 'No Python 3 was found for the window.')"
            case $(gui_os) in
                Darwin) echo "  $(t 'Get it from python.org, or with Homebrew: brew install python-tk')" ;;
                *) echo "  $(t 'Install tkinter for it with the package tool of this system (the package is usually called python3-tk).')" ;;
            esac
        fi
    fi
    if [ -z "$GUI_PY" ]; then
        echo "  $(t 'Continuing with the text installer.')"
        return 0
    fi
    [ -f "$TOP/gui/mu300-gui.py" ] || { echo "  $(t 'Continuing with the text installer.')"; return 0; }
    say "$(t 'Starting the graphical installer (Python {1})' "$GUI_PY_VERSION")"
    _gs=0
    MU300_LANG=$MU300_LANG "$GUI_PY" "$TOP/gui/mu300-gui.py" || _gs=$?
    [ $_gs != 0 ] || exit 0
    echo "  $(t 'The graphical installer could not start or stopped with an error ({1}).' "$_gs")"
    echo "  $(t 'Continuing with the text installer.')"
    return 0
}
