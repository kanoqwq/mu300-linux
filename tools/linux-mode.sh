# Shared by install.sh and uninstall.sh: both need the device in rooted Android, but it may be running MU300 Linux
# right now (then there is no adb device, only SSH on the USB network). Offer to send it back to Android.
# Uses: say(), die(), ask(), and t() from tools/i18n.sh (loaded here when the caller has not)
command -v t >/dev/null 2>&1 || . "$TOP/tools/i18n.sh"

# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1 (MU300_IP: another address)
MU300_IPS=${MU300_IP:-192.168.77.1 192.168.78.1}

# true when something answers on the Linux SSH port of the USB network; MU300_IP is then that device, and only it
# is watched from here on (another device may still be running Linux next to it)
linux_mode_running() {
    command -v nc >/dev/null || return 1
    for _lm_ip in $MU300_IPS; do
        nc -z -G 3 -w 3 "$_lm_ip" 22 >/dev/null 2>&1 && { MU300_IP=$_lm_ip; MU300_IPS=$_lm_ip; return 0; }
    done
    return 1
}

# ask the running Linux to boot Android next and reboot; then wait for adb
linux_mode_to_android() {
    say "$(t 'The device is running MU300 Linux, not Android')"
    echo "  $(t 'Installing and uninstalling happen from Android (slot a), so the device has to reboot first.')"
    ask go "$(t 'Reboot the device into Android now? (yes/no)')" yes
    [ "$go" = yes ] || die "$(t 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)')"
    # -t: sudo needs a terminal to ask for the device password, and everything runs in one sudo call so it is
    # asked only once. reboot cuts the connection, so ssh's exit status says nothing: watch the port instead.
    for user in ubuntu root; do
        echo "  $(t '{1} - enter the device password when asked (Ctrl-C to skip)' "$user@$MU300_IP")"
        ssh -t -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8 \
            "$user@$MU300_IP" 'if [ "$(id -u)" = 0 ]; then S=; else S=sudo; fi; $S sh -c "/opt/mu300/bin/mu300-next-boot android && sync && reboot"' || true
        n=0
        while [ $n -lt 8 ]; do
            linux_mode_running || { echo "  $(t 'rebooting')"; break; }
            n=$((n + 1)); sleep 5
        done
        linux_mode_running || break
    done
    if linux_mode_running; then
        die "$(t 'could not reboot it over SSH; on the device run: sudo mu300-next-boot android && sudo reboot')"
    fi
    echo "  $(t 'waiting for Android')"
    n=0
    while [ $n -lt 60 ]; do
        select_device quiet
        [ "$(adb get-state 2>/dev/null)" = device ] && { echo "  $(t 'Android is up')"; return 0; }
        n=$((n + 1)); sleep 5
    done
    die "$(t 'the device did not come back as Android; boot it yourself (mu300-next-boot android)')"
}

# With more than one adb device attached (a phone, an emulator, a device over the network) every plain adb command
# fails with "more than one device/emulator", which read as "no adb device". Pick the F50 and point adb at it with
# ANDROID_SERIAL: the only device, else the only one that says it is an F50/MU300 or a U30 Air, else ask.
select_device() {  # select_device [quiet]: quiet never asks, it only picks what is unambiguous
    [ -n "${ANDROID_SERIAL:-}" ] && return 0
    _sd_all=$(adb devices -l 2>/dev/null | awk 'NR > 1 && $2 == "device"')
    [ -n "$_sd_all" ] || return 0
    _sd_f50=$(printf '%s\n' "$_sd_all" | grep -E '(model:F50|product:MU300|device:MU300|device:U30Air|product:MU5352|device:MU5352|model:V50|product:MU3351|device:MU3351)([[:space:]]|$)' | awk '{print $1}')
    _sd_one=
    [ -n "$_sd_f50" ] && [ "$(printf '%s\n' "$_sd_f50" | wc -l | tr -d ' ')" = 1 ] && _sd_one=$_sd_f50
    # the only adb device, and an F50/U30 Air: nothing to ask
    if [ -n "$_sd_one" ] && [ "$(printf '%s\n' "$_sd_all" | wc -l | tr -d ' ')" = 1 ]; then
        export ANDROID_SERIAL=$_sd_one; return 0
    fi
    # quiet (waiting for the device to come back as Android): only the one F50/U30 Air, never a question
    if [ "${1:-}" = quiet ]; then
        [ -n "$_sd_one" ] && { export ANDROID_SERIAL=$_sd_one; return 0; }
        return 1
    fi
    # Otherwise always ask: a phone or tablet next to the device is what the installer must never write to, and
    # picking one by itself is how it got close (the F50 was in Linux, a tablet was the only one in Android)
    echo "  $(t 'which adb device is the F50, V50 or U30 Air?')"
    printf '%s\n' "$_sd_all" | awk '{ m = ""; for (i = 3; i <= NF; i++) if ($i ~ /^model:/) m = substr($i, 7); printf "    %d) %s %s\n", NR, $1, m }'
    _sd_def=1
    [ -n "$_sd_one" ] && _sd_def=$(printf '%s\n' "$_sd_all" | awk -v s="$_sd_one" '$1 == s {print NR}')
    ask _sd_n "$(t 'Device')" "$_sd_def"
    _sd_s=$(printf '%s\n' "$_sd_all" | awk -v n="$_sd_n" 'NR == n {print $1}')
    [ -n "$_sd_s" ] || die "$(t 'invalid choice')"
    export ANDROID_SERIAL=$_sd_s
}

# call before anything else that needs adb
# is one of the adb devices an F50/U30 Air?
target_attached() { adb devices -l 2>/dev/null | awk 'NR > 1 && $2 == "device"' | grep -q -E '(model:F50|product:MU300|device:MU300|device:U30Air|product:MU5352|device:MU5352|model:V50|product:MU3351|device:MU3351)([[:space:]]|$)'; }

require_android() {
    # The device in Linux, and only a phone or tablet in Android: that is not the one to install to - offer to
    # send the device back to Android first
    if [ -z "${ANDROID_SERIAL:-}" ] && ! target_attached && linux_mode_running; then
        linux_mode_to_android && return 0
    fi
    select_device
    if [ "$(adb get-state 2>/dev/null)" = device ]; then
        # an Android that is still starting answers adb before su and storage are ready: pulls then failed
        _ra=0
        while [ "$(adb shell getprop sys.boot_completed </dev/null 2>/dev/null | tr -d '\r')" != 1 ] && [ $_ra -lt 60 ]; do
            [ $_ra = 0 ] && echo "  $(t 'waiting for Android to finish starting')"
            _ra=$((_ra + 1)); sleep 2
        done
        return 0
    fi
    linux_mode_running && linux_mode_to_android && return 0
    die "$(t 'no adb device (boot Android, enable USB debugging)')"
}
