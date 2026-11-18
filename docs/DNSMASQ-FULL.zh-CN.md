# 默认 DNS/DHCP 实现

新镜像默认安装发行版 `dnsmasq-full`，由 APK 依赖求解替换 `dnsmasq`，
不先强制卸载、不同时放两份二进制。保留原有 DHCP、DNS 配置；编译支持
DHCPv6、DNSSEC、nftset、conntrack 不意味着默认启用这些功能。
当前发行版 full 未编译 ipset，不宣称具有该选项。

完整构建和复用干净基底的增量组包均显式选 full；各机型仍交付匹配内核模块，
不混入 APK 源为发行版标准内核下载的模块。登记的内核已内建 NF_CT_NETLINK。
差分更新按已安装包归属合并，用户自行安装的 full 和其他插件保留；
对于固件默认精简版迁往 full，使用目标包声明的 provider 和共享文件归属识别替换，
同步包数据库、world 与包管理辅助文件。无线、密码、静态租约和 DNS 配置不重置。

离线验收包括实际 APK 替换及配置校验，以及共享安装器
`tests/test_ota_package_variant.py` 的首次替换、后续更新、回滚和用户已安装 full 情形。
更换 full 不会自动修正代理插件退出后残留的无效 DNS 端口；这是另一项运行配置问题。
