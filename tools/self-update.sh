# Bring this copy of the project up to date with GitHub before the installer does anything: an old installer
# is how people ended up with old fixes long after new ones were published. Sourced by install.sh; uses say(),
# t(), $TOP and $REPO. Nothing here may stop an install: without GitHub (offline, blocked, rate-limited) it says
# so and the local copy runs.
#   git clone:  fast-forward to GitHub's main (not with local changes, not when this copy is ahead of it)
#   zip/tarball download:  replace the files that differ from GitHub's main; the commit checked is kept in
#               .mu300-source, so the next run downloads nothing when there is nothing new. Paths listed in
#               .mu300-keep (one per line, # comments; not part of the project) stay as they are - a copy with
#               fixes of its own keeps them (install.ps1 does the same, issue #6)
#   MU300_NO_SELF_UPDATE=1  skip all of this

# self_update SCRIPT ARGS...: returns when this copy is current; otherwise updates it and restarts SCRIPT
self_update() {
    _su_script=$1; shift
    [ -z "${MU300_NO_SELF_UPDATE:-}" ] && [ -z "${MU300_SELF_UPDATED:-}" ] || return 0
    command -v curl >/dev/null || return 0
    _su_remote=$(curl -fsSL --connect-timeout 5 --max-time 15 "https://api.github.com/repos/$REPO/commits/main" 2>/dev/null |
        sed -n 's/^  "sha": "\([0-9a-f]\{40\}\)",*$/\1/p' | head -n1)
    if [ -z "$_su_remote" ]; then
        echo "  $(t 'could not check GitHub for a newer installer; continuing with this copy')"
        return 0
    fi
    # (-e: in a git worktree .git is a file, and such a copy was taken for a download and overwritten with main)
    if [ -e "$TOP/.git" ] && command -v git >/dev/null; then
        [ "$(git -C "$TOP" rev-parse HEAD 2>/dev/null)" = "$_su_remote" ] && return 0
        # a copy that already has GitHub's commit is ahead of it (someone working on the project): leave it alone
        git -C "$TOP" merge-base --is-ancestor "$_su_remote" HEAD 2>/dev/null && return 0
        if [ -n "$(git -C "$TOP" status --porcelain --untracked-files=no 2>/dev/null)" ]; then
            echo "  $(t 'a newer installer is on GitHub, but this copy has local changes; not updating it')"
            return 0
        fi
        say "$(t 'Updating the installer to the newest version from GitHub')"
        # by URL, not by remote name: a clone may call it anything, or be a fork. Stalled transfers are cut off.
        if ! GIT_HTTP_LOW_SPEED_LIMIT=1000 GIT_HTTP_LOW_SPEED_TIME=20 GIT_TERMINAL_PROMPT=0 \
                git -C "$TOP" fetch -q "https://github.com/$REPO.git" main 2>/dev/null ||
           ! git -C "$TOP" merge -q --ff-only FETCH_HEAD 2>/dev/null; then
            echo "  $(t 'could not update this copy (it has commits of its own?); continuing with it')"
            return 0
        fi
    else
        [ "$(cat "$TOP/.mu300-source" 2>/dev/null)" = "$_su_remote" ] && return 0
        say "$(t 'Checking the installer against the newest version on GitHub')"
        _su_tmp=$(mktemp -d)
        if ! curl -fsSL --connect-timeout 10 --max-time 600 "https://codeload.github.com/$REPO/tar.gz/$_su_remote" |
                tar -xzf - -C "$_su_tmp" 2>/dev/null; then
            rm -rf "$_su_tmp"
            echo "  $(t 'could not download it; continuing with this copy')"
            return 0
        fi
        _su_src=$(find "$_su_tmp" -mindepth 1 -maxdepth 1 -type d | head -n1)
        # the files listed in .mu300-keep are left out of the new copy before anything is compared or copied
        _su_kept=0
        if [ -r "$TOP/.mu300-keep" ]; then
            while IFS= read -r k; do
                k=$(printf '%s' "$k" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s|^\./||')
                case $k in ''|'#'*) continue ;; esac
                [ -f "$_su_src/$k" ] && rm -f "$_su_src/$k" && _su_kept=$((_su_kept + 1))
            done < "$TOP/.mu300-keep"
        fi
        _su_n=$(cd "$_su_src" && find . -type f | while IFS= read -r f; do cmp -s "$f" "$TOP/$f" || echo "$f"; done | wc -l | tr -d ' ')
        [ "$_su_n" = 0 ] || cp -R "$_su_src"/. "$TOP"/
        [ "$_su_kept" = 0 ] || echo "  $(t 'this copy has changes of its own, so {1} files were left as they are' "$_su_kept")"
        echo "$_su_remote" > "$TOP/.mu300-source"
        rm -rf "$_su_tmp"
        [ "$_su_n" = 0 ] && return 0
        echo "  $(t '{1} files updated' "$_su_n")"
    fi
    echo "  $(t 'restarting the updated installer')"
    # a secret answer was taken out of the environment (tools/answers.sh): the restarted installer needs it once more
    for _su_k in ${ANSWERS_SECRET:-}; do eval "[ -z \"\${$_su_k+set}\" ] || export $_su_k"; done
    MU300_SELF_UPDATED=1 MU300_LANG=$MU300_LANG exec sh "$_su_script" "$@"
}
