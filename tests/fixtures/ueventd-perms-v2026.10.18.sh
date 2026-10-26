#!/bin/sh
# generated from vendor/etc/ueventd.rc: apply Android device node ownership on plain Linux
for n in /dev/apipe-pcm; do [ -e "$n" ] && chown 1000:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/apipe-cmd-in; do [ -e "$n" ] && chown 1000:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/apipe-cmd-out; do [ -e "$n" ] && chown 1000:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/vbc_turning; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/vbc_turning*; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/l_agdsp_a; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/l_agdsp_b; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/audio_dsp*; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 666 "$n"; done
for n in /dev/audio_pipe*; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 660 "$n"; done
for n in /dev/audio_dsp_call_info; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/logo; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/nr_*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/calinv; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/pm_sys*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/ch_sys*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/vdsp*; do [ -e "$n" ] && chown 1047:1000 "$n" && chmod 660 "$n"; done
for n in /dev/iio:device*; do [ -e "$n" ] && chown 1000:0 "$n" && chmod 0660 "$n"; done
for n in /dev/data0_gnss; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0666 "$n"; done
for n in /dev/block/by-name/gnssmodem_a; do [ -e "$n" ] && chown 1000:0 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/gnssmodem_b; do [ -e "$n" ] && chown 1000:0 "$n" && chmod 0660 "$n"; done
for n in /dev/sttygnss0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_gnss0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_gnss1; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/stime_pm; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/stime_ch; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/wcn_gnss_dump; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_gnss; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_wcn0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyS3; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyS4; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyS0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/gnss_pmnotify_ctl; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/power_ctl; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/gnss_dbg; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/fm; do [ -e "$n" ] && chown 1013:1013 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyM0; do [ -e "$n" ] && chown 1002:3001 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyM1; do [ -e "$n" ] && chown 1002:3001 "$n" && chmod 0660 "$n"; done
for n in /dev/trusty-ipc-dev0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/tshm; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/tui_dev; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/madev0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sttybt0; do [ -e "$n" ] && chown 1002:3001 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyBT0; do [ -e "$n" ] && chown 1002:3001 "$n" && chmod 0660 "$n"; done
for n in /dev/trusty-ipc-dev0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/stty_lte*; do [ -e "$n" ] && chown 1001:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_lte; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_pm; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_ch; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sctl_pm; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sctl_ch; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sdiag_lte; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_ldsp; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_time_sync; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte5; do [ -e "$n" ] && chown 1001:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte4; do [ -e "$n" ] && chown 1013:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte6; do [ -e "$n" ] && chown 1013:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte9; do [ -e "$n" ] && chown 1001:1001 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_lte14; do [ -e "$n" ] && chown 1041:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_pm*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0666 "$n"; done
for n in /dev/spipe_ch*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/snv_lte; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/stty_nr*; do [ -e "$n" ] && chown 1001:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_nr; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/slog_phy; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/sdiag_nr; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr5; do [ -e "$n" ] && chown 1001:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr4; do [ -e "$n" ] && chown 1013:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr6; do [ -e "$n" ] && chown 1013:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr9; do [ -e "$n" ] && chown 1001:1001 "$n" && chmod 0660 "$n"; done
for n in /dev/spipe_nr14; do [ -e "$n" ] && chown 1013:1005 "$n" && chmod 0660 "$n"; done
for n in /dev/snv_nr; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/vser; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/ttyGS*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/ion; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/sprd_ion; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/sprd_dmabuf; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/dma_heap/uncached_carveout_mm; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/dma_heap/uncached_carveout_oem; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/dma_heap/carveout_fd; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/gsp; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0660 "$n"; done
for n in /dev/mali0; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/pvr_sync; do [ -e "$n" ] && chown 1000:1003 "$n" && chmod 0666 "$n"; done
for n in /dev/sprd_jpg; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0666 "$n"; done
for n in /dev/sprd_jpg1; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_vsp; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/vdma; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/vpu_enc0; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/vpu_enc1; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_vpp; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_vsp_enc; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_image; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_isp; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_sensor; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/sprd_cpp; do [ -e "$n" ] && chown 1000:1006 "$n" && chmod 0660 "$n"; done
for n in /dev/input/event*; do [ -e "$n" ] && chown 1000:1004 "$n" && chmod 0660 "$n"; done
for n in /dev/map_user; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/iio:device*; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/gnssmodem; do [ -e "$n" ] && chown 1000:0 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/gnssmodem_a; do [ -e "$n" ] && chown 1000:0 "$n" && chmod 0660 "$n"; done
for n in /dev/rpmb0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/tmc_etb; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/pn553; do [ -e "$n" ] && chown 1027:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/st21nfc; do [ -e "$n" ] && chown 1027:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/st54spi; do [ -e "$n" ] && chown 1027:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/miscdata; do [ -e "$n" ] && chown 0:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/persist; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/sysdumpdb; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/by-name/fulldumpdb; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/mmcblk*rpmb; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/mmcblk*rpmb; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
for n in /dev/block/memdisk0p1; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0770 "$n"; done
for n in /dev/block/pmem0; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0770 "$n"; done
# chown/chmod from vendor init *.rc (Android init applies these after ueventd)
for n in /sys/class/typec/port0/power_role; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/typec/port0/data_role; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/typec/port0/port_type; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/typec/port0/power_role; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/typec/port0/data_role; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/typec/port0/port_type; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/usb_notify/usb_control/usb_data_enabled; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/usb_notify/usb_control/usb_data_enabled; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chmod 0666 "$n"; done
for n in /sys/power/wakeup_count; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/module/zte_misc/parameters/cc_connect; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/soc0/soc_manufacturer; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /sys/devices/soc0/soc_model; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /sys/devices/soc0/soc_manufacturer; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/devices/soc0/soc_model; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/devices/platform/soc/soc:aon/64400000.spi/spi_master/spi4/spi4.0/sc27xx-7sreset/hard_mode; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/asound/sprdphonesc2730/asoc-sprd-debug; do [ -e "$n" ] && chown 1041:1005 "$n"; done
for n in /proc/asound/sprdphonesc2730/asoc-sprd-debug; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/devices/virtual/misc/sprd_sensor/camera_sensor_name; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/devices/virtual/misc/sprd_sensor/camera_sensor_name; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/devices/virtual/misc/sprd_flash/test; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/devices/virtual/misc/sprd_flash/test; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/devfreq/isp-dvfs/isp_governor/set_work_freq; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/devfreq/isp-dvfs/isp_governor/set_work_freq; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/devices/virtual/misc/sprd_flash/test; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/devices/virtual/misc/sprd_flash/test; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/display/dphy0/hop; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dphy0/ssc; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/panel0/name; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/bg_color; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/refresh; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/irq_register; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/display/dispc0/irq_unregister; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/display/panel0/esd_check_enable; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/display/dsi/dpms_mode; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /sys/class/display/dispc0/bg_color; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/display/dispc0/refresh; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dphy0/hop; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dphy0/ssc; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/panel0/name; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/panel0/resolution; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/panel0/esd_check_enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dsi/dpms_mode; do [ -e "$n" ] && chown 1003:1000 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/dpu_version; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/irq_register; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/irq_unregister; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chown 1003:1000 "$n"; done
for n in /sys/class/display/dispc0/low_res_simu; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/slp; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cm; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/gamma; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/hsv; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/epf; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/scl; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/enable; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/disable; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/slp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/gamma; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/hsv; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/epf; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/scl; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/disable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/max_brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/backlight/max_brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/display/dispc0/frame_count; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/frame_count; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/graphics/fb0; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/fb0; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/adf0; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /dev/adf-interface0.0; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /dev/adf-overlay-engine0.0; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /sys/class/display/dphy0/hop; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dphy0/ssc; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/panel0/name; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chmod 666 "$n"; done
for n in /sys/class/display/dispc0/bg_color; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/refresh; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/display/dphy0/hop; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dphy0/ssc; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/panel0/name; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/vsync_rate_report; do [ -e "$n" ] && chown 1003:1000 "$n"; done
for n in /sys/class/display/dispc0/bg_color; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/display/dispc0/refresh; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/display/dispc0/disable_flip; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/max_brightness; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/iq_mem; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/sprd-adf/dispc0/dynamic_pclk; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/sprd-adf/dispc0/dynamic_mipi_clk; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/sprd_bm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/dmc_mpu; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/enable; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/sr_epf; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cm; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/ltm; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/lut3d; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/status; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_mode; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_hist; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_hist_v2; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_gain; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_bl_fix; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_cur_bl; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/vsync_count; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/frame_no; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_run; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_state; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_param; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/slp_lut; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/luts_print; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/update_luts; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/ud; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/panel0/resolution; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/display/dispc0/PQ/enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/sr_epf; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/ltm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/lut3d; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/status; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_mode; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_hist; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_hist_v2; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_gain; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_bl_fix; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_cur_bl; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_run; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/vsync_count; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/frame_no; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_state; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/cabc_param; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/slp_lut; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/luts_print; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/update_luts; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/display/dispc0/PQ/ud; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/cptl/wdtirq; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/stop; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/start; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/modem; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/deltanv; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/gdsp; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/ldsp; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/cdsp; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/fixnv; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/runnv; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/cpcmdline; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/modemassert; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/cptl/ddr_smem; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/pmic/aon_iram; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/cptl/ldinfo; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/cptl/mini_dump; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/cptl/mem; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/cptl/modemassert; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/ddr_smem; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/aon_iram; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/wdtirq; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/stop; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/start; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/modem; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/deltanv; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/ldsp; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/gdsp; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/cdsp; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/fixnv; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/runnv; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/cpcmdline; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/ldinfo; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/cptl/mini_dump; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/cptl/mem; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/modem; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/phycp; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/pmsys; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/chsys; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/mdm_ctrl; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/sctl_ch; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/devices/platform/spool@0/base_addr; do [ -e "$n" ] && chmod 444 "$n"; done
for n in /sys/devices/platform/spool@1/base_addr; do [ -e "$n" ] && chmod 444 "$n"; done
for n in /sys/devices/platform/sipc-virt/sipc-virt:core@5/sipc-virt:core@5:channel@5/base_addr; do [ -e "$n" ] && chmod 444 "$n"; done
for n in /sys/devices/platform/sipc-virt/sipc-virt:core@7/sipc-virt:core@7:channel@5/base_addr; do [ -e "$n" ] && chmod 444 "$n"; done
for n in /dev/modem; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/phycp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/pmsys; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/chsys; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/mdm_ctrl; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sctl_ch; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/platform/spool@0/base_addr; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/platform/spool@1/base_addr; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/platform/sipc-virt/sipc-virt:core@5/sipc-virt:core@5:channel@5/base_addr; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/platform/sipc-virt/sipc-virt:core@7/sipc-virt:core@7:channel@5/base_addr; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cp_dump; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/cp_dump; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/debug-log/freq; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/modem/debug-log/channel; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/modem/debug-log/freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/debug-log/channel; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/sctl_pm; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/iq_mem; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/iq_mem; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/sctl_pm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/mdbg; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/mdbg/assert; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/mdbg/wdtirq; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/mdbg/at_cmd; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/mdbg/loopcheck; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/mdbg; do [ -e "$n" ] && chmod 770 "$n"; done
for n in /proc/mdbg/assert; do [ -e "$n" ] && chmod 760 "$n"; done
for n in /proc/mdbg/wdtirq; do [ -e "$n" ] && chmod 760 "$n"; done
for n in /proc/mdbg/at_cmd; do [ -e "$n" ] && chmod 760 "$n"; done
for n in /proc/mdbg/loopcheck; do [ -e "$n" ] && chmod 760 "$n"; done
for n in /dev/mbox; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sipc_smsg; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sipc_smem; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sipc_sbuf; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sipc_sblock; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /dev/sipx; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/devices/platform/soc/soc:ipa-apb/soc:ipa-apb:sipa-dele/sipa_dele_reset; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/power_supply/battery/charge_control_limit; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/backlight/sprd_backlight/max_brightness; do [ -e "$n" ] && chmod 0644 "$n"; done
for n in /sys/class/display/backlight/max_brightness; do [ -e "$n" ] && chmod 0644 "$n"; done
for n in /sys/class/thermal/thermal_zone4/trip_point_0_temp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/thermal_zone4/trip_point_1_temp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/thermal_zone4/user_power_range; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device3/min_core_num; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device3/min_freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device3/max_ctrl_temp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device4/min_core_num; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device4/min_freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device4/max_ctrl_temp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device5/min_core_num; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device5/min_freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/cooling_device5/max_ctrl_temp; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/thermal_zone4/policy; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/thermal_zone4/policy; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/thermal/thermal_zone4/thm_enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/thermal/thermal_zone3/thm_enable; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/auto_dfs_on_off; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scaling_force_ddr_freq; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scenario_dfs; do [ -e "$n" ] && chmod 220 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scenario_dfs; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/exit_scene; do [ -e "$n" ] && chmod 220 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/exit_scene; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scene_boost_dfs; do [ -e "$n" ] && chmod 220 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scene_boost_dfs; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scene_freq_set; do [ -e "$n" ] && chmod 220 "$n"; done
for n in /sys/class/devfreq/scene-frequency/sprd-governor/scene_freq_set; do [ -e "$n" ] && chown 1047:1000 "$n"; done
for n in /sys/class/power_supply/battery/stop_charge; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/power_supply/battery/stop_charge; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/stop_charge; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/enable_power_path; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/stop_charge; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/enable_power_path; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/jeita_control; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/power_supply/battery/charger.0/keep_awake; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/power_supply/sprdfgu/cc_test_cmd; do [ -e "$n" ] && chmod 0666 "$n"; done
for n in /sys/class/power_supply/battery/input_current_limit; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/power_supply/battery/constant_charge_current; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/stop; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/start; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/pm_sys; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/mem; do [ -e "$n" ] && chmod 440 "$n"; done
for n in /proc/pmic/status; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/wdtirq; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/ldinfo; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/pmic/stop; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/start; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/pm_sys; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/mem; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/status; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/wdtirq; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/cali_lib; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /proc/pmic/ldinfo; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/bus/iio/devices/trigger0/name; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/length; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/enable; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/trigger/current_trigger; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/bus/iio/devices/trigger0/name; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/length; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/enable; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/trigger/current_trigger; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/name; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/name; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/gryo_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/acc_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/mag_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/pressure_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/light_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/prox_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/color_temp_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/firmware_class/parameters/path; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/gryo_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/acc_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/mag_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/pressure_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/light_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/prox_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/color_temp_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/firmware_class/parameters/path; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/bus/iio/devices/trigger0/name; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/length; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/enable; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/trigger/current_trigger; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/bus/iio/devices/trigger0/name; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/length; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/buffer/enable; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/trigger/current_trigger; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/name; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/class/sprd_sensorhub/sensor_hub/iio/name; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/gryo_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/acc_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/mag_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/pressure_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/light_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/prox_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/color_temp_firms; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/firmware_class/parameters/path; do [ -e "$n" ] && chown 1000:0 "$n"; done
for n in /sys/module/sensorhub/parameters/gryo_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/acc_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/mag_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/pressure_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/light_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/prox_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/sensorhub/parameters/color_temp_firms; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/module/firmware_class/parameters/path; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/sc27xx:red/brightness; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:green/brightness; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:blue/brightness; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:red/trigger; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:green/trigger; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:blue/trigger; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/leds/sc27xx:red/brightness; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/leds/sc27xx:green/brightness; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/leds/sc27xx:blue/brightness; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/leds/sc27xx:red/trigger; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/leds/sc27xx:green/trigger; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/leds/sc27xx:blue/trigger; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_power_enable; do [ -e "$n" ] && chmod 220 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_dump; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_subsys; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_power_enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_dump; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/misc/gnss_common_ctl/gnss_subsys; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/touchscreen/ts_suspend; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/touchscreen/ts_suspend; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /dev/cpuctl; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/foreground; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/background; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/top-app; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/rt; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/tasks; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/foreground/tasks; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/background/tasks; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/top-app/tasks; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/rt/tasks; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cpuctl/tasks; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/cpuctl/foreground/tasks; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/cpuctl/background/tasks; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/cpuctl/top-app/tasks; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /dev/cpuctl/rt/tasks; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/kernel/hmp/boost; do [ -e "$n" ] && chown 1013:1000 "$n"; done
for n in /sys/kernel/hmp/boostpulse; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/kernel/hmp/packing_boost; do [ -e "$n" ] && chown 1013:1000 "$n"; done
for n in /sys/kernel/hmp/packing_boostpulse; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/kernel/hmp/boostpulse_duration; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/kernel/hmp/boost; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/kernel/hmp/boostpulse; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/kernel/hmp/packing_boost; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/kernel/hmp/packing_boostpulse; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/kernel/hmp/boostpulse_duratio; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/boostpulse; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/boostpulse_duration; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/boostpulse; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/boostpulse_duration; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/cluster0_core_max_limit; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/cluster0_core_min_limit; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/cluster0_core_max_limit; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/devices/system/cpu/cpuhotplug/cluster0_core_max_limit; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/cluster0_freq_min; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cluster0_freq_max; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cluster1_freq_min; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cluster1_freq_max; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/cluster0_freq_min; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/cluster0_freq_max; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/cluster1_freq_min; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/cluster1_freq_max; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy0/scaling_governor; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy0/scaling_setspeed; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy0/scaling_governor; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy4/scaling_governor; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy4/scaling_setspeed; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/system/cpu/cpufreq/policy4/scaling_governor; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /proc/sprd_sysdump; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/tmc_etb; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/tmc_etb; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /dev/sprd_bm; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/dmc_mpu; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /dev/sprd_bm; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/dmc_mpu; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/serdes0/channel; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/modem/serdes1/channel; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/modem/serdes0/channel; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/serdes1/channel; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/serdes0/freq; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/modem/serdes1/freq; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/modem/serdes0/freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/modem/serdes1/freq; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /dev/rt5512; do [ -e "$n" ] && chown 1000:1005 "$n"; done
for n in /dev/rt5512; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /proc/reserve_space/black_list; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /proc/reserve_space/white_list; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /proc/reserve_space/black_list_comm; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /proc/reserve_space/white_list_comm; do [ -e "$n" ] && chmod 640 "$n"; done
for n in /proc/reserve_space/app_guid; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /sys/class/leds/skey0/mode_operation; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey0/sensy_config; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey0/aot_cali; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey1/mode_operation; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey1/sensy_config; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey1/aot_cali; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/leds/skey0/mode_operation; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/skey0/sensy_config; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/skey0/aot_cali; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/skey1/mode_operation; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/skey1/sensy_config; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/class/leds/skey1/aot_cali; do [ -e "$n" ] && chmod 660 "$n"; done
for n in /sys/block/zram0/writeback_limit; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/block/zram0/writeback_limit; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/block/zram0/writeback_limit_enable; do [ -e "$n" ] && chown 0:1000 "$n"; done
for n in /sys/block/zram0/writeback_limit_enable; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /sys/class/leds/aw_led/groupcolor; do [ -e "$n" ] && chmod 0660 "$n"; done
for n in /sys/class/leds/aw_led/groupcolor; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/touchscreen/wake_gesture; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/touchscreen/wake_gesture; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /proc/touchscreen/smart_cover; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /proc/touchscreen/smart_cover; do [ -e "$n" ] && chmod 644 "$n"; done
for n in /proc/touchscreen/screen_state_interface; do [ -e "$n" ] && chmod 0664 "$n"; done
for n in /proc/touchscreen/screen_state_interface; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/bus/platform/devices/zte_touch/uevent; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_cmd; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_filename; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_ini_file_path; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_save_file_path; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_save_file_name; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_bsc_calibration; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_bsc_calibration_start; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_cmd; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_filename; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_ini_file_path; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_save_file_path; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_save_file_name; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_bsc_calibration; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/devices/virtual/tsp_fw/touchscreen/tpd_test_bsc_calibration_start; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /dev/gtp_tools; do [ -e "$n" ] && chmod 0646 "$n"; done
for n in /sys/class/tp_ps/enable; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/tp_ps/delay; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/tp_ps/batch; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/tp_ps/flush; do [ -e "$n" ] && chown 1000:1000 "$n"; done
for n in /sys/class/tp_ps/enable; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/tp_ps/delay; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/tp_ps/batch; do [ -e "$n" ] && chmod 664 "$n"; done
for n in /sys/class/tp_ps/flush; do [ -e "$n" ] && chmod 664 "$n"; done
# from Android system init.rc: wakelocks are written by uid system
for n in /sys/power/wake_lock /sys/power/wake_unlock; do [ -e "$n" ] && chown 1000:1000 "$n" && chmod 0660 "$n"; done
true
