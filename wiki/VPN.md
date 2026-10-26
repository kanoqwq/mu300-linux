# VPN

**In one sentence:** an optional module, `mu300-vpn`, sends the device's own traffic **and** everything from its
Wi-Fi and USB clients through your VPN server, with a kill switch so nothing leaks when the tunnel is down.

**The simple version:** imagine every parcel leaving the house going into a locked van first. The kill switch means
that if the van is not there, parcels wait at the door instead of being sent unprotected.

The VPN is a separate project with its own releases: **[dikeckaan/mu300-linux-vpn](https://github.com/dikeckaan/mu300-linux-vpn)**.
Its README is the full reference (commands, profiles, settings, security model). This page is the short tour.

```mermaid
flowchart LR
    C[Wi-Fi and USB clients] --> D[Device]
    D --> K{Kill switch}
    K -->|only tunnel traffic| T[VPN engine<br/>WireGuard / OpenVPN / Xray / sing-box / mihomo]
    T --> U[Mobile data or Wi-Fi client uplink] --> S[(Your VPN server)] --> I[Internet]
    K -. tunnel down: nothing leaves .-> X[blocked]
```

## Install the module

It needs mu300-linux **v2026.10.16 or newer**; module **v2.0.0** (a core plus engine extras, subscriptions, the panel's
VPN section, the IPv6 guard) needs **v2026.10.20 or newer**. The installer asks about it; otherwise, on the device:

```sh
sudo mu300-extra install vpn                                # online: the core and the engines its profiles need (first time: Xray)
sudo mu300-vpn engine install sing-box                      # each engine is an extra: xray, sing-box, mihomo, dae
sudo mu300-extra install vpn-mihomo                         # the same, by the extra's name (vpn-xray, vpn-singbox, vpn-dae)
sudo mu300-extra install vpn --from /mnt/usb/               # offline, verified: a directory with the tarballs and their SHA256SUMS
sudo mu300-extra remove vpn-mihomo                          # an engine; refused while the VPN runs on it
sudo mu300-extra remove vpn                                 # turn the VPN off first
```

On OpenWrt leave out `sudo`. The module lives on the Linux partition (`/mnt/mu300-disk/extra/vpn`, the engines in
`extra/vpn-xray` and so on), so Ubuntu and OpenWrt share one copy, and updates bring the core and the engines to their
latest release as one set. A tarball is installed only when its hash is in the `SHA256SUMS` next to it (v1 modules
keep both their engines when upgraded). On OpenWrt with the control panel all of this is also in the panel's **VPN**
menu (see [Control Panel (LuCI)](Control-Panel-(LuCI)#the-panels-pages)); on Ubuntu with the panel extra, the MU300
page in Cockpit turns the VPN on and off and picks the profile. Both panels, like `mu300-toolkit`, show their VPN
section only once the module is installed: without it there is nothing of a VPN in them.

## Use it

```sh
sudo mu300-vpn profile import ~/office.conf Office    # a file: WireGuard .conf, .ovpn, Xray/sing-box .json, mihomo .yaml
cat client.ovpn | sudo mu300-vpn profile import -     # from stdin, so nothing is left in the process list
mu300-vpn profile list                                # * marks the active one
sudo mu300-vpn profile use office                     # make it active
sudo mu300-vpn on                                     # start now and at every boot
mu300-vpn status                                      # profile, engine, tunnel, exit IP (never a secret)
sudo mu300-vpn off                                    # stop, and remove the kill switch
```

Share links (`vless://`, `vmess://`, `trojan://`, `ss://`) can be imported too; prefer stdin for them so the link does
not end up in your shell history. `mu300-toolkit` has a VPN menu.

## What it supports

| Profile | Engine |
|---|---|
| WireGuard `.conf` | the kernel's WireGuard (the mainline kernels; the 5.4 vendor kernel has none) |
| OpenVPN `.ovpn` | the system's OpenVPN package: `apt-get install openvpn` (Ubuntu), `apk add openvpn-openssl` (OpenWrt) |
| VLESS, VMess, Trojan, Shadowsocks links; Xray JSON | Xray-core with hev-socks5-tunnel |
| the same links behind the kill switch; sing-box JSON | sing-box |
| Clash/mihomo YAML | mihomo (`mu300-extra install vpn-mihomo`) |
| hysteria2, TUIC links; dae config | [dae](https://github.com/daeuniverse/dae), the eBPF proxy (`mu300-extra install vpn-dae`): needs kernel 6.18, 7.2 or 7.3 of v2026.10.20 or newer (they carry BTF); never 5.4 |

Tailscale's own traffic goes through the tunnel by default.

**Subscriptions** (v2): `mu300-vpn subscription add NAME URL` takes a URL that yields profiles (share links, a mihomo
or dae list), refreshed by hand or on a schedule; a refresh overwrites edits made to its profiles.

## Settings worth knowing

`mu300-vpn settings set KEY VALUE`, from the module's README:

| Key | Default | Meaning |
|---|---|---|
| `KILL_SWITCH` | 1 | nothing but the tunnel leaves the device, also while the tunnel is down |
| `TAILSCALE` | 1 | Tailscale's traffic through the tunnel |
| `IPV6` | auto | `auto`: IPv6 through the tunnel when the tunnel carries it (a WireGuard or OpenVPN config's own IPv6 address; Xray, sing-box and mihomo are asked at every start, with a fetch through the tunnel, from the module release after v2.0.1 - before that they stay IPv4), otherwise IPv6 is **blocked** beside the tunnel so nothing leaks past the kill switch (the IPv6 guard, v2); `1` always, `0` never |
| `REMOTE_DNS` | 1.1.1.1 | DNS for the device and its clients, through the tunnel |
| `LAN_CIDRS` | empty | more networks that stay local (the device's own LAN always does) |

## Fails closed

* A device whose VPN is on is never updated without the module. Offline, `mu300-update apply` stops before changing
  anything and says how to stage it (see [Updating](Updating#with-the-vpn-on)).
* A system whose VPN is on with the kill switch and has lost the module keeps mobile data and the Wi-Fi client
  **down** until the module is installed again (`mu300-extra install vpn`) or the VPN is turned off. A missing engine
  fails closed the same way, behind the kill switch, while the service fetches it again; `mu300-toolkit` says which one.
* `mu300-extra remove vpn-<engine>` is refused while the VPN runs on that engine.

## Known limits

Some carriers drop UDP and block OpenVPN by deep packet inspection. A raw Xray/sing-box/mihomo config that contains a
`direct`/`freedom` outbound sends that traffic outside the tunnel, past the kill switch, by its own choice.

> Using a VPN may break your operator's terms or local law. Check before you turn it on.
