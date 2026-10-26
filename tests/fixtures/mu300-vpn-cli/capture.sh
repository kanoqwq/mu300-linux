#!/bin/sh
# Makes this directory's files again from the real mu300-vpn: run inside a container of the openwrt-luci rootfs (busybox,
# jq, jsonfilter), with the VPN module's tree (bin/, lib/) mounted at /v and this directory at /out:
#   docker run --rm -v MODULE:/v:ro -v tests/fixtures/mu300-vpn-cli:/out IMAGE /bin/sh /out/capture.sh
# A scratch store; the service, nft and mu300-extra are stubs, so nothing runs and nothing is downloaded. Check the
# exit_ip of status-exit-ip.out before committing: it is the machine's real public address (replace it with
# 203.0.113.77).
set -u
R=/tmp/rv
rm -rf $R /opt/vpnmod; mkdir -p $R/disk/extra $R/opt $R/run /opt/vpnmod
cp -r /v/bin /v/lib /opt/vpnmod/
echo v2.0.0-test > /opt/vpnmod/VERSION
printf 'xray 26.3.27\nsing-box 1.12.9\n' > /opt/vpnmod/components
echo 'ENABLE=0' > $R/vpn.conf
printf '#!/bin/sh\necho "svc $*" >> /tmp/rv/svc.log\n' > $R/svc
cat > $R/extra <<'EOF'
#!/bin/sh
echo "mu300-extra $*"
case "$1" in install)
  n=$2; d=/tmp/rv/disk/extra/$n/bin; mkdir -p $d
  case $n in vpn-xray) b="xray hev-socks5-tunnel" ;; vpn-singbox) b=sing-box ;; vpn-mihomo) b=mihomo ;; vpn-dae) b=dae ;; *) b= ;; esac
  for x in $b; do printf '#!/bin/sh\necho "%s 1.0"\n' $x > $d/$x; chmod 755 $d/$x; done ;;
remove) rm -rf /tmp/rv/disk/extra/$2 ;;
esac
EOF
chmod 755 $R/svc $R/extra
$R/extra install vpn-xray >/dev/null; $R/extra install vpn-singbox >/dev/null
V() {
    MU300_VPN_CONF=$R/vpn.conf MU300_VPN_DIR=$R/vpn MU300_VPN_RUN=$R/run MU300_VPN_SVC=$R/svc MU300_EXTRA_CMD=$R/extra \
    MU300_DISK=$R/disk MU300_OPT=$R/opt MU300_LAN_CONF=$R/no MU300_VPN_URL=http://127.0.0.1:9/none \
    sh /opt/vpnmod/bin/mu300-vpn "$@"
}
O=/out
c() { n=$1; shift; V "$@" > $O/$n.out 2> $O/$n.err < /dev/null; echo $? > $O/$n.rc; }
echo 'vless://11111111-2222-3333-4444-555555555555@vpn.example.com:443?security=tls&type=ws&host=vpn.example.com&path=%2Fws#Office' | V profile import - >/dev/null
echo 'trojan://SECRETPASS@tr.example.net:443?security=tls&sni=tr.example.net#Trojan' | V profile import - >/dev/null
printf '[Interface]\nPrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5eHg=\nAddress = 10.7.0.2/32\n[Peer]\nPublicKey = cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHVieHg=\nEndpoint = wg.example.org:51820\nAllowedIPs = 0.0.0.0/0\n' > /tmp/home.conf
V profile import /tmp/home.conf "Home WG" >/dev/null
V profile use office >/dev/null
c status status --json
c profile-list profile list --json
c profile-show-reveal profile show home-wg --json --reveal
c engines engines --json
c settings-json settings --json
c settings-get settings get
c killswitch killswitch --json
c version version --json
c latency-one latency trojan --json
c latency-all latency --all --json
c latency-udp latency home-wg --json
c sub-list subscription list --json
c profile-show-missing profile show nosuch --json
c use-missing use nosuch
c restart-off restart
c reveal-nojson profile show office --reveal
c logs logs -n 5
# a subscription, served by the rootfs's own uhttpd (start it first: uhttpd -h /www -p 127.0.0.1:80)
printf 'vless://22222222-2222-3333-4444-555555555555@n1.example.com:443?security=tls#Node%%201\ntrojan://PASSWORD2@n2.example.com:443?security=tls#Node%%202\n' > /www/sub-test.txt
echo 'http://127.0.0.1/sub-test.txt' | V subscription add "My provider" - --insecure > $O/sub-add.out 2> $O/sub-add.err; echo $? > $O/sub-add.rc
c sub-list subscription list --json
c profile-list-sub profile list --json
# the VPN on, its tunnel "up" (lo stands in for the interface)
sed -i 's/^ENABLE=.*/ENABLE=1/' $R/vpn.conf
echo lo > $R/run/iface
c status-running status --json
c status-exit-ip status --json --exit-ip
c remove-active profile remove office
c engine-remove-needed engine remove sing-box
rm -f /www/sub-test.txt
# a server that does not answer (TEST-NET-1), tested and removed again
sed -i 's/^ENABLE=.*/ENABLE=0/' $R/vpn.conf
echo 'trojan://x@192.0.2.1:9?security=tls#Dead' | V profile import - >/dev/null
c latency-unreachable latency dead --json
V profile remove dead >/dev/null
