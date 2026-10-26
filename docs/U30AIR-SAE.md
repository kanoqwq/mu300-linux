# U30 Air firmware SAE offload: disabled

**Status (2026-10-17): disabled in every layer, on every device (ZTE F50, U30 Air, V50).** The hotspot is
WPA2-PSK (CCMP) only. This page says why, what changed for a device that had turned the offload on, and what it
would take to bring WPA3 back. The full history is [FINDINGS 80](FINDINGS.md); the work that would have to come
first is [the key-handoff notes](U30AIR-SAE-HANDOFF.md).

## Why

On the SC2355 the SAE exchange of a WPA3 access point runs in the chip's firmware, so stock hostapd never sees it
and a WPA3-only hotspot lets nobody in. The offload (an opt-in since PR #74 / #165) made it work with two vendor
interfaces: hostapd gave the firmware the passphrase (vendor command OUI `0x001374`, subcommand 43), and the firmware
answered each association with the derived PMK and PMKID, which a patched hostapd imported.

That PMK came back as a vendor element, `dd 34 40:45:da:04 <PMK 32 bytes><PMKID 16 bytes>`, appended to the
`EVT_NEW_STATION` element list - the same list that carries the client's own association-request elements, sent
over the air. Nothing marks where the client's elements end and the firmware's record begins. A client could put a
record of its own choosing there, with a PMK it picked, and whenever the firmware did not append its own (an
association without a completed SAE exchange, PMKSA caching, a firmware path nobody has measured) hostapd would
import the client's PMK and the 4-way handshake would succeed **without the passphrase**. Required PMF and the
handshake MIC do not help: they prove knowledge of the PMK, and the client chose it. The firmware is a closed blob
and the driver has no separate SAE-completion signal, so this cannot be shown safe. Found in the review of PR #74,
analysed again for #237; the owner disabled the mode on 2026-10-17 rather than keep an opt-in that might be an
authentication bypass.

## What was removed, layer by layer

| Layer | Now |
| --- | --- |
| hostapd | The patch that imported the record (`openwrt/patches/hostapd/990-u30-sae-offload.patch`) and its SDK recipe (`openwrt/build-u30-sae.sh`) are gone. Images carry the stock `wpad-basic-mbedtls`; `openwrt/build-rootfs.sh` refuses `MU300_SAE_APK_DIR`. |
| Driver (6.18, 7.2, 7.3: `upstream/modules/sprd_wlan_combo`; 5.4: `kernel/patches/wlan_combo-softap-no-sae.patch`) | `sprd_report_softap()` drops every `dd .. 40:45:da:04` element (and a malformed tail) from the association elements before `cfg80211_new_sta()`, whoever wrote it, so even a hand-built hostapd cannot import it. `vendor_set_sae_password()` (subcommand 43) returns `-EOPNOTSUPP`: the firmware is never given an SAE password. |
| wifi-scripts (OpenWrt) | `openwrt/patches/wifi-scripts-wpa2-only.patch`: an access point with `encryption` `sae`, `sae-mixed`, `sae-compat`, `psk3`, `psk3-mixed`, or the `u30_sae_offload` hostapd option, is not started; the log says `WPA3/SAE is disabled on this Wi-Fi chip ... WPA2-PSK only`. Down, never open. |
| Boot (OpenWrt) | `openwrt-wifi-sae-guard` is replaced by `openwrt-wifi-wpa2-only migrate` (below). |
| Web device manager, `mu300-api` | WPA3 was never offered; a hotspot still on SAE is switched to WPA2-PSK (CCMP) on the next save, with its password or the new one; without one the save is refused. |
| Ubuntu | Always WPA2-PSK. `hotspot-start` refuses `ENCRYPTION`/`SECURITY`/`KEY_MGMT` asking for SAE/WPA3 in `hotspot.conf` with one line, and starts WPA2. |

The driver changes that stop it printing the passphrase, station keys and raw command/event payloads
(`kernel/patches/wlan_combo-sae-private-logs.patch` and the same in the mainline tree) stay: they are worth having
without the offload.

## A device that had turned it on

At the first boot of an image with this change, before the Wi-Fi start that brings the hotspot up,
`/opt/mu300/bin/openwrt-wifi-wpa2-only migrate` (called by `mu300-hw`):

* turns every access point that asks for SAE into `encryption psk2+ccmp` **with the same password**, takes out
  `ieee80211w`, `sae_pwe` and the `u30_sae_offload` option, and keeps the settings as they were in
  `/etc/mu300/wireless.before-wpa2` (root only);
* logs one line (`logread -e mu300-wifi`) and writes `/etc/mu300/wpa3-disabled`, which LuCI's Status > Overview and
  the control panel's home page show as a notice (English, Turkish, Chinese) until it is dismissed there;
* if the access point has no password WPA2 can use (8-63 printable ASCII or 64 hex digits), or the change did not
  stick, switches that access point **off** instead and says so (log line at error level, the notice in red). It is
  never left open. The wifi-scripts refuse SAE anyway, so a migration that did not happen leaves it down, not open.

Clients that had joined with WPA3 join again with the same password. Nothing is done on a device whose hotspot is
WPA2 (every image's default).

## What it would take to revisit

WPA3 on this chip comes back only with a key handoff whose origin can be verified - not with a better parser of the
same element list:

1. A firmware-originated PMK/PMKID delivered apart from the client's elements (a dedicated event or query), bound to
   the BSS, the station and a completed SAE exchange (or a verified PMKSA resumption), with a generation that a WCN
   reset or a new association invalidates. Without firmware support or documentation from Unisoc this does not
   exist; marking the existing record "authenticated" in the driver is the same trust problem with another name.
2. The driver delivering only that verified key, through a reviewed interface; client elements never a source of
   key material (the strip above stays).
3. hostapd importing only from that interface, failing closed on anything missing, stale or mismatched.
4. A device test that tries the bypass: a client that skips SAE (`wpa_cli pmksa_add` with a PMK of its own) and
   appends a forged record must fail the 4-way handshake; plus the functional cases (WPA3 join, wrong password,
   PMKSA resumption, WCN recovery) on F50 and U30 Air, on 5.4 and the mainline kernels.

[U30AIR-SAE-HANDOFF.md](U30AIR-SAE-HANDOFF.md) has the details of 1-3. The last code of the offload is in git history
(the hostapd patch, recipe and guard as of commit `2e5cf929`).

## Credits

The offload, its hardware measurements and the review notes are **Snrat**'s work (PR #74, continued as #165; the
probing of the vendor command, the PMK record, the SDK recipe, the device runs on 7.2.9 and 7.2.10, and the
key-handoff notes). The provenance problem was found in the review of PR #74 and analysed again for #237. Disabling
the mode is no judgement on that work: it showed what the firmware does, and that is why it is known that this
interface cannot be trusted as it is.
