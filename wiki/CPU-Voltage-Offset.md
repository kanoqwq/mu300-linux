# CPU Voltage Offset (EXPERIMENTAL)

> **This feature is experimental and can PERMANENTLY DAMAGE your device. Use it entirely at your own risk.**
> Changing the CPU voltage can make the device unstable, corrupt your data, or break the hardware for good. Nobody
> can repair or replace a device damaged this way for you. If you are not sure, leave it off (it is off by default).
>
> **Türkçe:** Bu özellik deneyseldir ve cihazınıza kalıcı zarar verebilirsiniz. Tüm risk size aittir.
>
> **中文：** 本功能为实验性功能，可能对您的设备造成永久性损坏。风险由您自行承担。

**In one sentence:** on the mainline kernels you can move the CPU voltage a little (lower for less heat and power,
or slightly higher), and the device takes the change back by itself when a boot with it goes wrong.

## What it does

The SoC firmware, not Linux, sets the CPU voltages. It accepts one offset per core cluster, once per boot, when CPU
frequency scaling starts. The offset moves the whole voltage table of that cluster (and of its cache memory) by the
same amount.

| | |
|---|---|
| Domains | 0 = CPUs 0-3, 1 = CPUs 4-6, 2 = CPU 7 |
| Range | -25 mV to +12.5 mV, in steps of 3.125 mV (-25000 to 12500 µV) |
| Default | 0, 0, 0 (off: the stock voltages) |
| Kernels | mainline 6.18, 7.2 and 7.3 only (not 5.4; on 7.3 not yet tried on a device) |
| Firmware | only the SoC firmware (SML) build whose encoding is known: the F50's. Any other build, the U30 Air's included for now, is refused |
| Raising | only when the device tree gives the CPU regulator's maximum; a frequency that would then need more than that maximum is not used |

The kernel refuses anything outside the range, between the steps, or from the kernel command line, and only root can
set it.

## How a new value is applied

1. You set it. Nothing changes yet.
2. At the next reboot the value runs as **one trial boot**.
3. When that boot has been up **10 minutes**, the value is marked good and is used at every boot from then on.
4. The value is **dropped automatically** (back to 0, 0, 0, the setting cleared) when:
   * a boot with the offset ends without a clean shutdown: a crash, a watchdog reset, a hang, or a power cut;
   * the trial boot ends before its 10 minutes, even with a clean reboot (no second try: set it again if you want).

A power cut counts as a failure on purpose: the device cannot tell it from a crash. Set the value again afterwards.

Test the value under load during the trial boot. `mu300-cpu voltage stress 10` runs `mu300-toolkit`'s CPU stress test
for 10 minutes and saves a summary under `/var/log/mu300-stress-*.txt`.

## Where to set it

Every place shows the warning and asks you to type the word **`EXPERIMENTAL`** before a non-zero value is saved.

* **OpenWrt control panel:** System > CPU, the "CPU voltage offset (experimental)" section.
* **Ubuntu panel:** the Power page.
* **`sudo mu300-toolkit`:** Performance > CPU voltage offset.
* **The command line:**

```sh
sudo mu300-cpu voltage status                                     # what runs now, what is asked for, the table
sudo mu300-cpu voltage set -6250,-3125,0 --confirm EXPERIMENTAL   # µV for domains 0, 1, 2
sudo mu300-cpu voltage stress 10                                  # 10-minute CPU stress test
sudo mu300-cpu voltage off                                        # back to 0 at the next boot
```

`status` also says when the device took a value back and why (`unclean-end`, `no-good-mark`, `unknown-firmware`, ...).

## Getting out of trouble

* **It boots but is unstable:** `sudo mu300-cpu voltage off`, then reboot. The current boot keeps its offset until
  the reboot.
* **It crashes or hangs:** do nothing special. The boot that crashed did not shut down cleanly, so the next boot runs
  at the stock voltages and the setting is cleared.
* **Linux does not come up at all:** after the configured number of failed boots in a row (5 by default, see
  [Going Back to Android](Going-Back-to-Android)) the device starts Android by itself. From Android (root) or from a
  computer with the Linux disk mounted, delete `.mu300/cpu-voltage` (and `.mu300/cpu-voltage-good`) on the Linux
  disk; the next Linux boot then runs at the stock voltages.
* Nothing about this feature can stop the device from booting: when anything is unclear, the stock voltages are used.

## Details

The encoding of the firmware call and the first measurements on an F50 are kano's work
([github.com/kanoqwq](https://github.com/kanoqwq)). How this project applies it, the limits and why they were
chosen, and what was tested: `docs/FINDINGS.md`, section 133.
