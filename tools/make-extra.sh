#!/bin/sh
# Build an extra: the optional parts that are not in the images and that mu300-extra installs on the device.
#   tools/make-extra.sh NAME OUT.tar.gz [TAG]     (TAG: the release it belongs to, default dev)
# Extras of this repository:
#   lang  LuCI's catalogs in every language of the OpenWrt feed besides the images' own (tr, zh-cn): luci-base and each
#         LuCI app of the images, from the luci-i18n-* packages (apk add in the OpenWrt base image that
#         openwrt/build-rootfs.sh imports, so apk checks every package against the feed's signed index), plus the
#         MU300 panel's catalogs (openwrt/luci-app-mu300/po, tools/po2lmo.py). Needs docker with arm64 support.
#   panel  the web panel for Ubuntu (rootfs/panel): bin/mu300-panel and the MU300 page of Cockpit (cockpit/mu300, with
#         po.<lang>.js from rootfs/panel/po, tools/cockpit-i18n.py). Cockpit itself comes from Ubuntu's archive when
#         the extra is installed.
#   panel-offline  the same, plus Cockpit's packages for each Ubuntu release of the images (debs/24.04, debs/26.04):
#         what "apt-get install --no-install-recommends cockpit-ws cockpit-system cockpit-bridge" adds to the
#         release's Ubuntu image (mu300-ubuntu:<release>, the image the rootfs is built from), downloaded with apt in
#         that image, so apt checks each package against the archive's signed index. Needs docker with arm64 support.
#         Its ./name is panel: mu300-extra install panel --from FILE installs it.
# The VPN extras (vpn, vpn-mihomo) are not built here: they are the module of github.com/dikeckaan/mu300-linux-vpn
# (its tools/make-module.sh), which mu300-extra downloads from that repository's releases.
# Layout (what mu300-update's extra_unpack checks): ./name ./release ./components ./manifest (sha256 of every file),
# ./languages (uci key, tab, name) and ./i18n/<component>.<code>.lmo; owned by root.
set -eu
NAME=${1:?usage: tools/make-extra.sh NAME OUT.tar.gz [TAG]}
OUT=${2:?usage: tools/make-extra.sh NAME OUT.tar.gz [TAG]}
TAG=${3:-dev}
TOP=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/mu300-extra.XXXXXX"); trap 'rm -rf "$tmp"' EXIT
case $NAME in
    lang)
        IMG=mu300-openwrt-base:${MU300_WRT_VER:-25.12.6}
        docker image inspect "$IMG" >/dev/null 2>&1 || {
            echo "$IMG missing: openwrt/build-rootfs.sh imports it (build an OpenWrt image first)" >&2; exit 1; }
        mkdir -p "$tmp/x/i18n" "$tmp/feed"
        # (no apostrophes in here: one single-quoted argument to sh -c)
        docker run --rm --platform linux/arm64 -v "$tmp/feed":/out "$IMG" /bin/sh -eu -c '
mkdir -p /var/lock /tmp
apk update >/dev/null
# the LuCI apps of the images: those of the base image, plus SQM that build-rootfs.sh adds
apk add luci-app-sqm >/dev/null
apk del luci-app-attendedsysupgrade >/dev/null 2>&1 || true
apps=$(apk list --installed "luci-app-*" | sed -n "s/^luci-app-\([^ ]*\)-[0-9][^ -]* .*/\1/p")
apk search "luci-i18n-*" | sed -n "s/^\(luci-i18n-.*\)-[0-9][^-]*$/\1/p" | sort -u > /tmp/all
langs=$(sed -n "s/^luci-i18n-base-//p" /tmp/all | grep -vx -e tr -e zh-cn)
pk=
for l in $langs; do
    for c in base $apps; do grep -qx "luci-i18n-$c-$l" /tmp/all && pk="$pk luci-i18n-$c-$l"; done
done
apk add $pk >/dev/null
for l in $langs; do cp /usr/lib/lua/luci/i18n/*.$l.lmo /out/; done
apk list --installed "luci-i18n-*" | cut -d" " -f1 | sort > /out/packages
uci show luci.languages > /out/names
'
        mv "$tmp"/feed/*.lmo "$tmp/x/i18n/"
        python3 - "$TOP" "$tmp/feed" "$tmp/x" <<'PY'
import os, re, subprocess, sys
top, feed, x = sys.argv[1:]
table = {}
for line in open(os.path.join(top, 'openwrt/luci-languages.tsv'), encoding='utf-8'):
    if line.strip() and not line.startswith('#'):
        d, code, name = line.rstrip('\n').split('\t')
        table[d] = (code, name)
names = {}
for line in open(os.path.join(feed, 'names'), encoding='utf-8'):
    m = re.match(r"^luci\.languages\.([a-z_]+)='(.*)'$", line.rstrip('\n'))
    if m:
        names[m.group(1)] = m.group(2)
# the panel's catalogs, except the two the openwrt-luci image carries itself
ours = []
for d, (code, name) in sorted(table.items()):
    if d in ('tr', 'zh_Hans') or not os.path.isfile(os.path.join(top, 'openwrt/luci-app-mu300/po', d, 'mu300.po')):
        continue
    subprocess.run([sys.executable, os.path.join(top, 'tools/po2lmo.py'),
                    os.path.join(top, 'openwrt/luci-app-mu300/po', d, 'mu300.po'),
                    os.path.join(x, 'i18n', f'mu300.{code}.lmo')], check=True)
    ours.append(code)
    names[code.replace('-', '_')] = name          # our name where LuCI has none (az, kk, id); the same otherwise
names.pop('tr', None); names.pop('zh_cn', None)
lmos = os.listdir(os.path.join(x, 'i18n'))
line_ok = re.compile('^[a-z]{2,3}(_[a-z]{2})?\t[^\t"`$<>&\\\'\x00-\x1f\x7f]{1,100}$')
with open(os.path.join(x, 'languages'), 'w', encoding='utf-8') as f:
    for key in sorted(names):
        code = key.replace('_', '-')
        if not any(n.endswith(f'.{code}.lmo') for n in lmos):
            continue
        line = f'{key}\t{names[key]}'
        assert line_ok.match(line) and len(line.split('\t')[1].encode()) <= 100, line
        f.write(line + '\n')
for n in lmos:
    code = n[:-4].rsplit('.', 1)[1]
    assert re.match(r'^[a-z0-9-]+\.[a-z]{2,3}(-[a-z]{2})?\.lmo$', n) and code.replace('-', '_') in names, n
with open(os.path.join(x, 'components'), 'w', encoding='utf-8') as f:
    f.write(open(os.path.join(feed, 'packages'), encoding='utf-8').read())
    f.write('luci-app-mu300 catalogs: ' + ' '.join(ours) + '\n')
PY
        echo "lang: $(wc -l < "$tmp/x/languages" | tr -d ' ') languages, $(ls "$tmp/x/i18n" | wc -l | tr -d ' ') catalogs" >&2 ;;
    panel|panel-offline)
        x=$tmp/x
        mkdir -p "$x/bin" "$x/cockpit/mu300"
        cp "$TOP/rootfs/panel/bin/mu300-panel" "$x/bin/mu300-panel"
        chmod 755 "$x/bin/mu300-panel"
        for f in manifest.json index.html mu300.js mu300.css; do cp "$TOP/rootfs/panel/cockpit/mu300/$f" "$x/cockpit/mu300/"; done
        python3 "$TOP/tools/cockpit-i18n.py" check >&2 || { echo "the MU300 page's translations are incomplete" >&2; exit 1; }
        python3 "$TOP/tools/cockpit-i18n.py" build "$x/cockpit/mu300"
        printf 'mu300-panel %s\ncockpit page mu300 (en tr zh_CN)\n' "$TAG" > "$x/components"
        if [ "$NAME" = panel-offline ]; then
            for u in ${MU300_PANEL_UBUNTU:-24.04 26.04}; do
                img=mu300-ubuntu:$u
                docker image inspect "$img" >/dev/null 2>&1 || {
                    echo "$img missing: docker build -t $img --build-arg BASE=ubuntu:$u rootfs (as make-release.sh does)" >&2; exit 1; }
                mkdir -p "$x/debs/$u"
                # (no apostrophes in here: one single-quoted argument to sh -c)
                docker run --rm --platform linux/arm64 -v "$x/debs/$u":/out "$img" /bin/sh -eu -c '
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
pk=$(apt-get install -s --no-install-recommends cockpit-ws cockpit-system cockpit-bridge | sed -n "s/^Inst \([^ ]*\) .*/\1/p")
[ -n "$pk" ]
cd /out && apt-get download $pk >/dev/null
chown -R 0:0 /out'
                ls "$x/debs/$u"/cockpit-ws_*.deb >/dev/null 2>&1 || { echo "no cockpit-ws package for Ubuntu $u" >&2; exit 1; }
                printf 'ubuntu %s: %s\n' "$u" "$(cd "$x/debs/$u" && ls | sed 's/_[^_]*$//' | tr '\n' ' ')" >> "$x/components"
            done
            NAME=panel
        fi
        echo "panel: $(find "$x" -type f | wc -l | tr -d ' ') files" >&2 ;;
    vpn|vpn-mihomo) echo "$NAME is the VPN module of github.com/dikeckaan/mu300-linux-vpn (its tools/make-module.sh), not built here" >&2; exit 2 ;;
    *) echo "unknown extra '$NAME' (lang, panel, panel-offline)" >&2; exit 2 ;;
esac
echo "$NAME" > "$tmp/x/name"
echo "$TAG" > "$tmp/x/release"
# python's tarfile, not tar: the same root-owned archive from macOS (bsdtar) and Linux (GNU tar)
python3 - "$tmp/x" "$OUT" <<'PY'
import os, sys, tarfile
src, out = sys.argv[1], sys.argv[2]
def root(ti):
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = 'root'
    return ti
# ./manifest: the sha256 of every file, as sha256sum writes it (mu300-update checks the unpacked tree against it)
import hashlib
lines = []
for d, _, fs in os.walk(src):
    for f in fs:
        path = os.path.join(d, f)
        rel = './' + os.path.relpath(path, src)
        if rel != './manifest':
            lines.append('%s  %s\n' % (hashlib.sha256(open(path, 'rb').read()).hexdigest(), rel))
with open(os.path.join(src, 'manifest'), 'w') as m:
    m.writelines(sorted(lines))
with tarfile.open(out, 'w:gz', format=tarfile.GNU_FORMAT) as t:
    for name in ('name', 'release', 'components', 'languages', 'manifest', 'i18n', 'bin', 'cockpit', 'debs'):
        if os.path.exists(os.path.join(src, name)):
            t.add(os.path.join(src, name), arcname='./' + name, filter=root)
PY
echo "$NAME extra ($TAG): $OUT, $(du -h "$OUT" | cut -f1)"
