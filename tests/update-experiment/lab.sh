#!/bin/sh
# One update of a real release image with user state planted in it, inside the container run.sh starts (as root,
# privileged: it makes an ext4 filesystem in a file, mounts it and changes root into the installed system).
#   lab.sh device  OS OLD.tar.gz RELEASE_DIR TAG OUT.tsv
#       the device's own path: the old image is installed, the planted system is entered with chroot and runs its
#       installed "mu300-update apply OS" against RELEASE_DIR served on 127.0.0.1 (MU300_RELEASE_URL): the files of
#       release TAG, with SHA256SUMS and mu300-update, so the installed updater continues with the release's
#   lab.sh from OS OLD.tar.gz ZIP RELEASE_DIR OUT.tsv
#       the device's path without a network: the planted system gets the new release's mu300-update (as a user of a
#       release before apply --from does it: FINDINGS 86) and runs "mu300-update apply --from ZIP --unsigned OS", ZIP
#       being the release's Magisk zip of that system (--unsigned: a release before the key has none to check with,
#       and a file brought by hand is taken only on the owner's word); nothing is served
#   lab.sh android OS OLD.tar.gz NEW.tar.gz ANDROID_INSTALL_SH OUT.tsv
#       the installers' path (install.sh, install.ps1, the Magisk zip): the install loop of tools/android-install.sh in
#       update mode, run by mksh with toybox's commands and the one true awk first on PATH, as on Android
# OS is ubuntu, openwrt or openwrt-luci. OUT.tsv gets one line per planted item (experiment.py verify); the update's
# output goes to OUT.tsv.log, the update report, when there is one, to OUT.tsv.report, and for the device path
# OUT.tsv.rollback has the same items looked for in the system kept for a rollback (<os>.old).
# Variations, from the environment:
#   MU300_LAB_ENV="NAME=VALUE ..."   for the update (MU300_UBUNTU=26.04, MU300_UPDATE_KEEP=list)
#   MU300_LAB_APPLY="--no-rollback"  more arguments of mu300-update apply
#   MU300_LAB_KERNEL=6.18            the kernel the device is set to (5.4)
#   MU300_LAB_OLD_VERSION=TAG        what the old system says it is (a release no manifest is known of: dev)
#   MU300_LAB_FREE=MB                fill the filesystem until only that much is free, before the update
#   MU300_LAB_BIG=MB                 a file of that size in the old system (/srv/big.img), as much data of the user
#   MU300_LAB_AGAIN="DIR TAG"        device: after the update, start the new system the same way and update it again,
#                                    to release TAG served from DIR (OUT.tsv is then of the second update)
#   MU300_LAB_PLANT="--live"         more arguments of experiment.py plant
#   MU300_LAB_KEEP=1                 leave everything mounted under /w/lab for a look
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
mode=$1 os=$2 old=$3
W=${MU300_LAB_WORK:-/w/lab}
kind=ubuntu; case $os in openwrt*) kind=openwrt ;; esac

clean() {
    # every mount below W, the deepest first
    awk -v w="$W" 'index($2, w) == 1 { print $2 }' /proc/mounts | sort -r | while read -r m; do umount "$m" 2>/dev/null || umount -l "$m" || true; done
    rm -rf "$W"
}
disk() {  # disk DIR: an ext4 filesystem of 6 GiB (a sparse file), mounted at DIR
    mkdir -p "$W" "$1"
    truncate -s 6G "$W/disk.img"
    mke2fs -q -t ext4 -L mu300root -F "$W/disk.img"
    mount -o loop,noatime "$W/disk.img" "$1"
}
install_old() {  # install_old ROOT: the old image as an installer leaves it, on a device that has been used
    mkdir -p "$1"
    tar --numeric-owner -xzpf "$old" -C "$1"
    # what an installer adds: the device's own vendor files, and the accounts are the device's
    mkdir -p "$1/opt/mu300/android/vendor/lib64" "$1/lib/firmware"
    echo vendor > "$1/opt/mu300/android/vendor/lib64/libril.so"
    echo firmware > "$1/lib/firmware/wcnmodem.bin"
    rm -f "$1/etc/.mu300-accounts-from-image"
    echo linux > "$1/etc/mu300/default-boot"
    [ -z "${MU300_LAB_OLD_VERSION:-}" ] || echo "$MU300_LAB_OLD_VERSION" > "$1/etc/mu300/image-version"
}
beside() {  # beside DISK: another system, the extras and the boot settings on the partition, which no update of OS touches
    other=openwrt; [ "$kind" = openwrt ] && other=ubuntu
    mkdir -p "$1/$other/etc/mu300" "$1/$other/root" "$1/extra/vpn/bin" "$1/.mu300"
    echo v2026.10.20 > "$1/$other/etc/mu300/image-version"; echo "a file of the other system" > "$1/$other/root/file"
    printf '#!/bin/sh\n' > "$1/extra/vpn/bin/mu300-vpn"; chmod 755 "$1/extra/vpn/bin/mu300-vpn"; echo 2.0 > "$1/extra/vpn/VERSION"
    echo 5 > "$1/.mu300/boot-attempts"
}
# (every file with its attributes and content, every directory by name: mu300-update makes and removes its own
# working files in extra/, which changes that directory's time and nothing else)
beside_sum() { ( cd "$1" && find "$other" extra .mu300/boot-attempts ! -type d -exec stat -c '%A %u %g %s %y %n' {} + | sort -k7 &&
    find "$other" extra -type d | sort && find "$other" extra -type f -exec sha256sum {} + | sort -k2 ) 2>&1; }
fill() {  # fill DISK: leave MU300_LAB_FREE MB free (a file beside the systems); MU300_LAB_BIG: the user's big file
    [ -z "${MU300_LAB_BIG:-}" ] || { mkdir -p "$1/$os/srv"; fallocate -l "${MU300_LAB_BIG}M" "$1/$os/srv/big.img"; }
    [ -n "${MU300_LAB_FREE:-}" ] || return 0
    free=$(df -Pm "$1" | awk 'NR == 2 { print $4 }')
    [ "$free" -le "$MU300_LAB_FREE" ] || fallocate -l "$((free - MU300_LAB_FREE))M" "$1/filler"
    echo "free before the update: $(df -Pm "$1" | awk 'NR == 2 { print $4 }') MB"
}
enter() {  # enter ROOT DISK: the mounts a running system has
    mkdir -p "$1/mnt/mu300-disk"
    mount --bind "$2" "$1/mnt/mu300-disk"
    mount -t proc proc "$1/proc"
    mount --bind /dev "$1/dev"
    mount -t tmpfs tmpfs "$1/run"
    mount -t tmpfs tmpfs "$1/tmp"
}
leave() { for m in mnt/mu300-disk proc dev run tmp; do umount "$1/$m" 2>/dev/null || true; done; }
sane() {  # sane ROOT: the updated system holds together, as far as it can be asked without booting it
    enter "$1" "${1%/*}"
    if [ "$kind" = ubuntu ]; then
        chroot "$1" /bin/sh -c 'echo "dpkg --audit: $(dpkg --audit 2>&1 | wc -l) lines"; echo "packages: $(dpkg -l | grep -c "^ii")";
            echo "libraries in the cache: $(ldconfig -p | wc -l)"; id ubuntu; id kaan; id myapp; ls -ln /var/lib/myapp/db.sqlite;
            echo "sudo: $(visudo -c 2>&1 | tail -n 1)"; echo "sshd: $(/usr/sbin/sshd -t 2>&1 | grep -v "no hostkeys" | head -n 2)";
            echo "units that do not load: $(SYSTEMD_OFFLINE=1 systemd-analyze verify --root=/ multi-user.target 2>&1 | grep -c "Failed to load")"' 2>&1 || true
    else
        chroot "$1" /bin/sh -c 'echo "packages: $(apk info 2>/dev/null | wc -l)"; echo "uci: $(uci show 2>&1 | wc -l) lines, errors: $(uci show 2>&1 | grep -ci "error\|parse")";
            id kaan; echo "enabled services: $(ls /etc/rc.d | wc -l)"; echo "init scripts without their file: $(for l in /etc/rc.d/*; do [ -e "$l" ] || echo "$l"; done | tr "\n" " ")"' 2>&1 || true
    fi
    leave "$1"
}
update_in() {  # update_in ROOT REL TAG LOG: the system at ROOT runs its installed updater against REL
    ( cd "$2" && exec python3 -m http.server 8099 --bind 127.0.0.1 ) >/dev/null 2>&1 &
    srv=$!
    enter "$1" "$D"
    sleep 1
    rc=0
    # shellcheck disable=SC2086
    chroot "$1" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root MU300_RELEASE="$3" \
        MU300_RELEASE_URL=http://127.0.0.1:8099 MU300_FETCH_DELAY=0 ${MU300_LAB_ENV:-} \
        /bin/sh /opt/mu300/bin/mu300-update apply ${MU300_LAB_APPLY:-} "$os" >> "$4" 2>&1 || rc=$?
    echo "mu300-update apply: exit $rc" >> "$4"
    kill $srv 2>/dev/null || true
    for d in "$D/$os.old" "$D/$os.broken" "$D/$os"; do [ ! -d "$d" ] || leave "$d"; done
}
from_in() {  # from_in ROOT ZIP LOG: the system at ROOT updates itself from ZIP (on the partition), offline
    enter "$1" "$D"
    rc=0
    # (no server: an address that refuses, should anything try the network)
    # shellcheck disable=SC2086
    chroot "$1" /usr/bin/env -i PATH=/usr/sbin:/usr/bin:/sbin:/bin HOME=/root MU300_FETCH_DELAY=0 \
        MU300_RELEASE_URL=http://127.0.0.1:9 MU300_API_URL=http://127.0.0.1:9 ${MU300_LAB_ENV:-} \
        /bin/sh /opt/mu300/bin/mu300-update apply --from "/mnt/mu300-disk/${2##*/}" --unsigned ${MU300_LAB_APPLY:-} "$os" >> "$3" 2>&1 || rc=$?
    echo "mu300-update apply --from: exit $rc" >> "$3"
    for d in "$D/$os.old" "$D/$os.broken" "$D/$os"; do [ ! -d "$d" ] || leave "$d"; done
}

clean
case $mode in
device)
    rel=$4 tag=$5 out=$6
    D=$W/disk
    disk "$D"
    install_old "$D/$os"
    mkdir -p "$D/boot" "$D/.mu300"; echo "${MU300_LAB_KERNEL:-5.4}" > "$D/boot/kernel"; echo "$os" > "$D/.mu300/boot-os"
    beside "$D"
    # shellcheck disable=SC2086
    python3 "$HERE/experiment.py" plant "$D/$os" "$kind" "$W/spec.json" ${MU300_LAB_PLANT:-}
    beside_sum "$D" > "$W/beside.before"
    fill "$D"
    : > "$out.log"
    update_in "$D/$os" "$rel" "$tag" "$out.log"
    if [ -n "${MU300_LAB_AGAIN:-}" ]; then
        # the "reboot": the new system is the one that runs now, and updates itself again
        set -- $MU300_LAB_AGAIN
        echo "==== again, to $2 ====" >> "$out.log"
        update_in "$D/$os" "$1" "$2" "$out.log"
    fi
    new=$D/$os
    # the system kept for a rollback is the old one, exactly: every planted item as it was (OUT.tsv.rollback)
    if [ -d "$D/$os.old" ] && [ -z "${MU300_LAB_AGAIN:-}" ]; then
        python3 "$HERE/experiment.py" verify "$D/$os.old" "$W/spec.json" > "$out.rollback"
        echo "kept for a rollback ($os.old): $(cut -f2 "$out.rollback" | sort | uniq -c | tr '\n' ' ')"
    fi
    ls "$D" | tr '\n' ' '; echo
    [ -z "${MU300_LAB_BIG:-}" ] || ls -ln "$D/$os/srv/big.img" 2>&1 | awk '{ print "the big file in the new system:", $5, $NF }'
    ;;
from)
    zip=$4 rel=$5 out=$6
    D=$W/disk
    disk "$D"
    install_old "$D/$os"
    mkdir -p "$D/boot" "$D/.mu300"; echo "${MU300_LAB_KERNEL:-5.4}" > "$D/boot/kernel"; echo "$os" > "$D/.mu300/boot-os"
    beside "$D"
    # shellcheck disable=SC2086
    python3 "$HERE/experiment.py" plant "$D/$os" "$kind" "$W/spec.json" ${MU300_LAB_PLANT:-}
    beside_sum "$D" > "$W/beside.before"
    fill "$D"
    # the new release's updater, as the user fetches it (its own file: what the carry block then sees as the
    # project's program changed by the user, and the release's again)
    install -m 755 "$rel/mu300-update" "$D/$os/opt/mu300/bin/mu300-update"
    cp "$zip" "$D/${zip##*/}"
    : > "$out.log"
    from_in "$D/$os" "$zip" "$out.log"
    rm -f "$D/${zip##*/}"
    new=$D/$os
    if [ -d "$D/$os.old" ]; then
        python3 "$HERE/experiment.py" verify "$D/$os.old" "$W/spec.json" > "$out.rollback"
        echo "kept for a rollback ($os.old): $(cut -f2 "$out.rollback" | sort | uniq -c | tr '\n' ' ')"
    fi
    ls "$D" | tr '\n' ' '; echo
    ;;
android)
    newtar=$4 script=$5 out=$6
    T=$W/T; M=$T/mu300root
    disk "$M"
    install_old "$M/$os"
    mkdir -p "$M/.mu300"
    beside "$M"
    # shellcheck disable=SC2086
    python3 "$HERE/experiment.py" plant "$M/$os" "$kind" "$W/spec.json" --no-mount ${MU300_LAB_PLANT:-}
    beside_sum "$M" > "$W/beside.before"
    fill "$M"
    cp "$newtar" "$T/mu300-$os.tar.gz"
    # Android's commands: toybox's, and the one true awk
    mkdir -p "$W/android-bin"
    for a in $(toybox); do ln -sf "$(command -v toybox)" "$W/android-bin/$a"; done
    ln -sf "$(command -v original-awk)" "$W/android-bin/awk"
    {
        printf 'set -e\numask 022\nsay() { echo "[device] $*"; }\nsd_unmark() { :; }\nextra_keep_vpn() { :; }\nssid=; psk=\n'
        printf 'T="%s"; M="%s"\n' "$T" "$M"
        for b in stage user-packages install-os; do
            awk -v b="$b" '$0 == "# --- " b " end" { p = 0 } p { print } $0 == "# --- " b " begin" { p = 1 }' "$script"
        done
    } > "$W/install.sh"
    rc=0
    # shellcheck disable=SC2086
    env -i PATH="$W/android-bin:/usr/sbin:/usr/bin:/sbin:/bin" OSES="$os" UPDATE=1 PWHASH= DEFAULT_LINUX=1 BOOT_OS="$os" \
        WIPE_LEGACY=0 ${MU300_LAB_ENV:-} mksh "$W/install.sh" > "$out.log" 2>&1 || rc=$?
    echo "android-install.sh (install loop): exit $rc" >> "$out.log"
    new=$M/$os
    D=$M
    ;;
*) echo "usage: lab.sh device|from|android ..." >&2; exit 2 ;;
esac
python3 "$HERE/experiment.py" verify "$new" "$W/spec.json" > "$out"
[ ! -f "$new/etc/mu300/update-report" ] || cp "$new/etc/mu300/update-report" "$out.report"
beside_sum "$D" > "$W/beside.after"
# (mu300-update brings an installed VPN module to its latest release, by design: on that path only the other system
# is compared)
[ "$mode" = android ] || for f in before after; do grep -v ' extra' "$W/beside.$f" | grep -v '^extra' > "$W/beside.$f.sys"; mv "$W/beside.$f.sys" "$W/beside.$f"; done
if cmp -s "$W/beside.before" "$W/beside.after"; then echo "beside it on the partition: untouched"; else echo "SOMETHING BESIDE IT ON THE PARTITION CHANGED"; diff "$W/beside.before" "$W/beside.after" | head; fi
sane "$new" > "$out.sane" 2>&1 || true
grep -E 'exit [0-9]|not enough room|nothing was changed' "$out.log" | tail -n 4
cut -f2 "$out" | sort | uniq -c | tr '\n' ' '; echo
[ -n "${MU300_LAB_KEEP:-}" ] || clean
