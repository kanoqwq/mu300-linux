# 固定 TTL 与软件 Flow Offload 共存（2026-11-06）

适用于 E5 全机型、MU300 的 6.18／7.2，以及 U30 Pro／F50 Pro。

## 实现

- 将旧 `inet mu300_ttl` 的 postrouting 规则迁移到 `netdev mu300_ttl` 的蜂窝 egress 链。
  软件 flowtable 的直接发送仍经过 `dev_queue_xmit()` 的 netdev egress，故可同时改写 TTL／IPv6 Hop Limit。
- 软件卸载保留用户选择。只有旧 TTL 功能保存了 `flow_offloading` 代关记录时，才在新规则就绪后恢复。
- 硬件卸载可能绕过 CPU；固定 TTL 期间暂停它，保存归属，关闭 TTL 时恢复。失败保留标记供重试。
- IPv4 链路本地／组播／广播，以及 IPv6 链路本地／组播、ND／MLD 控制报文不改写。
- 原子替换自有表，不清空用户规则。配置保存、操作互斥、UCI 提交及 firewall reload 错误均保留重试状态。
- net hotplug 跟随 `sipa_ethN` 的新增／删除／移动重新绑定；WAN ifup／ifupdate 与开机服务继续应用保存设置。
- 新 hook 纳入 LuCI 包及根文件构建路径；Pro 的成品根校验增加源文件比对，防止漏带。
- 页面改为“软件流量卸载”，说明共存及硬件限制；中／英／土翻译同步更新。

## 验证

- 三仓库各 19 项隔离后端回归：校验、互斥、热插拔、迁移、用户关闭卸载、写入／提交／reload 失败重试及 JSON 状态。
- 三仓库中／英／土页面回归：文案、等待接口、硬件暂停状态、输入校验、提交期间禁用控件、失败后恢复。
- 已核对 E5 九个现成机型内核、MU300 两个内核与 Pro 6.12 配置均启用 netdev egress。
- U30 Pro 实机内核（6.12.94-u30pro-p1）独立网络命名空间：IPv4 NAT、IPv6 转发各 43 包，
  postrouting 计数各为 1，证实后续走软件 flowtable；出口 TTL／Hop Limit 均为 65。
  在已加速连接上改为 128、关闭后恢复正常递减均通过；IPv4 校验和、IPv6 UDP 校验和通过。
  IPv6 组播保持 1、NDP 保持 255、LAN 出口保持 50。
- U30 Pro 真实 `sipa_eth0` IPv4／IPv6 各发一个小 UDP 探测包，捕获出口跳数均为 65。
  已热部署引擎、JSON 适配器、net hook、页面和翻译，逐文件备份在本机 work/ttl-egress/before。
  测试结束恢复原来的 TTL 关闭、软件卸载开启、硬件卸载关闭；修复文件保留。
- 其他机型完成源码／配置回归，未进行各机型实机测试；没有重新打包或刷写启动镜像。

## 测试注意与独立发现

首轮命名空间测试直接创建名为 `sipa_eth0` 的 veth，触发已有 Pro SIPA 驱动异常：
`sipa_dummy_netdev_join()` 仅凭名字强转 `netdev_priv` 为 SIPA 私有结构并解引用 `pdata`，
未排除其他 namespace／驱动。该缺陷与 TTL 规则无关，现场已保存，用户授权正常重启后恢复。
本轮不改 SIPA 内核；此问题仍需单独修复。

现有 `openwrt/tests/ttl-packets.py` 只创建 `ttl_lan`／`ttl_wan` 等虚拟接口，
通过测试用 nft 包装器将生成规则中的设备绑定映射到 `ttl_wan`，保留完整 egress／flowtable／NAT 路径。
peer 的 ingress 丢弃仅供 AF_PACKET 仿真，避免报文重新进入本 namespace 路由。
该测试须显式以 root 运行，自动创建并销毁自己的 namespace，不应在默认构建中直接访问实机。

参考：
- https://docs.kernel.org/networking/nf_flowtable.html
- https://github.com/torvalds/linux/blob/v6.12/net/netfilter/nf_flow_table_ip.c
- https://github.com/torvalds/linux/blob/v6.12/net/core/dev.c
