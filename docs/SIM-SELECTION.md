# F50 internal and external SIM selection (and the V50's cards)

The default remains the external card (slot 0). Some F50 units also have an internal
SIM exposed as slot 1. Selecting slot 1 does not provision an eSIM or write any SIM
identifier. It requires a device already fitted with an active internal SIM.

| Source | Event / command / data TTY | CID 1 interface |
| --- | --- | --- |
| External (0, default) | nr0 / nr1 / nr2 | sipa_eth0 |
| Internal (1) | nr3 / nr4 / nr5 | sipa_eth8 |

## OpenWrt

In Network → Interfaces → WAN, choose **SIM source** and set that card's APN and
PDP type. Save the configuration, then reboot the device into Linux. Each source
retains its own APN/PDP settings; changing the selector does not erase the other
profile. Only one cellular interface named `wan` is supported by this selector.

The equivalent commands for the tested China Mobile internal card are:

```sh
uci set network.wan.apn_internal='cmnet'
uci set network.wan.pdptype_internal='IPV4V6'
mu300-sim select internal
mu300-sim status
```

For callers that share the dual-SIM command vocabulary, `mu300-sim default 1`
selects the external card and `mu300-sim default 2` selects the internal card.
`mu300-sim status --json` reports physical card numbers (`active` and `default`)
and whether the detected device supports slot 1 (`slot1_supported`), along with
`hot: 0` and `available: 0`; F50 selection is boot-time only. The
`hot` and `switch` commands fail explicitly instead of attempting a live SIPC
channel switch.

`mu300-sim select external` restores slot 0 on the next Linux boot. The external
profile uses the existing `network.wan.apn` and `network.wan.pdptype` options.
Use that card's carrier APN, not necessarily `cmnet` (`ctnet` was used for the
China Telecom comparison card). Selection does not change the default boot OS.

The active slot is recorded atomically in `/run/mu300/sim-slot` when modem userspace
first starts. All callers use that value until reboot, including teardown after
the next-boot selection has changed. Do not delete this file or restart TTY owners
to attempt a live switch: closing and reopening SIPC TTYs can wedge the channels.
The existing OpenWrt automatic WAN setup uses the selected card after reboot.

On systemd installations, `mu300-sim select` stores the choice in
`/etc/mu300/sim-slot`; configure the selected carrier in the existing
`/etc/mu300/mobile-data.conf`. The two-profile LuCI editor is OpenWrt-specific.

## ZTE V50: built-in and external cards

A V50 carries up to four cards on the modem's two SIM interfaces (docs/FINDINGS.md 204; ZTE's table, read from its
firmware by kanoqwq). Which ones depends on the board variant (ZTE's `board_id`):

| board_id | cards |
| --- | --- |
| 0 | built-in SIM 1, 2, 3 |
| 1 | built-in SIM 1, 2, 3 and the external SIM |
| 2 | built-in SIM 1, 2 and the external SIM |
| 3 | built-in SIM 1, 2 |

Built-in SIM 1 and the external SIM share SIM1 (slot 0); built-in SIM 2 and 3 share SIM2 (slot 1). A board switch
picks the card on each interface. Choose the card, then reboot:

```sh
mu300-sim status            # card in use / next boot card
mu300-sim card 3            # 0, 1, 2: built-in SIM 1, 2, 3; 3: the external SIM
mu300-sim card none         # no choice: the switches stay as the bootloader sets them
```

`card` saves the card and its interface (the same `sim_slot` as `select`), so the WAN page's profile for that
interface is used (SIM1: APN/PDP type, SIM2: the internal profile). At the next boot the switch is set before the
modem starts; nothing is switched while the modem runs. LuCI: Cellular > SIM; Cockpit: the SIM card; the API:
`sim.card`. Without a choice nothing changes from before: the switches stay as the bootloader leaves them.

`mu300-sim card N --now` (experimental, kanoqwq's sequence from ZTE's firmware) switches a card on the SIM in use
without a reboot: mobile data goes down, the card is powered off, switched, powered on after 5 s, and the data comes
back; a failure switches the previous card back. A card on the other SIM needs the reboot.

## Devices without a supported slot-1 backend

The F50 boot path and the U30 Air/OpenWrt native backend are separate implementations. On a device without a
supported backend (including U30 Air outside OpenWrt),
`mu300-sim select internal` refuses, `mu300-sim status --json` says `slot1_supported: 0`, and a saved
`network.wan.sim_slot=1` is ignored for the boot (`mu300-sim: ignoring unsupported slot 1; using external slot 0`),
so every reader - the WAN, the AT daemons, the dashboard - stays on the external card.

The pages say so (issue #194; they offered the internal SIM on every device, and a U30 Air then showed the external
card's operator with no hint why):

- `dashboard-info` (the panel's local snapshot, `mu300dash sysinfo`/`status`) carries `"sim": {"slot": 0|1,
  "wanted": 0|1, "slot1_supported": 0|1}` from `mu300-sim status --json` (`null` without the selector).
- Network → Interfaces → WAN (`protocol/mu300cell.js`) reads it and does not offer **Internal SIM** where
  `slot1_supported` is 0. If the configuration already has slot 1, the entry stays as "Internal SIM (slot 1): not
  supported on this device", so the page shows what is saved; choosing the external SIM and saving puts it right.
  Without the panel, or when the call fails, both sources are offered as before.
- The home page's connection card gets a **SIM source** row only when the card asked for is not the one in use:
  "not supported on this device: the external SIM is in use", or on the F50 "in use after a reboot".

The U30 Air/OpenWrt backend has its own channel layout and broker-only cold start/live switch (PR #166).
Its version-1 status retains `slot1_supported: 1` for these existing pages, alongside the separate
`capabilities.boot_selection` and `capabilities.live_switch` fields. A failed/unknown current-card observation stays
`active: null`; the dashboard does not turn the runtime routing pin into a confirmed active card. F50 retains its
boot-only path. `docs/U30AIR-SIM2.md` is that backend's page (how to choose the second card, what happens when it
does not start, the live switch); `docs/FINDINGS.md` 74 has what two units showed.

## Why the data-route command is needed

On the tested F50, the internal card registered, received a PDP address, and
returned `CONNECT` to `AT+CGDATA="M-ETHER",1`, yet DNS, TCP and IPv6 router
solicitations received no reply. The SIPA delegate was loaded and responding.
Selecting the packet-data route with `AT+SPSWDATA` immediately restored replies
and IPv6 router advertisements. The command is used by the stock RIL's preferred
data-modem path. This patch sends it only for the opt-in slot 1 path and requires
an `OK` response before reporting a successful dial.

The slot 1 context definition and PCO/QoS setup follow the observed stock RIL.
The original slot 0 sequence is retained. Stock Android requests `cmnet/IPV4V6`
on this card but reports `cmiot5g` in `CGCONTRDP` after a successful connection;
that difference alone is not evidence of a wrong configured APN.

The dashboard shares slot 1's general command daemon; it must not fall back to
the slot 0-only nr6/nr7 pool. Its persistent identity cache is separate per slot.
The IPv6 monitor and USSD listener use the selected event stream. Empty LTE
neighbor data is also handled without aborting the rest of the dashboard.

## Validation status

The slot 1 routing sequence and dashboard fixes were exercised on an existing
OpenWrt 25.12.5 installation with Linux 5.4.254. DNS, HTTP 200, growing RX counts,
IPv4/IPv6 assignment and rebooted automatic startup were observed. Android/Linux
identity comparisons matched; no identity writes were used.

The generalized selector in this branch still requires a clean-flash hardware
regression. In particular, test external → internal → external across reboots
with both cards present, checking the active identity, APN, interface, DNS/TCP,
old interface cleanup, and dashboard data each time. Do not claim live switching
or mainline-kernel hardware validation from the existing 5.4 results. No device
identifiers, identity hashes, SSH keys or raw private traces are in this patch.
