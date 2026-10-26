#!/bin/sh
# Announce a release on the project's Telegram channel (.github/workflows/telegram.yml and magisk.yml).
#   tools/telegram-announce.sh release TAG [published|released]   a release published (a prerelease too) or promoted
#   tools/telegram-announce.sh zips TAG                            its Magisk zips attached
# Needs TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (repository secrets), gh and jq. The release's own notes are the
# static asset table, so the message is short: what the release is, the "New in" list of the wiki Home at that tag
# (the first lines, without the links), and where to read on. Nothing from the environment but the two secrets
# goes out, and the token never reaches a log: curl takes the URL from a file.
set -eu
MODE=${1:-}; TAG=${2:-}; ACTION=${3:-published}
REPO=${GITHUB_REPOSITORY:-dikeckaan/mu300-linux}
[ -n "$TAG" ] || { echo "usage: $0 release|zips TAG [published|released]" >&2; exit 2; }
case "$TAG" in v[0-9]*) ;; *) echo "not a release tag: $TAG" >&2; exit 2 ;; esac
case "$TAG" in *[!0-9A-Za-z._-]*|*..*) echo "not a release tag: $TAG" >&2; exit 2 ;; esac
: "${TELEGRAM_BOT_TOKEN:?}" "${TELEGRAM_CHAT_ID:?}"

esc() { sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g'; }
URL=https://github.com/$REPO/releases/tag/$TAG
rel=$(gh release view "$TAG" -R "$REPO" --json name,isPrerelease,publishedAt,assets)
pre=$(printf '%s' "$rel" | jq -r .isPrerelease)
nassets=$(printf '%s' "$rel" | jq -r '.assets|length')
nzips=$(printf '%s' "$rel" | jq -r '[.assets[].name|select(endswith(".zip"))]|length')

case "$MODE" in
release)
    # "released" also fires for a release published as a release straight away, right after "published": one
    # message then, not two. A promotion comes later: the release has been there for a while.
    if [ "$ACTION" = released ]; then
        age=$(( $(date +%s) - $(date -d "$(printf '%s' "$rel" | jq -r .publishedAt)" +%s 2>/dev/null || echo 0) ))
        [ "$age" -gt 600 ] || { echo "published just now: the 'published' event announces it"; exit 0; }
        kind="Now the latest release"
        lead="Tested on the boards and promoted. Devices on an earlier release take it with <code>mu300-update apply</code>; the installer and the Magisk installers ship it from now on."
    elif [ "$pre" = true ]; then
        kind="Pre-release"
        lead="Under test on the boards; it becomes the latest release once it passes. To try it now: <code>MU300_RELEASE=$(printf '%s' "$TAG" | esc) mu300-update apply</code>"
    else
        kind="New release"
        lead="Devices update with <code>mu300-update apply</code>. New installations: the installer and images on the release page; the Magisk installers follow shortly."
    fi
    # the "New in TAG" list of the wiki Home on main (the docs usually land after the tag): one line per bullet
    # (wrapped lines joined, long ones shortened), bold kept, links to the wiki's own pages dropped, other links
    # reduced to their text
    new=$(gh api "repos/$REPO/contents/wiki/Home.md" --jq .content 2>/dev/null | base64 -d 2>/dev/null |
        awk -v t="$TAG" '
            $0 ~ "^## New in " t { on=1; next }
            on && /^## / { exit }
            on && /^\* / { if (b != "") print b; b = $0; next }
            on && /^  +[^ ]/ && b != "" { sub(/^ +/, " "); b = b $0 }
            END { if (b != "") print b }' |
        sed -E 's/ *\[([^]]*)\]\([A-Za-z0-9_-]+\)//g; s/\[([^]]*)\]\([^)]*\)/\1/g; s/`//g; s/ +$//; s/ *\.\.\.$//' |
        awk '{ if (length($0) > 170) { s = substr($0, 1, 170); sub(/ [^ ]*$/, "", s); print s "…" } else print }' | esc |
        sed -E 's/\*\*([^*]+)\*\*/<b>\1<\/b>/g; s/^\* /• /' | head -n 8) || new=
    text="<b>MU300 Linux $(printf '%s' "$TAG" | esc)</b>
$kind

$lead"
    [ -z "$new" ] || text="$text

<b>What's new</b>
$new"
    text="$text

<a href=\"$URL\">Release page</a> ($nassets files: images, kernels 5.4 / 6.18 / 7.2 / 7.3, installer) · <a href=\"https://github.com/$REPO/wiki/Home\">Wiki</a> · <a href=\"https://kaandikec.com/mu300-linux/\">Site</a>"
    ;;
zips)
    text="<b>MU300 Linux $(printf '%s' "$TAG" | esc)</b>
Magisk installers attached

$nzips zips on the release page: Ubuntu 24.04 and 26.04, OpenWrt, OpenWrt with the control panel — each with kernel 5.4, 6.18, 7.2 or 7.3. Install from Magisk › Modules on the device; no computer needed.

<a href=\"$URL\">Release page</a> · <a href=\"https://github.com/$REPO/wiki/Installation\">Installation guide</a>"
    ;;
*) echo "usage: $0 release|zips TAG [published|released]" >&2; exit 2 ;;
esac

# MU300_TG_DRY=1: print the message instead of sending it (a look at the text, tests)
[ "${MU300_TG_DRY:-}" != 1 ] || { printf '%s\n' "$text"; exit 0; }
# the token stays out of the command line and the log: curl reads the URL from a config file
cfg=$(mktemp); trap 'rm -f "$cfg"' EXIT
printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_BOT_TOKEN" > "$cfg"
out=$(jq -n --arg c "$TELEGRAM_CHAT_ID" --arg t "$text" \
        '{chat_id: $c, text: $t, parse_mode: "HTML", disable_web_page_preview: true}' |
      curl -sS -K "$cfg" -H 'Content-Type: application/json' --data-binary @-)
printf '%s' "$out" | jq -e .ok >/dev/null || { echo "telegram: $(printf '%s' "$out" | jq -r '.description // "no answer"')" >&2; exit 1; }
echo "telegram: sent ($MODE $TAG $ACTION)"
