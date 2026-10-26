#!/bin/sh
# The files of a release's mirror, for the web installer (site/assets/web-installer/mirror.js): every Magisk zip cut
# into parts a repository may hold and a page may read, and a manifest that lists them.
#   tools/make-release-mirror.sh ZIPDIR OUT TAG
# writes OUT/<zip>.000, OUT/<zip>.001, ... (at most 45 MiB each) and OUT/manifest.json for the mu300-magisk-TAG-*.zip
# files of ZIPDIR. The release workflow (.github/workflows/magisk.yml) pushes OUT as the branch TAG of the mirror
# repository, where raw.githubusercontent.com serves it with the header a page needs (FINDINGS 72): GitHub does not
# let a page read a release's own files, and a file of a repository may have at most 100 MB.
#
# The manifest is a convenience for the page, not something it trusts: the page takes a zip only when its size and
# SHA-256 are the ones GitHub's API names for the release's own asset, and the device checks the file again.
set -eu
[ $# = 3 ] || { echo "usage: tools/make-release-mirror.sh ZIPDIR OUT TAG" >&2; exit 2; }
SRC=$1; OUT=$2; TAG=$3
PART_MIB=${MU300_MIRROR_PART_MIB:-45}
case $TAG in v[0-9]*) ;; *) echo "not a release tag: $TAG" >&2; exit 2 ;; esac
case $TAG in *[!0-9A-Za-z._]*|*..*) echo "not a release tag: $TAG" >&2; exit 2 ;; esac
case $PART_MIB in ''|*[!0-9]*|0) echo "MU300_MIRROR_PART_MIB must be a number of MiB" >&2; exit 2 ;; esac
[ "$PART_MIB" -le 45 ] || { echo "a part may have at most 45 MiB (the page refuses larger ones)" >&2; exit 2; }
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
size() { wc -c < "$1" | tr -d ' '; }

mkdir -p "$OUT"
rm -f "$OUT"/mu300-magisk-*.zip.[0-9][0-9][0-9] "$OUT/manifest.json"
found=
m=$OUT/manifest.json.tmp
printf '{"v":1,"tag":"%s","files":{' "$TAG" > "$m"
for zip in "$SRC"/mu300-magisk-"$TAG"-*.zip; do
    [ -f "$zip" ] || continue
    name=${zip##*/}
    case $name in *[!0-9A-Za-z._-]*) echo "unexpected file name: $name" >&2; exit 1 ;; esac
    total=$(size "$zip")
    [ "$total" -gt 0 ] || { echo "$name is empty" >&2; exit 1; }
    [ -z "$found" ] || printf ',' >> "$m"
    found=1
    printf '"%s":{"size":%s,"sha256":"%s","parts":[' "$name" "$total" "$(sha "$zip")" >> "$m"
    n=0; at=0
    while [ $((at * 1048576)) -lt "$total" ]; do
        part=$name.$(printf '%03d' "$n")
        dd if="$zip" of="$OUT/$part" bs=1048576 skip="$at" count="$PART_MIB" 2>/dev/null
        [ "$n" = 0 ] || printf ',' >> "$m"
        printf '{"name":"%s","size":%s,"sha256":"%s"}' "$part" "$(size "$OUT/$part")" "$(sha "$OUT/$part")" >> "$m"
        n=$((n + 1)); at=$((at + PART_MIB))
    done
    printf ']}' >> "$m"
    # the parts are the file: put together again they must be it, or nothing is published
    [ "$(cat "$OUT/$name".[0-9][0-9][0-9] | { if command -v sha256sum >/dev/null 2>&1; then sha256sum; else shasum -a 256; fi; } | cut -d' ' -f1)" = "$(sha "$zip")" ] ||
        { echo "the parts of $name do not add up to the file" >&2; exit 1; }
    echo "$name: $n parts"
done
[ -n "$found" ] || { rm -f "$m"; echo "no mu300-magisk-$TAG-*.zip in $SRC" >&2; exit 1; }
printf '}}\n' >> "$m"
mv "$m" "$OUT/manifest.json"
echo "mirror of $TAG: $OUT ($(ls "$OUT" | wc -l | tr -d ' ') files)"
