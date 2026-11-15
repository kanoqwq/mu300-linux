#!/bin/sh
# From rooted Android: copy the current Android hotspot SSID/passphrase into the Linux rootfs
# (/etc/mu300/hotspot.conf, mode 0600). The passphrase never leaves the device.
# requires tools/android-mount-mu300root.sh pushed to /data/local/tmp
set -eu
adb shell "su -c '
set -e
X=/data/misc/apexdata/com.android.wifi/WifiConfigStoreSoftAp.xml
R=/data/local/tmp/mu300root
ssid=\$(sed -n \"s/.*<string name=\\\"WifiSsid\\\">&quot;\\(.*\\)&quot;<\\/string>.*/\\1/p; s/.*<string name=\\\"WifiSsid\\\">\\([^&<]*\\)<\\/string>.*/\\1/p\" \$X | head -1)
psk=\$(sed -n \"s/.*<string name=\\\"Passphrase\\\">\\(.*\\)<\\/string>.*/\\1/p\" \$X | head -1 | sed \"s/&amp;/\\&/g; s/&lt;/</g; s/&gt;/>/g; s/&quot;/\\\"/g; s/&apos;/'\"'\"'/g\")
[ -n \"\$ssid\" ] && [ \${#psk} -ge 8 ] || { echo \"no usable SoftAP config in \$X\"; exit 1; }
sh /data/local/tmp/android-mount-mu300root.sh \$R >/dev/null
mkdir -p \$R/etc/mu300
umask 077
printf \"SSID=%s\\nPSK=%s\\nBAND=5\\nCHANNEL=auto\\nCOUNTRY=CN\\n\" \"\$ssid\" \"\$psk\" > \$R/etc/mu300/hotspot.conf
chown 0:0 \$R/etc/mu300/hotspot.conf; chmod 600 \$R/etc/mu300/hotspot.conf
sync
echo \"imported SSID \$ssid (passphrase \${#psk} chars) into \$R/etc/mu300/hotspot.conf\"
sh /data/local/tmp/android-mount-mu300root.sh -u \$R >/dev/null
'" | tr -d '\r'
