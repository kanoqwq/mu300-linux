# A release's Magisk zip, unpacked on a computer, is an installer for that computer too (START-HERE.md in the zip,
# tools/make-magisk-zips.sh): its computer/ directory is this project's installers, with a file .mu300-package in it,
# and the system and the kernel of the zip lie next to it - payload/<the two assets>, mu300/manifest naming them and
# mu300/SHA256SUMS, the release's own checksums. In that case install.sh installs exactly that system and that kernel,
# from those files, and downloads nothing. Sourced by install.sh (t, die and progress are its own; needs $TOP).
#   MU300_PACKAGE=DIR (or install.sh --package DIR)   the unpacked zip at DIR
#   computer/.mu300-package                           the unpacked zip is the directory above this copy
# The manifest is read the way the zip's own installer reads it (android/magisk/installer/mu300-install.sh,
# manifest_load): only its keys, never sourced, and every value exactly one a release has - the values become file
# names. Both payload files must have the checksum the manifest names and the one SHA256SUMS names.

# package_dir: the directory of the unpacked zip this run installs from, or nothing (a copy of the project)
package_dir() {
    if [ -n "${MU300_PACKAGE:-}" ]; then echo "$MU300_PACKAGE"; return 0; fi
    [ -f "$TOP/.mu300-package" ] && echo "$TOP/.."
    return 0
}

# package_load DIR: PKG (the directory, absolute) and PKG_TAG, PKG_SYSTEM, PKG_OS, PKG_UBUNTU, PKG_KERNEL,
# PKG_KERNEL_ASSET, PKG_ROOTFS_ASSET, PKG_SHA256_KERNEL, PKG_SHA256_ROOTFS from its mu300/manifest; dies when it is
# not the manifest of a release's zip
package_load() {
    PKG=$(cd "$1" 2>/dev/null && pwd) || die "$(t 'no unpacked Magisk zip at {1}' "$1")"
    [ -f "$PKG/mu300/manifest" ] && [ -f "$PKG/mu300/SHA256SUMS" ] ||
        die "$(t '{1} is not an unpacked Magisk zip of a release (it has no mu300/manifest and mu300/SHA256SUMS)' "$PKG")"
    PKG_TAG= PKG_SYSTEM= PKG_OS= PKG_UBUNTU= PKG_KERNEL= PKG_KERNEL_ASSET= PKG_ROOTFS_ASSET= PKG_SHA256_KERNEL= PKG_SHA256_ROOTFS=
    while IFS='=' read -r _pk _pv || [ -n "$_pk" ]; do
        _pv=$(printf '%s' "$_pv" | tr -d '\r')
        case $_pk in
            TAG) PKG_TAG=$_pv ;; SYSTEM) PKG_SYSTEM=$_pv ;; OS) PKG_OS=$_pv ;; UBUNTU) PKG_UBUNTU=$_pv ;;
            KERNEL) PKG_KERNEL=$_pv ;; KERNEL_ASSET) PKG_KERNEL_ASSET=$_pv ;; ROOTFS_ASSET) PKG_ROOTFS_ASSET=$_pv ;;
            SHA256_KERNEL) PKG_SHA256_KERNEL=$_pv ;; SHA256_ROOTFS) PKG_SHA256_ROOTFS=$_pv ;;
        esac
    done < "$PKG/mu300/manifest"
    _pok=1
    case $PKG_TAG in v[0-9]*) ;; *) _pok= ;; esac
    case $PKG_TAG in *[!0-9A-Za-z._-]*|*..*) _pok= ;; esac
    case $PKG_SYSTEM:$PKG_OS:$PKG_UBUNTU:$PKG_ROOTFS_ASSET in
        openwrt:openwrt::mu300-openwrt-rootfs.tar.gz) ;;
        openwrt-luci:openwrt-luci::mu300-openwrt-luci-rootfs.tar.gz) ;;
        ubuntu-24.04:ubuntu:24.04:mu300-ubuntu-rootfs.tar.gz) ;;
        ubuntu-26.04:ubuntu:26.04:mu300-ubuntu-26.04-rootfs.tar.gz) ;;
        *) _pok= ;;
    esac
    case $PKG_KERNEL:$PKG_KERNEL_ASSET in
        5.4:mu300-kernel.tar.gz|6.18:mu300-kernel-6.18.tar.gz|7.2:mu300-kernel-7.2.tar.gz|7.3:mu300-kernel-7.3.tar.gz) ;;
        *) _pok= ;;
    esac
    # (Ubuntu 26.04's programs need system calls 5.4 does not have: no release has that zip)
    [ "$PKG_UBUNTU:$PKG_KERNEL" != 26.04:5.4 ] || _pok=
    for _ph in "$PKG_SHA256_KERNEL" "$PKG_SHA256_ROOTFS"; do
        case $_ph in *[!0-9a-f]*) _pok= ;; esac
        [ ${#_ph} = 64 ] || _pok=
    done
    [ -n "$_pok" ] || die "$(t 'the package is incomplete or damaged (its manifest is not valid)')"
}

# package_label: the system for people, as the zip's name has it (ubuntu-24.04, openwrt-luci)
package_label() { echo "$PKG_SYSTEM"; }

# package_verify: both payload files against the manifest and against SHA256SUMS, with a verify event each; dies on
# the first that is missing or differs
package_verify() {
    for _pf in "$PKG_KERNEL_ASSET:$PKG_SHA256_KERNEL" "$PKG_ROOTFS_ASSET:$PKG_SHA256_ROOTFS"; do
        _pn=${_pf%%:*}; _pm=${_pf#*:}
        _ps=$(awk -v f="$_pn" '$2 == f || $2 == "*" f {print $1; exit}' "$PKG/mu300/SHA256SUMS" | tr -d '\r')
        [ -f "$PKG/payload/$_pn" ] || die "$(t '{1} is missing from the package (payload/)' "$_pn")"
        echo "  $_pn"
        _pa=$( (shasum -a 256 "$PKG/payload/$_pn" 2>/dev/null || sha256sum "$PKG/payload/$_pn" 2>/dev/null) | cut -d' ' -f1)
        if [ -z "$_ps" ] || [ "$_pa" != "$_pm" ] || [ "$_pa" != "$_ps" ]; then
            progress verify file "$_pn" result mismatch
            die "$(t 'checksum mismatch for {1}' "$_pn")"
        fi
        progress verify file "$_pn" result ok
    done
}
