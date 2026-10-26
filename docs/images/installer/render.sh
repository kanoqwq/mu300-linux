#!/bin/sh
# Render the installer screenshots in this directory from their .txt sources (real installer output; the answers
# typed at the prompts are written in, and local paths shortened). Needs docker; nothing is installed on the host.
#   sh docs/images/installer/render.sh
set -e
cd "$(dirname "$0")"
docker run --rm -v "$PWD":/w -w /w golang:1.24 sh -c '
set -e
apt-get update -qq >/dev/null 2>&1
apt-get install -y -qq librsvg2-bin fontconfig fonts-jetbrains-mono fonts-noto-cjk >/dev/null 2>&1
go install github.com/charmbracelet/freeze@latest >/dev/null 2>&1
for t in installer-*.txt; do
    n=${t%.txt}
    # freeze draws the window as SVG; librsvg renders it with fontconfig, which falls back to Noto CJK for the
    # Chinese of the language menu (the "NL" variant of the font has no ligatures: ==> stays ==>)
    /go/bin/freeze "$t" --language text --window --theme github-dark --padding 20,40 --margin 0 \
        --font.size 14 --line-height 1.3 --font.family "JetBrains Mono NL" -o "/tmp/$n.svg" >/dev/null
    rsvg-convert -z 2 "/tmp/$n.svg" -o "$n.png"
    echo "$n.png"
done'
