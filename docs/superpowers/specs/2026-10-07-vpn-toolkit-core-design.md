# VPN toolkit ①: profiles, engine drivers, one command for every protocol

Date: 2026-10-07. Status: architecture approved in conversation (sub-project ① of the VPN toolkit); this document
fixes the details. ② is the LuCI screen on top of it, ④ adds L2TP, PPTP, IKEv2 and Tailscale exit nodes.

## Why

`mu300-vpn` speaks one protocol: a VLESS share link in `/etc/mu300/vpn.conf`, run by Xray (behind
hev-socks5-tunnel) or sing-box. People have WireGuard configs, OpenVPN files, Clash/mihomo subscriptions, VMess,
Trojan and Shadowsocks links, and raw Xray/sing-box JSON from their panels. The user's goal is one VPN screen for all
of them. That needs, under the screen, a store of named profiles, one driver per engine behind a fixed contract, and
a core that owns everything the drivers must not differ in: the kill switch, policy routing, DNS, Tailscale.

What works today must keep working unchanged: the U30 Air runs a legacy VLESS `vpn.conf` (ENGINE=xray, kill switch
off), and every test in `tests/test_vpn.py` stays green (the kill switch fails closed, the download window, xray never
runs without the kill switch's sing-box substitute, Tailscale rules 5198-5200).

## What the user gets

```
mu300-vpn profile list                       id, type, name, * on the active one (TSV)
mu300-vpn profile show ID                    name, type, source, created, server - never a secret
mu300-vpn profile add TYPE NAME FILE|URI     store a profile of that type
mu300-vpn profile import FILE|URI [NAME]     the same, type sniffed from the scheme or the content
mu300-vpn profile edit ID FILE|URI           replace its config (checked first)
mu300-vpn profile set ID KEY VALUE           a per-type option (TLS_PIN_SHA256, MIHOMO_STACK, OVPN_USER, ...)
mu300-vpn profile remove ID                  not the active one while the VPN is on
mu300-vpn profile use ID                     make it active; a running VPN restarts on it
mu300-vpn profile export ID                  the raw config to the terminal (the one place secrets are printed)
mu300-vpn on | off | restart | status        on: ENABLE=1 + service; off: ENABLE=0, service stopped, kill switch down
mu300-vpn settings [get [KEY] | set KEY VALUE]
mu300-vpn engines [install ENGINE]           TSV: engine, present|missing, path or the command that gets it
mu300-vpn check [ID]                         validate a profile with its engine (default: the active one)
mu300-vpn gen | run | guard | status | engines   unchanged for the service units, mobile-data and wifi-client
```

`mu300-toolkit`'s VPN menu works in profiles: list, activate, import (paste a link or a path), remove, kill switch,
engines, on/off, restart.

## Storage

Everything under `/etc/mu300`, which updates keep. Each system (Ubuntu, OpenWrt, openwrt-luci, Arch) has its own.

```
/etc/mu300/vpn.conf                    ENABLE=0|1 - still THE switch (mu300-update, android-install.sh, wifi-client,
                                       mu300-extra, the dashboard and older images read it); legacy keys below it
/etc/mu300/vpn/                        0700
    settings                           KEY=VALUE, 0600
    active                             the active profile id
    legacy.snapshot                    the legacy keys as last migrated (0600; see Migration)
    profiles/<id>/                     0700; id: [a-z0-9-]{1,32}
        meta                           NAME= TYPE= SOURCE=manual|subscription:<id> CREATED=<epoch> + per-type options
        uri | config.json | config.yaml | wg.conf | client.ovpn      (0600) exactly one
        auth.txt                       OpenVPN user/password (0600), only when the profile has them
    cache/mihomo/                      mihomo's home (GeoIP/GeoSite downloads survive reboots)
/run/mu300-vpn/                        0700, generated configs, iface, server-ip, dns, pids
```

`meta` and `settings` are never sourced: values are read with `sed -n "s/^KEY=//p"` and written single-quoted with
`'` escaped, so a name typed in LuCI cannot run anything. `MU300_VPN_DIR` (tests) defaults to the directory of
`MU300_VPN_CONF` + `/vpn`, so a test that points the conf into a scratch directory gets its store there too.

### Settings

| key | values | default | |
|---|---|---|---|
| KILL_SWITCH | 0, 1 | 1 | anything but 0 is 1 (as today) |
| TAILSCALE | 0, 1 | 1 | anything but 0 is 1 |
| IPV6 | 0, 1 | 0 | |
| REMOTE_DNS | IPv4/IPv6 address | 1.1.1.1 | DNS for the device and clients, through the tunnel |
| BOOTSTRAP_DNS | IPv4/IPv6 address | 1.1.1.1 | for server names, outside the tunnel (sing-box, mihomo) |
| LAN_CIDRS | comma list of CIDRs | empty | the device's own LAN is always added (as today) |
| XRAY HEV SING_BOX MIHOMO OPENVPN | absolute path | empty | another engine binary (as XRAY= in vpn.conf today) |

`settings set` validates and refuses (exit 2) a value outside these; on read an invalid value counts as the default.

### Profiles and types

| TYPE | config | engine | from |
|---|---|---|---|
| xray | `uri` (vless/vmess/trojan/ss link) or `config.json` (raw Xray JSON) | xray + hev-socks5-tunnel | vpn extra |
| sing-box | `uri` (vless link) or `config.json` (raw sing-box JSON) | sing-box | vpn extra |
| mihomo | `config.yaml` (Clash/mihomo) | mihomo | new `vpn-mihomo` extra |
| wireguard | `wg.conf` (wg-quick format) | kernel WireGuard + `wg` | wireguard-tools package |
| openvpn | `client.ovpn` (+ `auth.txt`) | openvpn | `apk add openvpn-openssl` / `apt-get install openvpn` |
| l2tp pptp ikev2 tailscale-exit | reserved | - | "not available in this version" (④) |

Import sniffing: `vless://` → xray, `vmess://` `trojan://` `ss://` → xray; a file: `[Interface]` → wireguard;
`client`/`remote ` lines or `<ca>` → openvpn; JSON with `"protocol"` in outbounds → xray, with `"type"` → sing-box;
YAML with `proxies:` or `proxy-providers:` → mihomo. The ID is the name lowercased, every run of other characters a
`-`, cut to 32, `-2`, `-3` ... when taken; empty → the type.

## Engine drivers

`/opt/mu300/lib/vpn/<type>.sh`, sourced by `mu300-vpn` (`MU300_VPN_LIB` for tests). The loader refuses a type that
is not `[a-z-]+` or has no file; for the reserved ones it says `TYPE: not available in this version`. Each driver
defines:

| function | does |
|---|---|
| `drv_engines` | prints the programs it needs, one path per line (resolved: extra, then /opt/mu300/bin, then PATH) |
| `drv_engines_ok` | 0 when they are all executable (and, for wireguard, the kernel has it) |
| `drv_check` | validates the profile's file (`$PDIR`) and the rebuild of it, with the driver's own parser; errors to stderr. The engine's own check (`xray run -test`, `sing-box check`, `mihomo -t`) runs at `drv_gen`, on the rebuilt file, because xray's needs the resolved addresses |
| `drv_gen` | the runtime config under `$RUN` from the profile + settings (the check first, then the engine's own check on the file written); writes `$RUN/server-ip` (addresses to keep off the tunnel, one per line) and optionally `$RUN/dns` |
| `drv_start` | brings up ONE tunnel interface, sets `TUN` and `DRV_PIDS`, prints `tunnel up on $TUN`; or, with `DRV_FOREGROUND=1`, execs the engine (sing-box) |
| `drv_alive` | 0 while the engine and its interface are there |
| `drv_stop` | stops what `drv_start` started, removes its interface |
| `drv_status` | extra status lines, never a secret |
| `drv_import SRC` | writes the profile files into `$PDIR` from a link or a file, or fails |

`DRV_ROUTES=core` (default) means the core installs the routing; sing-box sets `self` (its `auto_route` does exactly
what the core does, same prefs and table, as today). The core then:

1. puts the full kill switch up (KILL_SWITCH=1), before anything that can fail - unchanged;
2. migrates a legacy vpn.conf (below), loads the active profile and its driver;
3. gets missing engines: vpn / vpn-mihomo extras by `mu300-extra adopt`/`install` (through the download window when
   the kill switch is up - the window code is unchanged, the extra's name is now a parameter); packages are never
   installed by the service, it fails closed with the command to run;
4. `drv_gen` (which includes the check);
5. adds the tunnel to the wan firewall zone on OpenWrt (as today, for every tunnel name);
6. KILL_SWITCH=0: the kill switch down;
7. clears leftover routing, `drv_start`, then (core routing) `routes_up`: rule 9000 `fwmark 0x2d0 lookup main`, 9001
   LAN_CIDRS → main, 9002 each server address → main, `default dev $TUN table 2022`, 9010 `lookup 2022`, the
   Tailscale rules, and the DNS nat (device's own port 53 → `$RUN/dns` or REMOTE_DNS, marked traffic exempt). IPV6=1
   adds the same for IPv6 (`ip -6`) when the tunnel has an IPv6 address;
8. waits while `drv_alive`; on any exit `drv_stop` and `routes_down` - the kill switch stays.

### Marks: every engine's own traffic carries 0x2d0

The kill switch lets only marked traffic out on an uplink, and rule 9000 keeps it off the tunnel. Each driver marks:

* **xray**: `streamSettings.sockopt.mark = 720` on every outbound - from links as today, and for raw JSON written
  by `jq` into every outbound of the rebuilt document, as `sockopt = {"mark":720}` (plus a checked `dialerProxy`). Raw JSON also gets our SOCKS inbound in place of its own `inbounds` (nothing listens
  on the LAN), and server names in `vnext`/`servers` are resolved by the core and replaced by addresses (the name
  stays as `serverName` where TLS/REALITY had none) - Xray's own resolver sits behind the tunnel it is building.
  What else of the config survives is an allowlist (Security).
* **sing-box**: `route.default_mark`. Raw JSON: `jq` replaces `inbounds` with our TUN (gvisor, `sbtun`,
  `auto_route`, `strict_route`, the LAN excluded - as generated today), sets `default_mark`, `auto_detect_interface`,
  and adds a `mu300-bootstrap` UDP DNS server (BOOTSTRAP_DNS) used as `default_domain_resolver` unless the config
  names its own. The rest is held to an allowlist (Security). `sing-box check` runs on the rebuilt file.
* **mihomo**: `routing-mark: 720`. The YAML is reduced to the top-level keys the Security section allows (awk: a
  top-level key and its indented block; every other block is removed) and ours appended: TUN `mh-mu300`, stack from
  the profile's `MIHOMO_STACK` (default gvisor), `auto-route: false`, `auto-detect-interface: false`, no DNS hijack;
  `dns` with `proxy-server-nameserver`/`default-nameserver` = BOOTSTRAP_DNS, `nameserver` = REMOTE_DNS,
  `respect-rules: true`; `allow-lan: false`; `bind-address: '127.0.0.1'`; no `external-controller` of any kind (the
  TUN is the only thing that listens). Checked with `mihomo -t`.
* **wireguard**: `wg set wg-mu300 fwmark 0x2d0`. `wg.conf`'s wg-quick keys (Address, DNS, MTU, Table, Pre/PostUp/Down,
  SaveConfig) are stripped for `wg setconf`, and so are ListenPort (a config opens no port on the device) and
  FwMark (the mark is ours, set by `wg set` after setconf); the Endpoint name is resolved, Address goes on with `ip addr`, MTU
  default 1420, DNS (first) to `$RUN/dns`. A peer without `0.0.0.0/0` in AllowedIPs gets a warning (traffic outside
  it is dropped by WireGuard - closed, not leaked). A kernel without WireGuard (the 5.4 vendor kernel) fails the check
  with that message.
* **openvpn**: `--mark 720`. openvpn never reads the user's `.ovpn`: it is parsed with openvpn's own lexical rules
  and held to an allowlist, and openvpn reads only `$RUN/openvpn.conf` (0600), which we write from what was accepted.
  * Lexing (no differential with openvpn): refused are NUL and every control character but TAB (CR only as a CRLF
    line end), a backslash outside the key blocks, lines over 1024 bytes, files over 256 KiB, an unclosed quote, a
    quote inside a word, an empty quoted word and a quoted word followed by text without a space. Words split on
    space/TAB, quotes group (no escapes), `#`/`;` start a comment at the start of a word, a leading `--` is allowed.
  * Blocks: only a line that is exactly `<ca>`, `<cert>`, `<key>`, `<tls-auth>`, `<tls-crypt>`, `<tls-crypt-v2>`,
    `<dh>`, `<extra-certs>` or `<pkcs12>` opens one, once each; it ends at a line that is exactly the closing tag.
    Any other line starting with `</` inside a block is refused (openvpn closes on a prefix), as is any other `<...>`
    line (`<connection>`, `<auth-user-pass>`, `<up>`, `<ca> # x`). The body must be PEM (armour and base64), the `#`
    lines of a static key in tls-auth/tls-crypt, or base64 for pkcs12; it is written byte for byte (minus CR). Text
    outside the armour of a certificate block (easy-rsa's dump) is left out with a note.
  * Directives: `client`, `tls-client`, `pull`, `remote HOST [PORT] [PROTO]`, `remote-random`, `proto`, `port`,
    `resolv-retry`, `nobind`, `float`, `persist-key`, `persist-tun`, `cipher`, `data-ciphers`, `data-ciphers-fallback`,
    `auth`, `tls-version-min/max`, `tls-cipher`, `tls-ciphersuites`, `tls-groups`, `remote-cert-tls server`,
    `remote-cert-eku`, `remote-cert-ku`, `verify-x509-name`, `key-direction`, `compress` (no arg, lz4-v2, stub-v2,
    lz4, stub), `tun-mtu`, `mssfix`, `fragment`, `ping`, `ping-restart`, `keepalive`, `explicit-exit-notify`,
    `server-poll-timeout`, `connect-retry(-max)`, `connect-timeout`, `hand-window`, `auth-nocache`,
    `auth-retry none|nointeract`, `reneg-sec`, `sndbuf`, `rcvbuf`, `txqueuelen`, `mute-replay-warnings`, each with
    its arguments checked and written again by us. Left out with a note: `route-nopull`, `redirect-gateway`, `route`,
    `route-ipv6`, `dhcp-option`, `block-outside-dns`, `register-dns`; silently: `verb`, `mute`, `dev tun*`,
    `dev-type tun`, `auth-user-pass` without an argument (ours is on the command line). `up`/`down` are refused
    except for the well-known DNS helpers (`/etc/openvpn/update-resolv-conf`, `/etc/openvpn/update-systemd-resolved`,
    `/etc/openvpn/scripts/update-systemd-resolved`), which are left out with a note, and `script-security 1|2` only
    along with one of them. Everything else is refused, naming the line and the directive (only names of our own
    vocabulary; an unknown first word is never repeated) and never an argument. Also refused: a tap profile, no
    remote, neither `client` nor `tls-client`, no `<ca>`/`<pkcs12>`.
  * `remote` names are resolved to addresses; the written config is read back by the same parser for `server-ip`.
  Run as `openvpn --config $RUN/openvpn.conf --dev tun-mu300 --dev-type tun --route-noexec --pull-filter accept
  ifconfig --pull-filter accept ifconfig-ipv6 --pull-filter accept topology --pull-filter accept "dhcp-option DNS"
  --pull-filter accept ping --pull-filter accept ping-restart --pull-filter accept peer-id --pull-filter accept
  cipher --pull-filter accept auth-token --pull-filter accept route-gateway --pull-filter accept protocol-flags
  --pull-filter accept key-derivation --pull-filter accept tun-mtu --pull-filter ignore "" --mark 720
  --script-security 2 --up /opt/mu300/lib/vpn/openvpn-up --setenv MU300_VPN_RUN $RUN --auth-nocache --verb 3
  [--auth-user-pass $RUN/ovpn.auth]`: our options after `--config`, so they win. What the server pushes is held to
  an accept list, not a denylist: a pull filter is a prefix match and the first that matches wins, so the accepts
  come first and `ignore ""` (which matches everything) last. Accepted: the session's addresses and topology
  (`ifconfig`, `ifconfig-ipv6`, `topology`, `route-gateway`), the resolver our up script reads (`dhcp-option DNS`,
  which as a prefix also takes `DNS6`), the keepalive (`ping`, `ping-restart`; the prefix also takes `ping-exit`),
  and what the data-channel negotiation the client announced decided (`peer-id`, `cipher`, `auth-token`,
  `protocol-flags`, `key-derivation`, `tun-mtu`: IV_PROTO tls-ekm and dyn-tls-crypt, IV_MTU - a client that drops
  them while the server applies them cannot decrypt the data channel or gets a wrong MTU). None of them runs
  anything or names a file. Everything else is ignored by the catch-all: `route`, `route-ipv6`,
  `redirect-gateway`, `setenv`, `compress`/`comp-lzo`, `dns`, `client-nat`, `block-outside-dns` and every other
  `dhcp-option`. Not `--route-nopull`, which would also drop the pushed `dhcp-option DNS`. Not `--dns-updown
  disable` either: OpenVPN 2.6 (the images' version) does not know the option and would refuse to start; on 2.7
  the set `--up` script suppresses the built-in dns-updown by itself, and adding the option once the images carry
  2.7 is a follow-up. `ovpn.auth` is a 0600 copy of the profile's `auth.txt`, removed with the config when openvpn
  stops or fails to start. `openvpn-up` is our own script and the only one: it writes the first pushed
  `dhcp-option DNS` that is a proper IPv4 or IPv6 literal (or `dhcp-option DNS6` that is an IPv6 one) to
  `$RUN/dns`, and `$RUN/ovpn-up`. The driver waits for that file.

### Names behind the kill switch: the resolve window

Server names (wireguard Endpoint, openvpn remote, xray link and raw JSON) are resolved before the tunnel exists. With
the kill switch up the device's own DNS is dropped too (today's reason xray needs the sing-box substitute). The core
opens a **resolve window**: the download window's ruleset with only the DNS sets filled (the device's resolvers,
30 s timeouts; clients' DNS to the device dropped), resolves, and puts the full kill switch back - one transaction
each way, never a gap. The legacy behaviour stays as it is for VLESS links: with the kill switch, a VLESS xray profile
runs on sing-box when it is installed, and an allowInsecure link that needs a pin is refused rather than moved
(the existing tests). A non-VLESS xray link that asks for allowInsecure with no pin under the kill switch fails closed
(`mu300-vpn profile set ID TLS_PIN_SHA256 ...`).

## Migration

Per system, at every start of `mu300-vpn` (any command), idempotent, and only when `/etc/mu300/vpn` can be written
(as root); otherwise the legacy file is read in memory, as today.

* vpn.conf's legacy keys (everything but ENABLE: VLESS_URI ENGINE KILL_SWITCH TAILSCALE REMOTE_DNS BOOTSTRAP_DNS
  LAN_CIDRS IPV6 UPSTREAM_HTTP_PROXY TLS_PIN_SHA256 XRAY HEV SING_BOX) are compared with `legacy.snapshot`. Every key
  whose value differs (or is new, or is gone) is applied: a settings key is set (normalized: KILL_SWITCH/TAILSCALE
  not 0 → 1) or reset to its default; VLESS_URI/ENGINE/UPSTREAM_HTTP_PROXY/TLS_PIN_SHA256 update profile `legacy`
  (TYPE = ENGINE, or sing-box when ENGINE is unset and the kill switch on, else xray - today's default), created on
  the first non-empty VLESS_URI. The snapshot is rewritten. A first migration with no active profile makes `legacy`
  active.
* So: the first run moves everything; a later run does nothing; a later edit of vpn.conf (the old README's way, or an
  older image's `save_pin` after a downgrade and upgrade) wins for the keys it changed, and settings changed with the
  new commands are kept otherwise.
* vpn.conf is left in place with its keys (an older image installed over this one finds a working VLESS VPN) and
  gets one comment line at the top: `# mu300-vpn: migrated to /etc/mu300/vpn (profile legacy); only ENABLE is read
  from this file now`. `on`/`off` change only its ENABLE line (and create the file with just that line when there is
  none). The xray pin fetched for a link is saved in the profile's meta.

## Engines and the new extra

* `tools/fetch-mihomo.sh [OUTDIR]`: MetaCubeX/mihomo v1.19.32 `mihomo-linux-arm64-v1.19.32.gz`, pinned by sha256,
  writes `OUTDIR/mihomo`. `tools/make-extra.sh vpn-mihomo OUT TAG` builds `mu300-extra-vpn-mihomo.tar.gz`
  (`bin/mihomo`, `components`). `tools/make-release.sh` builds it, audits it with the others and lists it in the
  release notes. `mu300-update`: `EXTRAS="vpn lang vpn-mihomo"`, its description; `mu300-extra list` shows it.
* `mu300-vpn engines` prints one TSV line per engine: `xray`, `hev-socks5-tunnel`, `sing-box` (vpn extra),
  `mihomo` (vpn-mihomo extra), `wireguard` (kernel + wg), `openvpn` (package), each `present PATH` or
  `missing HOW`. Exit 0 when the active profile's engines are present (as the toolkit expects today).
* `mu300-vpn engines install ENGINE`: `mu300-extra install vpn|vpn-mihomo`, or `apk add openvpn-openssl` /
  `apk add wireguard-tools` on OpenWrt, `apt-get install -y openvpn` / `wireguard-tools` on Ubuntu, `pacman -S
  --noconfirm openvpn` / `wireguard-tools` on Arch. LuCI's Engines tab (②) calls this.
* Images: `jq` is added to Ubuntu, OpenWrt and Arch (the raw-JSON drivers need it), `wireguard-tools` to Ubuntu
  and Arch (OpenWrt has it). Nothing else grows.

## Other callers

* `wifi-client`'s `vpn_kill_switch` reads KILL_SWITCH from `etc/mu300/vpn/settings` first, then vpn.conf.
* The OpenWrt dashboard (`dashboard-info`) shows the active profile's type as the engine and treats the tunnel as up
  when `/run/mu300-vpn/iface` names an interface that exists (any type).
* systemd unit and init script: descriptions only; both still start on `vpn.conf`, which `on` creates.
* `mu300-extra remove vpn` still refuses while ENABLE=1 (the switch stayed where it was).

## Security

* IDs `[a-z0-9-]{1,32}` everywhere a path is built from one; profile directories 0700, files 0600, `$RUN` 0700.
* No secret reaches stdout, stderr or the log except through `profile export` (and `settings get` of a non-secret
  set); error messages name the profile id, never the link. `profile show` shows the server host and port only.
* OpenVPN: the `.ovpn` is hostile input too and is never given to openvpn. It is parsed with openvpn's lexical
  rules (anything the two could read differently is refused) into an allowlist of directives and key blocks, and
  openvpn reads only the config we write, with our options after it: `--script-security 2` with only our `--up`
  script; a file that wants any other script is refused (the known DNS helpers excepted, which are left out).
* **Raw configs are hostile input.** A raw Xray or sing-box config, and later a mihomo YAML, comes from a user's
  import, from LuCI or from a subscription, and is run as root. Everything that listens or exposes control is ours;
  a config is held to an allowlist of what it may contain, never a denylist, because each engine release adds
  features (some of which listen) faster than a denylist would follow.
* **Parsed once, strictly; the engine reads only what we built.** `json_strict` refuses, before anything else reads
  the file: more than 1 MiB (checked before jq sees it, and before import sniffing reads it); anything that is not exactly one JSON object (comments,
  trailing commas, a second document, a top level that is not an object); `nan`/`infinity`, which jq accepts; the
  same key twice in one object (jq keeps the last silently: its `--stream` event count of the file is then larger
  than that of what it kept); and two keys of one object that are equal under Go's case folding (`"type"` and
  `"Type"`), since the engines' decoder matches field names without regard to case. Keys are compared folded
  everywhere (`mu_fold`: ASCII lowercase, after mapping U+017F and U+212A, which Go folds onto `s` and `k`). Then a
  driver's jq program builds a NEW document from what its allowlist accepts and writes the core-controlled values
  last (inbounds, log, the mark / `default_mark`, `auto_detect_interface`, the bootstrap DNS), so they always win.
  The engine's own check (`xray run -test`, `sing-box check`, `mihomo -t`) runs on that file only, never on the
  profile's, and at `drv_gen` (xray's needs the resolved addresses; `drv_check` is the driver's own parser and the
  rebuild); the profile keeps the file as imported (`profile export` gives it back) and it is rebuilt at every start. No jq regex
  is used (OpenWrt's jq may lack it), and jq's error output is never shown (it quotes the file).
* **Refusals** name a key from our own lists, a type from our list of known refused types, or a top-level/route
  key reduced to printable characters (at most 40 characters, at most 8 reasons), never a value: an unknown type is
  "a type it does not know", an unknown tag "a tag that is none of its outbounds". The checks run in `drv_check`, so
  at import (which rolls back), at `profile edit`, at `check`, and again at every start; import sniffing reads the
  file strictly first, too.
* **The listener guard** (`json_listens_loopback_only`) checks the rebuilt file before the engine runs: no
  `listen_port`/`external_controller` at all; `listen` only 127.0.0.1, wherever they sit and however their keys are
  spelled (folded). It sits behind the rebuild, so a later change to a rebuild that lets a listener through fails
  closed.
* **Refused at any depth** of what is kept (folded): `listen`, `listen_port`, `external_controller`,
  `executable_path`, `data_directory`, `torrc`, `extra_args`, `redirect` (Xray's freedom), `override_address`,
  `override_port` (sing-box's direct and route options), and for Xray `reverse`. Also refused: `masterKeyLog`
  (Xray's `tlsSettings` and `realitySettings`: the engine would write the TLS session keys to a file, as root);
  `private_key_path` and `client_key_path` (sing-box) and `keyFile` and `certificateFile` (Xray's `certificates`),
  because they read a key from a path of the config's choosing and panels put keys inline; and Xray's unix-socket
  transport (`dsSettings`, and a `network` of `domainsocket` or `ds`). Also refused: a key spelled
  differently from a field name the checks read - for Xray, any key of the outbound shape, at any depth (a lone
  `"Protocol"` is the protocol to Xray, but not to the checks; `"Settings"`, `"Mark"`, `"downloadsettings"` are
  refused naming `settings`, `mark`, `downloadSettings`). The case-folding check of the strict parse looks at every
  object, data maps included (HTTP `headers`, Xray's `dns.hosts`), so a config whose header names differ only in
  case is refused for it; a panel that emits such a map has to change it. A header name itself is data: any name,
  with its value checked for type.
  `path` is not refused: a transport's path (ws, httpupgrade, xhttp) is a URL path, a local rule set's path is a
  file sing-box reads, and no type the allowlists accept writes a file (`cache_file` goes with `experimental`).
* **What a raw config may do:** pick its outbounds and the servers they reach, route between them with its own
  rules, and name its DNS servers. That is also its exposure, said plainly: a `direct`/`freedom` outbound (or a DNS
  server whose detour is one) sends traffic out of the tunnel onto the uplink with the engine's mark, past the kill
  switch. Split tunnelling is the config's own choice, and the kill switch does not override it; a user who wants
  everything through the tunnel imports a config that routes everything through the tunnel. A config can also name
  files the engine reads, which parse or fail: `certificate_path`, an ECH `config_path`, a `hosts` server's `path`,
  a local rule set's `path` (sing-box), and `ext:` geo files (Xray). That is a documented exposure, not a refusal:
  none of them is a key, and the engine does not show what it read. Key files are not on this list: they are
  refused (above). It can name no file the engine writes. mihomo reads files a proxy or a provider names the same
  way: a proxy's `ca` (a certificate path), an `ssh` proxy's `private-key` given as a path, a provider's `path`.
  They are confined to mihomo's home directory (`$VPN_DIR/cache/mihomo`) by mihomo's own safe-path check, since
  `SAFE_PATHS` and `SKIP_SAFE_PATH_CHECK` are unset in its environment: a path elsewhere makes that proxy or
  provider fail, and what was read is not shown. A documented exposure, not a refusal: none of them is a key the
  driver removes. Every reference between its parts must name something it has: a route rule's outbound, a
  detour, a dialer proxy to an unknown tag refuses the config.
* **xray raw JSON.**
  * Top level: kept as the config has them: `outbounds`, `routing`, `dns`, `fakedns`, `observatory`,
    `burstObservatory` (the last three open nothing). Ours: `inbounds` (one SOCKS inbound on 127.0.0.1:SOCKS_PORT)
    and `log` (`{"loglevel":"warning","access":"none"}`, no access or error file). Dropped without a word, because
    panels put them in nearly every config: `api` and `metrics` (each opens a listener), `stats`, `policy` (only
    meaningful to them), and `remarks`; a routing rule whose `outboundTag` is the API's tag goes with the API.
    Anything else - `reverse` (its portals take connections in), `transport`, a key a later Xray adds - refuses the
    config.
  * Outbound `protocol` (compared lowercased, as Xray does): `vless`, `vmess`, `trojan`, `shadowsocks`, `socks`,
    `http`, `wireguard`, `freedom`, `blackhole`, `dns`. Anything else refuses the config; `dokodemo-door`,
    `loopback` and `hysteria` (sing-box has it, Xray-core does not) are named. `settings.redirect` (freedom) and
    `reverse` anywhere refuse it.
  * Every outbound is a NEW object, built from a shape per protocol (`XRAY_JSON_SHAPE` in xray.sh is the list; this
    summarises it): the exact key names Xray reads, each with a type, copied when present; a key not in the shape
    refuses the config by its path (`settings.foo`, `settings.vnext[0].users[0].bar`), a value of another type by
    its path and the type it should be ("key settings.vnext[0].port is not a number"), a null value is left out.
    Outbound: `tag`, `protocol`, `settings`, `streamSettings`, `proxySettings` (`tag`, `transportLayer`), `mux`
    (`enabled`, `concurrency`, `xudpConcurrency`, `xudpProxyUDP443`). Settings: `vless`/`vmess` `vnext[]` with
    `address`, `port`, `users[]` (`id`, `encryption`, `flow`, `security`, `level`, and `alterId`/`email`, which
    v2rayN writes into every user); `trojan`, `shadowsocks`, `socks`, `http` `servers[]` with `address`, `port`
    and the protocol's credentials (`password`/`method`/`users[]` of `user`/`pass`, `email`, `level`, ss's `uot`,
    `UoTVersion`); `wireguard` `secretKey`, `address`, `peers[]` (`publicKey`, `preSharedKey`, `endpoint`,
    `allowedIPs`, `keepAlive`), `mtu`, `reserved`, `workers`, `domainStrategy`; `freedom` `domainStrategy` (one of
    Xray's AsIs/UseIP*/ForceIP* values, anything else refused), `userLevel`, `fragment`, `noises`; `blackhole`
    `response.type`; `dns` `network`, `address`, `port`, `nonIPQuery`, `blockTypes`. streamSettings: `network`
    (one Xray knows, else refused), `security` (`none`, `tls`, `reality`), `tlsSettings` (`serverName`,
    `fingerprint`, `alpn`, `minVersion`, `maxVersion`, `cipherSuites`, the pins `pinnedPeerCertificateChainSha256`
    and `pinnedPeerCertSha256`, `verifyPeerCertInNames`/`verifyPeerCertByName`, `curvePreferences`,
    `enableSessionResumption`, `echConfigList`, `serverNameToVerify`, `show`), `realitySettings` (`serverName`,
    `fingerprint`, `publicKey`, `shortId`, `spiderX`, `mldsa65Verify`, `show`), and per transport `wsSettings`,
    `httpupgradeSettings`, `xhttpSettings` (with `xmux` and `extra`, whose `downloadSettings` is a dialer of its
    own: `address`, `port`, `network`, `security`, the TLS/REALITY/xhttp settings), `grpcSettings`, `kcpSettings`,
    `tcpSettings`/`rawSettings` (header type `none` or `http`), `httpSettings`, `quicSettings`. A `headers` map
    keeps the config's own names, values checked for type. Not in the shape, so refused by name: `certificates`,
    `masterKeyLog`, `dsSettings`, `disableSystemRoot`, `rejectUnknownSni`, `echServerKeys`, and anything a panel
    or a later Xray adds.
  * Dropped without a word, in any spelling, wherever they sit: `sockopt` and `sendThrough` (they bind to an
    interface or address, or set a mark; panels put `sockopt` in every outbound) and `allowInsecure` (Xray 26 has
    no such option: the engine verifies, and a server that needs a pin gets one by `pinnedPeerCertSha256`, as the
    link path does). The rebuild writes `streamSettings.sockopt = {"mark":720}`, plus the `dialerProxy` the config
    had, which only chains through another of its outbounds; xhttp's `downloadSettings` gets `sockopt =
    {"mark":720}` too, or that connection would leave unmarked. A `wireguard` outbound gets
    `settings.noKernelTun = true`, so it makes no interface or ip rules of its own. Server names in
    `vnext`/`servers` are resolved by the core. A `wireguard` outbound's `peers[].endpoint` and xhttp's
    `downloadSettings.address` are not: Xray resolves those itself, through its own DNS, which under the kill
    switch fails closed (the lookup is dropped, the outbound does not come up: a start-up failure, no leak). A
    config with either names an address there, or runs with the kill switch off.
  * `proxySettings.tag`, `dialerProxy` and every routing rule's `outboundTag` must name an outbound of the config.
  * `dns.servers`: a string, or an object reduced to `address`, `port`, `domains`, `expectIPs`, `skipFallback`,
    `clientIP`, `queryStrategy`, `tag`, `timeoutMs`.
  * No listener is left (our SOCKS inbound is the only one) and `reverse` is refused, so nothing arrives through
    the tunnel.
* **sing-box raw JSON.**
  * Top level: kept: `dns` (plus our `mu300-bootstrap` server), `outbounds`, `route`, `endpoints`. Ours: `inbounds`
    (our TUN only), `log` (`{"level":"warn","timestamp":false}`, stdout only, so no `output` path). Dropped:
    `experimental` as a whole (`clash_api`, `v2ray_api`, the `debug` listener, a `cache_file` path the engine would
    write). Any other top-level key - `services` (ssm-api, derp, resolved: all listen), `ntp`, `certificate`, a
    later sing-box's additions - refuses the config. Inside `route`: `rules`, `rule_set`, `final` and
    `default_domain_resolver` are the config's; `default_mark` and `auto_detect_interface` ours;
    `default_interface` dropped (the uplink is auto-detected); any other route key refuses the config.
  * Outbound `type`: `direct`, `block`, `socks`, `http`, `shadowsocks`, `vmess`, `vless`, `trojan`, `hysteria`,
    `hysteria2`, `tuic`, `shadowtls`, `anytls`, `ssh`, `selector`, `urltest`, `dns`. Anything else refuses the
    config (`tor`, which runs a program, is named). `direct`'s `override_address`/`override_port` refuse it.
  * Endpoints: only `wireguard`, each with a tag; the rebuild sets `system: false` and drops `listen_port`, `name`
    and `interface_name`, so it neither makes an interface nor listens. A `tailscale` endpoint (a tailnet whose
    members could reach the device) refuses the config. When there are endpoints, the rebuild puts
    `{"inbound":[<endpoint tags>],"action":"reject"}` first in `route.rules`: the peer is the config author's, and
    connections arriving from it must not reach the LAN or the device.
  * Stripped anywhere in an outbound, endpoint or DNS server: `routing_mark`, `bind_interface`,
    `inet4_bind_address`, `inet6_bind_address`, `reuse_addr`, `netns`, `protect_path`, `udp_fragment`,
    `tcp_fast_open`, `tcp_multi_path`. The mark is `route.default_mark` = 720.
  * `dns.servers[].type`: `local`, `udp`, `tcp`, `tls`, `https`, `quic`, `h3`, `hosts`, `fakeip`. `tailscale`,
    `resolved` and `dhcp` are refused and named, and so is a server without a type (the format before 1.12, which
    cannot sit next to our bootstrap server).
  * Every `detour` (outbounds, endpoints, DNS servers), a rule set's `download_detour`, a selector's or urltest's
    `outbounds`/`default`, a route rule's `outbound` and `route.final` must name an outbound or endpoint of the
    config; a DNS server's `domain_resolver` must name one of its DNS servers (or `mu300-bootstrap`).
* **mihomo YAML (Task 7 follows this).** Clash files carry many harmless keys, so unknown top-level keys are removed,
  not refused. Kept: `proxies`, `proxy-groups`, `proxy-providers`, `rules`, `rule-providers`, `sub-rules`, `hosts`,
  `mode`, `log-level`, `ipv6`, `geodata-mode`, `geodata-loader`, `geox-url`, `profile`, `sniffer`. Written by the
  driver, whatever the file had: `tun` (ours), `dns` (ours), `routing-mark: 720`, `allow-lan: false`,
  `bind-address: '127.0.0.1'`. No controller or API of any kind is written: `external-controller`,
  `external-controller-tls`/`-unix`/`-pipe`/`-cors`, `external-ui*`, `external-doh-server`, `tls` and `secret` are
  removed with the rest (there is no setting that asks for one; an unauthenticated control API could reconfigure
  mihomo, and a secret of the file's would be the file author's). `port`, `socks-port`,
  `mixed-port`, `redir-port` and `tproxy-port` are removed (the TUN is the only
  inbound; were one ever written, `allow-lan: false` and the bind address keep it on loopback). Everything else is
  removed with the unlisted keys - among them `listeners`, `tunnels`, `ss-config`, `vmess-config`, `tuic-server`
  (all listen), `ebpf`, `iptables`, `interface-name`, `ntp` (`write-to-system` would set the clock), `secret`,
  `external-*`. `interface-name` and `routing-mark` are stripped from every proxy and group as well (the mark is
  ours, the uplink the kernel's choice). mihomo runs with its home directory `$VPN_DIR/cache/mihomo` (0700; the
  geodata it downloads survives a reboot) and without `SAFE_PATHS` or `SKIP_SAFE_PATH_CHECK` in its environment, so
  a provider's `path` or the geodata it downloads stays inside it.
  * How the reduction reads the file (awk, no YAML parser, so the rules are about lines): a top-level entry is a
    line at column 0 of the form `KEY:` with KEY of `[A-Za-z0-9_-]`, and its block runs to the next such line (blank
    and `#` lines belong to the block they follow); a kept block is copied as it is, a dropped one goes, and the
    check names the dropped keys (they match that pattern, so nothing else of the file is repeated). A dropped block
    that defines an anchor (`&name`) is kept inert under a name of the driver's, `mu300-anchor-N`, which mihomo
    does not read (subscription templates and mihomo's own example put the defaults of providers and groups under
    keys of their own and merge them with `<<: *name`; without the anchor the file would not parse); it goes through
    the same removals and the same text check as a kept block, so nothing it anchors can bring the two keys in. Inside a kept
    block a line `^[ \t-]*interface-name:` or `routing-mark:` (any case) goes with the lines indented deeper than
    the key (a `-` before it stays, so the item keeps its place), and in a flow mapping or sequence the `KEY: value`
    segment goes with one comma (a quoted value, or a value up to the next `,` or closing bracket, brackets nested).
    After that the text `interface-name` or `routing-mark` anywhere outside a `#` line (a quoted key, an anchor,
    a trailing comment, any form awk did not undo) refuses the file naming the key. Refused as well, naming the
    rule: a second YAML document (`---` after the first line, `...`), a `%` directive, a TAB at the start of a line,
    a line over 4096 bytes, a file over 1 MiB, a control byte other than TAB, LF and CR, a line break inside a
    line - a lone CR, NEL (U+0085), LS (U+2028) or PS (U+2029): yaml.v3 ends a line at each of them and awk does
    not, so one kept line to the rules would be several top-level lines to mihomo - an explicit key (`? `, one way
    a key can span lines), a tag (`!`: `!!binary` spells a key without its text), a line ending in a backslash
    and a quoted scalar not closed on its line (the other ways a key can span lines; a `!` or a quote in the
    middle of a value belongs to the value), a hex escape (`\x`, `\u`, `\U`: the one way a key can be spelled
    without its text on one line), a column-0 line that is not `KEY:` (a quoted key, a list item, a merge key, an anchor, a flow collection
    continued at column 0: YAML would read it as top-level content that the line rules cannot see), a kept key
    given twice (mihomo's YAML reader refuses it anyway), a top-level key that is a kept name or one of ours in
    another spelling (case, `_` for `-`: refused by the name it collides with, as the JSON drivers refuse a
    misspelled shape key), a key of the driver's own reserved names (`mu300-anchor-N`), and a file without
    `proxies` and `proxy-providers`. The size cap comes before anything reads the file, as in the JSON drivers. A
    first line `---`, a BOM and CRLF line ends are taken. The written file is checked again before `mihomo -t`
    (no line break but LF, the guard's line model being yaml.v3's; every column-0 key one of the kept list, ours or `mu300-anchor-N`, `tun` and `dns` exactly once, `routing-mark: 720`,
    `allow-lan: false`, `bind-address` once, no `external-*` key), and
    `mihomo -t`'s own output is not shown (its YAML errors quote values); the message gives the command to run.
* The kill switch is unchanged: only marked traffic, NTP and the Wi-Fi client's DHCP leave on an uplink; forwarded
  traffic to an uplink is dropped. Every new driver marks its own sockets, so none of them needs an exception.

## Testing

* `tests/test_vpn.py`: kept green; functions that moved into drivers are called through the loader.
* `tests/test_vpn_drivers.py` (every shell): the driver contract per type with stub engines recording their
  arguments (`xray`, `hev-socks5-tunnel`, `sing-box`, `mihomo`, `wg`, `openvpn`, `ip`, `nft`); the URI parsers
  (vless, vmess base64 JSON, trojan, ss SIP002 and legacy base64, wg .conf); raw JSON/YAML forcing (marks, inbounds,
  TUN, no controller); migration (first, idempotent, an edited vpn.conf, not writable); profile CRUD, ID validation,
  `use` keeping the kill switch (no `delete table` between two runs); settings validation; `engines` output;
  reserved types; nothing secret in stdout/stderr of `list`/`show`/`status`/`run`.
* `tests/test_static.py`: the vpn-mihomo extra in make-release's build and audit lists and mu300-update's EXTRAS;
  jq in the three image package lists.
* On the device (U30 Air, OpenWrt, mainline 7.2): migration of the working legacy VLESS conf without reading it,
  `status` with the exit IP afterwards; WireGuard and OpenVPN against test servers in Docker on the oracle-arm box;
  mihomo with a minimal YAML to a Shadowsocks server there.

## Not in ①

L2TP, PPTP, IKEv2 (`CONFIG_XFRM_INTERFACE=m` is in the mainline config, so ④ can use an xfrm interface), Tailscale
exit nodes as a profile, subscriptions (SOURCE=subscription is reserved), per-app/per-client routing, the LuCI screen.
