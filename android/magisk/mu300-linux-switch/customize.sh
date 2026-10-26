#!/system/bin/sh
# Magisk install hook: check that this device is the one the module is for, and report what it found.
SKIPUNZIP=0

ui_print "- MU300 Linux switch"
by_name=/dev/block/by-name
[ -e "$by_name/misc" ] || by_name=$(dirname "$(ls -d /dev/block/platform/*/by-name/misc 2>/dev/null | head -n1)" 2>/dev/null)

if [ ! -e "$by_name/misc" ] || [ ! -e "$by_name/boot_a" ] || [ ! -e "$by_name/boot_b" ]; then
    ui_print "! No misc or A/B boot partitions: this is not an A/B device like the ZTE F50 / MU300."
    abort "! aborting"
fi

magic=$(dd if="$by_name/misc" bs=1 skip=2048 count=8 2>/dev/null | od -An -tx1 -v | tr -d ' \n' | cut -c9-16)
[ "$magic" = "42434142" ] || ui_print "! Warning: misc has no bootloader_control block yet ($magic)"

# Linux is on the slot Android is not running from
slot=$(getprop ro.boot.slot_suffix)
case $slot in _b) target=a ;; _a) target=b ;; *) abort "! Unknown Android boot slot: $slot" ;; esac
head_target=$(dd if="$by_name/boot_$target" bs=1 count=8 2>/dev/null | od -An -tx1 -v | tr -d ' \n')
if [ "$head_target" = "414e44524f494421" ]; then
    ui_print "- boot_$target holds a boot image"
else
    ui_print "! boot_$target is empty: install Linux first, then use this module."
fi

set_perm_recursive "$MODPATH" 0 0 0755 0644
set_perm "$MODPATH/switch.sh" 0 0 0755
set_perm "$MODPATH/action.sh" 0 0 0755
set_perm "$MODPATH/bootchain.sh" 0 0 0755
set_perm "$MODPATH/system/bin/mu300-linux" 0 0 0755

ui_print "- Installed. Use the module's Action button in the Magisk app,"
ui_print "  or run:  su -c mu300-linux        (su -c 'mu300-linux status' shows the slots)"
