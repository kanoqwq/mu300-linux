# 下游 IPv6 入站路由审计（2026-11-15）

## 反馈与结论边界

反馈为更新 11 月 14 日版本后，公网可访问 WAN IPv6 地址，但无法访问下游
IPv6 地址；放行防火墙、撤销补丁无效，Android 或重刷 11 月 13 日正常。
目前没有机型、内核版本、路由表或上下游抓包，不能确认实际故障根因。

MU300 的 ad7296c6 与其父提交，以及 Pro 的 c83f30a 与其父提交之间，
ndp-learn、ndp-interface.sh、ndp-events.sh 和对应 IPv6 默认配置没有变化。
两仓库新改的 PDP 地址解析仍由 RA 获取 IPv6 地址与前缀。共享安装器
471dcf8 → 57e159c 只改 ZIP 上传准备及相关测试，没有修改网络配置保留策略。

本次在现有 ARM OpenWrt 环境与隔离的 Docker 网络中复现了一处同症状的旧缺陷：
WAN 自动生成的共享 /64 直连路由 metric 256，优先于 ndp-learn 添加的
LAN /64 路由 metric 1024。没有 LAN /128 路由的下游地址会被送回 WAN，
而路由器 WAN 自身地址仍命中 local 表。此复现不能证明群友遇到的
11 月 14 日问题就是它，也不能证明修正后其机器已经恢复。

## 源码修正

MU300 与两款 Pro 的共享 ndp-learn 将其管理的 LAN /64 metric 改为 128。
这是对既有共享前缀路由的优先级修正，不删除 WAN 直连路由、不修改 WAN 地址、
上游默认路由、链路本地路由、RA 续期、NAT66 或防火墙权限。
前缀撤销仍只删除本服务管理的 LAN /64 与对应主机路由。

E5 默认走独立的 ModemManager／DHCPv6 前缀扩展路径，未运行此脚本，
因此没有把 MU300 的路由补丁套给 E5，也没有据此宣布 E5 无此类故障。

## 验证

- 修正前实际内核查询：下游地址经 sipa_eth0，metric 256；移除冲突直连路由
  后才经 br-lan。记录在 MU300 的 ipv6-inbound-route-audit 任务。
- 新增 tests/ipv6_inbound_netns.py，使用已有目标根与镜像，网络 none，
  仅在容器内创建 dummy 接口。覆盖 sipa_eth0／sipa_eth8、两个共存前缀、
  无主机路由的下游目标、WAN local、上游链路本地网关、重复修复，
  以及没有 WAN 直连 /64 的情形。它验证实际 FIB 选择，不是公网端到端收发。
- 既有前缀撤销、事件监控、切卡、纯 IPv6 与双栈续期回归通过。
  Shell 夹具显式绑定 ip stub，避免 standalone BusyBox ash 绕过 PATH，
  无意执行宿主的 ip applet。现有容器运行脚本同步使用新 metric。
- MU300：20261114-181909-ipv6-inbound-route-regression-37472；
  20261114-182049-ipv6-relay-shell-regression-41542（56 项，55 通过，1 项为
  默认未启用的只读 netlink 订阅检查而跳过）。
- Pro：20261114-182129-ipv6-inbound-pro-regression-46461（同组回归和双卡 FIB 检查）。
  任务目录时间戳为 UTC；上述任务在新加坡时间 11 月 15 日执行。

所有检查均为离线。没有连接设备、扩大放行规则、热部署、刷写或重打固件包。
故障机可先只读运行 ip -6 route get <下游公网IPv6>，核对实际出口；
若已走 br-lan，则此修正不足以解释现场，应继续对照入站与回包抓包，
而不是继续改变防火墙策略。
