# kernel.org signing keys (pinned)

`upstream/verify-kernel-src.sh` checks every mainline kernel tarball `upstream/build.sh` downloads (locally, in the
Mainline kernel build workflow and in the release workflow) against these keyrings with `gpgv`, and fails closed
(FINDINGS 161). There are two independent checks:

| file | used for | keys |
|---|---|---|
| `kernel-autosigner.gpg` / `.fpr` | `sha256sums.asc` of the `v6.x`/`v7.x` directory: the tarball's SHA-256 must be on a line of the signed text | Kernel.org checksum autosigner |
| `kernel-releasers.gpg` / `.fpr` | `linux-KV.tar.sign`: the releaser's signature over the uncompressed tar | Linus Torvalds, Greg Kroah-Hartman, Sasha Levin |

The `.gpg` files are binary keyrings (`gpg --export --export-options export-minimal`) that `gpgv --keyring` reads;
the `.fpr` files list the primary key fingerprints a signature may come from (the script checks gpgv's `VALIDSIG`
against them, so a key added to a keyring by mistake is still refused).

## Fingerprints

| key | fingerprint | created |
|---|---|---|
| Kernel.org checksum autosigner `<autosigner@kernel.org>` | `B8868C80BA62A1FFFAF5FDA9632D3A06589DA6B1` | 2013-01-24, rsa4096 |
| Linus Torvalds `<torvalds@kernel.org>` | `ABAF11C65A2970B130ABE3C479BE3E4300411886` | 2011-09-20, rsa2048 |
| Greg Kroah-Hartman `<gregkh@kernel.org>` | `647F28654894E3BD457199BE38DBBDC86092693E` | 2011-09-23, rsa4096 |
| Sasha Levin `<sashal@kernel.org>` | `E27E5D8A3403A2EF66873BBCDEA66FF797772CDC` | 2012-02-09, rsa4096 |

None of the keys expires. They were fetched on 2026-10-16 and the fingerprints agreed across these sources:

* kernel.org's Web Key Directory: `gpg --auto-key-locate clear,wkd --locate-keys autosigner@kernel.org
  torvalds@kernel.org gregkh@kernel.org sashal@kernel.org`;
* the kernel.org pgpkeys repository, `https://git.kernel.org/pub/scm/docs/kernel/pgpkeys.git`, `keys/<long key id>.asc`;
* <https://www.kernel.org/signature.html>, "Important fingerprints", for Linus, Greg and Sasha (the page describes
  the autosigner and `sha256sums.asc` but prints no fingerprint for it: WKD and pgpkeys are its two sources).

keys.openpgp.org returns these keys without user IDs (none of them is verified there), so it was not a source.
Ben Hutchings, the fourth releaser on signature.html, signs only old (3.x/4.x) stable kernels and is left out.

## Refreshing

A new releaser, an expired or revoked key: fetch the key from at least two of the sources above in a scratch
`GNUPGHOME`, compare the fingerprints, then

```sh
gpg --export --export-options export-minimal autosigner@kernel.org > upstream/keys/kernel-autosigner.gpg
gpg --export --export-options export-minimal torvalds@kernel.org gregkh@kernel.org sashal@kernel.org > upstream/keys/kernel-releasers.gpg
```

and update the `.fpr` files, this table and `tests/test_kernel_src_signature.py` (which pins the same list).
