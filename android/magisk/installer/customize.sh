# MU300 Linux installer zip. Magisk sources this file into its own installer shell, so it changes none of that
# shell's options and never exits: the installation runs as a child process (mu300/install.sh) under Magisk's
# busybox, and its exit status decides - 0 installed, 3 dry run, anything else not installed. Only after success
# does this zip become the "MU300 Linux" switch module (Action button, su -c mu300-linux). The three messages here
# stay English: they are shown before the installer knows the language.
SKIPUNZIP=1
if ! $BOOTMODE; then
    abort "! Install this zip in the Magisk app or with: magisk --install-module <zip> (not from recovery)"
fi
[ "${MAGISK_VER_CODE:-0}" -ge 26000 ] || abort "! This zip needs Magisk 26.0 or newer"
# Everything taken from the zip comes out of it here, once: the zip may lie on /sdcard, where any app can replace it
# while the installation runs, and the module's files (system/* among them) run as root later.
mu300_x=$TMPDIR/mu300-zip
rm -rf "$mu300_x"
unzip -o -q "$ZIPFILE" 'mu300/*' module.prop action.sh switch.sh bootchain.sh 'system/*' -d "$mu300_x" >&2 ||
    abort "! The zip could not be unpacked"
# settings a user put into the zip, at its top (zip mu300-magisk-....zip mu300-install.conf): the only way to tell an
# update anything about the password (mu300/install.sh, conf_read). Most zips have none.
mu300_conf=
if unzip -l "$ZIPFILE" mu300-install.conf >/dev/null 2>&1 &&
    unzip -o -q "$ZIPFILE" mu300-install.conf -d "$mu300_x" >&2 && [ -f "$mu300_x/mu300-install.conf" ]; then
    mu300_conf=$mu300_x/mu300-install.conf
fi
MU300_DIR=$mu300_x/mu300 ZIPFILE=$ZIPFILE MAGISKBIN=${MAGISKBIN:-/data/adb/magisk} MU300_ZIP_CONF=$mu300_conf \
    "${MAGISKBIN:-/data/adb/magisk}/busybox" sh "$mu300_x/mu300/install.sh"
mu300_rc=$?
rm -rf "$mu300_x/mu300"
case $mu300_rc in
    0) ;;
    3) rm -rf "$mu300_x"; abort "- Dry run: nothing was written, the module was not installed" ;;
    *) rm -rf "$mu300_x"; abort "! MU300 Linux was not installed; the messages above say why and what was not changed" ;;
esac
mkdir -p "$MODPATH" && cp -R "$mu300_x/module.prop" "$mu300_x/action.sh" "$mu300_x/switch.sh" "$mu300_x/bootchain.sh" "$mu300_x/system" "$MODPATH/" ||
    { rm -rf "$mu300_x"; abort "! The switch module could not be installed"; }
rm -rf "$mu300_x"
set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/switch.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/bootchain.sh" 0 0 0755
set_perm "$MODPATH/system/bin/mu300-linux" 0 0 0755
unset mu300_x mu300_rc mu300_conf
