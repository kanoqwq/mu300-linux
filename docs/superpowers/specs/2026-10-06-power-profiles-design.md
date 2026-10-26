# Power profiles: what the device does with its battery

Date: 2026-10-06. Status: design approved in conversation, awaiting review of this document.

## Why

The U30 Air ran flat under Linux and came back in Android. Two causes, found on the board (FINDINGS 33, charger):

* Under 6.18/7.2 the charger IC (SGM41511, a bq25601) has no driver, and the state Android leaves in its registers
  persists: input limit 500 mA, charging disabled. The battery never charges under mainline, and drains while
  plugged in whenever the load passes 2.5 W. That is fixed separately, by the `bq256xx` port (PR in progress); this
  document assumes a charger that charges and reports.
* When the flat battery was plugged in, LK booted the Linux slot in `charger` mode. Full Linux on an empty battery
  behind a 500 mA input died before `mu300-boot-ok`, five times, and LK fell back to Android.

Beyond the bug, nothing in Linux spends the battery with care: the hotspot, the modem and the big cores run the same
with nobody connected as with ten clients. Android's idle advantage (the AP core sleeps) is out of reach for now - the
mainline build has no suspend support at all - so the savings have to come from turning things off when they do no
good, and turning them back on when someone wants them.

Measured on the board before this work (7.2.9, hotspot on 5 GHz, data up, VPN up, one client): 97 % idle, the cores in
cluster power-down 95 % of the time, no userspace wake-up source above 5/s. The software is already quiet; the radios
are what is left.

## What the user gets

* Three profiles - `plugged`, `battery`, `saver` - each a small set of knobs; `plugged` applies on external power,
  `battery` on the battery, `saver` by hand or when the battery falls under its threshold.
* With nobody connected (no Wi-Fi station, no computer on USB) for `wifi_idle` minutes the hotspot goes off, the
  modem does what the profile says (`keep`, `lte`, `off`), the LEDs go dark. The Wi-Fi key brings it all back; so
  does a computer on USB or the charger.
* A boot that LK started in charger mode stays a charging boot - radios off, LED showing charge - until the Wi-Fi key
  is pressed; an unplugged device under 5 % powers off cleanly instead of browning out.
* Charging stops outside 0-45 °C battery temperature and resumes with hysteresis; an optional charge limit (80 %)
  for a device that lives on the charger.
* The panel (openwrt-luci) gets System → Power: state, profile, knobs. Every system gets `mu300-power` on the
  command line, including `mu300-power log` for measurements.

The user's defaults: `battery` turns the hotspot off after 10 minutes, the modem fully off when idle, LEDs off when
idle; `plugged` turns nothing off.

## Pieces

### `mu300-power` (one script, `/opt/mu300/bin`, dash-compatible, Ubuntu and OpenWrt)

```
mu300-power status                  state, profile and why, battery, charger, idle timer
mu300-power profile [NAME|auto]     force a profile, or back to automatic (plugged/battery by supply)
mu300-power set PROFILE.KNOB VALUE  edit /etc/mu300/power.conf
mu300-power wake                    leave idle (the keys call it); restarts the idle timer
mu300-power idle                    enter idle now (testing; the panel's "sleep now")
mu300-power log [SECONDS] [FILE]    append time, V, I, W, %, temp, state, profile every SECONDS (default 5)
mu300-power daemon                  the state machine; procd on OpenWrt, systemd on Ubuntu
```

Configuration `/etc/mu300/power.conf`, kept across updates like the rest of `etc/mu300`, `KEY=VALUE` lines read with
`sed`, as `led.conf` is:

```
PROFILE=auto                  # auto | plugged | battery | saver
SAVER_BELOW=20                # % battery that switches auto to saver; 0 never
CHARGE_TO=100                 # 100 | 80: stop charging at this %
plugged_WIFI_IDLE=0           # minutes with no client before the hotspot goes off; 0 never
plugged_RADIO_IDLE=keep       # keep | lte | off: the modem while idle
plugged_LEDS_IDLE=on          # on | off
plugged_CPU=full              # full | eco: eco takes the big core offline and caps the middle cluster at 1.5 GHz
battery_WIFI_IDLE=10
battery_RADIO_IDLE=off
battery_LEDS_IDLE=off
battery_CPU=full
saver_WIFI_IDLE=5
saver_RADIO_IDLE=off
saver_LEDS_IDLE=off
saver_CPU=eco
```

Missing keys take these defaults, so an empty file is the user's configuration. Values are validated on `set` and
again on read (an unknown value counts as the default; the daemon never fails over a typo).

### Inputs

| what | where | note |
|---|---|---|
| external power | the charger's `power_supply/*/online`, else `extcon0` `USB=1` | the second is the pre-driver fallback and the F50 (no battery) |
| battery | `power_supply/sc27xx-fgu`: `capacity`, `temp`, `voltage_now`, `current_now` | F50: no node, the daemon runs with battery knobs inert |
| charger | the bq256xx node: `status`, `usb_type`, `input_current_limit`; charging enable | names fixed by the driver PR; the daemon reads whatever node has `type=USB`/`Mains` |
| Wi-Fi clients | `iw dev wlan0 station dump`, count of `Station` | AP interface only; the client interface (`wifi-client`) is not a reason to stay awake |
| computer on USB | `/sys/class/udc/*/state` = `configured` | a charger does not enumerate: `not attached`/`powered` |
| keys | `mu300-buttons` calls `mu300-power wake` | no second evdev reader |
| boot mode | `/run/mu300/boot-mode`, written by init from the DT's `chosen/bootargs` (`androidboot.mode=charger`) | init already reads that file for the slot |

### State machine (the daemon, one loop every 10 s plus the key events)

```
            keys / USB host / plug      ┌──────────┐
   ┌────────────────────────────────────│   idle   │
   │                                    └──────────┘
   ▼                                          ▲
┌────────┐  no station and no USB host        │
│ active │  for WIFI_IDLE minutes ─────────────┘
└────────┘
   ▲
   │ Wi-Fi key
┌───────────────┐   boot-mode=charger
│ charging-boot │ ◄── at start
└───────────────┘
```

* Profile selection runs every loop: forced profile, else `saver` when the battery is under `SAVER_BELOW` and
  unplugged, else `plugged`/`battery` by the supply. A profile change while idle re-applies the idle actions of the
  new profile; a change to `plugged` while idle wakes (the charger is a person).
* **active → idle** applies, in this order and each one logged: LEDs (`mu300-led idle on`, which is `wake`'s
  opposite and ignores `LED_TIMEOUT`), hotspot off (`wifi down` / `systemctl stop mu300-hotspot`, as
  `mu300-buttons` does), modem (`mobile-data suspend lte|off`, below), CPU (`eco`: `echo 0 > cpu7/online`,
  `policy4/scaling_max_freq`). `WIFI_IDLE=0` means the state is never entered.
* **idle → active** reverses in the opposite order and restarts the idle timer. The timer also restarts on every
  station seen and every `configured` UDC.
* **charging-boot**: entered only at daemon start when `boot-mode` is `charger`. Hotspot and modem stay down
  (`hotspot-start` and `mobile-data up` are not run by the boot scripts when the file says `charger`; the daemon
  does not fight them), LEDs show charge (`mu300-led charge`: the battery LED breathes while charging, steady when
  full). The Wi-Fi key → active, which starts both. Unplugged and under 5 % for three loops → `poweroff`
  (`systemctl poweroff` on Ubuntu). The 300 s rescue timer and `mu300-boot-ok` run as on any boot: a charging boot
  is a successful boot.
* **Temperature guard**: battery `temp` over 450 (0.1 °C) or under 0 writes charging off; back on under 400 /
  over 30. `CHARGE_TO=80`: charging off at 80 %, on again under 75 %. Both through the charger node's enable; when
  the driver has none (5.4, pre-driver mainline) the guard logs once and does nothing.
* Every transition is one `logger -t mu300-power` line with the reason; `status` prints the last one.

### `mobile-data suspend | resume`

`mobile-data watch` restores the radio and the connection whenever it finds them down; the daemon must be able to
take them down without a fight. `suspend lte` writes `/run/mu300/mobile-data.suspend=lte` and asks the modem for
LTE only (the vendor AT for the access-technology preference, found on the board; if there is none, `lte` behaves
as `keep` and says so once); `suspend off` writes `off` and turns the radio off (`AT+CFUN=4`, what the watcher's own
recovery uses in reverse). The watcher skips its rounds while the file exists. `resume` removes it, turns the radio
on and brings data up through the existing paths. Both are idempotent.

### init

One line in the slot block: `androidboot.mode=charger` in the DT's `chosen/bootargs` → `charger` in
`/run/mu300/boot-mode` (else `normal`) and a `stage=boot-mode mode=...` log line. The boot scripts of both systems
(`hotspot-start`, `mobile-data up` via their units) check the file and leave the radios down in a charging boot.

### OpenWrt's own button handling

`/etc/rc.button/power` in the OpenWrt images runs `poweroff` on release: a tap powers the device off, beside
`mu300-buttons`' 3 s hold. The image build replaces it with a no-op (`mu300-buttons` owns the keys), and the
`wps`/`rfkill` handlers likewise. Checked on the board: whether procd actually delivers these events (if it does
not, the replacement is still right, and costs nothing).

### `mu300-buttons`

Every key press calls `mu300-power wake` first (it returns at once when active). The existing meanings stay:
power short wakes the LEDs, power held 3 s shuts down, Wi-Fi short toggles the band, Wi-Fi held toggles the hotspot.
In idle or charging-boot the Wi-Fi key's first press only wakes (the hotspot comes back on the band it had); the
band toggle needs a second press. That is what "the Wi-Fi key turns Wi-Fi on" means on a device that is asleep.

### `mu300-led`

Two new verbs: `idle on|off` (all LEDs dark regardless of `LED_TIMEOUT`, and back) and `charge` (the battery LED's
charging pattern for the charging boot; `wake` ends it). Nothing else changes; the Wi-Fi and data LEDs keep their
meanings when awake.

### Panel: System → Power (openwrt-luci)

A LuCI view `power.js` and the rpcd methods it needs on `mu300dash` (`power_status`, `power_set`), with the same
input validation and JSON escaping as the existing methods (FINDINGS 35). Shows: state and reason, profile in use
and the selector (`auto`/forced), the three profiles' knobs as a table, `SAVER_BELOW`, `CHARGE_TO`, "sleep now"
and "wake". Strings go through the English source and the tr/zh catalogs (`tools/check-i18n.py`, `luci-i18n.py`);
the other languages follow the existing translation route. The home tile keeps the battery and watts.

### `mu300-power log` and the measurements

`log` appends `epoch,V,mA,W,capacity,temp,state,profile` to `/var/log/mu300-power.csv` (or a given file) every 5 s,
from the fgu; the sign of `current_now` is negative while discharging (checked on the board: -5 mA with the charger
idle). The R&D series on the U30 Air, unplugged, 3 minutes each, hotspot idle unless said: baseline as shipped;
LEDs off; hotspot off; modem LTE-only; modem off; everything off (the idle floor); `eco` CPU; Android idle for the
target line (read from Android's `dumpsys battery` + the fgu). The numbers go into FINDINGS and decide the defaults'
fine print (whether `eco` is worth its latency, whether `lte` is worth having).

## Error handling

* Every sysfs read is `cat 2>/dev/null` with a default; a missing node disables that input, never the daemon.
* The daemon never leaves the radios down on exit: `trap` on TERM/INT runs the wake sequence (an update restarting
  the service must not strand the user).
* The temperature guard fails safe: no reading → no charging change.
* `set` rejects unknown knobs and values and leaves the file untouched; `status` says when a key in the file was
  ignored.
* A hotspot or modem command that fails is logged and the state still changes; the next loop retries the action
  that failed (idempotent commands).

### Amendment after the device-test incident (2026-10-07, FINDINGS 36)

* A key always shows life: `mu300-buttons` runs `mu300-led wake` first on every press, and `mu300-led wake` lights
  the LEDs for `LED_TIMEOUT` (60 s when 0) under `idle on` too. The idle flag means "not unasked", not "never".
* `mu300-power wake` restores by inspection (hotspot, modem mode file, LED idle flag, `cpu7`/eco cap, the VPN it
  stopped) without the daemon, and is what the keys call (in the background); the daemon's wake is the same code.
* Every external step has a deadline; `mobile-data suspend|resume` end within ~60 s and leave the files consistent.
* `RADIO_IDLE=off` stops `mu300-vpn` while the modem is off and starts it again on the wake, if it was running.
* The daemon self-checks each loop: `active` with the modem held down, or (`WIFI_IDLE=0`) with the hotspot down that
  nobody turned off on purpose, is restored.

## Tests

Unit tests in `tests/` in the existing style (stubs on PATH, fake sysfs tree, every shell, no device):

* `test_power.py`: profile selection (forced, saver threshold, supply); knob defaults and validation; idle timer
  (stations reset it, UDC `configured` resets it, `WIFI_IDLE=0` never idles); the idle action order and its reverse;
  charging-boot entry only at start and only with the file; low-battery poweroff needs three loops and no supply;
  temperature and `CHARGE_TO` hysteresis; `log` line format and sign; the exit trap wakes.
* `test_static.py`: the OpenWrt images carry no `rc.button/power` that powers off; both systems' hotspot and data
  units check `boot-mode`; `power.conf` is in `mu300-update`'s kept set; `mu300-power` is on `path-commands`.
* `test_device_scripts.py` / `test_boot_init.py`: init writes `boot-mode` from a `chosen/bootargs` with and
  without `androidboot.mode=charger`.
* `test_mu300dash_security.py`: `power_set` rejects bad knobs/values and escapes output.
* `tools/check-i18n.py` and the LuCI catalog test pass.

On the U30 Air: the measurement series above; idle → wake by each of the three triggers; the charging boot
simulated (unplug, `poweroff`, plug: LK's charger-mode boot, LED pattern, Wi-Fi key); the temperature guard forced
with a fake `temp` through the daemon's test hook (`MU300_SYS=` root override, as other tools use `MU300_*`);
`mu300-power log` across a profile change. On the F50: the daemon runs with no battery and does nothing wrong.

## Out of scope

System suspend (no kernel support; a separate investigation), the charger driver itself (its own PR), the Wi-Fi
AP's own power saving (beacon/DTIM; measure first), a graph in the panel, per-client policies.
