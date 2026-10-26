# Which release install.sh installs. Sourced by install.sh (t, ask and die are its own; needs $TOP, $REPO, $RELEASE).
#   MU300_RELEASE=<tag>       that release, and nothing is asked
#   MU300_RELEASE_URL=<url>   another place with the same files (the release is then called "custom"); nothing asked
# Otherwise the newest full release. When GitHub also has a prerelease published after it, release_resolve asks
# whether to install that beta instead (default: no). tools/release_pick.py decides which is which, for install.ps1
# and the graphical installer too.

# --- release-tag begin
# The release tags (docs/FINDINGS.md 125): vYYYY.MM.DD up to v2026.10.27, where the "day" was a counter, and from then
# on vYYYY.MM.DD.N: the real (UTC) day the release was built, N = 1..99 for the releases of that day. Every four-field
# tag is newer than every three-field one; four-field tags compare by the date, then by N, as numbers. The same block,
# word for word, is in tools/releases.sh (install.sh, make-release.sh), mu300-update and tools/android-install.sh
# (tests/test_releases.py holds the copies to each other and to tests/fixtures/release-tags.tsv).
# tag_ok TAG: TAG is a release tag (in the shell's own patterns: no grep, no locale, no regex engine to differ)
tag_ok() {
    case $1 in
        v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]) return 0 ;;
        v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9].[1-9]|v[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9].[1-9][0-9]) ;;
        *) return 1 ;;
    esac
    case ${1#v?????} in 0[1-9].*|1[0-2].*) ;; *) return 1 ;; esac
    case ${1#v????????} in 0[1-9].*|[12][0-9].*|3[01].*) ;; *) return 1 ;; esac
}
# tag_key TAG: a number in the order of the releases (1YYYYMMDD00 for vYYYY.MM.DD, 2YYYYMMDDNN for vYYYY.MM.DD.N);
# nothing and status 1 for what is no release tag
tag_key() {
    tag_ok "$1" || return 1
    case $1 in
        *.*.*.*) _tn=${1##*.}; [ ${#_tn} = 2 ] || _tn=0$_tn; _tk=${1%.*}; _te=2 ;;
        *) _tn=00; _tk=$1; _te=1 ;;
    esac
    _tk=${_tk#v}; _td=${_tk#*.}
    echo "$_te${_tk%%.*}${_td%.*}${_td#*.}$_tn"
}
# newer A B: release A is newer than release B; false when either is no release tag. The key is compared in two
# parts (the era and the date, nine digits; then N without its leading zero), so that no shell's integers overflow
# and none reads 08 as octal.
newer() {
    _na=$(tag_key "$1") && _nb=$(tag_key "$2") || return 1
    [ "${_na%??}" -gt "${_nb%??}" ] && return 0
    [ "${_na%??}" = "${_nb%??}" ] || return 1
    _na=${_na#?????????}; _nb=${_nb#?????????}
    [ "${_na#0}" -gt "${_nb#0}" ]
}
# --- release-tag end
# release_list: LATEST=<tag> and, with a newer prerelease, BETA=<tag>, from GitHub's list of releases. No token is
# needed. Nothing at all when the list does not come (no network, the API's rate limit, a proxy's page).
release_list() {
    curl -fsSL --connect-timeout 10 --max-time 30 "https://api.github.com/repos/$REPO/releases?per_page=15" 2>/dev/null |
        python3 "$TOP/tools/release_pick.py" 2>/dev/null || true
}

# release_resolve: RELEASE, when it is not set yet. BETA_OFFERED is the prerelease that was offered (empty: none).
release_resolve() {
    BETA_OFFERED=
    [ -z "$RELEASE" ] || return 0
    if [ -n "${MU300_RELEASE_URL:-}" ]; then RELEASE=custom; return 0; fi
    _rl=$(release_list)
    RELEASE=$(printf '%s\n' "$_rl" | sed -n 's/^LATEST=//p' | head -n1)
    _rb=$(printf '%s\n' "$_rl" | sed -n 's/^BETA=//p' | head -n1)
    if [ -z "$RELEASE" ]; then
        # the list did not come: the newest release alone, the way it was always looked up, and no question
        _rb=
        RELEASE=$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
            | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n1)
        [ -n "$RELEASE" ] || die "$(t 'cannot find the newest release of {1} (set MU300_RELEASE=<tag> to pick one)' "$REPO")"
    fi
    [ -n "$_rb" ] || return 0
    BETA_OFFERED=$_rb
    echo "  $(t 'A beta is out: {1}, a prerelease for testing, newer than the newest full release {2}.' "$_rb" "$RELEASE")"
    ask _ra "$(t 'Install the beta {1} instead of {2}? (yes/no)' "$_rb" "$RELEASE")" no MU300_ANS_BETA
    if [ "$_ra" = yes ]; then RELEASE=$_rb; fi
    return 0
}
