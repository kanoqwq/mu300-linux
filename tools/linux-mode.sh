# Shared by install.sh and uninstall.sh: both need the device in rooted Android, but it may be running MU300 Linux
# right now. Offer to send it back to Android.
# A device in Linux is on the USB network (SSH) and, since adb is on by default there (FINDINGS 70), in
# "adb devices" too. Its USB serial number says which system answers: MU300LINUX-... in Linux (boot/init), the bare
# number in Android. A device in Linux is never one to install to, and nothing else would stop it: "su -c id -u"
# says 0 there as well, because the adb shell is root.
# Uses: say(), die(), ask(), and t() from tools/i18n.sh (loaded here when the caller has not)
command -v t >/dev/null 2>&1 || . "$TOP/tools/i18n.sh"

# the USB network of each kind of device: F50 192.168.77.1, U30 Air 192.168.78.1, V50 192.168.79.1 (MU300_IP: another
# address)
MU300_IPS=${MU300_IP:-192.168.77.1 192.168.78.1 192.168.79.1}

# true when something answers on the Linux SSH port of the USB network; MU300_IP is then that device, and only it
# is watched from here on (another device may still be running Linux next to it)
linux_mode_running() {
    command -v nc >/dev/null || return 1
    for _lm_ip in $MU300_IPS; do
        nc -z -G 3 -w 3 "$_lm_ip" 22 >/dev/null 2>&1 && { MU300_IP=$_lm_ip; MU300_IPS=$_lm_ip; return 0; }
    done
    return 1
}

# the adb devices in Android that answer (never a device in Linux), as "adb devices -l" prints them
android_lines() { adb devices -l 2>/dev/null | awk 'NR > 1 && $2 == "device" && $1 !~ /^MU300LINUX/'; }
# a device in Linux is among the adb devices, whether or not it lets this computer in ("unauthorized": its adb
# takes authorised keys only)
linux_adb_listed() {  # linux_adb_listed [SERIAL]
    adb devices -l 2>/dev/null | awk -v s="${1:-}" 'NR > 1 && $1 ~ /^MU300LINUX/ && (s == "" || $1 == s) {f = 1} END {exit !f}'
}
# the serial number of the device in Linux that lets this computer in: the one ANDROID_SERIAL named (LINUX_WANTED,
# kept by require_android), else the only one; status 1: none, the named one is not there, or more than one
LINUX_WANTED=
linux_adb() {
    _la=$(adb devices -l 2>/dev/null | awk -v s="$LINUX_WANTED" 'NR > 1 && $2 == "device" && $1 ~ /^MU300LINUX/ && (s == "" || $1 == s) {print $1}')
    [ -n "$_la" ] && [ "$(printf '%s\n' "$_la" | wc -l | tr -d ' ')" = 1 ] && echo "$_la"
}
# more than one device in Linux among the adb devices
linux_adb_many() {
    [ "$(adb devices -l 2>/dev/null | awk 'NR > 1 && $1 ~ /^MU300LINUX/' | wc -l | tr -d ' ')" -gt 1 ]
}
# "adb get-state" for the Android to work on: nothing when the only adb devices are in Linux, "linux" when
# ANDROID_SERIAL names one of those
android_state() {
    case ${ANDROID_SERIAL:-} in MU300LINUX*) echo linux; return 0 ;; esac
    [ -n "${ANDROID_SERIAL:-}" ] || [ -n "$(android_lines)" ] || return 0
    adb get-state 2>/dev/null
}

# ask the running Linux to boot Android next and reboot; then wait for adb
linux_mode_to_android() {
    say "$(t 'The device is running MU300 Linux, not Android')"
    echo "  $(t 'Installing and uninstalling happen from Android, so the device has to reboot first.')"
    ask go "$(t 'Reboot the device into Android now? (yes/no)')" yes MU300_ANS_REBOOT_ANDROID
    [ "$go" = yes ] || die "$(t 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)')"
    # Over adb where the Linux lets this computer in (every computer, by default): a root shell, no password to
    # type, and it works where the USB network does not (RNDIS on a Mac). The marker file first: only our Linux
    # is told to reboot.
    _lm_gone=0 _lm_ssh=0
    if _lm_s=$(linux_adb) &&
       [ "$(adb -s "$_lm_s" shell 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300' </dev/null 2>/dev/null | tr -d '\r')" = mu300 ]; then
        echo "  $(t 'asking it over adb ({1})' "$_lm_s")"
        adb -s "$_lm_s" shell '/opt/mu300/bin/mu300-next-boot android && sync && reboot' </dev/null >/dev/null 2>&1 || true
        n=0
        while [ $n -lt 8 ]; do
            linux_adb_listed "$_lm_s" || { echo "  $(t 'rebooting')"; _lm_gone=1; break; }
            n=$((n + 1)); sleep 5
        done
    fi
    # -t: sudo needs a terminal to ask for the device password, and everything runs in one sudo call so it is
    # asked only once. reboot cuts the connection, so ssh's exit status says nothing: watch the port instead.
    # Only where the address can be no other device: with two boards in Linux on adb, 192.168.77.1 or .78.1 may
    # well be the other one (measured: the F50 named by ANDROID_SERIAL next to a U30 Air in Linux at .78.1).
    # MU300_IP, given by the user, is the one exception.
    # The same when the board ANDROID_SERIAL named is not on adb at all but another one in Linux is.
    _lm_ssh_ok=1
    if [ -z "${MU300_IP:-}" ]; then
        ! linux_adb_many || _lm_ssh_ok=0
        [ -z "$LINUX_WANTED" ] || linux_adb_listed "$LINUX_WANTED" || ! linux_adb_listed || _lm_ssh_ok=0
    fi
    if [ $_lm_gone = 0 ] && [ $_lm_ssh_ok = 0 ]; then
        die "$(t 'more than one device runs MU300 Linux and adb could not reboot this one: boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot), or give its address with MU300_IP')"
    fi
    for user in ubuntu root; do
        [ $_lm_gone = 0 ] && linux_mode_running || break
        _lm_ssh=1
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
    # still there: over SSH it did not work, or there was no way in at all (no SSH on this cable, and an adb that
    # takes authorised keys only or would not reboot)
    if [ $_lm_gone = 0 ] && linux_mode_running; then
        die "$(t 'could not reboot it over SSH; on the device run: sudo mu300-next-boot android && sudo reboot')"
    fi
    [ $_lm_gone = 1 ] || [ $_lm_ssh = 1 ] || ! linux_adb_listed ||
        die "$(t 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)')"
    echo "  $(t 'waiting for Android')"
    # Which device comes back: the serial number of our Linux is MU300LINUX-<Android's serial>-<eMMC id> (boot/init).
    # Never "whatever single device adb lists": while the F50 rebooted, a Pixel next to it was the only one, and the
    # check went on with the Pixel (measured; nothing was written, su failed there).
    _lm_back=${_lm_s:-$LINUX_WANTED}; _lm_back=${_lm_back#MU300LINUX}; _lm_back=${_lm_back#-}; _lm_back=${_lm_back%%-*}
    n=0
    while [ $n -lt 60 ]; do
        if [ -n "$_lm_back" ] && android_lines | awk -v s="$_lm_back" '$1 == s {f = 1} END {exit !f}'; then
            export ANDROID_SERIAL=$_lm_back
        else
            select_device quiet || true
        fi
        [ -n "${ANDROID_SERIAL:-}" ] && [ "$(android_state)" = device ] && { echo "  $(t 'Android is up')"; return 0; }
        n=$((n + 1)); sleep 5
    done
    die "$(t 'the device did not come back as Android; boot it yourself (mu300-next-boot android)')"
}

# With more than one adb device attached (a phone, an emulator, a device over the network) every plain adb command
# fails with "more than one device/emulator", which read as "no adb device". Pick the F50 and point adb at it with
# ANDROID_SERIAL: the only device, else the only one that says it is an F50/MU300 or a U30 Air, else ask.
select_device() {  # select_device [quiet]: quiet never asks, it only picks what is unambiguous
    [ -n "${ANDROID_SERIAL:-}" ] && return 0
    _sd_all=$(android_lines)
    [ -n "$_sd_all" ] || return 0
    _sd_f50=$(printf '%s\n' "$_sd_all" | grep -E 'model:F50|product:MU300|device:MU300|device:U30Air|device:MU3351' | awk '{print $1}')
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
    echo "  $(t 'which adb device is the F50 or U30 Air?')"
    printf '%s\n' "$_sd_all" | awk '{ m = ""; for (i = 3; i <= NF; i++) if ($i ~ /^model:/) m = substr($i, 7); printf "    %d) %s %s\n", NR, $1, m }'
    _sd_def=1
    [ -n "$_sd_one" ] && _sd_def=$(printf '%s\n' "$_sd_all" | awk -v s="$_sd_one" '$1 == s {print NR}')
    ask _sd_n "$(t 'Device')" "$_sd_def" MU300_ANS_DEVICE
    _sd_s=$(printf '%s\n' "$_sd_all" | awk -v n="$_sd_n" 'NR == n {print $1}')
    [ -n "$_sd_s" ] || die "$(t 'invalid choice')"
    export ANDROID_SERIAL=$_sd_s
}

# call before anything else that needs adb
# is one of the adb devices an F50/U30 Air?
target_attached() { android_lines | grep -q -E 'model:F50|product:MU300|device:MU300|device:U30Air|device:MU3351'; }

require_android() {
    # ANDROID_SERIAL naming the device in Linux: that is the one to send back to Android, not one to work on
    # (kept in LINUX_WANTED: with two boards in Linux it is the one that is rebooted, never the other)
    case ${ANDROID_SERIAL:-} in MU300LINUX*) LINUX_WANTED=$ANDROID_SERIAL; unset ANDROID_SERIAL ;; esac
    # The device in Linux, and only a phone or tablet in Android: that is not the one to install to - offer to
    # send the device back to Android first
    if [ -z "${ANDROID_SERIAL:-}" ] && ! target_attached && { linux_adb_listed || linux_mode_running; }; then
        linux_mode_to_android && return 0
    fi
    select_device
    if [ "$(android_state)" = device ]; then
        # whatever its serial number says: a root shell of our Linux must never be taken for a rooted Android
        [ -z "$(adb shell 'ls -d /opt/mu300/bin 2>/dev/null' </dev/null 2>/dev/null | tr -d '\r')" ] ||
            die "$(t 'The device is running MU300 Linux, not Android'): $(t 'boot Android yourself (on the device: sudo mu300-next-boot android && sudo reboot)')"
        # an Android that is still starting answers adb before su and storage are ready: pulls then failed
        _ra=0
        while [ "$(adb shell getprop sys.boot_completed </dev/null 2>/dev/null | tr -d '\r')" != 1 ] && [ $_ra -lt 60 ]; do
            [ $_ra = 0 ] && echo "  $(t 'waiting for Android to finish starting')"
            _ra=$((_ra + 1)); sleep 2
        done
        return 0
    fi
    { linux_adb_listed || linux_mode_running; } && linux_mode_to_android && return 0
    die "$(t 'no adb device (boot Android, enable USB debugging)')"
}
