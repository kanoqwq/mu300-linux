# 蜂窝 IPv6 RA 生命周期续期

## 问题与证据边界

用户报告部分运营商仅在数据连接建立时发送 RA，默认路由随后到期。
移动、电信、联通的具体时长属于用户现场报告，未在本轮逐卡复现；不能把某个
时长写成运营商常量。RA 头部的 Router Lifetime 为 16 位，最大 65535 秒；
界面中的倒计时不作为续期配置来源。

源码确认的缺口：MU300／Pro 的旧 NDP 恢复仅在没有全局地址时翻转 accept_ra，
地址仍在但默认路由即将到期时不会主动续期。E5 使用 ModemManager／odhcp6c，
保留其地址和路由所有权，增加独立的 RS 请求，不修改其拨号状态机。

## 实现

- 公共程序：`openwrt/overlay/usr/sbin/unisoc-ra-renew`；服务同名，procd 管理，
  开机 S98、停机 K10。无用户配置迁移、持久锁、缓存历史或新增安装器依赖。
- 从 netifd 获取正在使用的蜂窝 SIPA 接口，监听 netifd 和内核接口事件。
  尊重手动关闭、IPv4-only、IPv6 禁用及实际数据接口，切卡后移除旧接口期限。
  MU300 的 RA 仍由内核接收，E5 的动态 DHCPv6 子接口仍由 odhcp6c 接收。
- 被动读取真实 RA，校验类型、code、链路本地来源、hop limit=255、报文和选项长度。
  ICMPv6 校验和由内核校验。按路由器分别记录 Router Lifetime、PIO 有效／首选寿命、
  RIO 和 RDNSS，取最早有限寿命的一半发送 RS。未携带的选项不视为撤回；
  零寿命仅撤回对应项目，无限寿命不伪造成有限租期。每接口最多 512 项。
- 仅发送标准 RS 到 `ff02::2`，使用明确的接口索引及 hop limit=255。
  启动时先请求一次，以覆盖服务启动前已经错过 RA 的情况。
  无响应时三次短重试，随后 30～300 秒退避，最短发送间隔 4 秒。
  正常周期 RA 会推迟请求期限，不额外维持固定的高频轮询。
- 计时使用 CLOCK_BOOTTIME 的一次性 timerfd，包含休眠时间、不受校时影响；
  空闲时解除定时，不使用唤醒闹钟，休眠期间到期会在恢复后处理。
  依赖现有 OpenWrt Python 3.13（已验证当前目标为 3.13.15），不新加原生二进制。
- 不调用 AT，不读取 NR0，不修改默认路由／地址／DNS，不合成运营商 RA，
  不重拨、不重启基带、不把过期路由改为永久。运营商必须响应 RS 才能续期；
  若网络不响应，真实租期仍正常到期，不谎报成功。
- MU300／Pro 删除旧 ndp-learn 的 accept_ra 反复翻转逻辑；保留 LAN 前缀路由、
  邻居处理和旧前缀撤回。E5 的非 OpenWrt Debian 路径不在本次范围。

## 构建接入

三个仓库的程序、服务、离线安装器 `tools/stage-ra-renew.py` 和测试保持相同。
这里的“安装器”指根文件树的构建阶段，不是桌面／手机刷机 App。

- E5：`openwrt/build-rootfs.sh`、公共 `devices/m8/prepare-root.py`，覆盖全部九个
  OpenWrt profile；`openwrt/build-profile-tf.py` 检查文件字节、执行权限及启动链接。
- MU300：`openwrt/build-rootfs.sh`，覆盖 6.18／7.2 的公共系统树。
- Pro：`tools/u30pro-package.py` 的 finish 阶段及服务清单，覆盖 U30 Pro／F50 Pro。
- 已完成的旧 ZIP 不会自动变化；下一次重建系统树并打包才包含本修复，
  也可由新旧完整包生成正常差异补丁。此变更不需要重新编译内核或桌面安装器。

## 回归记录

1. `openwrt/tests/ra-renew.py`：24 项，包括 65535 秒及短寿命、部分 RA、撤回、
   无限寿命、异常报文、IPv4-only、切卡、接口重用、计时和退避、实际主循环清理、
   构建入口及安装幂等性。目标 ARM64 Python 3.13 全部通过，无跳过；
   WSL Python 3.10 仅跳过本机不提供的真实 timerfd API 测试。
2. `openwrt/tests/ra-renew-netns.py`：在现有 ARM64 目标运行环境的隔离容器网络中，
   使用真实 RS／RA 和 12 秒路由寿命。对端只响应 RS：禁用服务后可用路由到期，
   启用后跨原始期限续期（三次 RS），停止后又正常到期。
   判断依据是 `ip -6 route get` 的实际可用性，不以可能延迟清理的路由表条目为准。
3. MU300／Pro 原有 NDP、前缀撤回、接口映射和蜂窝移动性回归分别 46／48 项，
   dash／bash 通过，其中一项需显式启用的主机 netlink 探测跳过。
   首轮默认宿主 BusyBox ash 使用内建 ip，绕过测试 PATH 中的假 ip，造成九项
   测试夹具失败；未据此改生产逻辑，改用可正确拦截命令的两种 shell 重跑。
   尚未执行的 ucode/ubus 专项不计入本轮通过数量。
4. E5 原有网络恢复回归 18 项通过，保持漫游与用户开关约束。

相关日志在各仓库 `out/build-logs/`：

- Pro：`20261105-183037-carrier-ra-target-unit-17596`、
  `20261105-182317-carrier-ra-usable-expiry-9727`、
  `20261105-183202-carrier-ra-pro-ndp-regression-17697`。
- MU300：`20261105-183106-carrier-ra-mu-final-unit-17659`、
  `20261105-183025-carrier-ra-ndp-shell-regression-14482`。
- E5：`20261105-183047-carrier-ra-e5-unit-17629`、
  `20261105-183118-carrier-ra-e5-recovery-regression-17677`。

本轮没有操作实机，没有宣称通过真实运营商数小时续期测试。后续现场需核对
RS 后是否收到新 RA、路由到期时间是否刷新，以及切卡／手动断网后是否正确停发。

## 协议参考

- [RFC 4861](https://www.rfc-editor.org/rfc/rfc4861.html)：RS／RA 格式、校验及寿命处理。
- [RFC 8319](https://www.rfc-editor.org/rfc/rfc8319.html)：Router Lifetime 最大值。
- [odhcp6c ra.c](https://git.openwrt.org/project/odhcp6c/tree/src/ra.c)：现有 RA 处理保持不变。
- [Python 3.13 timerfd](https://docs.python.org/3.13/library/os.html#timer-file-descriptors)。

半寿命续期是针对现场兼容性的请求策略，不声称 RFC 要求固定周期发送 RS。
