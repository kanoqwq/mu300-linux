# Built-in nft capability packages

The project's kernel configurations build `NFT_SOCKET` and `NFT_TPROXY` in. Installing OpenWrt's
feed modules would supply binaries for another kernel. These two small APKs declare the already available
capabilities under the usual `kmod-nft-socket` and `kmod-nft-tproxy` names instead (issue #470).

Each package contains only a capability marker and the normal OpenWrt package file list. The package manager
owns both files and can remove them normally. There are no `.ko` files, autoload entries, firewall rules or
application changes. The date-based version identifies this firmware metadata, not a kernel ABI.

`openwrt/build-rootfs.sh` builds and installs the APKs into both OpenWrt variants. It checks the
selected mainline bundle's receipt-verified `kernel.config`, or `kernel/linux.config` for the BSP kernel,
including every IPv4/IPv6 helper. It does not infer the built kernel from the source fragment. By default it runs apk-tools
from the Alpine 3.23 Docker image with networking disabled; Docker may need to pull the image first. There is no
additional host apk dependency in the release workflow. For an offline build, set `MU300_APK_MKPKG` to a host
apk-tools binary with `mkpkg`, such as the SDK host tool or Alpine's apk-tools. The router's smaller apk binary
does not include the builder. Package ownership is normalized to root and host xattrs are excluded.

For a separate build:

```sh
python3 openwrt/nft-builtins/build.py --apk /path/to/host/apk \
    --config kernel/linux.config --config upstream/mu300-mainline.config --output work/nft-builtins
```

Input configurations must build the expressions, IPv4 and IPv6 in. A resolved configuration also
requires each IPv4/IPv6 socket and TPROXY helper to be built in. Image assembly installs with
`--no-scripts`, since its container shares the host kernel. On a live router, pre-install scripts additionally
require the expression and its IPv4/IPv6 helpers to be built in in `/proc/config.gz`, then check socket or TCP/UDP
TPROXY using `nft --check`. A failed check stops installation. The check never commits its candidate table/rules.

Locally built unsigned packages can be installed offline with `apk add --no-network --allow-untrusted FILE...`.
Do not use `--no-scripts` for a live install. Remove them with `apk del kmod-nft-socket kmod-nft-tproxy` if needed.
These declarations apply to the project's kernels; a replacement kernel without these features needs matching
real modules instead, and the declarations must be removed.
