include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-mu300
PKG_VERSION:=1.2.0
PKG_RELEASE:=20
PKG_LICENSE:=MIT
PKG_MAINTAINER:=MU300 Linux contributors
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-mu300
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=Unisoc modem control center
  DEPENDS:=+luci-base +rpcd +jsonfilter +curl +ca-bundle +openssl-util +msmtp +ucode +ucode-mod-fs +ucode-mod-ubus +ucode-mod-uci +ucode-mod-uloop
endef

define Package/luci-app-mu300/description
 A single LuCI application for the MU300 dashboard, cellular locks, raw AT
 terminal and SMS. Platform adapters isolate the UI from the modem daemon,
 serial port, SMS store and network-interface layout of the host firmware.
endef

define Package/luci-app-mu300/conffiles
/etc/mu300/sim-radio
/etc/config/unisoc_modem
/etc/config/unisoc_apn
/etc/config/mu300-charge
/etc/unisoc-modem/cpu.json
/etc/unisoc-cpu-cores.json
/etc/unisoc-modem/cpu-voltage.json
endef

define Build/Compile
endef

define Package/luci-app-mu300/install
	$(INSTALL_DIR) $(1)/www
	$(CP) ./htdocs/. $(1)/www/
	$(CP) ./root/. $(1)/
	chmod 0755 $(1)/etc/init.d/unisoc-cpu-cores $(1)/usr/libexec/rpcd/unisoc-cpu-cores $(1)/usr/libexec/unisoc-modem/cpu-cores
	chmod 0600 $(1)/etc/config/unisoc_apn
	chmod 0755 $(1)/etc/uci-defaults/97-unisoc-upgrade-policy
	chmod 0600 $(1)/etc/config/mu300-charge
	chmod 0755 $(1)/etc/init.d/mu300-charge $(1)/etc/hotplug.d/power_supply/75-mu300-charge $(1)/usr/libexec/rpcd/mu300-charge
	chmod 0755 $(1)/etc/init.d/unisoc-cpu
	chmod 0755 $(1)/etc/init.d/unisoc-modem-ui $(1)/etc/init.d/unisoc-sms-forward $(1)/etc/init.d/unisoc-traffic $(1)/etc/hotplug.d/net/90-unisoc-usb-host $(1)/etc/hotplug.d/iface/90-unisoc-usb-host $(1)/usr/libexec/rpcd/mu300dash $(1)/usr/libexec/unisoc-modem/*
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/i18n
	$(CP) ./lmo/. $(1)/usr/lib/lua/luci/i18n/
endef

define Package/luci-app-mu300/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	/usr/libexec/unisoc-modem/refresh-config
	chmod 0600 /etc/config/unisoc_apn
	state=$$(uci -q get unisoc_modem.main.state_dir 2>/dev/null)
	state=$${state:-/etc/unisoc-modem/lock-state.d}
	old=/etc/mu300/dash/lock-state.d
	if [ -d "$$old" ] && [ ! -d "$$state" ]; then
		mkdir -p "$$state"
		cp -a "$$old"/. "$$state"/
	fi
	ucode /usr/libexec/unisoc-modem/disable-channel-analysis || exit 1
	ucode /usr/libexec/unisoc-modem/disable-stock-upgrade || exit 1
	ucode /usr/libexec/unisoc-modem/filter-wifi-ciphers || exit 1
	rm -f /tmp/luci-indexcache /tmp/luci-modulecache/* 2>/dev/null
	uci -q get luci.languages.tr >/dev/null || uci set luci.languages.tr='Türkçe'
	uci commit luci
	/etc/init.d/rpcd restart 2>/dev/null || true
	/etc/init.d/unisoc-modem-ui enable 2>/dev/null || true
	/etc/init.d/unisoc-modem-ui start 2>/dev/null || true
	/etc/init.d/mu300-charge enable 2>/dev/null || true
	/etc/init.d/mu300-charge start 2>/dev/null || true
	/etc/init.d/unisoc-sms-forward enable 2>/dev/null || true
	/etc/init.d/unisoc-sms-forward start 2>/dev/null || true
	/etc/init.d/unisoc-traffic enable 2>/dev/null || true
	/etc/init.d/unisoc-traffic start 2>/dev/null || true
	/etc/init.d/unisoc-cpu enable 2>/dev/null || true
	/etc/init.d/unisoc-cpu-cores enable 2>/dev/null || true
}
exit 0
endef

define Package/luci-app-mu300/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	if [ "$$1" = remove ]; then
		/etc/init.d/unisoc-modem-ui stop 2>/dev/null || true
		/etc/init.d/unisoc-modem-ui disable 2>/dev/null || true
		/etc/init.d/unisoc-sms-forward stop 2>/dev/null || true
		/etc/init.d/unisoc-sms-forward disable 2>/dev/null || true
		/etc/init.d/unisoc-traffic stop 2>/dev/null || true
		/etc/init.d/unisoc-traffic disable 2>/dev/null || true
		/etc/init.d/unisoc-cpu disable 2>/dev/null || true
		/etc/init.d/unisoc-cpu-cores disable 2>/dev/null || true
		rm -f /etc/unisoc-modem/usb-boot.conf
	fi
	rm -f /tmp/luci-indexcache /tmp/luci-modulecache/* 2>/dev/null
	/etc/init.d/rpcd restart 2>/dev/null || true
}
exit 0
endef

define Package/luci-app-mu300/prerm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	if [ "$$1" = remove ]; then
		ucode /usr/libexec/unisoc-modem/filter-wifi-ciphers '' --remove || exit 1
	fi
}
exit 0
endef

$(eval $(call BuildPackage,luci-app-mu300))
