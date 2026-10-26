Imported from kanoqwq/mu300-linux, branch clean-tf-7.2, commit 35a1c55 (PR #22 and later). Changed here since: see git log.

# luci-app-mu300

A self-contained LuCI application for Unisoc cellular devices. It provides the
dashboard, live radio readings, persistent network/band/cell/EN-DC locks, a
guarded AT terminal and an SMS UI.
The Device Management page controls USB role and gadget network policy, and
lists host-side USB network adapters for optional attachment to the LAN bridge.
The TTL page (Cellular) sets the TTL of everything that leaves through mobile data
(`mu300-ttl`, through the `unisoc-modem/ttl` adapter).
The CPU page (System) picks a performance profile - power saving, balanced or
performance - which sets the CPU governor and the per-cluster frequency limits
(`mu300-cpu`, through the `unisoc-modem/cpu` adapter). It stays inside the hardware
frequency table and never changes voltage, so the kernel keeps throttling at its
thermal limit in every profile.
The Terminal page (System) frames ttyd: `rpcd/mu300term` hands a LuCI session with
the `luci-app-mu300-terminal` ACL a one-time token (one connection, 60 s), and ttyd's
command, `/usr/libexec/mu300-terminal-login`, opens a root login for that token and
nothing else, closing it after `ttyd.mu300.idle` seconds without activity. The image
configures ttyd for it (`openwrt/luci-overlay/etc/config/ttyd`: the LAN address only,
origin check, `url_arg`); the package alone does not install ttyd, and the page then
says so.

The dashboard follows LuCI's selected language. Its colors follow Aurora's existing
tokens when present, or the official Bootstrap theme's light/dark tokens.
The translations are standard LuCI catalogs: English message ids in the
JavaScript, `po/<language>/mu300.po` as the editable source. Turkish and
Simplified Chinese are in the image; the other languages (`po/*` besides those
two: machine/AI translations, corrections welcome) are in the lang extra
(`mu300-extra install lang`, or the Languages page under System), named by
`openwrt/luci-languages.tsv`.
`lmo/` is not kept in the tree; the image build compiles the catalogs with
`tools/po2lmo.py`, so the top-level menu and submenu stay translated on
unrelated LuCI pages where the dashboard JavaScript is not loaded.

The package does not start or own the modem. Platform-specific access is behind
small command adapters, so the LuCI and RPC code does not need to change for a
different Unisoc OpenWrt firmware.

## Data usage and live rates

Cellular > Data usage shows mobile data per day and per billing cycle (a configurable reset day), with an optional
monthly cap: a warning at a set percentage, and, only when turned on, mobile data off at the cap until the next
cycle. The page talks to `unisoc-modem/traffic` (mu300dash `traffic_get`/`traffic_set`), which runs the platform's
`mu300-traffic` (`MU300_TRAFFIC_CMD`); without it the page says data usage is not available. The count is that
command's, so the panel and its CLI always agree; on MU300 it samples the interface counters once a minute into
RAM and writes the disk rarely (see `mu300-traffic`).

The dashboard's download/upload rates come from a lane of their own: one rpcd `file` read of `/proc/net/dev` every
second (no process, no AT), timed in the browser. That is the only file the read grant names, with
`/proc/[0-9]*/net/dev` beside it because rpcd checks the path it resolved (`/proc/net` is `/proc/self/net`). The
status snapshot names the interface (`data_device`) and carries mu300-traffic's summary for the dashboard's usage
tiles.

## VPN

The top-level VPN menu has six pages (Overview, Profiles, Subscriptions, Engines, Settings, Logs) over the VPN
module's machine-readable CLI, `mu300-vpn ... --json` (github.com/dikeckaan/mu300-linux-vpn, spec
`2026-10-11-engine-extras`, section 4). Their backend is a second rpcd object, `mu300vpn`
(`/usr/libexec/rpcd/mu300vpn`), separate from mu300dash because what passes through it holds secrets: share links,
configs, subscription URLs and OpenVPN passwords reach the CLI on its stdin (from a 0600 file in the root-only runtime
directory), never in its argv, and nothing of a reply is logged. Reads (status, the lists, settings, the task) are in
the ACL's read grant; every change, the log and the config reveal are in the write grant. Downloads (an engine, the
module, a subscription, a config from a URL) and a latency round over every profile run as the one background task,
bounded in time, which the pages poll. Uploads go to `/tmp/mu300-vpn-upload` (a config) and
`/tmp/mu300-vpn-extra.tar.gz` (an extra's tarball), the only paths the ACL lets the panel write, and are moved out of
`/tmp` before anything reads them. Without the module the pages show a card that installs it
(`mu300-extra install vpn`). `tests/fixtures/fake-mu300-vpn` stands in for the CLI to try the pages without a device.

## System update

System > Update updates the device from the browser. It is not an updater of its own: every action is one
`mu300-update` command (`MU300_UPDATE_CMD`), run by the `unisoc-modem/update` adapter as a task of its own that
outlives the request, the page, the session and rpcd, and whose log (redacted) the page follows; the updater's own
lock decides whether a run may start. The page reads `mu300-update status` and `releases` (JSON), and for a device
without internet takes a release's Magisk zip: uploaded through cgi-io to
`/mnt/mu300-disk/.mu300-upload/update.zip`, the one path the ACL adds, in a directory only root can enter, looked
at with `mu300-update inspect` and installed with `mu300-update apply --from`. Reads (`update_get`, `update_job`)
are in the read grant, every change (`update_act`) in the write grant, and each parameter is a fixed word, a release
tag, a size or a SHA-256. The design, the trust model for uploaded files and release signatures are in
docs/FINDINGS.md 86. Without `mu300-update` the page says updating is not available.

## AT adapters

Configure `/etc/config/unisoc_modem`:

- `at_backend=mu300` uses an existing `mu300-at` daemon and keeps its locking.
- `at_backend=atinout` uses the configured `at_port` and a package-local lock.
  Use this only when no other process reads that tty.
- `at_backend=custom` runs the executable in `at_command`. It receives
  `TIMEOUT` and the complete `AT COMMAND` as its two arguments. A platform that
  already has a RIL/AT daemon should expose it through this adapter so every
  client shares that daemon's lock.
- `at_backend=auto` prefers `mu300-at`, then `atinout`, then the custom command.

The SMS page uses `sms_command`, whose CLI contract is the existing
`mu300-sms` interface: `list`, `show`, `send`, `delete` and `sync`. This keeps
SIM storage details out of LuCI and lets each firmware supply its own adapter.
`send` is called as `send --stdin NUMBER` with the text on stdin, so a message
never becomes a command-line argument; an adapter that only knows
`send NUMBER TEXT` refuses `--stdin` as a number and sends nothing.
On openwrt-luci the adapter is the SMS pool (`mu300-sms`, `mu300-smsd`): the
daemon syncs the SIM into `/etc/mu300/sms-pool` every 30 s and marks the
messages it reads from the SIM as read, so `sms delete read` (the system's own
SMS tool) deletes messages that no CLI user has seen. Delete from the panel or
with `mu300-sms delete`, which touches the SIM only with `--sim`.

Network interface and state paths are also configured in the same UCI section;
none of the web code requires `sipa_eth0`, `wan`, `br-lan` or `/opt/mu300` from
the host firmware.

## Persistent locks

The package owns `/etc/init.d/unisoc-modem-ui`. It starts a non-blocking procd
worker at boot only when saved locks exist and automatic application is enabled.
The worker probes the selected AT adapter every two seconds and replays the
settings immediately when it becomes ready. It never delays OpenWrt startup and
stops after `replay_timeout` seconds instead of polling forever. A platform
with a pre-radio hook may create `/run/unisoc-modem-early-hook-pending` before
AT startup. While that file exists, this worker sends no AT probes, preserving
the platform's first-command handshake. The platform removes it after its
radio-on attempt; if early replay did not create the completion marker, the
worker falls back to the normal late replay.

The portable worker activates saved mode/band/cell settings with one bounded
SFUN restart and raises the configured data interface afterwards. An EN-DC-only
replay needs no stack restart and is applied immediately. A platform with a
deliberate pre-radio integration may call
`/usr/libexec/unisoc-modem/lock replay early` from that hook after the AT
handshake and while the radio is off. Early replay reads back the saved fields
before writing its completion marker. A failed readback leaves late replay
available; a successful marker prevents duplicate application. `early` is
deliberately not a user-selectable setting because it is only safe at that exact
point in the platform radio sequence.

Where the platform has `/opt/mu300/bin/mobile-data`, the panel's radio on/off
and modem reset, and the SFUN restart of a lock apply or a late replay, run
under its radio lock (`mobile-data radio-locked`), so they never run beside the
dial's or the watchdog's radio sequence. Radio on/off and modem reset answer
"busy" while another sequence holds the lock; a lock apply waits up to two
minutes for it.

## USB device management

USB role defaults to device at every boot. The page can switch it immediately;
checking host auto-apply asks the package's boot worker to reapply host mode
at every boot. On the battery-less F50, the plugin writes the requested role
to sysfs directly: USB management disappears and an attached adapter may need
an externally powered hub. U30 Air uses `mu300-usb` and its charger boost/VBUS
checks. Other hardware can use the sysfs fallback or configure
`unisoc_modem.usb.role_command` with a platform-specific executable that
accepts `host` or `device`. The plugin must not bypass a known platform's
power-safety checks.

NCM/ECM/RNDIS selection is stored in `/etc/mu300/usb-net` (one line: `ncm`,
`ecm` or `rndis`) only when "Enable selected protocol" is checked. `boot/init`
reads that file from the system it has chosen to boot, after picking the root;
when it asks for other functions than the gadget was bound with, init rebinds
the gadget once before `switch_root` (`MU300_USBNET` on the kernel command line
still wins). RNDIS falls back to NCM, then ECM, on a kernel that cannot make
it, so a saved choice never leaves the system without USB networking. Other OpenWrt builds can implement the same one-line contract at
their own gadget setup point. The plugin itself owns the policy and UI, not the
kernel or gadget. `once` is consumed after a successful boot only if init left
`/run/mu300/usb-net-applied` (the selection was in effect for that boot);
subsequent boots use the platform's default NCM. Selecting persistent host mode automatically disables USB
network auto-apply. While the current role is host, network-mode controls are
disabled. The backend validates the same rules regardless of UI state.
On openwrt-luci, `boot/init` exposes RNDIS as a single USB configuration with
the ACM console, and RNDIS replaces the other network function; Windows does
not bind a composite RNDIS adapter when the device offers both RNDIS and NCM
configurations. On LAN handoff, the early DHCP server of the initramfs is
stopped and the address it used is removed from the bridge ports, so only
`br-lan` owns it.

USB adapter discovery uses the USB sysfs parent of each network device. On
refresh it attempts to bring discovered devices up. “Add to LAN” adds only a
verified USB adapter to the configured LAN bridge device's UCI port list,
commits `network`, and reloads networking. The selected port is reattached
on USB netdev hotplug and LAN ifup, without a polling daemon or another
network reload. The action is idempotent and only available in host mode.

The dashboard's hero line carries the link the USB-C cable negotiated, so a USB 2.0
cable or host port behind a 10 Gbps capable port is visible instead of only turning
up later as a throughput ceiling: `USB High Speed · 480 Mbps` with an amber dot while
the link is below the port's own maximum, `USB SuperSpeed+ · 10 Gbps` with a green one
at it, and nothing at all while the port is empty. The status snapshot reports it as
`usblink` (`speed`/`mbps` and `max`/`max_mbps`): the gadget's UDC in device mode, the
attached device in host mode - a root hub is never taken for the device - and `null`
when there is no link to report.

## Build

Copy this directory alone to `package/luci-app-mu300` in any compatible OpenWrt
buildroot, select `LuCI -> Applications -> luci-app-mu300`, and build normally.
No file outside this directory is copied into the package; platform-specific AT
and SMS implementations are discovered only through the documented adapters at
runtime.

For a source-tree hot install (without an `.ipk`/`.apk`), copy `root/` to `/`,
`htdocs/` to `/www/`, and `lmo/` to `/usr/lib/lua/luci/i18n/`; then
make `/etc/init.d/unisoc-modem-ui`, `/usr/libexec/rpcd/mu300dash` and the
`/usr/libexec/unisoc-modem/*` adapters executable; then
enable/start `unisoc-modem-ui` and
restart `rpcd`. Copying only `root/` leaves the LuCI menu visible but makes
`/luci-static/resources/view/mu300/*.js` return HTTP 404. Normal package
installation performs both copies through this package's `install` recipe.
