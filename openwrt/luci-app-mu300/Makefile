include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-mu300
PKG_VERSION:=1.0.1
PKG_RELEASE:=3
PKG_LICENSE:=MIT
PKG_MAINTAINER:=MU300 Linux contributors
PKGARCH:=all
# po2lmo, built by luci-base for the host
PKG_BUILD_DEPENDS:=luci-base/host

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-mu300
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=Unisoc modem control center
  DEPENDS:=+luci-base +rpcd +jsonfilter
endef

define Package/luci-app-mu300/description
 A single LuCI application for the MU300 dashboard, cellular locks, raw AT
 terminal and SMS. Platform adapters isolate the UI from the modem daemon,
 serial port, SMS store and network-interface layout of the host firmware.
endef

define Package/luci-app-mu300/conffiles
/etc/config/unisoc_modem
endef

# The catalogs are compiled from po/<lang>/mu300.po (no .lmo is committed). LuCI names Chinese zh-cn, as luci.mk
# does (LUCI_LC_ALIAS.zh_Hans); po2lmo writes no file for a catalog without translations (English).
MU300_LMO_LANG=$(if $(filter zh_Hans,$(1)),zh-cn,$(1))

define Build/Compile
	mkdir -p $(PKG_BUILD_DIR)/lmo
	set -e; $(foreach po,$(wildcard $(CURDIR)/po/*/mu300.po),$(STAGING_DIR_HOSTPKG)/bin/po2lmo $(po) $(PKG_BUILD_DIR)/lmo/mu300.$(call MU300_LMO_LANG,$(notdir $(patsubst %/,%,$(dir $(po))))).lmo;)
endef

define Package/luci-app-mu300/install
	$(INSTALL_DIR) $(1)/www
	$(CP) ./htdocs/. $(1)/www/
	$(CP) ./root/. $(1)/
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/i18n
	find $(PKG_BUILD_DIR)/lmo -name 'mu300.*.lmo' -exec $(CP) {} $(1)/usr/lib/lua/luci/i18n/ \;
	chmod 0755 $(1)/etc/init.d/unisoc-modem-ui $(1)/etc/hotplug.d/net/90-unisoc-usb-host $(1)/etc/hotplug.d/iface/90-unisoc-usb-host $(1)/usr/libexec/rpcd/mu300dash $(1)/usr/libexec/rpcd/mu300vpn $(1)/usr/libexec/unisoc-modem/*
	chmod 0755 $(1)/usr/libexec/rpcd/mu300term $(1)/usr/libexec/mu300-terminal-login $(1)/etc/hotplug.d/iface/95-mu300-terminal
endef

define Package/luci-app-mu300/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	state=$$(uci -q get unisoc_modem.main.state_dir 2>/dev/null)
	state=$${state:-/etc/unisoc-modem/lock-state.d}
	old=/etc/mu300/dash/lock-state.d
	if [ -d "$$old" ] && [ ! -d "$$state" ]; then
		mkdir -p "$$state"
		cp -a "$$old"/. "$$state"/
	fi
	rm -f /tmp/luci-indexcache /tmp/luci-modulecache/* 2>/dev/null
	uci -q get luci.languages.tr >/dev/null || uci set luci.languages.tr='Türkçe'
	uci commit luci
	/etc/init.d/rpcd restart 2>/dev/null || true
	/etc/init.d/unisoc-modem-ui enable 2>/dev/null || true
	/etc/init.d/unisoc-modem-ui start 2>/dev/null || true
}
exit 0
endef

define Package/luci-app-mu300/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || {
	if [ "$$1" = remove ]; then
		/etc/init.d/unisoc-modem-ui stop 2>/dev/null || true
		/etc/init.d/unisoc-modem-ui disable 2>/dev/null || true
		rm -f /etc/mu300/usb-net
	fi
	rm -f /tmp/luci-indexcache /tmp/luci-modulecache/* 2>/dev/null
	/etc/init.d/rpcd restart 2>/dev/null || true
}
exit 0
endef

$(eval $(call BuildPackage,luci-app-mu300))
