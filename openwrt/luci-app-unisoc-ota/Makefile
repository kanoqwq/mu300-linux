include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-unisoc-ota
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-unisoc-ota
  SECTION:=luci
  CATEGORY:=LuCI
  TITLE:=Unisoc verified file hotfix updater
  DEPENDS:=+luci-base +rpcd +python3
endef

define Build/Compile
endef

define Package/luci-app-unisoc-ota/install
	$(INSTALL_DIR) $(1)/usr/lib/unisoc-ota $(1)/www
	$(CP) ./root/. $(1)/
	$(CP) ./htdocs/. $(1)/www/
	chmod 0755 $(1)/etc/init.d/unisoc-ota $(1)/usr/libexec/rpcd/unisoc-ota
endef

define Package/luci-app-unisoc-ota/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || /etc/init.d/unisoc-ota enable
exit 0
endef

$(eval $(call BuildPackage,luci-app-unisoc-ota))
