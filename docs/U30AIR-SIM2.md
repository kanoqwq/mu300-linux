# U30 Air dual SIM (experimental)

A U30 Air has two SIM positions. OpenWrt on it can start either card at boot, and, when asked to, move the radio
from one to the other without a restart. Off by default: a device left alone boots SIM1 exactly as before.
Android, its partitions and the cards' identities are not touched. OpenWrt on the U30 Air only; on an F50 the
selector is the boot-time one of `docs/SIM-SELECTION.md`, and everywhere else nothing of this runs.

What was measured, on which unit, and why it works the way it does: `docs/FINDINGS.md` 74.

## Use the second card

Set SIM2's APN first (SIM1 keeps `network.wan.apn` / `pdptype`; SIM2 has its own pair), then choose it and reboot:

```sh
uci set network.wan.apn_internal='cmnet'          # your SIM2 carrier's APN
uci set network.wan.pdptype_internal='IPV4V6'
uci commit network
mu300-sim default 2
reboot
```

The same from the panel: Network > Interfaces > WAN (**SIM source**, the card's APN), or **Cellular > Dual SIM**
(**Use SIM2 at next boot**). `mu300-sim default 1` and a reboot go back.

About 25 s into the boot the radio is on SIM2 and netifd dials it on `sipa_eth8`. `mu300-sim status --json` says
where things are (`active`, `default`, `fallback`, `boot_check`, `wan_up`); the page shows the same.

**The first boot onto SIM2 is a trial.** A SIM2 that has never had mobile data under Linux gets five minutes from
its radio coming on. With data it is remembered and never tried again: later outages change nothing. Without, the
device goes back to SIM1 by itself: the settings are put back, the reason (`no-data`) is kept for the page, and
the device restarts once. That is deliberate. A card chosen from far away, over the device's own mobile data, that
registers and never gets a bearer would otherwise leave the device unreachable until somebody walks up to it.
While it waits the page counts the seconds down.

* More time: `uci set network.wan.sim_data_wait='900'; uci commit network` (seconds).
* No trial at all (stay on SIM2 whatever happens, for a card you know or a test at the desk): `sim_data_wait '0'`.
* No automatic restart anywhere in this feature: `uci set network.wan.sim_restart='0'`. The page then says
  "reboot required" where it would have restarted.

## When the second card does not start

Nothing here needs a hand on the device: every case ends on SIM1 with the modem under control. The reason stays on
the page (and in `status --json` as `fallback`) until you choose again.

| reason | what happened | where the device is |
|---|---|---|
| `no-sim` | no card answered in that position, with its SIM power on | on SIM1, in the same boot |
| `sim-pin` | the card is locked by its PIN (nothing here enters a PIN: unlock it in Android or a phone) | on SIM1, in the same boot |
| `wrong-card` | `/etc/mu300/sim2-iccid` names another card | on SIM1, in the same boot |
| `brokers-missing` | the second group of modem channels never became ready | on SIM1, in the same boot (about two minutes in) |
| `no-radio`, `data-route`, `modem-refused` | the modem was told to start SIM2 and did not | restarted into SIM1 |
| `no-data` | the trial above ran out | restarted into SIM1 |
| `unconfirmed` | the boot before never finished its start (power lost, a crash) | this boot is SIM1's |

In every case the settings are SIM1 without live switching again. Logs: `/run/mu300/sim2-cold-start.log` (every
command sent, and why it stopped; no answer of the modem is ever written there), `logread -e mu300-sim`.

## Live switching

```sh
mu300-sim hot 1       # then reboot once: both groups of channels are opened at boot
mu300-sim switch 2    # now; saved as the boot default once SIM2 has mobile data
mu300-sim switch 1
mu300-sim hot 0       # no more live switches
```

Or the checkbox and the two **Switch to** buttons on the Dual SIM page. A switch takes about four seconds of radio
and then waits up to a minute for the target's data.

* Before anything is touched, the target is asked whether a card is there (its SIM power is switched on for the
  question). No card, or a PIN: refused in a second, the connection never interrupted.
* The target gets data: it is saved as the boot default.
* The target gets no data and the card you came from had data: back to that card, default unchanged (about 70 s
  without data in all).
* The target gets no data and the card you came from had none either: the device stays on the target, which keeps
  dialling; the boot default is not changed. `mu300-sim switch <same card>` saves it once it has data, or
  `mu300-sim default <card>` saves it regardless.
* Mobile data switched off (the panel's button, `ifdown wan`, the data cap): it stays off. The card is switched and
  saved as the boot default without waiting for data, and nothing dials it, on the target or on the way back after
  a failure ([FINDINGS](FINDINGS.md) 104).
* Something breaks half way: one way back is tried. If that cannot be verified, or the switch is killed, the radio
  state is unknown: the device restarts into its saved boot default, which a failed switch never changes.

`mu300-sim default 1|2` only saves the card for the next boot. It needs no live switching, moves no radio and does
not care whether that card has data now.

While a switch runs, SMS, USSD, the AT terminal and the Locks page answer "busy". In a boot with live switching on
(or with SIM2 chosen) the Locks page cannot write, the modem reset and the power profile's modem suspend are
refused, and the panel's collectors share the command channel of the card in use. The locks already in the modem
stay in force. Voice, USSD and parts of the IPv6 relay still assume SIM1; use IPv4 on SIM2 until they are adapted.

## How it works

| card | URC / command / data channel | bearer |
| --- | --- | --- |
| SIM1 (slot 0) | nr0 / nr1 / nr2 | sipa_eth0 |
| SIM2 (slot 1) | nr3 / nr4 / nr5 | sipa_eth8 |

`network.wan.sim_slot` (0/1) is the boot's card and `network.wan.sim_hot` (0/1) live switching; a pre-release's
`/etc/mu300-sim-slot` and `-hot` are taken over once into options that are not set and kept under
`/etc/mu300/sim-migration/`. Every reader uses this boot's pin, `/run/mu300/sim-slot`, never the saved choice.

With SIM2 chosen or live switching on, `init.d/mu300-atd` starts an owner for each of nr1, nr2, nr4 and nr5. They
wait (inside their procd instances, 240 s at most) for `sbuf_5_006, state: 1,` in `/dev/sipc_sbuf`: the second
group's nodes exist before the modem is ready and an open before that fails. They are never restarted and never
reopen a channel: a reopened SIPC channel is a wedged one. `/etc/mu300-sim-pending` is left before any of it and
removed when the radio is up: a boot that never gets there makes the next one a SIM1 boot.

The cold start (`mu300-sim2-start`, under `mobile-data`'s radio lock, once per boot):

```text
nr1                 AT+SMMSWAP=0                  the RIL handshake, as every boot
nr1, nr4            AT+CFUN?                      both must be off
the card's command  AT+CPIN?                      "+CME ERROR: 10" is also what a position says whose SIM power
                    AT+SPACTCARD=<n>;+SFUN=2      is off: switched on, then asked again
                    AT+CCID                       compared with /etc/mu300/sim<n>-iccid if there is one; never logged
nr1                 AT+SPTESTMODE?                the work modes, read ...
the card's data     AT+SPTESTMODEM=<m0>,<m1>      ... and written back as they are
                    AT+SPSWDATA                   the packet-data route to this card
the card's command  AT+SPSWDATA?                  read back
the card's data     AT+SFUN=4                     the radio; +CFUN: 1 is "ready"
```

Registration and the bearer are netifd's, as for SIM1.

The live switch (`mu300-sim-u30`, one at a time under a kernel lock, 300 s for the whole of it):

```text
target's command    AT+SPACTCARD=<t>;+SFUN=2, AT+CPIN?     is a card there? (else SFUN=3 and refused)
                    ifdown wan
old card's command  AT+SPACTCARD=<o>;+SFUN=5, ;+SFUN=3     the card in use off
target's command    AT+SPACTCARD=<t>;+SFUN=2, AT+CPIN?
target's data       AT+SPSWDATA, AT+SPSWDATA? (command)    the route, read back
                    AT+SFUN=4
                    ifup wan, and up to 60 s for an address on the target's bearer
```

The card in use goes off before the other one comes on. The factory's order is the reverse, and it worked on the
unit this was first written on; on the maintainer's unit the second radio would not come on beside the first.

## Limits

* Experimental. Measured on two U30 Airs with Linux 7.2 (7.2.9, 7.2.10). On neither did both cards have data.
* Kernel 5.4 was not run. The readiness line comes from the same vendor driver; if it reads differently there, the
  boot ends on SIM1 (`brokers-missing`).
* A modem crash needs a restart; nothing reloads the modem.
* SMS (the pool, sending, forwarding) still uses SIM1's channels (`/run/mu300-at`): with SIM2 as the card in use,
  messages for SIM2 are not collected and none can be sent. Not adapted yet.
* A charging-only boot leaves both radios off.
* `mu300-update` keeps the settings (they are in `/etc/config/network` and `/etc/mu300`).

## Records

**2026-10-13, the maintainer's U30 Air** (OpenWrt 25.12.5, v2026.10.23, Linux 7.2.10; SIM1 with data, a second card
that registers for emergency service only). The default boot unchanged; the SIM2 cold start (radio ready at
24.2 s); the trial running out and the restart into SIM1; the readiness line made unreadable and the same-boot way
back; the live switch in the factory's order failing and in the new order working both ways; a switch to the card
without data and back; a switch with no data on either side; the controller killed in the middle. Times and the
modem's answers: `docs/FINDINGS.md` 74. Never seen there: data on `sipa_eth8`.

**2026-10-12, the contributor's U30 Air** (Snrat; OpenWrt 25.12.5, Linux 7.2.9; SIM1 without data service, SIM2
working), with the revision before the changes above (the factory's switch order, no trial, no automatic restart):

- With SIM2 and its WAN up on `sipa_eth8`, live switching off, `default 1` saved SIM1 without changing the active
  card or interrupting the WAN.
- Two boot trials exposed startup defects: an empty optional shell expansion generated
  `until [ -c /dev/stty_nr1 ]}`, and after that fix the first group's owners opened before the modem's shared
  buffer was ready and exited, leaving stale fifos. Since then all four owners wait for the readiness line and the
  warm-up requires a living owner, not just its fifo.
- A fresh Linux boot initialized SIM2, `CFUN=1` at about 21 s; WAN came up on `sipa_eth8`.
- `switch 1` reached SIM1's radio but no data service; after the wait one rollback restored SIM2 and its WAN
  (`no_data`), the saved default still SIM2, all four owner PIDs unchanged.
- `switch 2` from an offline SIM1 boot restored the WAN on `sipa_eth8` (`switched`), saved SIM2, and the next Linux
  boot came up on SIM2 again. A certificate-verified HTTPS request from a USB LAN client returned 200.
- `default 1` and a reboot initialized SIM1 at about 22 s; at 65 s the radio was ready, the WAN down, no fallback:
  no data did not invalidate the boot.

Not tested on that unit: a power-disconnect cycle, a fresh image, an empty position, a PIN-locked card.
