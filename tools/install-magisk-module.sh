#!/bin/sh
# Install the "switch to Linux" Magisk module on the connected device (rooted Android, adb + su + Magisk).
#   tools/install-magisk-module.sh            install (or update) the module
#   tools/install-magisk-module.sh --remove   remove it again
#
# The files are copied straight into /data/adb/modules, which is what Magisk reads at boot; that needs no zip
# tool on this computer and no Magisk app interaction. The module itself only ever writes the 32-byte
# bootloader_control block in misc - see android/magisk/README.md.
#
# Nothing here is fatal for an installation: a device without Magisk simply keeps using ./install.sh from a
# computer to start Linux.
set -eu
TOP=$(cd "$(dirname "$0")/.." && pwd)
SRC=$TOP/android/magisk/mu300-linux-switch
MOD=/data/adb/modules/mu300_linux_switch
TMP=/data/local/tmp/mu300-magisk

su_do() { adb shell "su -c '$1'" 2>/dev/null | tr -d '\r'; }

have_magisk() { [ -n "$(su_do 'magisk -v' || true)" ]; }

if [ "${1:-}" = --remove ]; then
    have_magisk || { echo "no Magisk on this device, nothing to remove"; exit 0; }
    # Magisk removes a module on the next boot when it finds this marker; deleting the directory outright would
    # leave its mounted files behind until the reboot anyway
    su_do "[ -d $MOD ] && touch $MOD/remove" >/dev/null 2>&1 || true
    su_do "rm -rf $TMP" >/dev/null 2>&1 || true
    echo "Magisk module marked for removal (gone after the next reboot)"
    exit 0
fi

[ -f "$SRC/module.prop" ] || { echo "module source missing at $SRC" >&2; exit 1; }
if ! have_magisk; then
    echo "no Magisk (or no root) on this device - skipping the on-device switch module"
    exit 0
fi

# The module names the release it came with, as the Magisk zip's does (android/magisk/installer/module.prop.in, the same
# version and versionCode as tools/make-magisk-zips.sh): an update from the computer replaced the zip's module of the
# release with the generic one, "v1.0" (seen on an F50, docs/FINDINGS.md 131). MU300_MODULE_TAG (the release installed;
# install.sh passes it), MU300_MODULE_SYSTEM, MU300_MODULE_KERNEL; without a release tag (a build of the checkout) the
# generic module.prop.
PROP=$SRC/module.prop
TAG=${MU300_MODULE_TAG:-}
. "$TOP/tools/releases.sh"
if code=$(tag_key "$TAG"); then
    case $code in 2*) code=${code#2} ;; *) code=${code#1}; code=${code%00} ;; esac
    PROP=$(mktemp)
    trap 'rm -f "$PROP"' EXIT
    # (the values go into sed's replacement: what could break it is left out)
    sys=$(printf '%s' "${MU300_MODULE_SYSTEM:-}" | tr -cd 'A-Za-z0-9. -' | sed 's/ *$//'); ker=$(printf '%s' "${MU300_MODULE_KERNEL:-}" | tr -cd '0-9.')
    sed -e "s/@TAG@/$TAG/" -e "s/@CODE@/$code/" -e "s/@SYSTEM@/${sys:-MU300 Linux}/" -e "s/@KERNEL@/${ker:-?}/" \
        "$TOP/android/magisk/installer/module.prop.in" > "$PROP"
fi

adb shell "rm -rf $TMP" >/dev/null 2>&1 || true
adb shell "mkdir -p $TMP/system/bin" >/dev/null 2>&1
adb push "$PROP" "$TMP/module.prop" >/dev/null 2>&1
for f in switch.sh action.sh bootchain.sh; do
    adb push "$SRC/$f" "$TMP/$f" >/dev/null 2>&1
done
adb push "$SRC/system/bin/mu300-linux" "$TMP/system/bin/mu300-linux" >/dev/null 2>&1

su_do "rm -rf $MOD && mkdir -p $MOD/system/bin && cp -a $TMP/module.prop $TMP/switch.sh $TMP/action.sh $TMP/bootchain.sh $MOD/ && cp -a $TMP/system/bin/mu300-linux $MOD/system/bin/ && chown -R 0:0 $MOD && chmod 755 $MOD/switch.sh $MOD/action.sh $MOD/bootchain.sh $MOD/system/bin/mu300-linux && chmod 644 $MOD/module.prop && rm -rf $TMP && sync" >/dev/null

ok=$(su_do "[ -x $MOD/switch.sh ] && echo yes")
[ "$ok" = yes ] || { echo "the Magisk module did not land on the device" >&2; exit 1; }

echo "Magisk module installed: after the next Android boot, 'su -c mu300-linux' (or the module's Action"
echo "button in the Magisk app) starts Linux without a computer."
