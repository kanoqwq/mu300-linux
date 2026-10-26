# JSON for the device's shell scripts, without jq or jsonfilter (Ubuntu has one, OpenWrt the other; this runs on
# both, and under busybox awk, mawk, gawk and the BSD awk the tests may use). Always run with LC_ALL=C: a string is
# bytes; \u escapes become UTF-8.
#
#   awk -v mode=flat -f json.awk     stdin: one JSON object whose values are strings, numbers, true/false or null
#                                    (mu300-api's requests). Prints one line per member: KEY<tab>TYPE<tab>VALUE, TYPE
#                                    s (string), n (number), b (true/false), z (null); in VALUE a backslash, newline,
#                                    tab and CR are written \\ \n \t \r. A key is [a-z][a-z0-9_]{0,31}. A nested value,
#                                    a key twice, a control character in a string other than tab, newline and CR, a
#                                    \u0000, or anything after the object: exit 1 and nothing printed. With -v anykey=1
#                                    a key may be any name ([A-Za-z_][A-Za-z0-9_]*: a tool's settings).
#   awk -v mode=check -f json.awk    stdin: one JSON value of any shape (a tool's answer); prints it again on one line,
#                                    every string escaped anew (so whatever a tool printed cannot break the reply it is
#                                    put into); exit 1 when it is not exactly one JSON value. Nesting at most 32 deep.
#   awk -v mode=str -f json.awk      stdin: any text; prints it as one JSON string (control characters escaped, a
#                                    trailing newline of the input dropped)
BEGIN {
    BS = "\\"; QU = "\""
    CTL = "[" sprintf("%c", 1) "-" sprintf("%c", 31) sprintf("%c", 127) "]"
    TABC = sprintf("%c", 9); CRC = sprintf("%c", 13)
    MARKS = "[" sprintf("%c", 1) sprintf("%c", 2) "]"
    MARKED = sprintf("%c", 1) "[^" sprintf("%c", 2) "]*" sprintf("%c", 2)
    for (i = 0; i < 16; i++) HEX[substr("0123456789abcdef", i + 1, 1)] = i
    for (i = 10; i < 16; i++) HEX[substr("ABCDEF", i - 9, 1)] = i
    S = ""; N = 0
}
{ S = (N++ ? S "\n" : "") $0 }
END {
    if (mode == "str") { printf "%s\n", enc(S); exit 0 }
    L = length(S); P = 1; BAD = 0
    if (mode == "flat") {
        flat()
        if (BAD) exit 1
        printf "%s", OUT
        exit 0
    }
    ws(); v = value(0); ws()
    if (BAD || P <= L) exit 1
    printf "%s\n", v
    exit 0
}
function fail() { BAD = 1; P = L + 1; return "" }
function ws(   c) {
    while (P <= L) { c = substr(S, P, 1); if (c == " " || c == "\n" || c == TABC || c == CRC) P++; else break }
}
# enc(TEXT): TEXT as a JSON string
# rep(T, FROM, TO): every FROM in T replaced by TO, literally. Not gsub: what a backslash in gsub's replacement means
# differs between awks, and busybox's awk (OpenWrt) turned "\\\\" into one backslash - a quote in a string came out
# unescaped and a backslash was lost (seen on a U30 Air: every SSID with a quote broke wifi.get's answer).
function rep(t, from, to,   o, i) {
    o = ""
    while ((i = index(t, from)) > 0) { o = o substr(t, 1, i - 1) to; t = substr(t, i + length(from)) }
    return o t
}
# esc(T): backslash, newline, tab and CR written \\ \n \t \r
function esc(t) { return rep(rep(rep(rep(t, BS, BS BS), "\n", BS "n"), TABC, BS "t"), CRC, BS "r") }
function enc(t,   o, c, i) {
    t = esc(t); t = rep(t, QU, BS QU)
    if (t ~ CTL) {
        o = ""
        for (i = 1; i <= length(t); i++) {
            c = substr(t, i, 1)
            if (c ~ CTL) o = o sprintf("\\u%04x", ord(c)); else o = o c
        }
        t = o
    }
    return QU t QU
}
function ord(c,   i) { for (i = 1; i < 128; i++) if (sprintf("%c", i) == c) return i; return 0 }
# utf8(CODEPOINT): its bytes
function utf8(n) {
    if (n < 128) return sprintf("%c", n)
    if (n < 2048) return sprintf("%c%c", 192 + int(n / 64), 128 + n % 64)
    if (n < 65536) return sprintf("%c%c%c", 224 + int(n / 4096), 128 + int(n / 64) % 64, 128 + n % 64)
    return sprintf("%c%c%c%c", 240 + int(n / 262144), 128 + int(n / 4096) % 64, 128 + int(n / 64) % 64, 128 + n % 64)
}
function hex4(   i, c, n) {
    if (P + 3 > L) return -1
    n = 0
    for (i = 0; i < 4; i++) {
        c = substr(S, P + i, 1)
        if (!(c in HEX)) return -1
        n = n * 16 + HEX[c]
    }
    P += 4
    return n
}
# str(): the string at P (P on its opening quote), decoded; BAD on any error. Raw text may hold no control
# character at all (JSON's rule); what a \u escape brings is marked \001..\002 for flat() to look at.
function str(   o, c, n, m, chunk, q, b, seg) {
    P++
    o = ""
    while (P <= L) {
        # the plain run up to the next quote or backslash, in pieces of 512 bytes
        chunk = substr(S, P, 512)
        q = index(chunk, QU); b = index(chunk, BS)
        if (q == 0 && b == 0) {
            if (chunk ~ CTL) return fail()
            o = o chunk; P += length(chunk)
            continue
        }
        if (b == 0 || (q > 0 && q < b)) {
            seg = substr(chunk, 1, q - 1)
            if (seg ~ CTL) return fail()
            P += q
            return o seg
        }
        seg = substr(chunk, 1, b - 1)
        if (seg ~ CTL) return fail()
        o = o seg; P += b
        c = substr(S, P, 1); P++
        if (c == QU || c == BS || c == "/") o = o c
        else if (c == "n") o = o "\n"
        else if (c == "t") o = o TABC
        else if (c == "r") o = o CRC
        else if (c == "b") o = o "\001" sprintf("%c", 8) "\002"
        else if (c == "f") o = o "\001" sprintf("%c", 12) "\002"
        else if (c == "u") {
            n = hex4()
            if (n <= 0) return fail()
            if (n >= 55296 && n < 56320) {
                if (substr(S, P, 2) != BS "u") return fail()
                P += 2; m = hex4()
                if (m < 56320 || m >= 57344) return fail()
                n = 65536 + (n - 55296) * 1024 + (m - 56320)
            } else if (n >= 56320 && n < 57344) return fail()
            o = o "\001" utf8(n) "\002"
        } else return fail()
    }
    return fail()
}
# a decoded string may hold \u-escaped control characters (marked \001..\002 while the raw text is checked)
function unmark(t) { gsub(MARKS, "", t); return t }
function num(   m) {
    m = substr(S, P, 64)
    if (!match(m, /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?/)) return fail()
    P += RLENGTH
    return substr(m, 1, RLENGTH)
}
function lit(   m) {
    m = substr(S, P, 5)
    if (substr(m, 1, 4) == "true") { P += 4; return "true" }
    if (m == "false") { P += 5; return "false" }
    if (substr(m, 1, 4) == "null") { P += 4; return "null" }
    return fail()
}
function value(d,   c, o, k, first) {
    if (d > 32) return fail()
    ws(); c = substr(S, P, 1)
    if (c == QU) return enc(unmark(str()))
    if (c == "{" || c == "[") {
        P++; o = c; first = 1
        for (;;) {
            ws()
            if (BAD) return ""
            if (substr(S, P, 1) == (c == "{" ? "}" : "]") && first) { P++; break }
            if (c == "{") {
                if (substr(S, P, 1) != QU) return fail()
                k = enc(unmark(str())); ws()
                if (substr(S, P, 1) != ":") return fail()
                P++
                o = o (first ? "" : ",") k ":" value(d + 1)
            } else o = o (first ? "" : ",") value(d + 1)
            if (BAD) return ""
            first = 0; ws()
            if (substr(S, P, 1) == ",") { P++; continue }
            if (substr(S, P, 1) == (c == "{" ? "}" : "]")) { P++; break }
            return fail()
        }
        return o (c == "{" ? "}" : "]")
    }
    if (c == "-" || c ~ /[0-9]/) return num()
    return lit()
}
# flat(): the request object, into OUT
function flat(   k, c, t, v, seen) {
    OUT = ""
    ws()
    if (substr(S, P, 1) != "{") return fail()
    P++; ws()
    if (substr(S, P, 1) == "}") { P++; ws(); if (P <= L) fail(); return }
    for (;;) {
        ws()
        if (substr(S, P, 1) != QU) return fail()
        k = unmark(str())
        if (BAD || length(k) > 32 || (k in seen)) return fail()
        if (anykey ? k !~ /^[A-Za-z_][A-Za-z0-9_]*$/ : k !~ /^[a-z][a-z0-9_]*$/) return fail()
        seen[k] = 1
        ws()
        if (substr(S, P, 1) != ":") return fail()
        P++; ws(); c = substr(S, P, 1)
        if (c == QU) {
            t = "s"; v = str()
            if (BAD) return
            # \u escapes may not bring a control character the raw text could not have, but tab, newline and CR
            if (v ~ MARKED) {
                if (cleanctl(v)) return fail()
            }
            v = unmark(v)
        } else if (c == "-" || c ~ /[0-9]/) { t = "n"; v = num() }
        else { v = lit(); t = (v == "null" ? "z" : "b") }
        if (BAD) return
        v = esc(v)
        OUT = OUT k "\t" t "\t" v "\n"
        ws()
        if (substr(S, P, 1) == ",") { P++; continue }
        if (substr(S, P, 1) == "}") { P++; break }
        return fail()
    }
    ws()
    if (P <= L) fail()
}
# cleanctl(MARKED): 1 when an escaped character is a control character other than tab, newline, CR
function cleanctl(t,   x) {
    while (match(t, MARKED)) {
        x = substr(t, RSTART + 1, RLENGTH - 2)
        if (x ~ CTL && x != TABC && x != CRC && x != "\n") return 1
        t = substr(t, RSTART + RLENGTH)
    }
    return 0
}
