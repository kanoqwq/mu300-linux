# Stock firmware images

Stock ZTE firmware is **not part of this repository** (it is not covered by the MIT licence of the project code; all
rights remain with ZTE/Unisoc). The one image the project keeps available, as a last-resort repair, is hosted on the
Internet Archive instead:

## trustos-F50_FLYMODEM_ZYV1.0.0B09.img

**https://archive.org/details/zte-f50-mu300-trustos-ZYV1.0.0B09**
(direct file: https://archive.org/download/zte-f50-mu300-trustos-ZYV1.0.0B09/trustos-F50_FLYMODEM_ZYV1.0.0B09.img)

The Trusty TEE image (`trustos`) as read from a ZTE F50 / MU300 running stock firmware `F50_FLYMODEM_ZYV1.0.0B09`.

| | |
|---|---|
| firmware version | `F50_FLYMODEM_ZYV1.0.0B09` (`ro.build.display.id`) |
| partitions | `trustos_a` and `trustos_b`, byte-identical on the source device |
| size | 6 291 456 bytes (the partition is 6 MiB) |
| sha256 | `39244b89987dbfccc44fbe6aef5298c457a0ce3fe0e4cec54717d131144212a6` |

It contains no device-specific data: the IMEI and the keys live in `prodnv` and the eMMC's RPMB area, not in this
image, which is why both slots are identical.

> **⚠️ This may not match your device.** `trustos` is verified together with `sml`, `uboot` and `vbmeta` as one
> chain, for one firmware version. Writing this image onto a device running a different firmware build can leave it
> unable to boot at all, which then needs BROM/SPD recovery. It exists as a last resort for devices whose own TEE
> is already damaged — **not** as something to flash "just in case".

### Before you ever need it

Make your own backup instead of relying on this file:

```sh
tools/backup-device.sh
```

That saves your unit's own `trustos`, bootloader chain and the irreplaceable calibration partitions, each verified
against the device.

### Restoring

Only with the device in rooted Android (adb + su), and only if you understand the warning above. Download the file,
check its hash, then:

```sh
sha256sum trustos-F50_FLYMODEM_ZYV1.0.0B09.img                     # must match the hash in the table
adb push trustos-F50_FLYMODEM_ZYV1.0.0B09.img /data/local/tmp/trustos.img
adb shell "su -c 'sha256sum /data/local/tmp/trustos.img'"           # and again on the device
adb shell "su -c 'dd if=/data/local/tmp/trustos.img of=/dev/block/by-name/trustos_a bs=1M && sync'"
```

Write `trustos_b` the same way only if that slot is damaged too. If the device no longer boots far enough for adb,
you need the BROM/SPD download mode and a full firmware package instead.
