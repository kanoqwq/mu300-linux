# A daemon's own directory under /run, for mu300-atd and other root daemons (sourced; POSIX sh, busybox
# ash included).
#
# /run takes files from every user on these images (boot/init mounts it 1777), so a name there may be somebody
# else's, made first: a directory whose owner reads what is written into it (the modem's answers, the callers'
# numbers) and puts his own files where a daemon expects its own (a command fifo, pid files it kills), or a symlink
# that sends root's writes elsewhere. Existing is therefore not enough: the directory must be a real directory,
# its owner the daemon's user, and nobody else may write it. One helper for both, so the two checks cannot drift.
#
#   run_dir_ours PATH [UID]         true when PATH is a directory (not a symlink to one) that UID (default 0) owns
#                                   and neither its group nor others may write
#   run_dir_take PATH [UID] [WHO]   make PATH that: created when missing; a foreign entry (another owner, a symlink,
#                                   a file) is said so on stderr and put out of the way, a directory of UID's that
#                                   others may write has that taken away - so a squatted name is recovered from,
#                                   not a reason to stay down. Only UID itself changes anything; for anybody else
#                                   (a member of the voice group reading the events) this is the check alone. False,
#                                   with the reason on stderr as "WHO: ...", when PATH is still not UID's own.
#
# Whoever made the foreign entry can still write its parent, so between looking at it and acting on it he may have
# put something else there. Nothing here therefore depends on what was seen, and nothing is deleted recursively:
#   * a non-directory goes with "rm -f --", an empty directory with "rmdir" - neither follows a symlink at the name
#     it is given, and each fails harmlessly on the other kind;
#   * a directory with something in it is moved, as it is, into a directory made for it on the spot (mkdir: ours
#     and 0700, or it fails and nothing is moved) - one rename, into a place nobody else can have prepared, and it
#     stays there to be looked at;
#   * the new directory is a plain mkdir, which makes nothing where a name already is;
# and what counts in the end is the check again, not that the steps ran.
run_dir_ours() {
    set -- "$1" "${2:-0}" $(ls -lnd "$1" 2>/dev/null)
    case ${3:-} in d????w*|d???????w*) return 1 ;; d*) ;; *) return 1 ;; esac
    [ "${5:-}" = "$2" ]
}
run_dir_take() {
    _rd=$1; _ru=${2:-0}; _rw=${3:-run-dir}
    if [ "$(id -u 2>/dev/null)" != "$_ru" ]; then
        # not ours to make either: made by this caller it would be the very directory this is about
        run_dir_ours "$_rd" "$_ru" && return 0
        echo "$_rw: $_rd is missing or not the daemon's own directory" >&2
        return 1
    fi
    # never "mkdir -p": through a symlink of somebody else's that would make the directory where it points
    ( umask 077; mkdir "$_rd" ) 2>/dev/null
    run_dir_ours "$_rd" "$_ru" && return 0
    set -- $(ls -lnd "$_rd" 2>/dev/null)
    case ${1:-}/${3:-} in
        d*/"$_ru")
            # ours, and in a sticky /run nobody else can put another entry in its place
            echo "$_rw: others could write $_rd; that is taken away" >&2
            chmod go-w "$_rd" 2>/dev/null ;;
        *)
            echo "$_rw: $_rd was somebody else's (made first, or a symlink); put out of the way" >&2
            rm -f -- "$_rd" 2>/dev/null || rmdir "$_rd" 2>/dev/null || {
                _rq=${_rd%/*}/.${_rd##*/}.foreign.$$
                if ( umask 077; mkdir "$_rq" ) 2>/dev/null; then
                    mv -- "$_rd" "$_rq/" 2>/dev/null && echo "$_rw: it is in $_rq now" >&2
                fi
            }
            ( umask 077; mkdir "$_rd" ) 2>/dev/null ;;
    esac
    run_dir_ours "$_rd" "$_ru" && return 0
    echo "$_rw: $_rd is still not the daemon's own directory; look at it and remove it by hand" >&2
    return 1
}
