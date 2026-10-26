# Sourced (sh, bash, busybox sh): case_sensitive DIR - status 0 when DIR keeps two names that differ only in case
# apart, else a message on stderr and status 1. A kernel's module tree has such names (mainline: xt_HL.ko and
# xt_hl.ko, xt_TCPMSS.ko and xt_tcpmss.ko, xt_DSCP.ko and xt_dscp.ko, xt_RATEEST.ko and xt_rateest.ko); on a file
# system that ignores case (macOS's default APFS, Windows' NTFS) each pair becomes one file, under one name with the
# other's contents, and the bundle loses a module without a word (docs/FINDINGS.md 212). Asked of the directory
# itself, by making two such files in it: a volume's name says nothing about a directory mounted from elsewhere.
case_sensitive() {
    _cs_d=$(mktemp -d "$1/.mu300-case.XXXXXX" 2>/dev/null) || { echo "cannot make a directory in $1" >&2; return 1; }
    : > "$_cs_d/mu300-case" && : > "$_cs_d/MU300-CASE" && _cs_n=$(ls "$_cs_d" | wc -l) || _cs_n=0
    rm -rf "$_cs_d"
    [ "$_cs_n" -eq 2 ] && return 0
    cat >&2 <<MSG
$1 is on a file system that ignores case: kernel modules whose names differ only in case (xt_HL.ko and xt_hl.ko)
would become one file there. Work on a case-sensitive volume instead, for example on macOS an APFS case-sensitive
sparse image:
  hdiutil create -size 100g -type SPARSE -fs 'Case-sensitive APFS' -volname mu300 ~/mu300.sparseimage
  hdiutil attach ~/mu300.sparseimage      # then clone or copy the tree to /Volumes/mu300 and run it from there
(docs/FINDINGS.md 212)
MSG
    return 1
}
