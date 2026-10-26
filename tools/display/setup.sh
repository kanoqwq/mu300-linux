#!/bin/sh
set -e
H=$1
cd /tmp && rm -rf mu300d && mkdir mu300d && cd mu300d
for f in mu300-display-load mu300-display.service mu300-desktop.service mu300-hdmi-test labwc/autostart; do
    mkdir -p "$(dirname "$f")"; wget -q -O "$f" "http://$H:8100/files/$f"
done
install -m 755 mu300-display-load /usr/local/sbin/mu300-display-load
install -m 755 mu300-hdmi-test /usr/local/bin/mu300-hdmi-test
install -m 644 mu300-display.service mu300-desktop.service /etc/systemd/system/
# install -d -o only owns the last component: create ~/.config first so it does not end up root-owned
install -d -o ubuntu -g ubuntu /home/ubuntu/.config /home/ubuntu/.config/labwc
install -m 644 -o ubuntu -g ubuntu labwc/autostart /home/ubuntu/.config/labwc/autostart
usermod -aG video,input,render ubuntu
systemctl daemon-reload
systemctl enable mu300-display.service mu300-desktop.service
sync
echo SETUP-OK
