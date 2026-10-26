/* mu300-pwcheck: is a password the one a crypt(3) hash was made of? For tools/android-pwcheck.sh, which runs on
 * Android as root and must never run a program of the Linux system it looks at (a Linux system's perl or openssl
 * would run with Android's root): this is the project's own, built statically against libxcrypt
 * (tools/make-release.sh, into the 5.4 kernel bundle next to busybox; the Magisk zips carry it as mu300/mu300-pwcheck).
 *
 *   printf '%s\n%s\n' PASSWORD HASH | mu300-pwcheck
 *
 * Both come on standard input, as data: the password on the first line, the hash (from the system's /etc/shadow,
 * untrusted) on the second. It prints one word and never either of them: MATCH (exit 0), NOMATCH (1), or
 * UNVERIFIABLE (2: a method this libxcrypt does not know, or a malformed hash). Methods: whatever libxcrypt does -
 * yescrypt ($y$, what Ubuntu's passwd writes), SHA-512/256-crypt ($6$, $5$, also with rounds=), MD5-crypt ($1$),
 * bcrypt ($2b$ ...). An empty hash is not a hash: android-pwcheck.sh decides an empty field itself.
 */
#include <crypt.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAXLINE 4096

static int read_line(char *buf, size_t n) {
    if (!fgets(buf, (int)n, stdin)) return 0;
    size_t l = strlen(buf);
    if (l && buf[l - 1] == '\n') buf[--l] = 0;
    else if (l == n - 1) return 0;           /* longer than a line may be: refused */
    if (l && buf[l - 1] == '\r') buf[--l] = 0;
    return 1;
}

static void wipe(void *p, size_t n) {
    volatile unsigned char *v = p;
    while (n--) *v++ = 0;
}

int main(void) {
    static char pw[MAXLINE], hash[MAXLINE];
    struct crypt_data *cd = calloc(1, sizeof *cd);
    int rc = 2;
    if (!cd || !read_line(pw, sizeof pw) || !read_line(hash, sizeof hash) || !hash[0]) goto out;
    const char *r = crypt_r(pw, hash, cd);
    /* libxcrypt: a failure is NULL or a string starting with '*' (never a valid hash) */
    if (!r || r[0] == '*') goto out;
    size_t lr = strlen(r), lh = strlen(hash);
    unsigned char d = (unsigned char)(lr != lh);
    for (size_t i = 0; i < lh && i < lr; i++) d |= (unsigned char)(r[i] ^ hash[i]);
    rc = d ? 1 : 0;
out:
    wipe(pw, sizeof pw);
    wipe(hash, sizeof hash);
    if (cd) { wipe(cd, sizeof *cd); free(cd); }
    puts(rc == 0 ? "MATCH" : rc == 1 ? "NOMATCH" : "UNVERIFIABLE");
    return rc;
}
