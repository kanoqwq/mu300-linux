# 蜂窝默认路由继承接口策略

2026-11-20 核对 MU300 与 Pro 的公共 mu300cell 协议，确认两个入口都额外
执行了 `ip -4 route replace default dev "$ifname"`：初次拨号及 PDP 地址更新。
未指定 metric 时产生跃点为 0 的路由，会覆盖 netifd 按用户配置安装的默认路由。

历史提交 `cd4fb28ba532a22d8c41fa5c9ac003ca2a98d169`（2026-10-01）为解决
netifd 删除蜂窝地址、看门狗约每 60 秒重拨而引入 address-external 和手工路由。
手工路由有明确的修复目的，但注释误把 address-external 当成所有地址及路由
均为外部所有。OpenWrt 25.12.5 的 netifd `2026.02.26~cbb83a18` 实际只对
地址应用这个标志；通过协议上报的路由仍继承接口 metric、路由表与 defaultroute。

修复与 Pro 一致：保留原来的地址所有权及 AT 流程，删除手工 IPv4 默认路由
增删，继续通过 proto_add_ipv4_route 上报。由 netifd 负责路由建立、更新及清理。
没有写死新的优先级，没有改变 IPv6 RA 处理，没有增加轮询或触发射频重启。
E5 使用独立的 ModemManager 路径，不移植 mu300cell 脚本。

离线验证：

- `tests/test_cellular_mobility.py`：24 项通过，覆盖地址续租、AT 失败、事件合并、
  无卡等待、双卡 owner 和看门狗；新增检查默认路由仅上报 netifd。
- `tests/test_cellular_route_metric.py`：在无外网的独立容器使用真实 netifd、ubus、
  内核 dummy 网卡及 AT stub；九项场景通过：Wi-Fi 优先、Wi-Fi 跃点重载、蜂窝
  跃点重载且不重拨、地址事件保留优先级、Wi-Fi 断开回落蜂窝、用户明确 metric=0、
  禁止默认路由、自定义路由表、关闭时清理。
- 记录：`out/build-logs/20261120-115509-mu300-cellular-route-metric-regression-4179/command.log`。

这是源码和离线集成验证，没有进行实机部署或硬件验收，本次未重新打包。
