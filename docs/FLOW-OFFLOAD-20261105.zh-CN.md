# fw4 软件 flowtable 端口发现修复（2026-11-05）

MU300 U30 Air 的 7.2.8 实机确认：软件加速虽已开启，原 flowtable 只含桥主设备，
缺少实际 usb0/wlan0，导致建流返回 ENOENT。加入真实成员后 IPv4、IPv6 连接均
出现 IPS_OFFLOAD，无需安装 sprd_sfp/SFE。未验证峰值吞吐，不能保证具体速度提升。

本仓库通过 `tools/stage-flow-offload.py` 修改 fw4 的单一设备发现函数。它读取
防火墙已解析的 zone/逻辑网络、netifd 当前设备和网桥/VLAN 下层设备，去重生成
端口；活跃蜂窝接口随 PDP/SIM 变化解析，不写死 sipa_eth0。不改变用户 offload
配置、TTL 关闭加速策略及既有硬件加速探测，不引入服务或定时器。

适用构建：MU300 全新根（两内核共用）；E5 全新根和公共机型 profile；Pro 的
全新根和 sources/finish。MU300/Pro 旧固定 SIPA 补丁已移除，由转换器明确迁移，
不保留两套运行时逻辑。未知 fw4 源码布局需先审查再适配，不覆盖整个模块。

验证入口（使用已有的目标 OpenWrt 容器，测试不访问硬件）：

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build \
  --label flow-offload-regression -- python3 openwrt/tests/flow-offload.py
python3 tools/build-release.py run --workspace /home/kano/openwrt-build \
  --label ttl-flow-regression -- python3 openwrt/tests/ttl.py
```

目标测试默认镜像 `mu300-openwrt-base:25.12.5`，可用 FLOW_TEST_IMAGE 显式选择
本仓库的等价 OpenWrt 测试容器；FLOW_FW4_SOURCE 可指定已准备 root 的原始 fw4.uc
来核验实际输入，不修改该 root。10 项目标 ucode 用例和 9 项 TTL 用例均已通过；
E5 还检查了其真实 base 的输入，Pro 的正式准备根已通过完整目标根检查。

MU300 6.18、E5 6.18、Pro 6.12 与 7.2 的内核建流代码不同，不能说全部旧内核
都完全无法加速。本轮仅 U30 Air 7.2 做了真实连接验证，其他为源码/构建回归。
不移植 Android 5.4 的 sprd_sfp，不改内核、AT、NV、无线参数，也不承诺提高
设备本机 iperf3 的吞吐；本次修复针对经过防火墙 FORWARD 的流量。
