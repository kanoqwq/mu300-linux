# USB-C DisplayPort / HDMI (5.4 kernel, experimental)

The F50/MU300 device tree ships with the USB-PD block (`pd@e00`) and the DisplayPort controller (`dptx@31890000`)
disabled, and ZTE's kernel config leaves the Unisoc display driver out. `kernel/build-display.sh` builds:

- `sprd-drm.ko`: Unisoc DRM (DPU1 + DPTX; DPU0/DSI linked but disabled at runtime)
- `sc27xx_pd.ko`: PMIC USB-PD PHY for the TCPM; the missing `connector` node is replaced by a dual-role software node
- `mu300-dp-enable.ko`: marks PD/DPTX "okay" (and the panel-less DPU0/DSI "disabled") at runtime and creates the devices

Install the modules (with `unisoc-iommu.ko` and `sprd_typec_displayport.ko` from the kernel build) in
`/usr/lib/modules/<release>/display/`, blacklist autoloading of `sprd_drm` and `sc27xx_pd`, then `setup.sh` installs:

- `mu300-display.service`: loads the stack in order at boot (`/dev/dri/card0`, connector `DP-1`)
- `mu300-desktop.service`: labwc (Wayland) as `ubuntu` once a monitor is connected; the 5.4 kernel has no VT, so it
  runs with `LIBSEAT_BACKEND=noop` and the pixman renderer
- `mu300-hdmi-test`: color bars through `modetest` (stop the desktop first)

Packages: `libdrm-tests labwc foot waybar swaybg wlr-randr xwayland fonts-dejavu-core`.

The adapter must use DisplayPort alternate mode (not DisplayLink). It occupies the only USB-C port, so power the
device through the adapter's PD input and use the Wi-Fi hotspot for SSH.

## Status (2026-09-17): no image, USB-PD receives nothing

- The DRM side works (`/dev/dri/card0`, connector `DP-1`), but DisplayPort alternate mode needs a USB-PD contract
  and the PMIC PD PHY never reports a received message or RX interrupt: not from a dock, not from a PD charger, not
  even with `sc27xx_pd` loaded from the initramfs 2.3 s after kernel start (inside the source's Source_Capabilities
  window). ZTE ships this node disabled and Android never uses PD on this product.
- `sc27xx_pd` is patched to find the AON APB syscon (26 MHz sine driver for the PD PHY) and to suppress hard resets:
  the device has no battery and a PD source drops VBUS on hard reset. Leave the services disabled.
- `mu300-pd-log.service`: appends Type-C/PD/DP kernel messages per boot, for tests on a dock/charger.
