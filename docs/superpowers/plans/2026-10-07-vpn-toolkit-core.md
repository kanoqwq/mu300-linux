# VPN toolkit ① Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mu300-vpn` becomes a profile-based VPN with one driver per engine (xray, sing-box, mihomo, wireguard,
openvpn) behind a fixed contract, with the existing kill switch, routing, DNS and Tailscale handling in a shared core.

**Architecture:** `rootfs/overlay/opt/mu300/bin/mu300-vpn` stays the one command and the core. Drivers are sourced
shell files in `rootfs/overlay/opt/mu300/lib/vpn/`. Profiles, settings and the migration from the legacy
`/etc/mu300/vpn.conf` live under `/etc/mu300/vpn/`. Raw JSON is rewritten with `jq`; YAML with awk.

**Tech Stack:** POSIX sh (dash, bash, busybox ash), awk, jq, nft, iproute2; Python unittest for tests.

**Spec:** `docs/superpowers/specs/2026-10-07-vpn-toolkit-core-design.md` (read it: tables of settings, types,
driver contract, forced keys, migration rules are there and are binding).

## Global Constraints

- Every script runs under dash, bash and busybox ash; tests run each shell via `self.each_shell()` (tests/helpers.py).
- Run tests: `cd tests && python3 -m unittest test_vpn test_vpn_drivers -v` (and `python3 -m unittest discover -s .` before a commit that touches shared files). No pytest.
- MARK=0x2d0 (720 decimal in JSON/YAML/openvpn), TABLE=2022, rule prefs 9000/9001/9002/9010, Tailscale 5198/5199/5200: unchanged.
- Tunnel names: xray `xtun`, sing-box `sbtun`, mihomo `mh-mu300`, wireguard `wg-mu300`, openvpn `tun-mu300`.
- Profile id regex `^[a-z0-9-]{1,32}$`; profile dirs 0700, files 0600; `$RUN` 0700.
- `meta` and `settings` are never sourced: read with sed, written single-quoted (`'` → `'\''`).
- `MU300_VPN_DIR` defaults to `$(dirname "$CONF")/vpn`; `MU300_VPN_LIB` defaults to `/opt/mu300/lib/vpn`; tests set `MU300_VPN_LIB` to `TOP/rootfs/overlay/opt/mu300/lib/vpn` (add `LIB = BIN.parent / 'lib' / 'vpn'` to tests/helpers.py).
- Never print a link, UUID, password, key or raw config except in `profile export`. Error messages name the profile id.
- The kill switch is only ever replaced in one `nft -f` transaction or deleted by `off`/ENABLE=0 — never deleted on its own during run.
- Commit messages end with the two lines:
  `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01DqKKtBQZCfxYnHuBCuDqqn`
- Comment style of this repo: full sentences explaining *why*, like the existing mu300-vpn comments. No emojis.

## Review Focus

- A legacy vpn.conf on a device that is not root-writable (status as a normal user): must work in memory, no error spam, no write.
- A profile name with quotes, `$()`, newlines, or non-ASCII: id slug safe, meta round-trips, nothing executes.
- Switching profiles while the kill switch is up: no `nft delete table inet mu300_vpn` between the two runs, routes of the old engine cleaned.
- An `.ovpn` with `up /etc/openvpn/x.sh`, `script-security 3`, `plugin`, inline `<ca>` containing a line starting with `up`: directives removed, inline blocks byte-identical.
- vmess/ss links with URL-safe base64 and missing padding, and an ss link with a plain (SIP002 2022) `method:password`.

---

### Task 1: Store, settings and migration in the core

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-vpn` (top section: config loading, lines ~15-46)
- Modify: `tests/helpers.py` (add `LIB`)
- Create: `tests/test_vpn_drivers.py` (class `Store`)

**Interfaces (produced, used by every later task):**
- `VPN_DIR` (store root), `PROFILES=$VPN_DIR/profiles`, `ACTIVE_FILE=$VPN_DIR/active`, `SETTINGS=$VPN_DIR/settings`
- `valid_id ID` → 0/1
- `kv_get FILE KEY` → prints value (unquoted) or nothing; `kv_set FILE KEY VALUE` (atomic tmp+mv, keeps 0600, creates file); `kv_del FILE KEY`
- `setting_valid KEY VALUE` → 0/1 (spec table); `setting KEY` → effective value with default; `settings_load` → sets shell vars KILL_SWITCH TAILSCALE IPV6 REMOTE_DNS BOOTSTRAP_DNS LAN_CIDRS MIHOMO_CONTROLLER and engine overrides `_xray _hev _sb _mihomo _openvpn`
- `migrate_legacy` (idempotent; only when `$VPN_DIR` parent writable or VPN_DIR writable); `legacy_inmemory` path when not writable
- `ACTIVE` (id or empty), `PDIR=$PROFILES/$ACTIVE`, `PTYPE` (meta TYPE), `profile_new TYPE NAME` → prints new id (creates dir 0700 + meta NAME TYPE SOURCE=manual CREATED)
- `slug NAME` → id candidate
- `ENABLE` read from vpn.conf (`sed -n 's/^ENABLE=//p'`, last wins; default 0); `set_enable 0|1` rewrites only that line (creates file 0600 with just it)

- [ ] **Step 1: Write failing tests** in `tests/test_vpn_drivers.py`, class `Store(ShellTest)` with a `vpn(shell, code, conf)` helper sourcing `BIN/mu300-vpn` with `MU300_LIB=1 MU300_VPN_CONF=tmp/vpn.conf MU300_VPN_RUN=tmp/run MU300_VPN_LIB=LIB MU300_LAN_CONF=tmp/no MU300_BIN=BIN MU300_OPT=tmp/opt MU300_DISK=tmp/disk` and stubs `ip` (`exit 0`), `nft` (`cat >/dev/null`). Tests:
  - `test_first_migration`: conf `ENABLE=1\nENGINE=xray\nKILL_SWITCH=0\nVLESS_URI='vless://u@h:443'\nREMOTE_DNS=9.9.9.9\nTLS_PIN_SHA256=ab\n` → after sourcing: `tmp/vpn/active` = `legacy`; `tmp/vpn/profiles/legacy/uri` = the link; meta TYPE=xray, TLS_PIN_SHA256=ab; settings KILL_SWITCH=0, REMOTE_DNS=9.9.9.9; modes 0700 dirs/0600 files; vpn.conf's first line is the marker comment and the rest unchanged; `echo $KILL_SWITCH $REMOTE_DNS $PTYPE` prints `0 9.9.9.9 xray`.
  - `test_migration_is_idempotent`: source twice; second run leaves every file's mtime/contents unchanged and the marker appears once.
  - `test_an_edited_conf_wins_for_changed_keys`: migrate; then `settings_set` equivalent via `kv_set "$SETTINGS" TAILSCALE 0`; edit vpn.conf REMOTE_DNS=8.8.8.8 → re-source: REMOTE_DNS=8.8.8.8, TAILSCALE stays 0; remove the REMOTE_DNS line → default 1.1.1.1.
  - `test_engine_default_follows_kill_switch`: no ENGINE, KILL_SWITCH unset → TYPE sing-box; KILL_SWITCH=0 → xray.
  - `test_not_writable_reads_in_memory`: make tmp read-only dir for VPN dir parent (chmod 555 on a conf dir), source → `$PTYPE`, `$KILL_SWITCH` from the conf, nothing created, no stderr. (skip when running as root)
  - `test_meta_never_executes`: `profile_new xray 'a$(touch pwned)'"'"'b'` → `kv_get meta NAME` prints the name exactly; `pwned` does not exist; id matches the regex.
  - `test_settings_validation`: `setting_valid` true for (KILL_SWITCH 0/1, IPV6 1, REMOTE_DNS 1.1.1.1 / 2606:4700::1111, LAN_CIDRS 10.0.0.0/8,fd00::/8, MIHOMO_CONTROLLER 127.0.0.1:9090, XRAY /x/xray) and false for (KILL_SWITCH yes, REMOTE_DNS 'a;b', LAN_CIDRS 10.0.0.0, MIHOMO_CONTROLLER 0.0.0.0:9090, XRAY relative, UNKNOWN x). An invalid value in the file reads as the default.
  - `test_valid_id`: valid `a`, `my-vpn-2`, 32 chars; invalid empty, 33 chars, `A`, `a/b`, `..`, `a b`.
  - `test_set_enable`: on a migrated conf flips only ENABLE; with no conf creates it 0600 containing `ENABLE=1`.
- [ ] **Step 2: Run** `cd tests && python3 -m unittest test_vpn_drivers.Store -v` → fails (functions missing).
- [ ] **Step 3: Implement** in mu300-vpn, replacing `[ -r "$CONF" ] && . "$CONF"` and the defaults block. Order at load: (1) `ENABLE` from conf by sed; (2) `migrate_legacy` (sources vpn.conf in a subshell that prints `KEY=<value>` lines for the 13 legacy keys with values escaped via `kv` quoting; compares to `legacy.snapshot` line by line; applies diffs per the spec's Migration section; writes snapshot; inserts marker line once via tmp+mv preserving mode); (3) if `$VPN_DIR` unusable (not writable and no settings) → `legacy_inmemory`: source vpn.conf as today into the same variables, `PTYPE` from ENGINE rule, `PURI=$VLESS_URI`; (4) `settings_load`; (5) `ACTIVE=$(cat active)`, `valid_id` or empty; `PTYPE=$(kv_get "$PDIR/meta" TYPE)`. Keep the existing derived logic after this (own LAN appended to LAN_CIDRS, KILL_SWITCH/TAILSCALE normalisation). Keep `VLESS_URI`, `ENGINE`, `TLS_PIN_SHA256`, `UPSTREAM_HTTP_PROXY` variables populated from the active profile when it is a link profile (`VLESS_URI=$(cat "$PDIR/uri")` only for vless links; `ENGINE=$PTYPE`) so the code below still works unchanged in this task. `save_pin` writes `kv_set "$PDIR/meta" TLS_PIN_SHA256` when a profile is active, else the conf as today.
  Key helpers:

```sh
q() { printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"; }
kv_get() { [ -r "$1" ] || return 0; sed -n "s/^$2=//p" "$1" | tail -n1 | sed "s/^'\(.*\)'\$/\1/; s/'\\\\''/'/g"; }
kv_set() {  # FILE KEY VALUE: one line, replaced in place; a value with a newline is refused
    case $3 in *'
'*) echo "a value cannot contain a newline" >&2; return 2 ;; esac
    _t=$1.new.$$; { [ -r "$1" ] && grep -v "^$2=" "$1"; printf '%s=%s\n' "$2" "$(q "$3")"; } > "$_t" &&
        chmod 600 "$_t" && mv "$_t" "$1"
}
valid_id() { case $1 in ''|*[!a-z0-9-]*) return 1 ;; esac; [ ${#1} -le 32 ]; }
```

- [ ] **Step 4: Run** `python3 -m unittest test_vpn test_vpn_drivers -v` → all pass (test_vpn unchanged and green: its conf-driven tests now migrate into `tmp/vpn`).
- [ ] **Step 5: Commit** `mu300-vpn: profiles and settings under /etc/mu300/vpn, and the legacy vpn.conf migrated into them`

### Task 2: Driver loader, the core run loop, xray and sing-box as drivers, the resolve window

**Files:**
- Create: `rootfs/overlay/opt/mu300/lib/vpn/xray.sh`, `rootfs/overlay/opt/mu300/lib/vpn/sing-box.sh`, `rootfs/overlay/opt/mu300/lib/vpn/uri.sh` (urldecode, param, json_str, json_list, hex2dec, parse_uri moved here; mu300-vpn sources uri.sh at load so tests calling `parse_uri`/`json_str` keep working)
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-vpn`, `tests/test_vpn.py` (only call sites that moved: `xray_routes_up/down` → `routes_up/routes_down`, `gen_singbox`/`outbound_json` after `load_driver sing-box`), `tests/test_vpn_drivers.py` (class `Contract`)

**Interfaces:**
- Consumes Task 1's store variables.
- `load_driver TYPE` → sources `$LIB/$TYPE.sh` after `case $TYPE in l2tp|pptp|ikev2|tailscale-exit) echo "$TYPE: not available in this version" >&2; return 1;; *[!a-z-]*|'') invalid`; resets `DRV_ROUTES=core DRV_FOREGROUND=0 TUN= DRV_PIDS=` and default no-op `drv_status`.
- Driver functions per spec table: `drv_engines drv_engines_ok drv_check drv_gen drv_start drv_alive drv_stop drv_status drv_import`; plus `DRV_EXTRA` (extra name that carries the engines: `vpn`, `vpn-mihomo`, or empty for packages) and `DRV_PKG` (package name per system, for messages).
- `engine_path NAME [EXTRA]` → first executable of `$DISK/extra/$EXTRA/bin/NAME`, `$OPTBIN/NAME`, `command -v NAME`; else the extra path (as `engine()` today, extended with PATH).
- `routes_up` / `routes_down` (old xray_routes_up/down generalized: `$TUN`; every line of `$RUN/server-ip` gets a 9002 rule; DNS nat to `$(cat $RUN/dns 2>/dev/null)` or REMOTE_DNS; when IPV6=1 and `ip -6 addr show dev $TUN scope global` has an address: `ip -6 rule add pref 9000 fwmark $MARK lookup main`, `ip -6 route replace default dev $TUN table $TABLE`, `ip -6 rule add pref 9010 lookup $TABLE`; routes_down removes v6 prefs too).
- `resolve_window` → `killswitch_fetch` + only DNS elements (`dns_allow 30`, split out of `fetch_allow`) when KILL_SWITCH=1; `resolve_close` → `killswitch_on`. `vpn_resolve HOST` → prints IPv4 (resolve4), opening/closing the window around it when KILL_SWITCH=1 and the window is not already open (`RESOLVE_OPEN` flag; drivers call `resolve_window` once, resolve all names, `resolve_close`).
- `ensure_engines` generalized: uses `drv_engines_ok`, `$DRV_EXTRA`; `fetch_engines` installs `$DRV_EXTRA` (message `mu300-extra install $DRV_EXTRA`); for an empty DRV_EXTRA prints `the $PTYPE engine is not installed: run mu300-vpn engines install $PTYPE` and returns 1.
- The `run)` branch becomes: ENABLE check → kill switch → `load_driver "$PTYPE"` (no active profile: "no active profile: mu300-vpn profile use ID", exit 1, kill switch stays) → `ensure_engines` → the existing xray→sing-box substitution (only for xray **link** profiles whose uri is `vless://`; implemented by `load_driver sing-box` and `PURI` reuse) → `drv_gen` → `ensure_firewall_zone` → `[KS=0] killswitch_off` → `routing_cleanup_force` (routes_down) → foreground: `tailscale_up; drv_start` (execs) / else `drv_start` then `routes_up`, write `$RUN/iface`, wait loop `while drv_alive; do sleep 2; ...pin check for xray...; done`, trap → `drv_stop; routes_down; rm -f $RUN/iface`.
- `gen)` → load + `drv_gen`. `check [ID]` → `drv_check` of that profile. `routing_cleanup` pgrep pattern extended with `[m]ihomo|[o]penvpn --config /run/mu300-vpn` and skip when `$RUN/iface` names an existing link.
- xray.sh keeps `gen_xray`, `xray_stream_json`, pin functions, `run_xray`'s reader logic split into `drv_start` (start xray with the fifo reader, wait SOCKS, start hev, wait xtun, `ip link set up`, sets `DRV_PIDS="$xpid $hpid"`), `drv_alive` (both pids alive), `drv_stop` (kill both, wait). sing-box.sh keeps `outbound_json`, `gen_singbox` (`DRV_ROUTES=self DRV_FOREGROUND=1`, `drv_start` = `exec "$BIN" run -c "$RUN/config.json"`).

- [ ] **Step 1: Failing tests** in test_vpn_drivers.py class `Contract`: (a) `test_reserved_types`: `load_driver l2tp` etc. → rc≠0, stderr `not available in this version`; `load_driver '../x'` refused. (b) `test_every_driver_defines_the_contract`: for each file in LIB matching `*.sh` except uri.sh/common: source it and `command -v drv_engines drv_engines_ok drv_check drv_gen drv_start drv_alive drv_stop drv_import` all found (later tasks' drivers are covered automatically). (c) `test_core_routes`: with the Tailscale ip stub from test_vpn.py (copy the stub body), `TUN=wg-mu300; printf '203.0.113.9\n198.51.100.1\n' > $RUN/server-ip; routes_up` → rules include `pref 9002 to 203.0.113.9 lookup main`, `pref 9002 to 198.51.100.1 lookup main`, `pref 9010 lookup 2022`; routes_down → none of 9000-9010 left. (d) `test_resolve_window`: KILL_SWITCH=1 with the KillSwitch nft stub (copy from test_vpn.py) and getent stub returning `203.0.113.5` for `srv.example`: `resolve_window; vpn_resolve srv.example; resolve_close` prints `203.0.113.5`; rulesets in order: window (has `dns4`, no element in fetch4), then full; no `delete table`.
- [ ] **Step 2: Run** → fail.
- [ ] **Step 3: Implement** the files and the core changes described above. Keep every comment block of the moved code with it.
- [ ] **Step 4: Run** `python3 -m unittest test_vpn test_vpn_drivers test_wifi_client test_extra -v` → pass. Fix test_vpn.py call sites only where functions moved/renamed; never weaken an assertion.
- [ ] **Step 5: Commit** `mu300-vpn: engine drivers - xray and sing-box move into /opt/mu300/lib/vpn, the core keeps the kill switch and routing`

### Task 3: Links for vmess, trojan, shadowsocks; the profile and service commands

**Files:**
- Modify: `rootfs/overlay/opt/mu300/lib/vpn/uri.sh` (parsers), `rootfs/overlay/opt/mu300/lib/vpn/xray.sh` (outbounds), `rootfs/overlay/opt/mu300/bin/mu300-vpn` (CLI), `tests/test_vpn_drivers.py` (classes `Uris`, `Cli`)

**Interfaces:**
- `parse_link URI` sets `PROTO` (vless|vmess|trojan|ss) and the fields `UUID HOST PORT TYPE SECURITY SNI FP ALPN FLOW INSECURE PBK SID WSPATH WSHOST SVC XMODE SPX` plus `PASSWORD METHOD AID` (vmess alterId, ss method); `parse_uri` stays the vless-only legacy entry (tests). vmess: `vmess://BASE64(JSON)` with keys v ps add port id aid scy net type host path tls sni alpn fp — decode with `b64d` (`tr '_-' '/+'`, pad with `=` to a multiple of 4, `jq -Rr '@base64d'`), fields read with `jq -r '.add // empty'`. trojan: `trojan://PASS@HOST:PORT?security=tls&sni=&type=&path=&host=&serviceName=` (default security tls). ss: SIP002 `ss://USERINFO@HOST:PORT[/][?plugin=..]#tag` where USERINFO is base64(method:password) or url-encoded `method:password`; legacy `ss://BASE64(method:password@host:port)#tag`. A `plugin` param → refused ("ss plugins are not supported").
- `b64d` in uri.sh.
- xray outbound per PROTO: vless (existing), vmess (`"protocol":"vmess","settings":{"vnext":[{"address","port","users":[{"id","alterId","security":scy or auto}]}]}`), trojan (`"protocol":"trojan","settings":{"servers":[{"address","port","password"}]}`), shadowsocks (`"protocol":"shadowsocks","settings":{"servers":[{"address","port","method","password"}]}`, no streamSettings security), all with `xray_stream_json` (mark).
- `sniff_type SRC` → prints type or fails; `profile_import SRC [NAME] [TYPE]`; the subcommands of the spec's command list; `on`/`off`/`restart` use `svc ACTION` which runs `${MU300_VPN_SVC:-}` when set (tests: a stub recording calls), else `systemctl ACTION mu300-vpn` when systemctl exists, else `/etc/init.d/mu300-vpn ACTION` when it exists; failures ignored with a message.
- `off`: `set_enable 0`, `svc stop`, `svc disable`, killswitch_off, routing_cleanup, prints `kill switch removed` (kept text). `on`: needs an active profile and `drv_engines_ok` (else message with `mu300-vpn engines install`), `set_enable 1`, `svc enable`, `svc restart`.
- `profile use ID`: valid + exists; writes `active` atomically; when ENABLE=1 → `svc restart` (the service's run replaces the kill switch in one transaction, so it stays up throughout).
- `profile remove ID`: refuses the active profile while ENABLE=1; `rm -rf` only after `valid_id`.
- `profile show ID` prints `id name type source created server` (server = HOST:PORT from the link/endpoint/remote, or `-`); `list` prints TSV `*`/` `, id, type, name.
- `status`: `profile: ID (TYPE)  enabled: E  kill switch: K (active|inactive)` then engine line, tunnel line from `$RUN/iface`, exit IP as today. Keep the substring `kill switch: 1 (active)` (test_vpn depends on it) and the not-installed message.
- `settings`: no args/`get` → all keys `KEY=value` (effective); `get KEY`; `set KEY VALUE` → `setting_valid` or exit 2; writes settings.

- [ ] **Step 1: Failing tests.** `Uris`: table-driven per shell —
  - vmess: build `{"v":"2","ps":"n","add":"vm.example","port":"8443","id":"11111111-2222-3333-4444-555555555555","aid":"0","scy":"auto","net":"ws","type":"none","host":"h.example","path":"/p","tls":"tls","sni":"s.example"}` base64 standard and URL-safe without padding → PROTO=vmess HOST=vm.example PORT=8443 TYPE=ws WSPATH=/p WSHOST=h.example SECURITY=tls SNI=s.example.
  - trojan `trojan://p%40ss@tr.example:443?type=grpc&serviceName=g&sni=x.example#t` → PASSWORD=p@ss HOST=tr.example TYPE=grpc SVC=g SECURITY=tls SNI=x.example.
  - ss SIP002 base64 `ss://` + b64url('aes-256-gcm:pw') + `@ss.example:8388#n` → METHOD=aes-256-gcm PASSWORD=pw HOST=ss.example PORT=8388; plain `ss://2022-blake3-aes-128-gcm:a%2Bb%3D@[2001:db8::2]:443` → METHOD=2022-blake3-aes-128-gcm PASSWORD=a+b= HOST=2001:db8::2; legacy `ss://` + b64('chacha20-ietf-poly1305:pw@1.2.3.4:8000') → all fields; `?plugin=obfs` → rc≠0.
  - For each: `gen_xray` with an xray stub (records `run -test -c`), resolve stub → the outbound's protocol, address = resolved IP, `streamSettings.sockopt.mark == 720`, json valid.
  `Cli` (script mode, `MU300_VPN_SVC` stub logging `svc $*`):
  - `profile import 'vless://…' Home` → prints id `home`; `profile list` shows `home\txray\tHome`; second import with the same name → `home-2`; `profile add wireguard X tmp/wg.conf` (file with `[Interface]`) → type wireguard (driver from Task 5 may not exist yet: add only checks `$LIB/TYPE.sh` exists for types present; this test lands in Task 5 instead — here use xray/sing-box).
  - `profile use home` with ENABLE=1 → `svc restart` logged; with ENABLE=0 → nothing logged.
  - `profile show home` stdout+stderr contain neither the UUID nor the full link; `profile export home` prints the link exactly.
  - `profile remove home` while active and ENABLE=1 → rc≠0; after `off` → removed.
  - `profile use ../x`, `profile show A` → rc 2, message `invalid profile id`.
  - `settings set KILL_SWITCH yes` → rc 2; `settings set REMOTE_DNS 9.9.9.9` → `settings get REMOTE_DNS` prints it.
  - `on` without profile → rc≠0 `no active profile`; `on` with profile and engine stubs → `set ENABLE=1` in conf and `svc enable`, `svc restart`; `off` → ENABLE=0, `svc stop`, `svc disable`, `kill switch removed`.
  - Switch keeps the kill switch: KillSwitch nft stub; profile A (sing-box vless link), run; `profile use B` (another vless link, xray type, KILL_SWITCH=1 → substitutes sing-box); run → events contain two `nft -f` full rulesets and no `nft delete table inet mu300_vpn`.
  - Secrets: for `run` with stub engines, `status`, `list`, `show`: the UUID never appears in stdout or stderr.
- [ ] **Step 2: Run** → fail. **Step 3: Implement.** **Step 4: Run** `python3 -m unittest test_vpn test_vpn_drivers -v` → pass.
- [ ] **Step 5: Commit** `mu300-vpn: VMess, Trojan and Shadowsocks links, and the profile, settings and on/off commands`

### Task 4: Raw Xray and sing-box JSON; jq in the images

**Files:**
- Modify: `rootfs/overlay/opt/mu300/lib/vpn/xray.sh`, `rootfs/overlay/opt/mu300/lib/vpn/sing-box.sh`, `rootfs/Dockerfile` (add `jq wireguard-tools` to the apt list), `openwrt/build-rootfs.sh` (add `jq` to the first `apk add` line, with a comment), `arch/build-rootfs.sh` (add `jq wireguard-tools` to the pacman list), `.github/workflows/tests.yml` (add `jq` to the apt-get install line), `tests/test_vpn_drivers.py` (class `RawJson`), `tests/test_static.py` (jq in the three image lists)

**Interfaces:**
- xray `drv_import`: a file whose content starts (after whitespace) with `{` → `jq -e '.outbounds|type=="array"'` → `config.json`; a link → `uri`.
- xray `drv_gen` for config.json: hosts = `jq -r '[.outbounds[]?.settings | (.vnext[]?.address, .servers[]?.address)] | .[] | select(test("^[0-9.]+$|:")|not)'`; each resolved with `vpn_resolve` inside one `resolve_window`; then

```sh
jq --argjson port "$SOCKS_PORT" --argjson mark 720 --argjson map "$MAP" '
  .log = {"loglevel":"warning","access":"none"}
  | .inbounds = [{"tag":"socks-in","listen":"127.0.0.1","port":$port,"protocol":"socks","settings":{"auth":"noauth","udp":true,"ip":"127.0.0.1"}}]
  | .outbounds |= map(
      (.streamSettings.sockopt.mark = $mark)
      | (if (.streamSettings.security // "") as $s | ($s == "tls" or $s == "reality") then . else . end)
      | (.settings.vnext |= (if . then map(. as $v | if $map[$v.address] then .address = $map[$v.address] | ._name = $v.address else . end) else . end))
      | (.settings.servers |= (if . then map(. as $v | if $map[$v.address] then .address = $map[$v.address] | ._name = $v.address else . end) else . end))
      | (([.settings.vnext[]?._name, .settings.servers[]?._name] | map(select(.)) | first) as $n
         | if $n and ((.streamSettings.security // "") == "tls") and ((.streamSettings.tlsSettings.serverName // "") == "") then .streamSettings.tlsSettings.serverName = $n
           elif $n and ((.streamSettings.security // "") == "reality") and ((.streamSettings.realitySettings.serverName // "") == "") then .streamSettings.realitySettings.serverName = $n
           else . end)
      | del(.settings.vnext[]?._name, .settings.servers[]?._name))' "$PDIR/config.json" > "$RUN/xray.json"
```
  (MAP is a JSON object name→IP built in sh); `$RUN/server-ip` = the resolved IPs plus literal IP addresses; then `xray run -test`. hev.yml as for links. No pin management for raw JSON.
- sing-box `drv_import`: JSON with `.outbounds[0].type` → config.json. `drv_gen` for config.json:

```sh
jq --arg tun "$TUN" --argjson addrs "$ADDRS" --argjson excl "$(json_list "$LAN_CIDRS")" --argjson mark 720 --arg boot "$BOOTSTRAP_DNS" '
  .inbounds = [{"type":"tun","tag":"tun-in","stack":"gvisor","interface_name":$tun,"address":$addrs,"mtu":1400,"auto_route":true,"strict_route":true,"route_exclude_address":$excl}]
  | .route.default_mark = $mark | .route.auto_detect_interface = true
  | .dns.servers = ((.dns.servers // []) + [{"type":"udp","tag":"mu300-bootstrap","server":$boot}])
  | .route.default_domain_resolver = (.route.default_domain_resolver // "mu300-bootstrap")' "$PDIR/config.json" > "$RUN/config.json"
```
  then `sing-box check -c`. `DRV_ROUTES=self` for both forms; the core applies the DNS nat only when `DRV_ROUTES=core`.
- Raw xray under the kill switch is allowed (names resolved through the window); the vless→sing-box substitution never applies to raw JSON.

- [ ] **Step 1: Failing tests** `RawJson`: xray config with two outbounds (vless `vnext` address `srv.example`, tls with no serverName; freedom `direct`) and an inbound `{"port":1080,"listen":"0.0.0.0"}` → generated: only our socks inbound on 127.0.0.1; every outbound has sockopt.mark 720; vnext address is the stubbed IP; tlsSettings.serverName = `srv.example`; server-ip has the IP; `xray run -test -c` called. sing-box config with a mixed inbound on 0.0.0.0 and `route.final` → only our tun inbound named sbtun with route_exclude_address = LAN list; default_mark 720; dns.servers has mu300-bootstrap; an existing `default_domain_resolver: "x"` is kept. Import sniffing: xray JSON → type xray, sing-box JSON → type sing-box; invalid JSON → rc≠0 and the profile dir not created.
- [ ] **Steps 2-4** as above; also run `python3 -m unittest test_static -v`.
- [ ] **Step 5: Commit** `mu300-vpn: raw Xray and sing-box configs, rewritten so routing and marks stay ours; jq in the images`

### Task 5: WireGuard driver

**Files:**
- Create: `rootfs/overlay/opt/mu300/lib/vpn/wireguard.sh`; Modify: `tests/test_vpn_drivers.py` (class `WireGuard`)

**Interfaces:** `TUN=wg-mu300`, `DRV_EXTRA=` (package `wireguard-tools`). `drv_engines_ok`: `wg` found and (`ip link add wg-mu300-t type wireguard` then del succeeds, or `wg-mu300` exists, or `[ -d /sys/module/wireguard ]`). `drv_import FILE`: needs `[Interface]` with PrivateKey and ≥1 `[Peer]` with PublicKey+Endpoint → `wg.conf` 0600. `drv_check`: same validation + the AllowedIPs warning. `drv_gen`: awk strips Address/DNS/MTU/Table/PreUp/PostUp/PreDown/PostDown/SaveConfig (case-insensitive keys, spaces around `=` allowed) into `$RUN/wg.conf`; Endpoint `host:port` / `[v6]:port` resolved (literal kept); `$RUN/wg.addr` (Address list), `$RUN/wg.mtu`, `$RUN/dns` (first DNS that is an IP), `$RUN/server-ip`. `drv_start`: `ip link del wg-mu300 2>/dev/null; ip link add wg-mu300 type wireguard; wg setconf wg-mu300 $RUN/wg.conf; wg set wg-mu300 fwmark 0x2d0; ip addr add A dev wg-mu300` for each; `ip link set wg-mu300 mtu M up`; echo `tunnel up on wg-mu300`. `drv_alive`: `ip link show wg-mu300`. `drv_stop`: `ip link del wg-mu300`. `drv_status`: `wg show wg-mu300 latest-handshakes` → `handshake: Ns ago` (no keys printed), `wg show wg-mu300 transfer` → bytes.

- [ ] **Step 1: Failing tests:** sample wg.conf with `Address = 10.7.0.2/32, fd00:7::2/128`, `DNS = 10.7.0.1, example.org`, `MTU = 1380`, `PostUp = iptables ...`, `Endpoint = wg.example:51820`, `AllowedIPs = 0.0.0.0/0, ::/0`, PresharedKey: generated wg.conf has no Address/DNS/MTU/PostUp line, Endpoint `203.0.113.7:51820`; the `ip`/`wg` stub log has `link add wg-mu300 type wireguard`, `setconf wg-mu300`, `set wg-mu300 fwmark 0x2d0`, `addr add 10.7.0.2/32 dev wg-mu300`, `mtu 1380`; `$RUN/dns` = 10.7.0.1; server-ip = 203.0.113.7. AllowedIPs `10.0.0.0/8` → check warns `0.0.0.0/0`. Import without PrivateKey → refused. `profile import tmp/x.conf` → type wireguard. Status output contains no `PrivateKey`/key material from the stub.
- [ ] **Steps 2-4.** **Step 5: Commit** `mu300-vpn: WireGuard profiles on the kernel's WireGuard, marked so they pass the kill switch`

### Task 6: OpenVPN driver

**Files:**
- Create: `rootfs/overlay/opt/mu300/lib/vpn/openvpn.sh`, `rootfs/overlay/opt/mu300/lib/vpn/openvpn-up` (0755, `#!/bin/sh`); Modify: `tests/test_vpn_drivers.py` (class `OpenVpn`), `tests/test_static.py` if it checks file modes under lib.

**Interfaces:** `TUN=tun-mu300`, `DRV_EXTRA=`, package `openvpn-openssl` (OpenWrt) / `openvpn`. `drv_import FILE` → `client.ovpn`; `profile set ID OVPN_USER u` / `OVPN_PASS p` write `auth.txt` (two lines, 0600) instead of meta. `drv_gen`: awk filter per the spec's list (directive = first word of a non-comment line, outside `<tag>…</tag>` blocks), `remote HOST [PORT] [PROTO]` hosts resolved (all remotes kept, each resolved), writes `$RUN/openvpn.conf`, `$RUN/server-ip`. `drv_start`: `rm -f $RUN/ovpn-up $RUN/dns; openvpn --config $RUN/openvpn.conf --dev tun-mu300 --dev-type tun --route-noexec --pull-filter ignore redirect-gateway --mark 720 --script-security 2 --up $LIB/openvpn-up --setenv MU300_VPN_RUN $RUN --auth-nocache --verb 3 [--auth-user-pass $PDIR/auth.txt] &`, wait ≤60 s for `$RUN/ovpn-up` while the pid lives. `openvpn-up`: `$1` is the dev; loops `foreign_option_1..N`, for `dhcp-option DNS x` (IPv4 or IPv6 literal) writes the first to `$RUN/dns`; writes `$1` to `$RUN/ovpn-up`; exits 0; never prints env. `drv_alive`: pid alive and link exists. `drv_stop`: TERM, wait 5 s, KILL.

- [ ] **Step 1: Failing tests:** an .ovpn with `client`, `dev tun0`, `remote vpn.example 1194 udp`, `remote 198.51.100.2 443 tcp`, `up /etc/openvpn/update-resolv-conf`, `script-security 3`, `plugin /x.so`, `redirect-gateway def1`, `route 10.0.0.0 255.0.0.0`, `<ca>\nup not-a-directive\n-----BEGIN…\n</ca>`, `auth-user-pass` → generated config has none of the removed directives, has `remote 203.0.113.8 1194 udp` and `remote 198.51.100.2 443 tcp`, the `<ca>` block byte-identical (including the `up not-a-directive` line); openvpn stub (records args; runs the `--up` script with `foreign_option_1='dhcp-option DNS 10.8.0.1'` and dev arg, then sleeps) → args contain `--mark 720`, `--route-noexec`, `--script-security 2`, `--up <LIB>/openvpn-up`, `--dev tun-mu300`; `$RUN/dns` = 10.8.0.1; with auth.txt → `--auth-user-pass <PDIR>/auth.txt`. `openvpn-up` alone with `foreign_option_1='dhcp-option DNS 1.2.3.4;rm -rf x'` → no dns written.
- [ ] **Steps 2-4.** **Step 5: Commit** `mu300-vpn: OpenVPN profiles, with our routing and our own up script only`

### Task 7: mihomo driver and the vpn-mihomo extra

**Files:**
- Create: `rootfs/overlay/opt/mu300/lib/vpn/mihomo.sh`, `tools/fetch-mihomo.sh`
- Modify: `tools/make-extra.sh` (case `vpn-mihomo`), `tools/make-release.sh` (build `mu300-extra-vpn-mihomo.tar.gz` after the vpn extra; add `vpn-mihomo` wherever the audit loops over extras; release-notes table row), `rootfs/overlay/opt/mu300/bin/mu300-update` (`EXTRAS="vpn lang vpn-mihomo"`, `extra_desc vpn-mihomo` = "mihomo (Clash.Meta), the engine of mu300-vpn's mihomo profiles (about 15 MB to download, 40 MB installed)"), `rootfs/overlay/opt/mu300/bin/mu300-extra` header comment, `tests/test_static.py`, `tests/test_extra.py` if it pins EXTRAS, `tests/test_vpn_drivers.py` (class `Mihomo`)

**Interfaces:** `tools/fetch-mihomo.sh [OUTDIR]`: `VER=1.19.32`, `SHA256=9dd862e28b46ff7d775f169cceebc28deccaa0a9e804237d421cd2571e0caba0` of `mihomo-linux-arm64-v$VER.gz` from `https://github.com/MetaCubeX/mihomo/releases/download/v$VER/`, gunzip, `install -m 755 → OUTDIR/mihomo`, same style as fetch-sing-box.sh. make-extra `vpn-mihomo`: `bin/mihomo`, components `mihomo $VER`. Driver: `DRV_EXTRA=vpn-mihomo`, `TUN=mh-mu300`, engine `engine_path mihomo vpn-mihomo` or settings MIHOMO. `drv_import`: YAML (no leading `{`) with a top-level `proxies:` or `proxy-providers:` → `config.yaml`. `drv_gen`: awk drops the top-level keys of the spec's list (a line `^KEY:` and following lines that are indented, blank, or comments until the next line starting at column 0 that is not a comment) and appends:

```yaml
routing-mark: 720
allow-lan: false
tun:
  enable: true
  device: mh-mu300
  stack: gvisor
  auto-route: false
  auto-redirect: false
  auto-detect-interface: false
  dns-hijack: []
  mtu: 1400
  inet4-address: [198.18.8.1/30]
dns:
  enable: true
  ipv6: false
  respect-rules: true
  default-nameserver: [BOOTSTRAP_DNS]
  proxy-server-nameserver: [BOOTSTRAP_DNS]
  nameserver: [REMOTE_DNS]
```
  (stack from meta MIHOMO_STACK ∈ gvisor|system|mixed; `external-controller: VALUE` appended only when MIHOMO_CONTROLLER is set). Home `$VPN_DIR/cache/mihomo` (0700). Check: `mihomo -t -d HOME -f $RUN/mihomo.yaml`. Start: `mihomo -d HOME -f $RUN/mihomo.yaml &` (stdout/stderr through, as the service log), wait ≤20 s for `mh-mu300`, `ip link set mh-mu300 up`. server-ip: empty (mihomo marks its own). alive: pid + link. stop: TERM/KILL.

- [ ] **Step 1: Failing tests:** YAML with `port: 7890`, `allow-lan: true`, `external-controller: 0.0.0.0:9090`, `secret: s`, `tun:\n  enable: true\n  auto-route: true\n`, `dns:\n  enhanced-mode: fake-ip\n`, `proxies:\n  - {name: a, type: ss, server: ss.example, port: 8388, cipher: aes-256-gcm, password: pw}\n`, `rules:\n  - MATCH,a\n` → generated YAML: exactly one top-level `tun:`, `dns:`; no `external-controller`, `secret`; `allow-lan: false`; `routing-mark: 720`; `proxies` and `rules` blocks unchanged; `port: 7890` kept; with `settings set MIHOMO_CONTROLLER 127.0.0.1:9090` → `external-controller: 127.0.0.1:9090`. mihomo stub records `-t -d … -f …`. test_static: `vpn-mihomo` in mu300-update EXTRAS, make-release builds and audits it, fetch-mihomo.sh pins a 64-hex SHA256.
- [ ] **Steps 2-4.** **Step 5: Commit** `mu300-vpn: mihomo profiles, and the vpn-mihomo extra that carries the engine`

### Task 8: `engines` and `engines install`

**Files:** Modify `rootfs/overlay/opt/mu300/bin/mu300-vpn`, `tests/test_vpn_drivers.py` (class `EnginesCmd`), `tests/test_vpn.py` only if the old `engines` output format is asserted (it is not; the toolkit only checks the exit status).

**Interfaces:** `engines` prints TSV lines in this order: `xray`, `hev-socks5-tunnel`, `sing-box`, `mihomo`, `wireguard`, `openvpn` — `NAME\tpresent\tPATH` or `NAME\tmissing\tHOW` where HOW is `mu300-extra install vpn`, `mu300-extra install vpn-mihomo`, or `mu300-vpn engines install wireguard|openvpn` followed by ` (` + the package command + `)`. Exit 0 iff the active profile's driver `drv_engines_ok` (no active profile: the vpn extra present). `engines install E`: xray|hev-socks5-tunnel|sing-box → `$EXTRA_CMD install vpn`; mihomo → `$EXTRA_CMD install vpn-mihomo`; openvpn/wireguard → `pkg_install PKG` which picks `apk add` (OpenWrt: `$SYSROOT/etc/openwrt_release`), `apt-get install -y` (with `apt-get update` first) when apt-get exists, `pacman -S --noconfirm --needed` when pacman exists; package names: openvpn → `openvpn-openssl` on OpenWrt else `openvpn`; wireguard → `wireguard-tools`. Unknown engine → rc 2. `MU300_SYSROOT` respected for the OpenWrt test.

- [ ] **Step 1: Failing tests:** stubs for extras present/missing → TSV lines exact; with `etc/openwrt_release` in sysroot and an `apk` stub, `engines install openvpn` calls `apk add openvpn-openssl`; without it and with an `apt-get` stub → `apt-get install -y openvpn`; `engines install mihomo` → extra stub called with `install vpn-mihomo`; `engines install foo` → rc 2.
- [ ] **Steps 2-4.** **Step 5: Commit** `mu300-vpn engines: every engine, where it is, and how to get the missing ones`

### Task 9: Callers, toolkit menu, docs

**Files:** Modify `rootfs/overlay/opt/mu300/bin/wifi-client` (`vpn_kill_switch`), `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/dashboard-info` (VPN block), `rootfs/overlay/opt/mu300/bin/mu300-toolkit` (`menu_vpn`), `rootfs/overlay/etc/systemd/system/mu300-vpn.service` and `openwrt/overlay/etc/init.d/mu300-vpn` (descriptions), `rootfs/overlay/etc/mu300/vpn.conf.example` (header: the legacy way still works and is migrated; point to `mu300-vpn profile import`), `README.md` (VPN section rewritten around profiles: import, use, on, settings, engines, each type's source, kill switch per type, Tailscale paragraph kept), `docs/FINDINGS.md` (new `### 26f. One VPN command for every protocol: profiles and engine drivers` after 26e: the decisions — ENABLE stays in vpn.conf and why, marks per engine, resolve window, raw-config rewriting, sing-box keeps auto_route, migration by snapshot diff, jq), `tests/README.md` (test_vpn_drivers row), `tests/test_wifi_client.py` (a case: settings KILL_SWITCH=0 with vpn.conf KILL_SWITCH=1 → not fatal).

**Interfaces:** `vpn_kill_switch()`:

```sh
vpn_kill_switch() {
    f=$R/etc/mu300/vpn.conf; s=$R/etc/mu300/vpn/settings
    [ -r "$f" ] && grep -q '^ENABLE=1' "$f" || return 1
    if [ -r "$s" ] && grep -q '^KILL_SWITCH=' "$s"; then ! grep -q "^KILL_SWITCH='\{0,1\}0'\{0,1\}\$" "$s"
    else ! grep -q '^KILL_SWITCH=0' "$f"; fi
}
```
dashboard-info: `VPN_ENGINE` = TYPE from `/etc/mu300/vpn/profiles/$(cat /etc/mu300/vpn/active)/meta` (sed, unquote) falling back to vpn.conf ENGINE; `VPN_UP=1` when `/run/mu300-vpn/iface` names a link that exists, or xtun/sbtun exist. Toolkit `menu_vpn` items: `Turn on`, `Turn off`, `Profiles (list / use)`, `Import a profile (link or file path)`, `Remove a profile`, `Kill switch on/off`, `Engines (install missing)`, `Restart`; each calls the mu300-vpn subcommands; title `VPN`.

- [ ] **Step 1: Failing test** in test_wifi_client.py (settings precedence). **Step 2: run → fail. Step 3: implement all files. Step 4:** full suite `cd tests && python3 -m unittest discover -s . 2>&1 | tail -5` → OK; `sh -n` on every changed shell file.
- [ ] **Step 5: Commit** `mu300-vpn: wifi-client, the dashboard and the toolkit on profiles; README and FINDINGS`
