# V50 board 2 内置 SIM1 的可选启动前路由修正

本修正默认关闭，只针对已确认受影响的 `board_id=2` V50。其他板型、
外置卡选择及实时切卡仍由原 `mu300-v50-sim` 控制。不要仅凭“移动卡”
或“V50”型号就启用；运营商与物理卡位不是同一个概念。

## 现场问题和证据范围

一台有两张内置卡、未插外置卡的 V50 在冷启动后：

- 设备树/原厂映射为 board 2，支持物理卡 `0,1,3`；物理卡 0 通过
  `psim_select=0` 接入逻辑 SIM0，物理卡 3 是同一逻辑栈的外置路径。
- 启动初期 `psim_select=1`，即指向空外置路径。原生较晚的选卡初始化
  后，内置移动卡仍曾返回 `+CME ERROR: 10` / `CFUN=0`；内置电信卡可用。
- 驱动的初始选择来自 `androidboot.psimswitch`。现有 `mu300-v50-sim boot`
  在 RIL 握手后的射频初始化流程内恢复路由。修正尝试将 SIM0 的内置路径
  提前到 `modem_control` 启动之前，保留原生后续流程。

2026-10-30 该设备的 R3 实验版本已验证：启动前一次写入 `psim_select=0`
后，两张卡均 `CPIN=READY`、`CFUN=1`、CEREG 注册状态 1，运营商分别
46000 / 46011。先前电信数据联网、随后用户手动切换移动数据均返回 HTTPS 200。
其持久化版本 R3P 又经一次真实重启自动执行：约启动 8.58 秒完成一次
1 字节写入和三次稳定回读；`chip_select` 保持原值 1；约 19 秒注册，
约 37 秒获得数据地址。移动接口绑定、校验证书的 HTTPS 返回 200。

这些结果证明这一台设备的早期路由修正有效；不证明所有 V50、其他板型、
运营商或射频模式都会遇到同一问题。`chip_select=1` 时电信卡依然正常注册，
因此本修正不根据卡名推断或强制更改另一选择器。

早期 shell 写入曾返回非零但回读已改变，未采集当时 errno。驱动源码确有
写后电平不符时返回 `-EIO` 的路径，但不能据此认定那次失败就是 EIO。
本提交的 writer 要求写入成功且完整，拒绝任何错误，包括回读符合时的 EIO。

**版本边界：**现场固件为 OpenWrt 25.12.5 / Linux 7.2.8，镜像标记
`mu300-r20261029.184837-ge509ca20-s498d117b`。本提交整理自已验证的 R3P，
面向 `minikano-openwrt-feats` 基线 `fe52afe3fce41a60a4971cbe306d7c5635869f1a`。
它使用 UCI 显式启用，去掉设备专用文件哈希，并严格拒绝写错误；这一整理版本
通过隔离测试，尚未单独构建固件或在硬件上重启验证，不能等同于 R3P 的实测。

## 启用与检查

仅在确认上述板型、卡位和症状，且已有本地恢复通道后使用：

```sh
/opt/mu300/bin/mu300-v50-sim status
/opt/mu300/bin/mu300-v50-sim-preboot plan
uci set unisoc_modem.main.v50_preboot_sim1='1'
uci commit unisoc_modem
```

这些命令不会切换当前卡或重启。启用只在下一次正常冷启动时执行。
不要手动调用 `apply`，它会拒绝非 `android-vendor-start` 的父进程，
也拒绝已经启动 `modem_control`、AT broker 或 RIL 的时刻。

所需 Python 3 已包含于 OpenWrt rootfs 软件包清单；启动使用设备内置的
BusyBox `timeout`。不要换成 GNU timeout，因为两者的父子进程语义不同。

启动后检查：

```sh
cat /etc/mu300/v50-sim-preboot/status
cat /etc/mu300/v50-sim-preboot/last-boot.log
cat /etc/mu300/v50-sim-preboot/write-detail.json
cat /var/log/mu300-v50-sim-preboot.log
/opt/mu300/bin/mu300-v50-sim status
```

`selector_verified_waiting_for_modem` 仅表示选择器检查通过，必须再通过
原生双卡状态/AT broker 核对 SIM READY、射频和注册，并使用当前数据接口
验证 DNS 与 HTTPS。另一张卡注册不代表已经实测其数据切换。

## 边界与失败行为

- 仅允许 board 2，且保存物理卡/数据逻辑槽组合为 `0:0` 或 `1:1`。
  外置 `3:0`、不一致状态及其他板型都不执行写入。
- 只修改 `psim_select`，不修改 `chip_select`、数据卡选择、RF 开关、
  APN、网络、防火墙或代理。原生控制器继续负责注册和数据联网。
- 一次完整的 1 字节写入后做三次独立回读；另一选择器必须不变。
  已为内置路径时不写硬件，也不清理身份缓存。
- 成功切换后仅清理逻辑 SIM0 的过时身份/状态缓存，备份旧身份文件；
  不清理 SIM1 的身份、短信、流量或锁频策略。
- `/run/mu300-v50-sim-preboot-claim` 限制本次启动只尝试一次。
  写前将 `inflight` 落盘并同步；失败或 5 秒超时后保留该标记，后续启动
  暂停修正，直到操作者检查。失败时原生 vendor 启动继续，不阻断电信初始化。
- 写入返回失败且 SIM0 读回已经改变时，只尝试恢复该选择器的原值；
  超时或失去可读回状态不能保证恢复硬件原值，故保留暂停标记而不循环写。
- `/etc/mu300/v50-sim-preboot` 保存最近两次日志和失败标记，默认私有权限。
  日志/缓存备份可能包含身份信息，请勿整目录公开。

## 停用和恢复

```sh
uci set unisoc_modem.main.v50_preboot_sim1='0'
uci commit unisoc_modem
```

停用不触碰当前射频/路由。下一次启动完全使用原流程。不要在带业务的运行
系统中直接写 sysfs 来“撤销”；需要完整撤回源代码时使用原固件的正常恢复路径。

若出现暂停，先备份上述状态和日志、停用并分析原因。确认已经修复且计划下次
冷启动再试时，才将 `inflight` 改名保存后重新启用。不要安装自动清理失败标记
的定时任务。源码重装/系统重置可能移除程序或 UCI 配置；配置备份不是固件包。

## 验证方式

`tests/test_v50_sim_preboot.py` 使用普通临时文件测试选择器、缓存和进程边界，
包含成功、无操作、外置/其他板型拒绝、正在运行时拒绝、逐启动限制、持久失败
暂停、写入失败/部分改变回退、超时及原生启动继续。writer 单元测试覆盖
open/write/close errno、短写及三次回读要求。这些是离线回归，不是 GPIO 或基带实测。
