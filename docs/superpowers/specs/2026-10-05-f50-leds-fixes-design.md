# F50 LEDs and the second F50's defects: design

Branch `f50-leds-fixes` (from `driver-fixes` cba8e92). Two jobs: the F50's LEDs under control as on the U30 Air
(job A), and four defects from the second F50's test (job B: F2-F5 of its report).

## A. The F50's LEDs

What the F50 has (`/sys/class/leds`, the same under 5.4, 6.18 and in ZTE's Android): the PMIC's RGB LED
(`sc27xx:red/green/blue`) and the PMIC's keypad backlight sink (`keyboard-backlight`, max 127). No power or battery
LED, no LDO LEDs.

What ZTE's Android does (F50-B, sampled from `/sys/class/leds`, no SIM):
* no service: `sc27xx:red` 255;
* hotspot up: `keyboard-backlight` 48, hotspot stopped (`cmd wifi stop-softap`): 0, started again: 48;
* boot (`/vendor/bin/start_wifi_rgb_led.sh`): `sc27xx:green` 255 and `keyboard-backlight` 48 take turns.

So the Wi-Fi light is `keyboard-backlight`, and it stays dark under our Linux because mu300-led maps no Wi-Fi LED on
the F50 (only the U30 Air's LDOs). The F50's `sc27xx:green` is white (kanoqwq's fork, which ZTE's boot script blinks
alternately with the Wi-Fi light; the U30 Air's PMIC LED is the same), so the network light follows the U30 Air:
blue on 4G, white on 5G, red without service. 4G/5G colours were not seen in Android (F50-B has no SIM) - that part
is the U30 Air's mapping and the fork's.

Design (mu300-led, F50):
* `data on` -> blue, `data 5g` -> white (green channel), `data error` -> red, `data off` -> dark;
* `wifi on|off` -> `keyboard-backlight` at ZTE's 48 (not max: ZTE's current), whichever band;
* `wifi sync` (new, both devices) -> on if wlan0 is an AP with an SSID, else off: OpenWrt calls it after a
  `wireless` reload (LuCI turning Wi-Fi off/on), which no hook reported before;
* `power` -> nothing on the F50 (no such LED); `alarm` -> red, white, blue in turn on the RGB LED, as on the U30 Air;
* timeout stays 0 on the F50 (no button to wake them).
* OpenWrt's `mu300cell` no longer forces `data on` after `mobile-data up` (which already set 4G/5G): it turned a 5G
  connection blue until the next watchdog round.
* Wi-Fi clients: not shown (ZTE's Android not observed with a client; the U30 Air does not either).

## B. Defects

* **F2 mu300-update.** The current updater already skips a system at the release's tag (`<os>: already <tag>`) and
  the old (v2026.09.25) one cannot be changed. New: `apply` removes stale `<os>.broken` copies (left by a rollback)
  before the free-space check, except one that is the running root (a rollback not yet rebooted). Tests.
* **F3 sipa kthreads (6.18/7.2).** `sipa-free-N` and `sipa-fill-recv-N` are created with `kthread_create()` and only
  woken in the IPA's runtime-PM resume; on a device whose IPA never resumes (no SIM) they stay in the kthread
  pre-start sleep (TASK_UNINTERRUPTIBLE): +1 load each, a hung-task report every 2 min. Fix: start them at once
  and have each wait (interruptibly, so neither load nor hung-task) for a `started` flag set where the vendor
  used to wake them; after that their loops are unchanged. Measured on F50-B on 6.18 (no SIM): load and reports
  before/after; F50 #1 (SIM) still has mobile data.
* **F4 early kernel messages.** Two causes: (1) `journald` has `ReadKMsg=no`; `kmsg-forward` puts warnings in the
  journal with `systemd-cat -t kernel`, so they are `journalctl -t kernel`, never `journalctl -k`
  (`_TRANSPORT=kernel`); (2) it reads with `dmesg --follow-new`, so everything before it started (about 8 s into
  the boot: the early call traces) is lost. Fix: the first start of a boot forwards the ring buffer from the start
  (`--follow`), restarts only new messages; kernel timestamps kept in the text. Document `journalctl -t kernel`.
* **F5 5.4 Wi-Fi MAC.** The vendor 5.4 wlan_combo uses `/mnt/vendor/wifimac.txt` (Android) or a random address;
  mainline's copy reads `androidboot.wifimac` from `/chosen/bootargs`. Port that to 5.4 as a wlan_combo patch,
  built in a private copy of the source, tested on F50-B by replacing the module.
