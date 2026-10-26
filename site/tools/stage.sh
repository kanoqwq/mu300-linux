#!/bin/sh
# Stage the website as it is published: site/ without site/tools, plus the device scripts of the web installer.
#   site/tools/stage.sh OUT
# .github/workflows/pages.yml publishes OUT; for a look on this computer:
#   sh site/tools/stage.sh /tmp/mu300-site && python3 -m http.server -d /tmp/mu300-site 8300
#   (WebUSB needs https or localhost: http://localhost:8300/web-installer/)
#
# The web installer (site/web-installer) pushes a few scripts to the device and runs them there. They are this
# repository's own, from tools/ and android/magisk, where their tests are; a copy kept under site/ would be a second
# text to hold equal, so they are copied here, with a SHA256SUMS the page checks what it fetched against.
set -eu
TOP=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:?usage: site/tools/stage.sh OUT}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
case $OUT in "$TOP/site"|"$TOP/site"/*|"$TOP") echo "OUT must not be the site itself or the repository" >&2; exit 1 ;; esac
# everything of site/ but tools/ (cp -R and a delete: no rsync needed)
rm -rf "$OUT/assets" "$OUT/web-installer"
(cd "$TOP/site" && tar -cf - --exclude ./tools .) | (cd "$OUT" && tar -xf -)
D=$OUT/assets/web-installer/device
rm -rf "$D"; mkdir -p "$D"
cp "$TOP/tools/android-web.sh" "$TOP/tools/storage.sh" "$TOP/tools/android-boot-image.sh" \
   "$TOP/tools/android-mount-mu300root.sh" "$TOP/android/magisk/mu300-linux-switch/switch.sh" "$D/"
(cd "$D" && for f in *.sh; do
    printf '%s  %s\n' "$( (sha256sum "$f" 2>/dev/null || shasum -a 256 "$f") | cut -d' ' -f1)" "$f"
done > SHA256SUMS)
echo "staged: $OUT ($(find "$OUT" -type f | wc -l | tr -d ' ') files)"
