# Messages of the installer scripts in other languages. i18n/<lang>.tsv holds one "English<TAB>translation" line
# per message, where {1}..{6} stand for the arguments; a message without a line there stays in English, so a
# missing translation never breaks anything. install.ps1 reads the same files. Needs $TOP.
#   MU300_LANG=en|tr|zh   the language (asked by choose_language when it is not set)
MU300_LANG=${MU300_LANG:-}

# t "English message with {1}" ARG...: the message in the chosen language, without a newline
t() {
    _tk=$1; shift
    _tf=/dev/null
    [ -n "$MU300_LANG" ] && [ "$MU300_LANG" != en ] && [ -f "$TOP/i18n/$MU300_LANG.tsv" ] && _tf=$TOP/i18n/$MU300_LANG.tsv
    TK=$_tk TF=$_tf T1=${1-} T2=${2-} T3=${3-} T4=${4-} T5=${5-} T6=${6-} awk 'BEGIN {
        s = ENVIRON["TK"]
        while ((getline l < ENVIRON["TF"]) > 0) {
            i = index(l, "\t")
            if (i > 1 && substr(l, 1, i - 1) == s) { s = substr(l, i + 1); break }
        }
        out = ""
        while ((i = index(s, "{")) > 0) {
            c = substr(s, i + 1, 1)
            if (c ~ /^[1-6]$/ && substr(s, i + 2, 1) == "}") { out = out substr(s, 1, i - 1) ENVIRON["T" c]; s = substr(s, i + 3) }
            else { out = out substr(s, 1, i); s = substr(s, i + 1) }
        }
        printf "%s", out s
    }'
}

# the language, asked once unless MU300_LANG is set; English is the default
choose_language() {
    case $MU300_LANG in en|tr|zh) return 0 ;; esac
    printf '\n  1) English   2) Türkçe   3) 中文\n  Language / Dil / 语言 [1]: '
    read -r _l || _l=
    case $_l in 2|tr|TR) MU300_LANG=tr ;; 3|zh|ZH) MU300_LANG=zh ;; *) MU300_LANG=en ;; esac
}

# answers typed in the chosen language count as the English keywords the scripts compare with
normalize_answer() {
    case $1 in
        evet|Evet|EVET|e|E|y|Y|Yes|YES|是|是的|好) echo yes ;;
        hayır|hayir|Hayır|Hayir|HAYIR|h|H|n|N|No|NO|否|不|不是) echo no ;;
        güncelle|guncelle|Güncelle|更新) echo update ;;
        sil|Sil|清除|擦除) echo wipe ;;
        *) printf '%s\n' "$1" ;;
    esac
}
