# The adb interface for tools

What a program (the web installer over WebUSB, a GUI, a script) can rely on when it talks to a device running
MU300 Linux over adb. The rest of adb to Linux - the daemon, the gadget, who is let in and why - is in
[FINDINGS 43 and 70](FINDINGS.md).

## Is this our Linux, before any command runs

The USB serial number, which is also the serial in `adb devices` and the one WebUSB reports:

| The device runs | Serial |
|---|---|
| MU300 Linux (Ubuntu or OpenWrt) | `MU300LINUX-<id>`, e.g. `MU300LINUX-324950664950-203d8329` (boot/init; `MU300LINUX` alone when the board gave no id) |
| Android | the bare number, e.g. `324950664950` |

The adb interface is class `0xff`, subclass `0x42`, protocol `0x01`, as on Android; VID:PID `0525:a4a1`.

A device in Linux is `device` in `adb devices` when it lets this computer in, and `unauthorized` when its adb takes
authorised keys only and this computer's key is not one of them (`mu300-usb adb auth keys`). It is not listed at all
when adb is off there (`mu300-usb adb off`), in the U30 Air's USB host role, or on a kernel without FunctionFS.

**The shell is root, in Linux too.** `adb shell su -c 'id -u'` prints `0` on Ubuntu, so "su works" does not mean
"rooted Android". Tell the two apart by the serial, and by the marker below before writing anything.

## What it is and what state it is in

```sh
adb shell /opt/mu300/bin/mu300-device --json
```

One line of JSON on stdout, exit status 0, nothing on stderr:

```json
{"mu300":1,"device":"f50","system":"openwrt-luci","version_id":"25.12.6","release":"v2026.10.23","kernel":"7.2.10","root":true,"adb":"open"}
```

| Field | Type | Values |
|---|---|---|
| `mu300` | number | The version of this format: `1`. Fields may be added without changing it; it changes when a field goes away or changes meaning |
| `device` | string | `f50`, `u30air` or `v50` (the ZTE V50, experimental: FINDINGS 95) |
| `system` | string | `ubuntu`, `openwrt` or `openwrt-luci`: the system that is running (the name of its directory on the Linux partition; a system of the user's own has its own name) |
| `version_id` | string | `VERSION_ID` of the system's `/etc/os-release`: `24.04` or `26.04` for Ubuntu, OpenWrt's version for OpenWrt; empty when there is none |
| `release` | string | The mu300-linux release the running system's image came from (`/etc/mu300/image-version`), e.g. `v2026.10.23`; `unknown` when the file is missing, `dev` for an image built without a version |
| `kernel` | string | `uname -r`: `5.4.…`, `6.18.…`, `7.2.…` or `7.3.…` |
| `root` | boolean | Whether the caller is root. Always `true` over adb; `false` when a program runs it over SSH as `ubuntu` |
| `adb` | string | `open` (every computer is let in), `keys` (only authorised keys), `off`, or `unavailable` (no adbd or no FunctionFS in this system) |

Strings hold only `A-Z a-z 0-9 . _ + -` (anything else is dropped, at most 64 characters), so the line needs no
escaping and no field can be forged by a file's contents. The path is given in full on purpose: `mu300-device` is on
the `PATH` of an adb shell too, but a tool should not depend on it.

On a system from before this command had `--json` (v2026.10.23 and older) the same call prints `f50` or `u30air` and
nothing else: treat output that does not parse as JSON as "an older release", and fall back to the marker.

**The marker**, for a yes/no answer on any release with adb: `adb shell 'test -x /opt/mu300/bin/mu300-next-boot && echo mu300'`
prints `mu300` on MU300 Linux and nothing on Android (there is no `/opt` there).

## More state, as text

These are for people; their wording can change. Use them when the JSON has no field for what is needed, and say so in
an issue, so that the field can be added.

| Command | Says |
|---|---|
| `mu300-update version` | the installed image, the systems on disk with their releases, the kernel and the boot image's release |
| `mu300-os` | the systems on disk and which one boots next |
| `mu300-usb adb status` | adb on or off, whom it lets in and why, whether adbd can run, the number of authorised keys |

## Sending the device back to Android

```sh
adb -s MU300LINUX-<id> shell '/opt/mu300/bin/mu300-next-boot android && sync && reboot'
```

The device leaves `adb devices` within seconds and comes back as Android with its own serial. This is what
`install.sh`, `install.ps1` and the uninstallers do when they find the device in Linux.

## Settings and actions: mu300-api

From the release that brings it (FINDINGS 110), every setting the web installer's device manager reads or changes
goes through one command:

```sh
printf '%s' '{"ssid":"Home","password":"a secret 123"}' | adb shell /opt/mu300/bin/mu300-api wifi.set
```

The method is the only argument; the request is one JSON object on **stdin** (send `{}` when there is nothing: the
command reads stdin until it is closed), never on the command line, where a password would be in `ps` for every
user. The answer is one line of JSON on stdout: `{"ok":1,...}` with exit status 0, or
`{"ok":0,"error":"CODE","detail":"..."}` with exit status 1 (2: no method). Before anything else, ask:

```sh
printf '{}' | adb shell '[ -x /opt/mu300/bin/mu300-api ] && /opt/mu300/bin/mu300-api hello || echo MU300-API-NONE'
```

`MU300-API-NONE` is an older release: use the commands above. Otherwise `hello` answers
`{"ok":1,"api":1,"system":"ubuntu","device":"f50","release":"...","methods":[...],"features":{...},"job":{"kind":"","state":"none"}}`.
**`api`** is the version of this interface: methods and fields may be added without changing it; it changes when one
goes away or changes meaning. `methods` is what this device has; `features` says which parts it has (`vpn` the
module installed, `vpn_extra` mu300-extra there, `wifi_client`, `sms` (`cli`, `pool` on openwrt-luci, or empty),
`locks` and `forward` (openwrt-luci), `dual_sim`, `battery` and `usb_role` (U30 Air), `wpa3` (always false)).

| Method | Request | Answer (besides `ok`) |
|---|---|---|
| `hello` | - | above |
| `overview` | - | `system os device release kernel kernel_choice hostname uptime now memory storage signal data traffic battery temperatures hotspot vpn lan boot` |
| `wifi.get` | - | `enabled client_mode ssid band channel channel_live country security hidden password_set channels clients` (the password never) |
| `wifi.set` | `ssid` `password` `band` (`2.4`/`5`) `channel` (`auto` or one of `channels`) `hidden` (bool); at least one | `restart` |
| `wifi.power` | `state` `on`/`off` (kept across restarts) | - |
| `wificlient.status`, `.scan` | - | `active`, `text` (wifi-client's own words) |
| `wificlient.join` | `ssid`, `password` or `open: true` | `job` |
| `wificlient.leave` | `forget` (bool) | `job` |
| `data.get` | - | `state iface address apn pdp auth user password_set signal sim roaming roaming_switch` (`roaming`: `mobile-data roaming --json`, null without it; FINDINGS 120) |
| `data.set` | `apn` `pdp` (`IP`/`IPV6`/`IPV4V6`) `auth` (`none`/`pap`/`chap`/`both`) `user` `password` `clear_credentials` `roaming` (bool: `mobile-data roaming on` or `off`; alone it restarts nothing) | `restart` |
| `data.power` | `state` | - |
| `sim.get`, `sim.select` | -, `slot` 1/2 | `sim` (mu300-sim's JSON) |
| `sim.card` | `card` 0-3 or `"none"` (ZTE V50 only: built-in SIM 1/2/3, external; at the next boot) | `sim` (mu300-sim's JSON, its `v50` member), `text` (FINDINGS 204) |
| `sim.state` | `fresh` (bool: ask the card, AT+CPIN?) | `state` (`ready`/`locked`/`absent`, or null) `slot` (mu300-sim-state, FINDINGS 186) |
| `voice.get`, `voice.set` | -, `slot` 1/2 `kind` volte/vonr `state` on/off | `voice` (`mobile-data voice --json --read`: the saved choice per card, `live` what the card in use says), `text` (FINDINGS 184) |
| `radio.get`, `radio.set` | -, `slot` 1/2/"all" `state` on/off | `radio` (`mu300-sim-radio status --json`: `enabled` per card, `all_off`, `active`, `busy`, `result`), `text`; `busy` while another change runs (FINDINGS 185) |
| `lock.get`, `lock.set` | `fresh` (bool: read the modem again first), `kind` (`mode endc lte nr cell reset auto_apply`) `val` | `data`: the lock program's reading, or `started queued` (openwrt-luci, and Ubuntu with `/usr/libexec/unisoc-modem/lock`; FINDINGS 190) |
| `vpn.status` | `exit_ip` (bool) | `installed`, `status` (mu300-vpn's JSON), `extra` |
| `vpn.profiles`, `.subs`, `.settings` | - | `profiles`, `subscriptions`, `settings` (`KILL_SWITCH`, `IPV6`) |
| `vpn.service` `vpn.killswitch` `vpn.use` | `op` on/off/restart, `value` on/off, `id` | - |
| `vpn.add` | `source` `link`/`text`, `data`, `name` | `id` |
| `vpn.add_url`, `vpn.sub_add` | `url` (https), `name`, `interval` | `job` |
| `vpn.rename` `vpn.remove` `vpn.setting` `vpn.sub_remove` | `id name`, `id`, `key` `IPV6` `value`, `id keep` | - |
| `vpn.sub_refresh` `vpn.install` `vpn.engine_install` | `id`/`all`, -, `name` | `job` |
| `vpn.logs` | `lines` 1-1000 | `log` |
| `sms.list` `sms.read` `sms.send` `sms.delete` | `page`, `id`, `number text`, `id sim` | `data` or `text` |
| `sms.job` | - | `data` (openwrt-luci) |
| `sms.forward`, `sms.forward_test`, `sms.forward_log_clear` | - | `data` (unisoc-modem/forward get: the settings with every secret masked, the queue, the log), `started`, - |
| `sms.forward_set` | `enabled webhook telegram email sms` (bool), `template allow deny keywords clear webhook_url webhook_format webhook_headers webhook_body webhook_expect telegram_token telegram_chat email_host email_port email_tls email_user email_password email_from email_to sms_number` (text; the adapter's own rules; to it on stdin only) | - (openwrt-luci and Ubuntu; FINDINGS 190) |
| `lan.get`, `lan.set` | -, `ip` (private, ending in `.1`) | `ip default_ip usb_role usb_role_switch adb` |
| `usb.role` | `role` `host`/`device` (U30 Air) | - |
| `usb.net` | `mode` (`ncm ecm rndis default`) `scope` (`once`/`permanent`); without `mode` it only reads | `mode once now` (the USB network from the next boot, and this boot's; mu300-usb net, or device-usb on openwrt-luci) |
| `adb.auth` | `mode` `open`/`keys`, `key` (an adbkey.pub line; required for `keys`) | - |
| `system.get` | - | `hostname timezone user ssh time release kernel kernel_choice boot extras` |
| `system.hostname` `system.timezone` `system.password` `system.ssh` | `name`, `zone`, `password`, `state` | `user` for the password |
| `system.reboot` `system.boot` `system.bootos` | -, `target` `linux android lock unlock`, `os` | - |
| `update.check` `update.apply` `kernel.set` `extra.install` `extra.remove` | -, -, `version`, `name`, `name` | `job` |
| `extra.list`, `logs` | -, `lines` | `extras`, `text` |
| `backup.export` | `passphrase` (8-256 characters: the file is encrypted), `with_password` (bool: the login password's hash) | `name encrypted file`: the backup as text (mu300-backup, FINDINGS 155); the one answer that carries secrets on purpose |
| `backup.import` | `file` (the text of a backup, 4 MiB at most), `passphrase`, `dry_run` (bool), `force` (bool: a file of another model), `only`/`skip` (sections, comma-separated) | dry run: `valid text` (what would change, or why the file is refused); else `job` |
| `vpn.engines` | - | `engines` (mu300-vpn engines --json: each engine, installed or not, its version and size) |
| `at.send` | `command` (one AT command, printable ASCII, 512 bytes at most; on mu300-at's stdin), `timeout` 1-60 (10) | `rc text` (the modem's answer) |
| `at.history` | - | `history`: `time command`, newest first, the last 40 of this boot (a PIN or password's arguments left out) |
| `ussd.send`, `ussd.cancel` | `code` (`[0-9*#+]`, 40 at most: a code or a menu's answer), - | `text` |
| `modem.restart` | - | - (mobile data dials again: Ubuntu's mu300-mobile-data restarted, OpenWrt's WAN down and up) |
| `ttl.get`, `ttl.set` | -, `value` 1-255 or `off` | `enabled value backend` (mu300-ttl) |
| `traffic.get`, `traffic.set` | -, `key` `reset_day cap warn cut used clear` and its `value` (1-31, bytes (0: none), 1-100, `on`/`off`, bytes, `yes`) | `traffic` (mu300-traffic json) |
| `power.get`, `power.set` | -, `key` `PROFILE SAVER_BELOW CHARGE_TO` or `plugged`/`battery`/`saver` `.WIFI_IDLE .RADIO_IDLE .LEDS_IDLE .CPU`, `value` | `state profile why supply battery charge_off conf text` (mu300-power) |
| `led.get`, `led.set` | -, `timeout` (seconds, 0: always on) and/or `test` (bool: each LED in turn) | `timeout device` |
| `zram.get`, `zram.set` | -, `state` on/off | `active enabled text` |
| `cpu.get` | - | `cpu` (mu300-cpu status --json), `voltage` (mu300-cpu voltage status --json, or null) |
| `cpu.profile` | `name` `saving balanced performance` | - |
| `cpu.voltage` | `op` `set` (`offsets` "A,B,C" in µV, `confirm` `EXPERIMENTAL`) / `off` / `stress` (`minutes` 1-120) | - (EXPERIMENTAL: it can damage the device for good) |
| `update.status` | - | `status` (mu300-update status), `source` (where the release files come from), `version` (text) |
| `update.source` | `url` (http(s) mirror of the release files) or `off` (GitHub) | `source` |
| `packages.get`, `packages.act` | -, `op` `retry` (a `job`) `skip` `dismiss` | `text` (mu300-user-packages status) |
| `docker.status` | - | `installed active containers` (name image state status; a stopped Docker is not started to ask) |
| `job.status` | `from` (a line number) | `job`: `kind state rc started ended text next cancel` |
| `job.cancel`, `job.clear` | - | - |

**Errors**: `bad-request`, `unknown-method`, `unknown-field` (detail: the field), `bad-value` (the field), `missing`
(the field), `not-root`, `unsupported` (what), `busy` (another job), `not-installed` (the VPN module), `failed` (the
tool's last line, an address's path and query cut off).

**Jobs**: a method that answers `{"job":"KIND"}` has started the device's one background job, in its own session: it
goes on when the adb connection ends. Follow it with `job.status {"from": N}` (N: the `next` of the last answer),
until `state` is not `running`; `done` has `rc`, `interrupted` none (the device restarted, say). `job.clear` forgets an
ended one. Restarts of the hotspot, the LAN, mobile data, adb and the device itself happen two seconds after the
answer: an `adb.auth`, `usb.role host` or `system.reboot` ends the connection then.

## What the interface does not promise

* That adb is there. It is on by default and open to every computer (the owner's decision of 2026-10-13, FINDINGS
  70), but the owner of a device can restrict it (`mu300-usb adb auth keys`) or turn it off (`mu300-usb adb off`),
  and a device that had adb set up under v2026.10.20-23 stays keys-only until told otherwise. A tool that meets
  `unauthorized` should say: on the device, `sudo mu300-usb adb key FILE` for this tool's public key, or
  `sudo mu300-usb adb auth open`.
* A network transport. adbd has no TCP or vsock listener in either mode; `adb tcpip` and `adb connect` do not work.
