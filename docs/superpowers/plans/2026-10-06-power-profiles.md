# Power Profiles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `mu300-power` tool and daemon that turns the hotspot, the modem, the LEDs and the big core off when nobody uses the device and back on when someone does, by profile (`plugged`/`battery`/`saver`), with a charging boot, a battery temperature guard and a LuCI page.

**Architecture:** One dash-compatible script `/opt/mu300/bin/mu300-power` (library functions + subcommands + `daemon` loop) shared by Ubuntu (systemd) and OpenWrt (procd), reading sysfs and calling the existing tools (`mobile-data`, `mu300-led`, `wifi`/`systemctl`). `mu300-buttons` feeds it key presses; init tells it the boot mode. The panel talks to it through an rpcd adapter (`unisoc-modem/power`) and two `mu300dash` methods.

**Tech Stack:** POSIX sh (dash, bash, busybox ash), Python unittest with stubs (`tests/helpers.py`), LuCI JS views, rpcd shell plugin, `tools/check-i18n.py` / `tools/luci-i18n.py`.

**Spec:** `docs/superpowers/specs/2026-10-06-power-profiles-design.md`

## Global Constraints

- Every shell script runs under dash, bash and busybox ash; tests use `ShellTest.each_shell()`; no bashisms in `mu300-power` (it is `#!/bin/sh`).
- Every path a script reads is under `$R` (`MU300_SYSROOT`), `/run` under `MU300_RUN`, config under `MU300_POWER_CONF`, so tests run against a fake tree; tools called are found on `PATH` (stubs).
- Config format: `KEY=VALUE` lines read with `sed -n 's/^KEY=\(...\)$/\1/p' | tail -n1`, as `mu300-led` reads `led.conf`.
- Defaults (spec): `PROFILE=auto SAVER_BELOW=20 CHARGE_TO=100`; `plugged`: `WIFI_IDLE=0 RADIO_IDLE=keep LEDS_IDLE=on CPU=full`; `battery`: `10 off off full`; `saver`: `5 off off eco`.
- Charging boot: low battery poweroff only there, only unplugged, only under 5 % for three loops.
- Temperature guard: charging off at `temp` > 450 or < 0 (0.1 °C), back on at < 400 and > 30; `CHARGE_TO=80`: off at ≥ 80 %, on at < 75 %. No reading → no change.
- Radio off is `AT+SFUN=5`, on is `mobile-data radio-on` (`AT+SFUN=4` + the existing waits); LTE-only is `AT+SPENDC=0` and back `AT+SPENDC=1` (the lock adapter's commands), applied with the stack restart `AT+SFUN=5` / `AT+SFUN=4`.
- Every user-visible LuCI string has tr and zh_Hans lines; `python3 tools/check-i18n.py` and `tests/test_luci_i18n.py` pass.
- Commit messages: one sentence of what and why, ending with the two attribution lines of this session.
- Never print or change `/etc/mu300/vpn.conf`; never flash a device from a task.

## Review Focus

1. A fuel gauge that reports `capacity=100` at 3.88 V (today's mainline): `SAVER_BELOW` and the low-battery poweroff must act on `capacity` only and the poweroff must need three consecutive loops — a one-sample glitch must not shut the device down (Task 6 tests `test_low_battery_needs_three_unplugged_loops`).
2. `iw dev wlan0 station dump` failing (no wlan0 while the hotspot is down) must count as zero stations, not as an error that stops the loop (Task 5 `test_station_dump_failure_is_zero_stations`).
3. The daemon killed while idle (an update restarts the service) must bring the radios back (Task 5 `test_exit_trap_wakes`).
4. `power.conf` with a typo (`battery_WIFI_IDLE=ten`) must behave as the default and `status` must say so (Task 4 `test_bad_value_is_default_and_reported`).
5. A charging boot that is then plugged into a computer must still stay a charging boot until the Wi-Fi key (USB host does not wake it: the spec's only exit is the key) — but an unplug must not power it off above 5 % (Task 6 `test_charging_boot_exits_only_on_key`).

---

## File structure

| file | responsibility |
|---|---|
| `boot/init` (slot block) | writes `/run/mu300/boot-mode` |
| `rootfs/overlay/opt/mu300/bin/mobile-data` | `suspend lte\|off`, `resume` |
| `rootfs/overlay/opt/mu300/bin/mu300-led` | `idle on\|off`, `charge on\|off` |
| `rootfs/overlay/opt/mu300/bin/mu300-power` | new: config, inputs, actions, state machine, `log`, CLI |
| `rootfs/overlay/opt/mu300/bin/mu300-buttons` | calls `mu300-power wake` |
| `rootfs/overlay/etc/systemd/system/mu300-power.service`, `openwrt/overlay/etc/init.d/mu300-power` | run the daemon |
| `rootfs/overlay/etc/systemd/system/mu300-hotspot.service`, `mu300-mobile-data.service` | `ConditionPathExists=!/run/mu300/charging-boot` |
| `rootfs/overlay/opt/mu300/lib/path-commands` | `mu300-power` on PATH |
| `openwrt/build-rootfs.sh` | neutralise `/etc/rc.button/power`, `wps`, `rfkill` |
| `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power` | rpcd adapter: `get` → JSON, `set KEY VALUE`, `wake`, `idle` |
| `openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash` | `power_get`, `power_set` |
| `openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js`, `mu300/common.js`, `menu.d`, `acl.d`, `po/` | the page |
| `tests/test_power.py` (new), `test_boot_init.py`, `test_device_scripts.py`, `test_static.py`, `test_mu300dash_security.py` | tests |
| `docs/FINDINGS.md` | section 36: power profiles and the measurements |

---

### Task 1: init writes the boot mode

**Files:**
- Modify: `boot/init` (inside `# --- slot begin` … `# --- slot end`, function `publish_slot`)
- Test: `tests/test_boot_init.py` (class `Slot`)

**Interfaces:**
- Produces: `$MU300_RUN/mu300/boot-mode` containing `charger` or `normal`; log line `stage=boot-mode mode=<mode> source=<file|default>`.

- [ ] **Step 1: Write the failing test**

In `tests/test_boot_init.py`, class `Slot`, after `test_cmdline_names_the_slot`:

```python
    def test_boot_mode_from_lk_bootargs(self):
        # LK's "charger" boot (a flat battery plugged in) opens our slot too: init says so for mu300-power
        for shell in self.each_shell():
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0 loglevel=3',
                                          bootargs='androidboot.slot_suffix=_b androidboot.mode=charger')
            self.assertEqual((run / 'boot-mode').read_text().strip(), 'charger')
            self.assertIn('stage=boot-mode mode=charger', log)
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0',
                                          bootargs='androidboot.slot_suffix=_b androidboot.mode=normal')
            self.assertEqual((run / 'boot-mode').read_text().strip(), 'normal')
            out, log, run = self.run_slot(shell, cmdline='root=/dev/ram0', bootargs=None)
            self.assertEqual((run / 'boot-mode').read_text().strip(), 'normal')
            self.assertIn('stage=boot-mode mode=normal source=default', log)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `cd tests && python3 -m unittest test_boot_init.Slot.test_boot_mode_from_lk_bootargs -v`
Expected: FAIL (`boot-mode` does not exist).

- [ ] **Step 3: Implement in `publish_slot`**

Add before the closing `}` of `publish_slot` in `boot/init`:

```sh
    # LK's boot mode, for mu300-power: "charger" is a flat battery plugged in (LK opens our slot in that mode too)
    _mode= _msrc=default
    for _f in ${MU300_CMDLINE_SRC:-/proc/cmdline /proc/device-tree/chosen/bootargs}; do
        [ -r "$_f" ] || continue
        _mode=$(tr '\000' ' ' < "$_f" | sed -n 's/.*androidboot\.mode=\([a-z]*\).*/\1/p')
        [ -n "$_mode" ] && { _msrc=$_f; break; }
    done
    case $_mode in charger) ;; *) _mode=normal ;; esac
    echo "$_mode" > "$_r/boot-mode"
    log "stage=boot-mode mode=$_mode source=$_msrc"
```

- [ ] **Step 4: Run the test and the whole init suite**

Run: `cd tests && python3 -m unittest test_boot_init -v 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add boot/init tests/test_boot_init.py
git commit -m "init: publish LK's boot mode (charger or normal) for mu300-power"
```

---

### Task 2: `mobile-data suspend | resume`

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mobile-data` (new functions before `watch()`, dispatch at the bottom `case`, the watch loop's skip line)
- Test: `tests/test_device_scripts.py` (the existing mobile-data harness: see the class whose docstring says "/run is the scratch directory's run/; mu300-led and mu300-at are stubs on PATH", around line 413)

**Interfaces:**
- Produces: `mobile-data suspend lte|off` (idempotent; writes `/run/mu300-mobile-data.suspend` with the mode), `mobile-data resume`; `watch` skips its rounds while the file exists.
- Consumes: the script's own `at CMD [TIMEOUT]`, `down`, `up`, `radio_on` functions.

- [ ] **Step 1: Write the failing tests**

Add to the mobile-data test class in `tests/test_device_scripts.py` (use its existing helper that runs the script with a stub `mu300-at` logging to `$STUBLOG/at.log` — read the class first and reuse its names):

```python
    def test_suspend_off_takes_the_radio_down_and_stops_the_watcher(self):
        for shell in self.each_shell():
            self.prepare_mobile_data()
            r = self.run_mobile_data(shell, 'suspend off')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual((self.run_dir / 'mu300-mobile-data.suspend').read_text().strip(), 'off')
            self.assertTrue((self.run_dir / 'mu300-mobile-data-down').exists())
            self.assertIn('AT+SFUN=5', self.at_log())
            # a second call does nothing more
            n = self.at_log().count('AT+SFUN=5')
            self.run_mobile_data(shell, 'suspend off')
            self.assertEqual(self.at_log().count('AT+SFUN=5'), n)

    def test_suspend_lte_switches_endc_off_and_resume_restores(self):
        for shell in self.each_shell():
            self.prepare_mobile_data()
            self.run_mobile_data(shell, 'suspend lte')
            self.assertEqual((self.run_dir / 'mu300-mobile-data.suspend').read_text().strip(), 'lte')
            self.assertIn('AT+SPENDC=0', self.at_log())
            self.assertFalse((self.run_dir / 'mu300-mobile-data-down').exists())  # data stays up on LTE
            self.run_mobile_data(shell, 'resume')
            self.assertFalse((self.run_dir / 'mu300-mobile-data.suspend').exists())
            self.assertIn('AT+SPENDC=1', self.at_log())

    def test_watch_skips_rounds_while_suspended(self):
        for shell in self.each_shell():
            self.prepare_mobile_data()
            (self.run_dir / 'mu300-mobile-data.suspend').write_text('off\n')
            r = self.run_mobile_data(shell, 'watch', MU300_WATCH_INTERVAL='0', MU300_WATCH_ROUNDS='2')
            self.assertNotIn('AT+CGACT?', self.at_log())
```

`MU300_WATCH_ROUNDS` is new: the loop exits after that many rounds when set (tests only). If the class has no `prepare_mobile_data`/`run_mobile_data`/`at_log` helpers, add them in the same style as the class's existing setup (stub `mu300-at` appends its argument to `$STUBLOG/at.log` and prints `OK`; `ip`, `mu300-led`, `logger`, `sleep` are no-op stubs; `/run/` is rewritten to the scratch run dir as the class already does).

- [ ] **Step 2: Run them to make sure they fail**

Run: `cd tests && python3 -m unittest test_device_scripts -k suspend -k watch_skips -v`
Expected: FAIL (`suspend: unknown command` or similar).

- [ ] **Step 3: Implement**

In `mobile-data`, before `watch()`:

```bash
# mu300-power takes the modem down while nobody uses the device; watch must not fight it. The mode file says how:
#   off  radio off (AT+SFUN=5), data down
#   lte  5G NSA off (AT+SPENDC=0, the lock adapter's switch) with the stack restarted; data stays up
# resume undoes it. Both are idempotent: a second call finds the file and does nothing.
SUSPEND=/run/mu300-mobile-data.suspend
suspend() {
    local mode=${1:-off}
    case $mode in off|lte) ;; *) echo "usage: mobile-data suspend off|lte" >&2; return 2 ;; esac
    [ "$(cat "$SUSPEND" 2>/dev/null)" = "$mode" ] && return 0
    if [ -s "$SUSPEND" ]; then resume; fi
    echo "$mode" > "$SUSPEND"
    case $mode in
        off) down || true
             at 'AT+SFUN=5' 10 >/dev/null || true ;;
        lte) at 'AT+SPENDC=0' 5 >/dev/null || true
             at 'AT+SFUN=5' 10 >/dev/null || true
             at 'AT+SFUN=4' 30 >/dev/null || true ;;
    esac
    echo "modem suspended ($mode)" >&2
}
resume() {
    local mode
    mode=$(cat "$SUSPEND" 2>/dev/null) || return 0
    rm -f "$SUSPEND"
    case $mode in
        off) radio_on || true
             rm -f /run/mu300-mobile-data-down ;;   # the watcher reconnects on its next round
        lte) at 'AT+SPENDC=1' 5 >/dev/null || true
             at 'AT+SFUN=5' 10 >/dev/null || true
             at 'AT+SFUN=4' 30 >/dev/null || true ;;
    esac
    echo "modem resumed (was $mode)" >&2
}
```

In `watch()`'s loop, replace `[ -e /run/mu300-mobile-data-down ] && continue` with:

```bash
        [ -e /run/mu300-mobile-data-down ] && continue
        [ -s "$SUSPEND" ] && continue   # mu300-power holds the modem down; nothing to restore
```

and after `sleep "${MU300_WATCH_INTERVAL:-30}"` add the test-only round counter:

```bash
        if [ -n "${MU300_WATCH_ROUNDS:-}" ]; then
            rounds=$((${rounds:-0} + 1)); [ "$rounds" -gt "$MU300_WATCH_ROUNDS" ] && break
        fi
```

(declare `local rounds=0` beside `local fails=0`). In the bottom `case`, add `suspend) suspend "${2:-off}" ;;` and `resume) resume ;;`, and extend the usage comment at the top of the file with the two commands. Note the `down` function also touches `/run/mu300-mobile-data-down`; `resume off` removes it so the watcher's next round reconnects — or call `up` directly if the class's tests show `up` is cheap under the stubs; keep the watcher route (it already handles a slow radio).

- [ ] **Step 4: Run the tests**

Run: `cd tests && python3 -m unittest test_device_scripts -v 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mobile-data tests/test_device_scripts.py
git commit -m "mobile-data: suspend (radio off or LTE only) and resume, which the watcher respects"
```

---

### Task 3: `mu300-led idle` and `charge`

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-led` (usage comment, the `case "$1 ${2:-}"` dispatch, `show_all`/`wake`)
- Test: `tests/test_device_scripts.py` (the mu300-led tests; the class runs the script with `MU300_SYSROOT` and a fake `/sys/class/leds`)

**Interfaces:**
- Produces: `mu300-led idle on` (all LEDs dark, `wake` and `set_led` stay dark until `idle off`), `mu300-led idle off` (back to what each LED should show), `mu300-led charge on` (the power LED blinks 1 s/1 s via the `timer` trigger), `mu300-led charge off`.
- State file: `$STATE/.idle`.

- [ ] **Step 1: Write the failing tests**

```python
    def test_idle_darkens_everything_and_wake_cannot_undo_it(self):
        for shell in self.each_shell():
            self.fake_leds('u30air', 'sc27xx:green', 'net_blue', 'zte-ldo2')
            self.led(shell, 'power on'); self.led(shell, 'data on')
            self.led(shell, 'idle on')
            self.assertEqual(self.brightness('sc27xx:green'), '0')
            self.assertEqual(self.brightness('net_blue'), '0')
            self.led(shell, 'wake')
            self.assertEqual(self.brightness('net_blue'), '0')   # still idle
            self.led(shell, 'data 5g')                           # a state change while idle is remembered, not shown
            self.assertEqual(self.brightness('net_blue'), '0')
            self.led(shell, 'idle off')
            self.assertEqual(self.brightness('sc27xx:green'), self.max_brightness('sc27xx:green'))

    def test_charge_blinks_the_power_led(self):
        for shell in self.each_shell():
            self.fake_leds('u30air', 'sc27xx:green')
            self.led(shell, 'charge on')
            self.assertEqual(self.read_led('sc27xx:green', 'trigger'), 'timer')
            self.assertEqual(self.read_led('sc27xx:green', 'delay_on'), '1000')
            self.led(shell, 'charge off')
            self.assertEqual(self.read_led('sc27xx:green', 'trigger'), 'none')
```

Reuse or add the class helpers `fake_leds(device, *names)` (creates `$R/sys/class/leds/NAME/{brightness,max_brightness=255,trigger}` and sets `MU300_DEVICE`), `led(shell, args)`, `brightness(name)`, `max_brightness(name)`, `read_led(name, file)`; follow the existing mu300-led tests in the file for the exact environment (device name comes from `mu300-device`, which the tests stub).

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_device_scripts -k idle -k charge -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `mu300-led`: `set_led` records the wanted state in `$STATE/NAME` and writes the LED (read the function). Add an idle check so the write is skipped while idle:

```sh
idle() { [ -e "$STATE/.idle" ]; }
```

and in `set_led` (where it writes), guard the `write` call: `idle || write "$1" "$2"` (keep recording the state). In `show_all` (what `wake` and `idle off` call to show the recorded states) add at the top `idle && return 0`. New dispatch entries:

```sh
    "idle on")
        mkdir -p "$STATE" 2>/dev/null
        touch "$STATE/.idle"
        for f in "$STATE"/*; do [ -f "$f" ] && write "${f##*/}" 0; done
        write "$POWER" 0 ;;
    "idle off")
        rm -f "$STATE/.idle"
        show_all ;;
    "charge on")
        [ -n "$POWER" ] && [ -w "$LEDS/$POWER/trigger" ] || exit 0
        echo timer > "$LEDS/$POWER/trigger" 2>/dev/null
        echo 1000 > "$LEDS/$POWER/delay_on" 2>/dev/null
        echo 1000 > "$LEDS/$POWER/delay_off" 2>/dev/null ;;
    "charge off")
        [ -n "$POWER" ] || exit 0
        echo none > "$LEDS/$POWER/trigger" 2>/dev/null
        write "$POWER" 0; idle || show_all ;;
```

Add the two verbs to the usage comment at the top (`mu300-led idle on|off`: mu300-power's idle, everything dark whatever LED_TIMEOUT says; `mu300-led charge on|off`: the charging boot, the power LED blinks). The fake sysfs in tests needs `delay_on`/`delay_off` files created when `trigger` is written? No: `echo 1000 > delay_on` creates the file in the fake tree; the test reads it.

- [ ] **Step 4: Run the device-script tests**

Run: `cd tests && python3 -m unittest test_device_scripts -v 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-led tests/test_device_scripts.py
git commit -m "mu300-led: idle (dark until told otherwise) and charge (blinking power LED) for mu300-power"
```

---

### Task 4: `mu300-power` configuration, `status`, `set`, `profile`

**Files:**
- Create: `rootfs/overlay/opt/mu300/bin/mu300-power` (mode 755)
- Create: `tests/test_power.py`

**Interfaces:**
- Produces (shell functions, all in the one file; later tasks add to it):
  - `conf_get KEY DEFAULT` → value from `$CONF` or DEFAULT; `knob PROFILE KNOB` → validated value (`WIFI_IDLE` digits, `RADIO_IDLE` keep|lte|off, `LEDS_IDLE` on|off, `CPU` full|eco), invalid → default + the key appended to `$RUN/power/ignored`.
  - `conf_set KEY VALUE` → validates, rewrites the file (replace the line or append), exit 2 on a bad key/value with a message, file untouched.
  - `pick_profile` → `plugged|battery|saver` from `PROFILE`, supply, capacity, `SAVER_BELOW`.
  - CLI: `mu300-power status`, `mu300-power set PROFILE.KNOB VALUE | set KEY VALUE`, `mu300-power profile [NAME|auto]`.
- Environment: `R=${MU300_SYSROOT:-}`, `RUN=${MU300_RUN:-/run}/mu300`, `CONF=${MU300_POWER_CONF:-/etc/mu300/power.conf}`.
- Inputs (shell functions): `plugged` (true when a `power_supply` of type `USB` or `Mains` has `online=1`, else when any `$R/sys/class/extcon/*/state` has a line `USB=1`), `battery_dir` (the first `power_supply` of type `Battery` with `present` = 1 or no `present` file; empty when none), `capacity` (its `capacity`, empty without), `battery_temp`.

- [ ] **Step 1: Write the failing tests**

`tests/test_power.py`:

```python
"""mu300-power: profiles, idle radios and the charging boot, against a fake / (MU300_SYSROOT), a fake /run and
stub commands. The spec is docs/superpowers/specs/2026-10-06-power-profiles-design.md."""
import os
import unittest

from helpers import ShellTest, BIN

POWER = BIN / 'mu300-power'


class PowerTest(ShellTest):
    def setUp(self):
        super().setUp()
        self.root = self.tmp / 'root'
        self.run_dir = self.tmp / 'run'
        (self.run_dir / 'mu300').mkdir(parents=True)
        self.conf = self.tmp / 'power.conf'
        for name in ('iw', 'wifi', 'systemctl', 'mobile-data', 'mu300-led', 'logger', 'poweroff'):
            self.stub(name, 'echo "$(basename "$0") $*" >> "$STUBLOG/calls"')
        self.stub('iw', 'echo "iw $*" >> "$STUBLOG/calls"; cat "$STUBLOG/stations" 2>/dev/null')
        self.stub('mu300-device', 'echo u30air')

    # --- the fake device
    def psy(self, name, **files):
        d = self.root / 'sys/class/power_supply' / name
        d.mkdir(parents=True, exist_ok=True)
        for k, v in files.items():
            (d / k).write_text(f'{v}\n')
        return d

    def battery(self, capacity=64, temp=281, status='Discharging', current=-470000):
        return self.psy('sc27xx-fgu', type='Battery', present=1, capacity=capacity, temp=temp, status=status,
                        voltage_now=3850000, current_now=current)

    def charger(self, online=1, usb_type='SDP', charge_type='Fast'):
        return self.psy('bq256xx-charger', type='USB', online=online, usb_type=usb_type, charge_type=charge_type)

    def write_conf(self, text):
        self.conf.write_text(text)

    def calls(self):
        p = self.tmp / 'calls'
        return p.read_text() if p.exists() else ''

    def power(self, shell, args, **env):
        return self.sh(shell, f'"{POWER}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_POWER_CONF=self.conf, MU300_POWER_INTERVAL=0, **env)


class Config(PowerTest):
    def test_defaults_without_a_file(self):
        self.battery()
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('profile: battery (auto)', r.stdout)
            self.assertIn('battery: WIFI_IDLE=10 RADIO_IDLE=off LEDS_IDLE=off CPU=full', r.stdout)
            self.assertIn('plugged: WIFI_IDLE=0 RADIO_IDLE=keep LEDS_IDLE=on CPU=full', r.stdout)
            self.assertIn('saver: WIFI_IDLE=5 RADIO_IDLE=off LEDS_IDLE=off CPU=eco', r.stdout)

    def test_set_rewrites_one_key_and_validates(self):
        for shell in self.each_shell():
            self.write_conf('PROFILE=auto\nbattery_WIFI_IDLE=10\n')
            r = self.power(shell, 'set battery.WIFI_IDLE 15')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\n')
            r = self.power(shell, 'set saver.CPU eco')
            self.assertEqual(self.conf.read_text(), 'PROFILE=auto\nbattery_WIFI_IDLE=15\nsaver_CPU=eco\n')
            r = self.power(shell, 'set battery.RADIO_IDLE sometimes')
            self.assertEqual(r.returncode, 2)
            self.assertIn('RADIO_IDLE', r.stderr)
            self.assertNotIn('sometimes', self.conf.read_text())
            r = self.power(shell, 'set battery.COLOUR red')
            self.assertEqual(r.returncode, 2)
            r = self.power(shell, 'set CHARGE_TO 80')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('CHARGE_TO=80\n', self.conf.read_text())
            r = self.power(shell, 'set CHARGE_TO 90')
            self.assertEqual(r.returncode, 2)

    def test_bad_value_is_default_and_reported(self):
        self.battery()
        for shell in self.each_shell():
            self.write_conf('battery_WIFI_IDLE=ten\nSAVER_BELOW=abc\n')
            r = self.power(shell, 'status')
            self.assertIn('battery: WIFI_IDLE=10', r.stdout)
            self.assertIn('ignored in power.conf: SAVER_BELOW battery_WIFI_IDLE', r.stdout)

    def test_profile_selection(self):
        for shell in self.each_shell():
            self.battery(capacity=50)
            self.write_conf('')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            self.charger(online=1)
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            self.charger(online=0)
            self.battery(capacity=15)
            self.assertIn('profile: saver (auto, battery under 20 %)', self.power(shell, 'status').stdout)
            self.write_conf('SAVER_BELOW=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)
            r = self.power(shell, 'profile plugged')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('PROFILE=plugged\n', self.conf.read_text())
            self.assertIn('profile: plugged (forced)', self.power(shell, 'status').stdout)
            self.power(shell, 'profile auto')
            self.assertIn('(auto', self.power(shell, 'status').stdout)
            self.assertEqual(self.power(shell, 'profile loud').returncode, 2)

    def test_plugged_falls_back_to_extcon_without_a_charger_node(self):
        for shell in self.each_shell():
            self.battery()
            e = self.root / 'sys/class/extcon/extcon0'
            e.mkdir(parents=True)
            (e / 'state').write_text('USB=1\nUSB-HOST=0\n')
            self.assertIn('profile: plugged (auto)', self.power(shell, 'status').stdout)
            (e / 'state').write_text('USB=0\nUSB-HOST=0\n')
            self.assertIn('profile: battery (auto)', self.power(shell, 'status').stdout)

    def test_no_battery_is_plugged(self):
        # the F50: no battery node; the daemon runs with battery knobs inert and the device counts as plugged
        for shell in self.each_shell():
            r = self.power(shell, 'status')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('battery: none', r.stdout)
            self.assertIn('profile: plugged (auto)', r.stdout)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_power -v 2>&1 | tail -3`
Expected: FAIL (no such file).

- [ ] **Step 3: Write `mu300-power` (first part)**

```sh
#!/bin/sh
# What the MU300 does with its battery: profiles, idle radios, the charging boot.
#   mu300-power status                  state, profile and why, battery, charger, idle timer
#   mu300-power profile [NAME|auto]     force a profile (plugged|battery|saver), or back to automatic
#   mu300-power set PROFILE.KNOB VALUE  knobs: WIFI_IDLE (minutes, 0 never), RADIO_IDLE keep|lte|off,
#                                       LEDS_IDLE on|off, CPU full|eco; also set SAVER_BELOW N, CHARGE_TO 100|80
#   mu300-power wake                    leave idle (the keys call it); restarts the idle timer
#   mu300-power idle                    enter idle now
#   mu300-power log [SECONDS] [FILE]    append time,V,mA,W,%,temp,state,profile every SECONDS (5) to FILE
#                                       (/var/log/mu300-power.csv)
#   mu300-power daemon                  the state machine (mu300-power.service, /etc/init.d/mu300-power)
# Design: docs/superpowers/specs/2026-10-06-power-profiles-design.md. Configuration: /etc/mu300/power.conf,
# KEY=VALUE, kept across updates with the rest of etc/mu300; a missing key is its default below.
set -u
export PATH="$PATH:/opt/mu300/bin:/opt/mu300/busybox-bin:/usr/sbin:/sbin"
R=${MU300_SYSROOT:-}
RUN=${MU300_RUN:-/run}/mu300
CONF=${MU300_POWER_CONF:-/etc/mu300/power.conf}
ST=$RUN/power
PROFILES="plugged battery saver"
KNOBS="WIFI_IDLE RADIO_IDLE LEDS_IDLE CPU"

say() { echo "mu300-power: $*" >&2; logger -t mu300-power -- "$*" 2>/dev/null || true; }

# --- configuration
default() {  # default KEY: the built-in value of a top-level key or PROFILE_KNOB
    case $1 in
        PROFILE) echo auto ;; SAVER_BELOW) echo 20 ;; CHARGE_TO) echo 100 ;;
        plugged_WIFI_IDLE) echo 0 ;;  plugged_RADIO_IDLE) echo keep ;; plugged_LEDS_IDLE) echo on ;;  plugged_CPU) echo full ;;
        battery_WIFI_IDLE) echo 10 ;; battery_RADIO_IDLE) echo off ;;  battery_LEDS_IDLE) echo off ;; battery_CPU) echo full ;;
        saver_WIFI_IDLE) echo 5 ;;    saver_RADIO_IDLE) echo off ;;    saver_LEDS_IDLE) echo off ;;   saver_CPU) echo eco ;;
        *) return 1 ;;
    esac
}
valid() {  # valid KEY VALUE
    case $1 in
        PROFILE) case $2 in auto|plugged|battery|saver) return 0 ;; esac ;;
        SAVER_BELOW) case $2 in [0-9]|[1-9][0-9]|100) return 0 ;; esac ;;
        CHARGE_TO) case $2 in 100|80) return 0 ;; esac ;;
        *_WIFI_IDLE) case $2 in ''|*[!0-9]*) ;; *) [ ${#2} -le 4 ] && return 0 ;; esac ;;
        *_RADIO_IDLE) case $2 in keep|lte|off) return 0 ;; esac ;;
        *_LEDS_IDLE) case $2 in on|off) return 0 ;; esac ;;
        *_CPU) case $2 in full|eco) return 0 ;; esac ;;
    esac
    return 1
}
conf_raw() { sed -n "s/^$1=\(.*\)\$/\1/p" "$CONF" 2>/dev/null | tail -n1; }
conf_get() {  # conf_get KEY: the file's value when valid, else the default; an invalid one is noted for status
    _v=$(conf_raw "$1")
    if [ -n "$_v" ] && valid "$1" "$_v"; then echo "$_v"; return 0; fi
    [ -z "$_v" ] || { mkdir -p "$ST" 2>/dev/null; echo "$1" >> "$ST/ignored" 2>/dev/null; }
    default "$1"
}
knob() { conf_get "$1_$2"; }
conf_set() {  # conf_set KEY VALUE: validate, then replace the line or append it
    default "$1" >/dev/null || { echo "mu300-power: no such key: $1" >&2; return 2; }
    valid "$1" "$2" || { echo "mu300-power: bad value for $1: $2" >&2; return 2; }
    mkdir -p "${CONF%/*}" 2>/dev/null
    [ -f "$CONF" ] || : > "$CONF"
    if grep -q "^$1=" "$CONF"; then
        sed "s/^$1=.*\$/$1=$2/" "$CONF" > "$CONF.tmp" && mv "$CONF.tmp" "$CONF"
    else
        echo "$1=$2" >> "$CONF"
    fi
}

# --- the device
psy_of_type() {  # psy_of_type TYPE...: the first power_supply directory whose type is one of them
    for _d in "$R"/sys/class/power_supply/*; do
        [ -f "$_d/type" ] || continue
        _t=$(cat "$_d/type" 2>/dev/null)
        for _w in "$@"; do [ "$_t" = "$_w" ] && { echo "$_d"; return 0; }; done
    done
    return 1
}
battery_dir() {
    _d=$(psy_of_type Battery) || return 1
    [ ! -f "$_d/present" ] || [ "$(cat "$_d/present" 2>/dev/null)" = 1 ] || return 1
    echo "$_d"
}
rd() { cat "$1" 2>/dev/null; }
capacity() { _d=$(battery_dir) && rd "$_d/capacity"; }
battery_temp() { _d=$(battery_dir) && rd "$_d/temp"; }
charger_dir() { psy_of_type USB Mains; }
plugged() {
    if _d=$(charger_dir); then [ "$(rd "$_d/online")" = 1 ]; return $?; fi
    for _s in "$R"/sys/class/extcon/*/state; do grep -qx 'USB=1' "$_s" 2>/dev/null && return 0; done
    battery_dir >/dev/null || return 0   # no battery at all (the F50): it runs on the cable
    return 1
}
pick_profile() {  # prints NAME and sets WHY
    _p=$(conf_get PROFILE)
    if [ "$_p" != auto ]; then WHY=forced; echo "$_p"; return; fi
    if plugged; then WHY=auto; echo plugged; return; fi
    _below=$(conf_get SAVER_BELOW); _cap=$(capacity)
    case $_cap in ''|*[!0-9]*) _cap= ;; esac
    if [ "$_below" != 0 ] && [ -n "$_cap" ] && [ "$_cap" -lt "$_below" ]; then
        WHY="auto, battery under $_below %"; echo saver; return
    fi
    WHY=auto; echo battery
}

# --- commands
cmd_status() {
    rm -f "$ST/ignored" 2>/dev/null
    _prof=$(pick_profile)
    echo "profile: $_prof ($WHY)"
    echo "state: $(rd "$ST/state" || echo active)${ST:+}"
    [ -s "$ST/reason" ] && echo "last change: $(rd "$ST/reason")"
    if _b=$(battery_dir); then
        echo "battery: $(rd "$_b/capacity") % $(rd "$_b/status" | tr 'A-Z' 'a-z'), $(( $(rd "$_b/voltage_now" || echo 0) / 1000 )) mV, $(( $(rd "$_b/current_now" || echo 0) / 1000 )) mA, $(( $(rd "$_b/temp" || echo 0) / 10 )) C"
    else
        echo "battery: none"
    fi
    if _c=$(charger_dir); then echo "charger: $(rd "$_c/status" | tr 'A-Z' 'a-z') online=$(rd "$_c/online") usb_type=$(rd "$_c/usb_type" | sed -n 's/.*\[\([A-Za-z_]*\)\].*/\1/p')"; fi
    echo "supply: $(plugged && echo plugged || echo battery)"
    echo "SAVER_BELOW=$(conf_get SAVER_BELOW) CHARGE_TO=$(conf_get CHARGE_TO)"
    for _p in $PROFILES; do
        echo "$_p: WIFI_IDLE=$(knob "$_p" WIFI_IDLE) RADIO_IDLE=$(knob "$_p" RADIO_IDLE) LEDS_IDLE=$(knob "$_p" LEDS_IDLE) CPU=$(knob "$_p" CPU)"
    done
    [ -s "$ST/idle-since" ] && echo "idle timer: last client $(( $(uptime_s) - $(rd "$ST/idle-since") )) s ago"
    [ -s "$ST/ignored" ] && echo "ignored in power.conf: $(sort -u "$ST/ignored" | tr '\n' ' ' | sed 's/ $//')"
    return 0
}
cmd_set() {
    [ $# -eq 2 ] || { echo "usage: mu300-power set PROFILE.KNOB VALUE | set KEY VALUE" >&2; return 2; }
    _k=$1
    case $_k in *.*) _k="${_k%%.*}_${_k#*.}" ;; esac
    conf_set "$_k" "$2"
}
cmd_profile() {
    [ $# -eq 0 ] && { pick_profile; return 0; }
    conf_set PROFILE "$1"
}
uptime_s() { cut -d. -f1 "$R/proc/uptime" 2>/dev/null || date +%s; }

case "${1:-}" in
    status)  cmd_status ;;
    set)     shift; cmd_set "$@" ;;
    profile) shift; cmd_profile "$@" ;;
    *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
```

(`mu300-device` is not needed here; the stub in the test is for later tasks.) `chmod 755` the file.

- [ ] **Step 4: Run the tests**

Run: `cd tests && python3 -m unittest test_power -v 2>&1 | tail -3`
Expected: OK. Also `shellcheck -s sh rootfs/overlay/opt/mu300/bin/mu300-power` if shellcheck is installed (warnings about `$ST`-style prefixing are fine; fix real ones).

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-power tests/test_power.py
git commit -m "mu300-power: profiles and knobs in power.conf, status, set, profile"
```

---

### Task 5: the daemon — idle and wake

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-power` (add inputs, actions, state machine, `wake`, `idle`, `daemon`)
- Test: `tests/test_power.py` (new class `Daemon`)

**Interfaces:**
- Produces: `mu300-power daemon` loop (`MU300_POWER_INTERVAL` seconds, default 10; `MU300_POWER_LOOPS=N` runs N loops then exits — tests only), `mu300-power wake`, `mu300-power idle`.
- State under `$ST`: `state` (`active|idle|charging-boot`), `profile`, `idle-since` (uptime s of the last activity), `reason`, `wake` (a flag the `wake` command touches; the daemon consumes it), `cpu-max4` (saved `scaling_max_freq` of policy4 for `eco`).
- Actions (each one function, idempotent): `leds_idle on|off`, `hotspot off|on`, `modem_idle MODE|resume`, `cpu eco|full`.
- `is_openwrt` = `[ -f "$R/etc/openwrt_release" ]`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_power.py`:

```python
class Daemon(PowerTest):
    def setUp(self):
        super().setUp()
        self.battery()
        (self.root / 'proc').mkdir(parents=True, exist_ok=True)
        self.uptime(0)
        udc = self.root / 'sys/class/udc/25100000.dwc3'
        udc.mkdir(parents=True)
        (udc / 'state').write_text('not attached\n')
        cpu = self.root / 'sys/devices/system/cpu'
        (cpu / 'cpu7').mkdir(parents=True)
        (cpu / 'cpu7/online').write_text('1\n')
        (cpu / 'cpufreq/policy4').mkdir(parents=True)
        (cpu / 'cpufreq/policy4/scaling_max_freq').write_text('2301000\n')
        (cpu / 'cpufreq/policy4/cpuinfo_max_freq').write_text('2301000\n')
        (self.root / 'etc').mkdir(exist_ok=True)
        (self.root / 'etc/openwrt_release').write_text('DISTRIB_ID=OpenWrt\n')

    def uptime(self, seconds):
        (self.root / 'proc/uptime').write_text(f'{seconds}.00 0.00\n')

    def stations(self, n):
        (self.tmp / 'stations').write_text(''.join(f'Station 02:00:00:00:00:0{i} (on wlan0)\n' for i in range(n)))

    def usb_host(self, attached):
        (self.root / 'sys/class/udc/25100000.dwc3/state').write_text('configured\n' if attached else 'not attached\n')

    def loops(self, shell, n=1, **env):
        r = self.power(shell, 'daemon', MU300_POWER_LOOPS=n, **env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def state(self):
        return (self.run_dir / 'mu300/power/state').read_text().strip()

    def test_idle_after_wifi_idle_minutes_without_clients(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\nbattery_RADIO_IDLE=off\nbattery_LEDS_IDLE=off\n')
            self.stations(0)
            self.uptime(100); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 9 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(100 + 10 * 60); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            c = self.calls()
            # the order: LEDs, hotspot, modem, CPU
            self.assertLess(c.index('mu300-led idle on'), c.index('wifi down'))
            self.assertLess(c.index('wifi down'), c.index('mobile-data suspend off'))
            self.assertIn('idle', (self.run_dir / 'mu300/power/reason').read_text())

    def test_a_station_or_a_usb_host_restarts_the_timer(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=10\n')
            self.uptime(0); self.stations(1); self.loops(shell)
            self.uptime(9 * 60); self.stations(1); self.loops(shell)
            self.uptime(18 * 60); self.stations(0); self.loops(shell)   # 9 min since the last station
            self.assertEqual(self.state(), 'active')
            self.uptime(19 * 60 + 1); self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60 + 1); self.usb_host(False); self.loops(shell)   # still 10 min since the USB host
            self.assertEqual(self.state(), 'active')
            self.uptime(29 * 60 + 2); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_wifi_idle_zero_never_idles(self):
        for shell in self.each_shell():
            self.setUp()
            self.charger(online=1)   # plugged: WIFI_IDLE=0
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(24 * 3600); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertNotIn('wifi down', self.calls())

    def test_wake_reverses_in_the_opposite_order(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_CPU=eco\n')
            self.stations(0)
            self.uptime(0); self.loops(shell)
            self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '0')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '1500000')
            (self.tmp / 'calls').unlink()
            r = self.power(shell, 'wake')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            c = self.calls()
            self.assertLess(c.index('mobile-data resume'), c.index('wifi up'))
            self.assertLess(c.index('wifi up'), c.index('mu300-led idle off'))
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpu7/online').read_text().strip(), '1')
            self.assertEqual((self.root / 'sys/devices/system/cpu/cpufreq/policy4/scaling_max_freq').read_text().strip(), '2301000')

    def test_idle_command_enters_idle_now_and_ubuntu_uses_systemctl(self):
        for shell in self.each_shell():
            self.setUp()
            (self.root / 'etc/openwrt_release').unlink()
            self.stations(0)
            self.power(shell, 'idle')
            self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.assertIn('systemctl stop mu300-hotspot', self.calls())

    def test_radio_idle_lte_and_keep(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=lte\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertIn('mobile-data suspend lte', self.calls())
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\nbattery_RADIO_IDLE=keep\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertNotIn('mobile-data suspend', self.calls())

    def test_plugging_in_while_idle_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            self.charger(online=1)
            self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('plugged', (self.run_dir / 'mu300/power/reason').read_text())

    def test_station_dump_failure_is_zero_stations(self):
        for shell in self.each_shell():
            self.setUp()
            self.stub('iw', 'echo "command failed: No such device (-19)" >&2; exit 237')
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')

    def test_exit_trap_wakes(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=1\n')
            self.stations(0); self.uptime(0); self.loops(shell); self.uptime(61); self.loops(shell)
            self.assertEqual(self.state(), 'idle')
            (self.tmp / 'calls').unlink()
            # the daemon's loop gets TERM: the trap must leave the radios up
            r = self.sh(shell, f'"{POWER}" daemon & p=$!; sleep 1; kill -TERM $p; wait $p; echo rc=$?',
                        MU300_SYSROOT=self.root, MU300_RUN=self.run_dir, MU300_POWER_CONF=self.conf,
                        MU300_POWER_INTERVAL=1)
            self.assertIn('wifi up', self.calls())
            self.assertEqual(self.state(), 'active')
```

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_power.Daemon -v 2>&1 | tail -3`
Expected: FAIL (`daemon` unknown).

- [ ] **Step 3: Implement**

Insert before the final `case` in `mu300-power`:

```sh
# --- inputs
is_openwrt() { [ -f "$R/etc/openwrt_release" ]; }
stations() { iw dev wlan0 station dump 2>/dev/null | grep -c '^Station' || true; }
usb_host() { for _s in "$R"/sys/class/udc/*/state; do [ "$(rd "$_s")" = configured ] && return 0; done; return 1; }
activity() {  # anyone there? a Wi-Fi station, a computer on USB, or a key press (the wake flag)
    [ "$(stations)" -gt 0 ] && return 0
    usb_host && return 0
    [ -e "$ST/wake" ]
}

# --- actions (idempotent; a failure is logged and the state still changes - the next loop retries)
hotspot() {  # hotspot on|off
    if is_openwrt; then
        if [ "$1" = off ]; then wifi down; mu300-led wifi off; else wifi up; ( mu300-led wifi sync 120 ) </dev/null >/dev/null 2>&1 & fi
    else
        if [ "$1" = off ]; then systemctl stop mu300-hotspot; else systemctl start mu300-hotspot; fi
    fi 2>/dev/null || say "hotspot $1 failed"
}
leds_idle() { mu300-led idle "$1" 2>/dev/null || true; }
modem_idle() {  # modem_idle keep|lte|off | modem_idle resume
    case $1 in
        keep) ;;
        lte|off) mobile-data suspend "$1" 2>/dev/null || say "mobile-data suspend $1 failed" ;;
        resume) mobile-data resume 2>/dev/null || say "mobile-data resume failed" ;;
    esac
}
CPU=$R/sys/devices/system/cpu
cpu() {  # cpu eco|full
    if [ "$1" = eco ]; then
        [ -s "$ST/cpu-max4" ] || rd "$CPU/cpufreq/policy4/scaling_max_freq" > "$ST/cpu-max4" 2>/dev/null
        echo 1500000 > "$CPU/cpufreq/policy4/scaling_max_freq" 2>/dev/null
        echo 0 > "$CPU/cpu7/online" 2>/dev/null
    else
        echo 1 > "$CPU/cpu7/online" 2>/dev/null
        _m=$(rd "$ST/cpu-max4"); [ -n "$_m" ] || _m=$(rd "$CPU/cpufreq/policy4/cpuinfo_max_freq")
        [ -z "$_m" ] || echo "$_m" > "$CPU/cpufreq/policy4/scaling_max_freq" 2>/dev/null
        rm -f "$ST/cpu-max4"
    fi
    return 0
}

# --- the state machine
set_state() { echo "$1" > "$ST/state"; echo "$2" > "$ST/reason"; say "$1: $2"; }
enter_idle() {  # enter_idle PROFILE REASON: LEDs, hotspot, modem, CPU - in that order
    [ "$(knob "$1" LEDS_IDLE)" = off ] && leds_idle on
    hotspot off
    modem_idle "$(knob "$1" RADIO_IDLE)"
    cpu "$(knob "$1" CPU)"
    set_state idle "$2"
}
leave_idle() {  # the reverse order
    cpu full
    modem_idle resume
    hotspot on
    leds_idle off
    rm -f "$ST/wake"
    uptime_s > "$ST/idle-since"
    set_state active "$1"
}
loop() {
    mkdir -p "$ST"
    _prof=$(pick_profile); _state=$(rd "$ST/state"); _state=${_state:-active}
    _old=$(rd "$ST/profile"); echo "$_prof" > "$ST/profile"
    _now=$(uptime_s)
    if activity; then echo "$_now" > "$ST/idle-since"; fi
    [ -s "$ST/idle-since" ] || echo "$_now" > "$ST/idle-since"
    case $_state in
        active)
            rm -f "$ST/wake"
            if [ -e "$ST/idle-now" ]; then rm -f "$ST/idle-now"; enter_idle "$_prof" "idle now (asked)"; return; fi
            _min=$(knob "$_prof" WIFI_IDLE)
            [ "$_min" = 0 ] && return 0
            _since=$(rd "$ST/idle-since")
            if [ $((_now - _since)) -ge $((_min * 60)) ]; then enter_idle "$_prof" "idle: no client for $_min min ($_prof)"; fi ;;
        idle)
            if [ -e "$ST/wake" ]; then leave_idle "woken by a key"; return; fi
            if usb_host; then leave_idle "woken by a computer on USB"; return; fi
            if [ "$_prof" = plugged ]; then leave_idle "woken: plugged in"; return; fi
            if [ -n "$_old" ] && [ "$_old" != "$_prof" ]; then enter_idle "$_prof" "idle: profile now $_prof"; fi ;;
    esac
}
cmd_daemon() {
    mkdir -p "$ST"
    rm -f "$ST/state" "$ST/wake" "$ST/idle-now"
    uptime_s > "$ST/idle-since"
    trap 'if [ "$(rd "$ST/state")" = idle ]; then leave_idle "daemon stopping"; fi; exit 0' TERM INT
    _n=0
    while :; do
        loop
        _n=$((_n + 1))
        [ -n "${MU300_POWER_LOOPS:-}" ] && [ "$_n" -ge "$MU300_POWER_LOOPS" ] && break
        sleep "${MU300_POWER_INTERVAL:-10}" &
        wait $!
    done
}
```

Make the test harness and the real daemon agree: with `MU300_POWER_LOOPS` the daemon must NOT reset `state` at start (the tests call it once per loop). Change `cmd_daemon`'s reset to run only without `MU300_POWER_LOOPS`:

```sh
    if [ -z "${MU300_POWER_LOOPS:-}" ]; then rm -f "$ST/state" "$ST/wake" "$ST/idle-now"; uptime_s > "$ST/idle-since"; fi
```

And the commands:

```sh
cmd_wake() { mkdir -p "$ST"; touch "$ST/wake"; }
cmd_idle() { mkdir -p "$ST"; touch "$ST/idle-now"; }
```

with `wake) cmd_wake ;; idle) cmd_idle ;; daemon) cmd_daemon ;;` in the dispatch. (`sleep ... & wait` lets the TERM trap run at once instead of after the sleep.)

- [ ] **Step 4: Run the tests**

Run: `cd tests && python3 -m unittest test_power -v 2>&1 | tail -3`
Expected: OK under every shell.

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-power tests/test_power.py
git commit -m "mu300-power daemon: idle after WIFI_IDLE minutes without clients, wake by key, USB host or charger"
```

---

### Task 6: charging boot, temperature guard, charge limit, `log`

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-power`
- Test: `tests/test_power.py` (class `Guards`)

**Interfaces:**
- `charging-boot` state: entered in `cmd_daemon` when `$RUN/boot-mode` says `charger` (also writes `$RUN/charging-boot` for the Ubuntu units); left only by the wake flag; unplugged and `capacity` < 5 for three consecutive loops → `poweroff` (OpenWrt) / `systemctl poweroff` (Ubuntu).
- `guard` (every loop): charging enable through the charger's `charge_type` (`N/A` off, `Fast` on, per the bq256xx port); `$ST/charge-off` holds the reason (`temp`|`limit`) while off.
- `mu300-power log [SECONDS] [FILE]`.

- [ ] **Step 1: Write the failing tests**

```python
class Guards(Daemon):
    def test_charging_boot_exits_only_on_key(self):
        for shell in self.each_shell():
            self.setUp()
            (self.run_dir / 'mu300/boot-mode').write_text('charger\n')
            self.charger(online=1); self.battery(capacity=3)
            r = self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)
            self.assertEqual(self.state(), 'charging-boot')
            self.assertTrue((self.run_dir / 'mu300/charging-boot').exists())
            c = self.calls()
            self.assertIn('wifi down', c); self.assertIn('mobile-data suspend off', c); self.assertIn('mu300-led charge on', c)
            self.usb_host(True); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # a computer does not end a charging boot
            self.charger(online=0); self.battery(capacity=50); self.loops(shell); self.loops(shell); self.loops(shell)
            self.assertEqual(self.state(), 'charging-boot')      # unplugged above 5 %: nothing happens
            self.assertNotIn('poweroff', self.calls())
            self.power(shell, 'wake'); self.loops(shell)
            self.assertEqual(self.state(), 'active')
            self.assertIn('mu300-led charge off', self.calls())
            self.assertFalse((self.run_dir / 'mu300/charging-boot').exists())

    def test_low_battery_needs_three_unplugged_loops(self):
        for shell in self.each_shell():
            self.setUp()
            (self.run_dir / 'mu300/boot-mode').write_text('charger\n')
            self.charger(online=0); self.battery(capacity=3)
            self.power(shell, 'daemon', MU300_POWER_LOOPS=1, MU300_POWER_FRESH=1)
            self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.charger(online=1); self.loops(shell)          # a plug in between resets the count
            self.charger(online=0); self.loops(shell); self.loops(shell)
            self.assertNotIn('poweroff', self.calls())
            self.loops(shell)
            self.assertIn('poweroff', self.calls())

    def test_temperature_guard_with_hysteresis(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1); self.battery(temp=460)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'temp')
            self.battery(temp=420); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet under 40.0
            self.battery(temp=390); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')
            self.battery(temp=-5); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(temp=20); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')   # not yet over 3.0
            self.battery(temp=40); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_charge_limit_80(self):
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('CHARGE_TO=80\n')
            ch = self.charger(online=1); self.battery(capacity=81)
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.assertEqual((self.run_dir / 'mu300/power/charge-off').read_text().strip(), 'limit')
            self.battery(capacity=77); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'N/A')
            self.battery(capacity=74); self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_guard_without_a_reading_or_a_switch_changes_nothing(self):
        for shell in self.each_shell():
            self.setUp()
            ch = self.charger(online=1)
            (ch / 'charge_type').unlink()
            self.battery(temp=460); self.loops(shell)
            self.assertFalse((ch / 'charge_type').exists())
            self.setUp()
            ch = self.charger(online=1); b = self.battery(); (b / 'temp').unlink()
            self.loops(shell)
            self.assertEqual((ch / 'charge_type').read_text().strip(), 'Fast')

    def test_log_line(self):
        for shell in self.each_shell():
            self.setUp()
            self.battery(capacity=64, temp=281, current=-470000)
            out = self.tmp / 'power.csv'
            r = self.power(shell, f'log 0 {out}', MU300_POWER_LOOPS=2)
            self.assertEqual(r.returncode, 0, r.stderr)
            lines = out.read_text().splitlines()
            self.assertEqual(lines[0], 'epoch,mV,mA,mW,capacity,temp,state,profile')
            self.assertEqual(len(lines), 3)
            f = lines[1].split(',')
            self.assertEqual(f[1:6], ['3850', '-470', '-1809', '64', '281'])   # 3.85 V * -0.47 A = -1.81 W
            self.assertEqual(f[6:], ['active', 'battery'])
```

Note: `MU300_POWER_FRESH=1` makes `cmd_daemon` do its start-up reset even with `MU300_POWER_LOOPS` set (so a test can start a charging boot); without it the loops continue the state as in Task 5.

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_power.Guards -v 2>&1 | tail -3`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `mu300-power`:

```sh
# --- the charging boot: LK opened our slot for a flat battery on the charger; stay light until the Wi-Fi key
boot_mode() { rd "$RUN/boot-mode"; }
enter_charging_boot() {
    hotspot off
    modem_idle off
    mu300-led charge on 2>/dev/null || true
    touch "$RUN/charging-boot"
    echo 0 > "$ST/low-loops"
    set_state charging-boot "charging boot (LK boot mode charger)"
}
leave_charging_boot() {
    mu300-led charge off 2>/dev/null || true
    rm -f "$RUN/charging-boot"
    leave_idle "$1"
}
low_battery_check() {  # unplugged and under 5 % for three loops in a row: a clean poweroff, not a brown-out
    _cap=$(capacity); case $_cap in ''|*[!0-9]*) echo 0 > "$ST/low-loops"; return 0 ;; esac
    if plugged || [ "$_cap" -ge 5 ]; then echo 0 > "$ST/low-loops"; return 0; fi
    _n=$(( $(rd "$ST/low-loops" || echo 0) + 1 )); echo "$_n" > "$ST/low-loops"
    [ "$_n" -ge 3 ] || return 0
    say "battery at $_cap % and unplugged for three loops: powering off"
    if is_openwrt; then poweroff; else systemctl poweroff; fi
}

# --- charging guard: temperature and the charge limit, through the charger's charge_type (bq256xx: N/A off, Fast on)
charging_switch() { _c=$(charger_dir) && [ -f "$_c/charge_type" ] && echo "$_c/charge_type"; }
set_charging() {  # set_charging on|off REASON
    _sw=$(charging_switch) || return 0
    if [ "$1" = off ]; then
        [ -s "$ST/charge-off" ] || { echo 'N/A' > "$_sw" 2>/dev/null; say "charging off: $2"; }
        echo "$2" > "$ST/charge-off"
    else
        [ -s "$ST/charge-off" ] && { echo Fast > "$_sw" 2>/dev/null; say "charging on: $2"; }
        rm -f "$ST/charge-off"
    fi
}
guard() {
    charging_switch >/dev/null || return 0
    _t=$(battery_temp); _cap=$(capacity); _off=$(rd "$ST/charge-off")
    _want=on _why=
    case $_t in ''|*[!0-9-]*) ;; *)
        if [ "$_t" -gt 450 ] || [ "$_t" -lt 0 ]; then _want=off _why=temp
        elif [ "$_off" = temp ] && { [ "$_t" -ge 400 ] || [ "$_t" -le 30 ]; }; then _want=off _why=temp; fi ;;
    esac
    if [ "$_want" = on ] && [ "$(conf_get CHARGE_TO)" = 80 ]; then
        case $_cap in ''|*[!0-9]*) ;; *)
            if [ "$_cap" -ge 80 ]; then _want=off _why=limit
            elif [ "$_off" = limit ] && [ "$_cap" -ge 75 ]; then _want=off _why=limit; fi ;;
        esac
    fi
    if [ "$_want" = off ]; then set_charging off "$_why"; else set_charging on "back in range"; fi
}
```

Change `loop` so `guard` runs first every loop, and add the `charging-boot` case:

```sh
        charging-boot)
            low_battery_check
            if [ -e "$ST/wake" ]; then leave_charging_boot "woken by the Wi-Fi key"; return; fi ;;
```

and in `cmd_daemon`'s start-up (when `MU300_POWER_LOOPS` is unset or `MU300_POWER_FRESH` is set): after the reset, `[ "$(boot_mode)" = charger ] && enter_charging_boot`. The temperature guard's "no reading → no change" is the `case ''|*[!0-9-]*` branch; a missing `charge_type` makes `charging_switch` fail and `guard` return.

`log`:

```sh
cmd_log() {
    _every=${1:-5}; _file=${2:-/var/log/mu300-power.csv}
    [ -s "$_file" ] || echo 'epoch,mV,mA,mW,capacity,temp,state,profile' > "$_file"
    _n=0
    while :; do
        if _b=$(battery_dir); then
            _mv=$(( $(rd "$_b/voltage_now" || echo 0) / 1000 )); _ma=$(( $(rd "$_b/current_now" || echo 0) / 1000 ))
            echo "$(date +%s),$_mv,$_ma,$(( _mv * _ma / 1000 )),$(rd "$_b/capacity"),$(rd "$_b/temp"),$(rd "$ST/state" || echo active),$(rd "$ST/profile" || pick_profile)" >> "$_file"
        else
            echo "$(date +%s),,,,,,$(rd "$ST/state" || echo active),$(rd "$ST/profile" || pick_profile)" >> "$_file"
        fi
        _n=$((_n + 1)); [ -n "${MU300_POWER_LOOPS:-}" ] && [ "$_n" -ge "$MU300_POWER_LOOPS" ] && break
        sleep "$_every"
    done
}
```

with `log) shift; cmd_log "$@" ;;` in the dispatch. `(( _mv * _ma / 1000 ))`: 3850 × −470 / 1000 = −1809 mW; the test expects that.

- [ ] **Step 4: Run all power tests**

Run: `cd tests && python3 -m unittest test_power -v 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-power tests/test_power.py
git commit -m "mu300-power: the charging boot, the battery temperature and charge-limit guard, and log"
```

---

### Task 7: wiring — buttons, services, PATH, OpenWrt's rc.button, kept config

**Files:**
- Modify: `rootfs/overlay/opt/mu300/bin/mu300-buttons`
- Create: `rootfs/overlay/etc/systemd/system/mu300-power.service`, `openwrt/overlay/etc/init.d/mu300-power`
- Modify: `rootfs/overlay/etc/systemd/system/mu300-hotspot.service`, `mu300-mobile-data.service` (a `ConditionPathExists=!/run/mu300/charging-boot` line each)
- Modify: `rootfs/overlay/opt/mu300/lib/path-commands` (add `mu300-power`)
- Modify: `openwrt/build-rootfs.sh` (after the overlay is copied: `for b in power wps rfkill; do printf '#!/bin/sh\n# mu300-buttons owns the keys (see /opt/mu300/bin/mu300-buttons)\nexit 0\n' > "$ROOT/etc/rc.button/$b"; done` — use the script's own variable for the rootfs directory)
- Test: `tests/test_static.py`

**Interfaces:**
- Consumes: `mu300-power wake|daemon` (Tasks 5-6).

- [ ] **Step 1: Write the failing tests**

In `tests/test_static.py` (class `Rules`):

```python
    def test_power_profiles_are_wired_in(self):
        # the daemon runs on both systems, the keys wake it, the Ubuntu units skip the radios in a charging boot,
        # the command is on PATH, and OpenWrt's own power-key handler (a tap = poweroff) is neutralised
        self.assertIn('mu300-power', (TOP / 'rootfs/overlay/opt/mu300/lib/path-commands').read_text().split())
        buttons = (BIN / 'mu300-buttons').read_text()
        self.assertIn('mu300-power wake', buttons)
        unit = (TOP / 'rootfs/overlay/etc/systemd/system/mu300-power.service').read_text()
        self.assertIn('ExecStart=/opt/mu300/bin/mu300-power daemon', unit)
        self.assertIn('After=mu300-hotspot.service mu300-mobile-data.service', unit)
        init = (TOP / 'openwrt/overlay/etc/init.d/mu300-power').read_text()
        self.assertIn('procd_set_param command /opt/mu300/bin/mu300-power daemon', init)
        self.assertRegex(init, r'START=9[6-9]')
        for u in ('mu300-hotspot.service', 'mu300-mobile-data.service'):
            self.assertIn('ConditionPathExists=!/run/mu300/charging-boot', (TOP / 'rootfs/overlay/etc/systemd/system' / u).read_text())
        build = (TOP / 'openwrt/build-rootfs.sh').read_text()
        for b in ('power', 'wps', 'rfkill'):
            self.assertRegex(build, rf'rc\.button/\$b|rc\.button/{b}')
        self.assertIn('for b in power wps rfkill', build)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_static.Rules.test_power_profiles_are_wired_in -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`mu300-buttons`: before the `case` in the read loop add `mu300-power wake 2>/dev/null || true` as the first statement of the loop body, and change the comment block: "Every press wakes mu300-power first (idle or a charging boot: the hotspot and the modem come back); the band toggle and the hotspot toggle then act on the awake device". To make "the first press only wakes" true, make the Wi-Fi key's two actions conditional: run them only when `mu300-power` was already awake:

```sh
    was_idle=0; [ "$(cat /run/mu300/power/state 2>/dev/null)" = active ] || was_idle=1
    mu300-power wake 2>/dev/null || true
    case "$code $kind" in
        ...
        "$KEY_WIFI short") mu300-led wake; [ "$was_idle" = 1 ] || mu300-wifi-band toggle | logger -t mu300-buttons 2>/dev/null ;;
        "$KEY_WIFI long")  mu300-led wake; [ "$was_idle" = 1 ] || hotspot_toggle ;;
```

(`/run/mu300/power/state` absent → treated as not active → the press only wakes; that is right when the daemon is not running too: nothing else to do.) Hmm: when the daemon is not installed/running, the band toggle would never work. Use: `was_idle=1` only when the state file exists and is not `active`:

```sh
    was_idle=0; s=$(cat /run/mu300/power/state 2>/dev/null); [ -z "$s" ] || [ "$s" = active ] || was_idle=1
```

`mu300-power.service`:

```ini
[Unit]
Description=MU300 power profiles: idle radios when nobody is connected, the charging boot, the charge guard
After=mu300-hotspot.service mu300-mobile-data.service mu300-buttons.service
ConditionPathExists=/opt/mu300/bin/mu300-power

[Service]
ExecStart=/opt/mu300/bin/mu300-power daemon
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable it where the other `mu300-*.service` units are enabled (grep `rootfs/assemble.sh` for `mu300-buttons.service` and add the new unit the same way).

`openwrt/overlay/etc/init.d/mu300-power`:

```sh
#!/bin/sh /etc/rc.common
# power profiles: the hotspot, the modem and the LEDs go off when nobody is connected, the charging boot, the
# charge guard (see /opt/mu300/bin/mu300-power)
START=97
USE_PROCD=1
export PATH="$PATH:/opt/mu300/busybox-bin"

start_service() {
	[ -x /opt/mu300/bin/mu300-power ] || return 0
	procd_open_instance power
	procd_set_param command /opt/mu300/bin/mu300-power daemon
	procd_set_param respawn 3600 10 0
	procd_close_instance
}
```

(mode 755; check how `openwrt/build-rootfs.sh` enables init scripts - the `rc.d` symlink or `/etc/init.d/X enable` - and do the same for this one.) Add the `ConditionPathExists=!/run/mu300/charging-boot` line to the two Ubuntu units' `[Unit]` sections. Add `mu300-power` to `path-commands`. In `openwrt/build-rootfs.sh`, after the overlay copy, add the `rc.button` loop quoted in **Files**.

- [ ] **Step 4: Run the static and extra tests**

Run: `cd tests && python3 -m unittest test_static test_extra -v 2>&1 | tail -3`
Expected: OK (`test_the_commands_on_path_are_the_same_everywhere` now covers `mu300-power`).

- [ ] **Step 5: Commit**

```bash
git add rootfs/overlay/opt/mu300/bin/mu300-buttons rootfs/overlay/etc/systemd/system rootfs/overlay/opt/mu300/lib/path-commands openwrt/overlay/etc/init.d/mu300-power openwrt/build-rootfs.sh rootfs/assemble.sh tests/test_static.py
git commit -m "mu300-power runs on both systems; the keys wake it; OpenWrt's tap-to-poweroff handler goes"
```

---

### Task 8: rpcd — `unisoc-modem/power` adapter and `power_get` / `power_set`

**Files:**
- Create: `openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power` (mode 755)
- Modify: `openwrt/luci-app-mu300/root/usr/libexec/rpcd/mu300dash` (new `m_power_get`, `m_power_set`, dispatch, `do_list`)
- Modify: `openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json` (`power_get` read, `power_set` write)
- Test: `tests/test_mu300dash_security.py`, `tests/test_power.py` (class `Adapter`)

**Interfaces:**
- Adapter: `power get` prints one JSON object `{"state":"idle","reason":"...","profile":"battery","why":"auto","supply":"battery","battery":{"capacity":64,"status":"discharging","mv":3850,"ma":-470,"temp":281}|null,"charger":{"status":"...","online":1,"usb_type":"SDP"}|null,"charge_off":"temp"|"","idle_since_s":123|null,"conf":{"PROFILE":"auto","SAVER_BELOW":20,"CHARGE_TO":100,"plugged":{"WIFI_IDLE":0,"RADIO_IDLE":"keep","LEDS_IDLE":"on","CPU":"full"},"battery":{...},"saver":{...}},"ignored":["..."]}`; `power set KEY VALUE` (KEY as `mu300-power set` takes it) prints `{"ok":1}` or `{"ok":0,"error":"..."}`; `power wake`, `power idle` print `{"ok":1}`.
- rpcd: `power_get {}` → the object; `power_set {op: "set"|"wake"|"idle", key, value}`; `allowed` patterns: `key` `(PROFILE|SAVER_BELOW|CHARGE_TO|(plugged|battery|saver)\.(WIFI_IDLE|RADIO_IDLE|LEDS_IDLE|CPU))`, `value` `[a-z0-9]{1,5}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_power.py`:

```python
ADAPTER = TOP / 'openwrt/luci-app-mu300/root/usr/libexec/unisoc-modem/power'


class Adapter(Daemon):
    def adapter(self, shell, args):
        return self.sh(shell, f'"{ADAPTER}" {args}', MU300_SYSROOT=self.root, MU300_RUN=self.run_dir,
                       MU300_POWER_CONF=self.conf, MU300_POWER_BIN=POWER)

    def test_get_is_json_with_the_state_and_the_knobs(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            self.write_conf('battery_WIFI_IDLE=15\nbattery_COLOUR=red\n')
            self.charger(online=0, usb_type='Unknown [SDP] CDP DCP')
            r = self.adapter(shell, 'get')
            self.assertEqual(r.returncode, 0, r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(d['profile'], 'battery'); self.assertEqual(d['why'], 'auto')
            self.assertEqual(d['conf']['battery']['WIFI_IDLE'], 15)
            self.assertEqual(d['conf']['plugged']['RADIO_IDLE'], 'keep')
            self.assertEqual(d['battery']['capacity'], 64); self.assertEqual(d['battery']['ma'], -470)
            self.assertEqual(d['charger']['usb_type'], 'SDP')
            self.assertEqual(d['state'], 'active')

    def test_set_wake_idle(self):
        import json
        for shell in self.each_shell():
            self.setUp()
            r = self.adapter(shell, 'set battery.WIFI_IDLE 20')
            self.assertEqual(json.loads(r.stdout), {'ok': 1})
            self.assertIn('battery_WIFI_IDLE=20', self.conf.read_text())
            r = self.adapter(shell, 'set battery.WIFI_IDLE never')
            d = json.loads(r.stdout); self.assertEqual(d['ok'], 0); self.assertIn('WIFI_IDLE', d['error'])
            self.assertEqual(json.loads(self.adapter(shell, 'idle').stdout), {'ok': 1})
            self.assertTrue((self.run_dir / 'mu300/power/idle-now').exists())
            self.assertEqual(json.loads(self.adapter(shell, 'wake').stdout), {'ok': 1})
            self.assertTrue((self.run_dir / 'mu300/power/wake').exists())
```

`tests/test_mu300dash_security.py`: find the table that lists the adapters stubbed by name (around line 237: `'device-usb', 'lock', 'languages'`) and the method→adapter-call table (line 374: `('lang_get', {}, [['languages', 'get']])`), add `'power'` to the stubs with reply `'{"ok":1}'`, and rows:

```python
        ('power_get', {}, [['power', 'get']]),
        ('power_set', {'op': 'set', 'key': 'battery.WIFI_IDLE', 'value': '15'}, [['power', 'set', 'battery.WIFI_IDLE', '15']]),
        ('power_set', {'op': 'wake'}, [['power', 'wake']]),
```

and to the refusals table (find the class/test that checks bad inputs are refused without running the adapter) rows for `power_set` with `{'op': 'set', 'key': 'battery.COLOUR', 'value': 'red'}`, `{'op': 'set', 'key': 'battery.WIFI_IDLE', 'value': '15; reboot'}`, `{'op': 'format'}` → refused, adapter not called. Also add `power_get` to the ACL read list check if the file has one (grep `lang_get` in the test to see).

- [ ] **Step 2: Run to verify failure**

Run: `cd tests && python3 -m unittest test_power.Adapter test_mu300dash_security -v 2>&1 | tail -3`
Expected: FAIL.

- [ ] **Step 3: Implement**

`unisoc-modem/power`:

```sh
#!/bin/sh
# rpcd's view of mu300-power (mu300dash power_get / power_set): one JSON object for the page, and the three
# writes. Everything the page shows comes from here; the validation of keys and values is mu300-power's.
set -u
P=${MU300_POWER_BIN:-/opt/mu300/bin/mu300-power}
R=${MU300_SYSROOT:-}
RUN=${MU300_RUN:-/run}/mu300
ST=$RUN/power
CONF=${MU300_POWER_CONF:-/etc/mu300/power.conf}
export MU300_POWER_CONF=$CONF
rd() { cat "$1" 2>/dev/null; }
jstr() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/\t/\\t/g' | tr -d '\n\r' | sed 's/^/"/; s/$/"/'; }
jnum() { case $1 in ''|*[!0-9-]*) echo null ;; *) echo "$1" ;; esac; }
knobs() {  # knobs PROFILE: the object of one profile, read through mu300-power status (validated there)
    _l=$("$P" status 2>/dev/null | sed -n "s/^$1: //p")
    _w=$(echo "$_l" | sed -n 's/.*WIFI_IDLE=\([0-9]*\).*/\1/p'); _r=$(echo "$_l" | sed -n 's/.*RADIO_IDLE=\([a-z]*\).*/\1/p')
    _d=$(echo "$_l" | sed -n 's/.*LEDS_IDLE=\([a-z]*\).*/\1/p'); _c=$(echo "$_l" | sed -n 's/.*CPU=\([a-z]*\).*/\1/p')
    printf '{"WIFI_IDLE":%s,"RADIO_IDLE":%s,"LEDS_IDLE":%s,"CPU":%s}' "$(jnum "$_w")" "$(jstr "$_r")" "$(jstr "$_d")" "$(jstr "$_c")"
}
get() {
    _st=$("$P" status 2>/dev/null)
    _prof=$(echo "$_st" | sed -n 's/^profile: \([a-z]*\) (\(.*\))$/\1/p'); _why=$(echo "$_st" | sed -n 's/^profile: [a-z]* (\(.*\))$/\1/p')
    _state=$(echo "$_st" | sed -n 's/^state: //p'); _reason=$(echo "$_st" | sed -n 's/^last change: //p')
    _supply=$(echo "$_st" | sed -n 's/^supply: //p')
    _sb=$(echo "$_st" | sed -n 's/^SAVER_BELOW=\([0-9]*\) .*/\1/p'); _ct=$(echo "$_st" | sed -n 's/.*CHARGE_TO=\([0-9]*\)$/\1/p')
    _ign=$(echo "$_st" | sed -n 's/^ignored in power.conf: //p')
    _bat=null
    for _d in "$R"/sys/class/power_supply/*; do
        [ "$(rd "$_d/type")" = Battery ] || continue
        [ ! -f "$_d/present" ] || [ "$(rd "$_d/present")" = 1 ] || continue
        _bat=$(printf '{"capacity":%s,"status":%s,"mv":%s,"ma":%s,"temp":%s}' "$(jnum "$(rd "$_d/capacity")")" \
            "$(jstr "$(rd "$_d/status" | tr 'A-Z' 'a-z')")" "$(jnum $(( $(rd "$_d/voltage_now" || echo 0) / 1000 )))" \
            "$(jnum $(( $(rd "$_d/current_now" || echo 0) / 1000 )))" "$(jnum "$(rd "$_d/temp")")")
        break
    done
    _chg=null
    for _d in "$R"/sys/class/power_supply/*; do
        case $(rd "$_d/type") in USB|Mains) ;; *) continue ;; esac
        _ut=$(rd "$_d/usb_type" | sed -n 's/.*\[\([A-Za-z_]*\)\].*/\1/p')
        _chg=$(printf '{"status":%s,"online":%s,"usb_type":%s}' "$(jstr "$(rd "$_d/status" | tr 'A-Z' 'a-z')")" "$(jnum "$(rd "$_d/online")")" "$(jstr "$_ut")")
        break
    done
    _since=null; [ -s "$ST/idle-since" ] && _since=$(jnum $(( $(cut -d. -f1 "$R/proc/uptime" 2>/dev/null || echo 0) - $(rd "$ST/idle-since") )))
    _ignj=$(for _k in $_ign; do jstr "$_k"; done | paste -sd, - 2>/dev/null || true)
    printf '{"state":%s,"reason":%s,"profile":%s,"why":%s,"supply":%s,"battery":%s,"charger":%s,"charge_off":%s,"idle_since_s":%s,' \
        "$(jstr "${_state:-active}")" "$(jstr "$_reason")" "$(jstr "$_prof")" "$(jstr "$_why")" "$(jstr "$_supply")" "$_bat" "$_chg" "$(jstr "$(rd "$ST/charge-off")")" "$_since"
    printf '"conf":{"PROFILE":%s,"SAVER_BELOW":%s,"CHARGE_TO":%s,"plugged":%s,"battery":%s,"saver":%s},"ignored":[%s]}\n' \
        "$(jstr "$(sed -n 's/^PROFILE=//p' "$CONF" 2>/dev/null | tail -n1 | grep -Ex 'auto|plugged|battery|saver' || echo auto)")" \
        "$(jnum "$_sb")" "$(jnum "$_ct")" "$(knobs plugged)" "$(knobs battery)" "$(knobs saver)" "$_ignj"
}
case "${1:-}" in
    get)  get ;;
    set)  if _e=$("$P" set "${2:-}" "${3:-}" 2>&1); then echo '{"ok":1}'; else printf '{"ok":0,"error":%s}\n' "$(jstr "$_e")"; fi ;;
    wake) "$P" wake >/dev/null 2>&1; echo '{"ok":1}' ;;
    idle) "$P" idle >/dev/null 2>&1; echo '{"ok":1}' ;;
    *)    echo '{"ok":0,"error":"usage: power get|set KEY VALUE|wake|idle"}'; exit 2 ;;
esac
```

(`paste` is not on busybox by default — check `openwrt/build-rootfs.sh`'s package list; if absent, build `_ignj` with a loop and a comma variable instead.) In `mu300dash`, beside `LANGS=`: `POWERA=${MU300_POWER_ADAPTER:-$DASH/power}`; methods:

```sh
m_power_get() { reply_obj "$("$POWERA" get 2>/dev/null)" 'The power adapter gave no valid reply'; }
m_power_set() {
    local op key value out
    inp '@.op'; op=$V
    inp '@.key'; key=$V
    inp '@.value'; value=$V
    allowed "$op" '(set|wake|idle)' || { refuse 'Unknown power operation'; return; }
    case $op in
        set)
            allowed "$key" '(PROFILE|SAVER_BELOW|CHARGE_TO|(plugged|battery|saver)\.(WIFI_IDLE|RADIO_IDLE|LEDS_IDLE|CPU))' ||
                { refuse 'Unknown power setting'; return; }
            allowed "$value" '[a-z0-9]{1,5}' || { refuse 'Invalid power value'; return; }
            out=$("$POWERA" set "$key" "$value" 2>/dev/null) ;;
        *)  [ -z "$key$value" ] || { refuse 'Invalid power request'; return; }
            out=$("$POWERA" "$op" 2>/dev/null) ;;
    esac
    reply_obj "$out" 'The power adapter gave no valid reply'
}
```

Dispatch: `power_get) run_m power_get m_power_get ;;` and `power_set) run_m power_set m_power_set ;;`; add both to `do_list` with their parameter shapes as the other methods are listed (`power_set`: `{"op":"str","key":"str","value":"str"}`). ACL: `power_get` in `read.ubus.mu300dash`, `power_set` in `write.ubus.mu300dash`. Follow how the test file stubs adapters (`MU300_*_ADAPTER`-style env or a `DASH` dir override) and set `MU300_POWER_ADAPTER` the same way.

- [ ] **Step 4: Run the tests**

Run: `cd tests && python3 -m unittest test_power test_mu300dash_security -v 2>&1 | tail -3`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add openwrt/luci-app-mu300/root tests/test_power.py tests/test_mu300dash_security.py
git commit -m "mu300dash: power_get and power_set through the unisoc-modem/power adapter"
```

---

### Task 9: the LuCI page System → Power

**Files:**
- Create: `openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js`
- Modify: `openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js` (`callPowerGet`, `callPowerSet`, export)
- Modify: `openwrt/luci-app-mu300/root/usr/share/luci/menu.d/luci-app-mu300.json` (`admin/system/power`, order after Languages)
- Modify: `openwrt/luci-app-mu300/po/en/mu300.pot` (or however the English source is kept — read `tools/luci-i18n.py` and `tests/test_luci_i18n.py` first), `po/tr/mu300.po`, `po/zh_Hans/mu300.po`
- Test: `tests/test_luci_i18n.py` (existing checks: every `_()` string in views has tr and zh_Hans entries), `tests/test_static.py`

**Interfaces:**
- Consumes: `power_get` → the object of Task 8; `power_set {op,key,value}`.

- [ ] **Step 1: Write the failing test**

In `tests/test_static.py` (class `Rules`):

```python
    def test_power_page_is_wired(self):
        menu = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/luci/menu.d/luci-app-mu300.json').read_text())
        self.assertEqual(menu['admin/system/power']['action']['path'], 'mu300/power')
        common = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/mu300/common.js').read_text()
        self.assertIn("method: 'power_get'", common)
        self.assertIn("method: 'power_set', params: [ 'op', 'key', 'value' ]", common)
        view = (TOP / 'openwrt/luci-app-mu300/htdocs/luci-static/resources/view/mu300/power.js').read_text()
        for s in ('WIFI_IDLE', 'RADIO_IDLE', 'LEDS_IDLE', 'CPU', 'SAVER_BELOW', 'CHARGE_TO', 'callPowerSet'):
            self.assertIn(s, view)
        acl = json.loads((TOP / 'openwrt/luci-app-mu300/root/usr/share/rpcd/acl.d/luci-app-mu300.json').read_text())
        self.assertIn('power_get', acl['luci-app-mu300']['read']['ubus']['mu300dash'])
        self.assertIn('power_set', acl['luci-app-mu300']['write']['ubus']['mu300dash'])
```

(import `json` at the top of the file if missing.) Then `cd tests && python3 -m unittest test_static.Rules.test_power_page_is_wired test_luci_i18n -v` → FAIL.

- [ ] **Step 2: Implement `common.js`**

Beside `callLangSet`:

```js
var callPowerGet = rpc.declare({ object: 'mu300dash', method: 'power_get', expect: { '': {} } });
var callPowerSet = rpc.declare({ object: 'mu300dash', method: 'power_set', params: [ 'op', 'key', 'value' ], expect: { '': {} } });
```

and export them in the returned object next to `callLangGet: callLangGet, callLangSet: callLangSet,`.

- [ ] **Step 3: Write `power.js`**

Follow `languages.js`'s shape (`view.extend`, `load`, `render`, `paint`, `M.injectCss()`, `M.busy`, `M.toast`, `M.errText`, strings in `_()`, every backend value inside `E()` arrays). Sections:

1. **State card**: `_('State')`: `active` → `_('Awake')`, `idle` → `_('Asleep: hotspot off')`, `charging-boot` → `_('Charging boot: press the Wi-Fi key to start')`; the reason line; `_('Profile in use: %s (%s)')` with the profile name translated (`_('Plugged in')`, `_('On battery')`, `_('Saver')`) and why (`_('automatic')`, `_('forced')`, or the "battery under N %" text); supply; the battery line from `battery` (`_('%d %%, %s, %d mV, %d mA, %.1f °C')`); the charger line (`_('Charger: %s, %s port')`); `charge_off` → `_('Charging paused: battery temperature')` / `_('Charging paused: charge limit')`. Buttons: `_('Sleep now')` → `callPowerSet('idle')`, `_('Wake')` → `callPowerSet('wake')`, then reload after 2 s.
2. **Profile**: a `<select>` with `auto` (`_('Automatic: plugged in / on battery')`), `plugged`, `battery`, `saver` → `callPowerSet('set', 'PROFILE', v)`. `SAVER_BELOW`: a number input 0-100 with `_('Switch to Saver under %')`; `CHARGE_TO`: a select 100 / 80 with `_('Stop charging at')` and the note `_('80 % keeps the battery healthier on a device that stays plugged in.')`.
3. **Knobs table**: rows = the three profiles, columns = `_('Hotspot off after (min, 0 = never)')` (number input 0-9999), `_('Modem when idle')` (select: `keep` `_('Keep as is')`, `lte` `_('LTE only (5G off)')`, `off` `_('Off')`), `_('LEDs when idle')` (`on` `_('On')`, `off` `_('Off')`), `_('CPU')` (`full` `_('Full')`, `eco` `_('Eco: big core off')`). Each change → `callPowerSet('set', profile + '.' + knob, value)`; on `ok !== 1` toast `_('Failed: %s')` and reload.
4. If `ignored` is non-empty, a warning line `_('Ignored lines in power.conf: %s')`.
5. A help paragraph: `_('Nobody connected means no Wi-Fi client and no computer on USB. The Wi-Fi key, a computer on USB or the charger wake the device.')`.

- [ ] **Step 4: Menu, i18n**

`menu.d`: add

```json
	"admin/system/power": {
		"title": "Power",
		"order": 61,
		"action": { "type": "view", "path": "mu300/power" },
		"depends": { "acl": [ "luci-app-mu300" ] }
	}
```

(order: right after the Languages entry; read its order and add 1; the menu title string must exist in the catalogs too — check how `Languages` is translated in `po/`). Regenerate/extend the English source and add the tr and zh_Hans translations for every new string (`python3 tools/luci-i18n.py` — read its usage; if it extracts strings, run it, then fill the two catalogs by hand). The other 29 languages: follow the existing route (the design's AI-translated catalogs were produced by a script in `tools/` — read `tools/luci-i18n.py --help`; if it has a translate step, run it; if not, leave the other languages to fall back to English and say so in the PR).

- [ ] **Step 5: Run the tests**

Run: `cd tests && python3 -m unittest test_static test_luci_i18n test_po2lmo -v 2>&1 | tail -3 && cd .. && python3 tools/check-i18n.py`
Expected: OK, OK.

- [ ] **Step 6: Commit**

```bash
git add openwrt/luci-app-mu300
git commit -m "luci-app-mu300: System > Power - state, profile, knobs, charge limit"
```

---

### Task 10: docs and FINDINGS

**Files:**
- Modify: `docs/FINDINGS.md` (new section 36 "Power profiles"; the measurement table filled in by the device work, with the numbers' provenance; until measured, the section says which runs are planned and what the tool is)
- Modify: `README.md` / `docs/` where the commands are listed (grep `mu300-led` in `README.md` and `docs/*.md` to find the command list; add `mu300-power` with one line), and the OpenWrt panel description where Languages is mentioned.
- Test: `tests/test_static.py` already checks README/docs consistency in places (`grep -n README tests/test_static.py`); run the whole suite.

- [ ] **Step 1: Write**

FINDINGS 36: the two causes (charger state, charger-mode boot) with a pointer to 33d; the measurement method (`mu300-power log`, the fgu's sign, 3-minute runs unplugged, hotspot idle unless stated); a table with the planned rows (baseline, LEDs off, hotspot off, LTE only, modem off, all off, eco, Android idle) and a "measured on" column; what the defaults rest on. README: one line per new command in the list; the panel: "System → Power".

- [ ] **Step 2: Run everything**

Run: `cd tests && python3 -m unittest discover -v 2>&1 | tail -4 && cd .. && python3 tools/check-i18n.py`
Expected: OK.

- [ ] **Step 3: Commit and push the branch, open the PR**

```bash
git add docs README.md
git commit -m "docs: power profiles (FINDINGS 36), mu300-power in the command lists"
git push -u origin power-profiles
gh pr create --title "Power profiles: idle radios when nobody is connected, the charging boot, the charge guard" --body "..."
```

PR body: what, why (the U30 Air ran flat; FINDINGS 33d/36), what changed per file group, tests, "device testing: pending (U30 Air measurement series, keys, charging boot)", and the two attribution lines.

---

## Device work (after the PR builds; not a unit-test task)

1. On the U30 Air (192.168.78.1, openwrt-luci): install the branch's `mu300-power`, `mobile-data`, `mu300-led`, `mu300-buttons`, the init.d script and the adapter by `scp` (no image rebuild yet); `/etc/init.d/mu300-power start`; `mu300-power status`.
2. Keys: Wi-Fi key short/long when awake (band / hotspot toggle unchanged); `mu300-power idle` then Wi-Fi key → wakes only.
3. Measurement series with `mu300-power log 5 /tmp/power.csv` unplugged, 3 minutes each; `mu300-power set` between runs; the floor with everything off; Android idle from `dumpsys battery` for the target line. Numbers → FINDINGS 36.
4. Charging boot: unplug, `poweroff`, plug in → LK charger mode → our slot: `cat /run/mu300/boot-mode`, LED blinking, hotspot/modem down, Wi-Fi key → up.
5. The guard with a forced reading: `MU300_SYSROOT` pointing at a copy of `/sys/class/power_supply` with `temp=460`, one loop, check `charge_type`; this needs the bq256xx kernel (PR #55) on the device.

## Self-review notes

- Spec coverage: inputs (T4/T5), profiles+knobs (T4), state machine (T5), charging boot + low battery (T6), guard + CHARGE_TO (T6), `mobile-data suspend/resume` (T2), init (T1), rc.button (T7), `mu300-buttons` (T7), `mu300-led` verbs (T3), panel (T8/T9), `log` and measurements (T6, device work), error handling (sysfs defaults T4; exit trap T5; guard fail-safe T6; `set` validation T4; retry by idempotent actions T5), tests listed in the spec map to T1-T9. The spec's "boot scripts check boot-mode" is done by the daemon's start-up actions on both systems plus `ConditionPathExists` on Ubuntu's two units (T6/T7) - noted as a deviation that keeps OpenWrt's netifd path untouched.
- Names used across tasks: `$ST/state|reason|profile|idle-since|wake|idle-now|cpu-max4|charge-off|low-loops|ignored`, `$RUN/boot-mode`, `$RUN/charging-boot`, `mobile-data suspend lte|off`, `mobile-data resume`, `mu300-led idle on|off`, `mu300-led charge on|off`, `MU300_POWER_LOOPS`, `MU300_POWER_FRESH`, `MU300_POWER_INTERVAL`, `MU300_POWER_BIN`, `MU300_POWER_ADAPTER`, `power_get`/`power_set` - consistent in T4-T9.
