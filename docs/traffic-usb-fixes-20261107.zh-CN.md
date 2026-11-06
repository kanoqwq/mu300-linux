# 流量池卡槽隔离与 U30 Air 非 PD USB 角色

## 确认的问题和修改

MU300 原生看板始终读取 `run/unisoc-modem-traffic/view.json`，该文件属于
SIM1。现在显式查看卡槽优先，未指定时跟随实际数据卡；SIM2 读取
`run/unisoc-modem-traffic/sim1/view.json`，缺失时不挪用另一张卡的统计。
统计快照补齐套餐名称，主页同时显示对应名称。

流量池页面刷新现在同时同步套餐表单与统计，保留同一张卡未保存的编辑；
查看另一张卡时完整载入它的配置。读取携带明确卡槽并丢弃迟到或错卡响应；
保存和清空绑定用户实际确认的表单卡槽，不在确认结束时重新取当前查看卡槽。
E5 保持其自身 `view-sim0.json` / `view-sim1.json` 布局，未改统计数据库格式。

## USB 现场证据

实机 U30 Air，7.2.8，原版本
`mu300-r20261106.135915-g9b4da05f-sf64b99bc`：
日志记录三次角色切换失败，Type-C partner 的
`supports_usb_power_delivery=no`、`power_operation_mode=default`；
原脚本无条件写 `data_role`，而该接口是协商数据角色交换。

U30 Air 非 PD／未连接时通过 TCPM 的 `port_type` 请求 source/sink，
等待内核处理 CC 接入。已协商 PD 时保留数据角色交换，可保留主机＋受电。
正常启动恢复 dual，之后仍由已有启动策略应用用户保存的设置。
不绕过 Type-C 驱动写充电芯片，不增加用户态轮询或强制 VBUS。

接口依据：[Linux TCPM 的 tcpm_dr_set 与 tcpm_port_type_set](https://github.com/torvalds/linux/blob/master/drivers/usb/typec/tcpm/tcpm.c)。

## 验证范围

- E5 原生看板 11 项、MU300 原生看板 18 项、USB 管理 24 项、U30 Air
  PD／非 PD 分支 6 项离线测试通过。
- 两仓库真实 ucode/ubus 私有流量服务各 46 项 RPC 检查通过，包含分卡
  保存、计数、确认清空和不影响另一张卡。
- 两仓库实际网页/RPC 声明在手机和桌面各一轮浏览器检查通过，包含表单
  切卡、未保存编辑、乱序响应、确认期间换卡后保存／清空范围。
- USB 脚本已在该 U30 Air 热部署，原文件在电脑 `work/usb-role-20261107/`
  与设备 `/tmp/mu300-usb-before-20261107` 保留。当前非 PD 设备模式／sink、
  USB NCM 480 Mbit/s 和 SSH 连通检查通过。
- 流量池网页和原生看板已热部署到同一 U30 Air，实际 RPC 回读确认两张卡
  分别返回自身额度和用量。原件及校验记录保留在 `work/traffic-slot-20261107/`。
- 本轮尚未实物拔插验收 OTG 和 PD 扩展坞，不把离线检查视为这些硬件已验收。
