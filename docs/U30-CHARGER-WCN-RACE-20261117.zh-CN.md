# U30 关机充电灯与 WCN dump／掉电竞态

## 确认范围

用户反馈 U30 关机充电结束后仍呼吸，并提供了另一位用户的 AI 分析和
`wcn_reset_cp2 → wcn_chip_power_off → pcie_runtime_put →
wcn_firmware_ready_close → sprd_pcie_mem_read → sprd_pcie_bar_map` 调用链。
没有取得该次 panic 的原始 kmsg，未在实机故意触发故障。

以下缺口可从本仓库源码确认，修正适用于共用源码的 6.18／7.2 发行线。

## 关机充电灯

`mu300-charge-mode` 在进入充电等待前调用一次 `mu300-led battery`，随后等待
`mu300-keys -c`；正常的 LED 事件服务此时尚未启动。原等待器收到电源事件后
只回读 online，未更新 LED，所以进入 Full 后仍保留原硬件呼吸模式。

充电等待器现于首次读取和已验证的内核 power_supply 事件后执行现有
`mu300-led battery`，消息丢失 ENOBUFS 时也重新读取并刷新。不增加周期轮询，
不改变充电参数，不把 100% 强制视作硬件充满；仍以已有供应者返回的
Charging／Full 状态决定呼吸／常亮。非电源消息和用户态伪造来源不触发刷新。
拔线 500 ms 去抖及电源键长按启动保留。调用只在原有 U30 关机充电入口发生，
正常按键监听和无电池机型不进入该入口。

## WCN 生命周期

旧断言入口持有 debug mutex，但未持 power_lock 检查上电／掉电状态，
且在 debug MMIO 之后才设置 carddump。正常 stop_marlin 可以在该窗口移除
PCIe 卡；掉电标志在 stop 末尾清除也不能弥补这个检查与使用间的窗口。
手动 rebootwcn 原先同样没有上电检查，并提前清除了保护。

修正如下：

- `wcn_begin_dump()` 在 power_lock 内同时检查仍上电、未处于掉电过程，并设置
  carddump。断言和非集成式 dumpmem／rebootwcn 先取得此保护再访问总线。
- stop_marlin 原有的锁内 carddump 检查保留：若 stop 先完成，dump 被拒绝；
  若 dump 先取得保护，正常 stop 不拆除总线。显式 poweroff_wcn 也遵循该锁和保护，
  已关闭时不再人为设置子系统位去重复掉电。
- rebootwcn 在整个复位期间保留 carddump，结束后再清除；无线已经关闭时返回
  ENODEV，不访问旧 BAR，也不隐式开启无线。
- 强制复位使用的 wcn_chip_power_off 在同一锁内跳过已经掉电的芯片，避免再次
  runtime_put／访问固件同步区。上电状态下的复位和通知顺序保留。

锁顺序保持 debug mutex → power_lock。power_lock 只用于原子认领，不横跨整个
dump／reset；强制复位原有流程还会取得它，不能在认领后一直持锁。
集成式 WCN 的原有断言分支保持原实现；本次确认和测试对象是 MU300 PCIe WCN。

## 离线验证与构建

- 原生 C 充电等待测试：真实 poll、管道／套接字，模拟内核事件元数据，验证
  Charging → Full 刷新、拔线、消息丢失恢复、忽略无关／伪造事件及原有按键逻辑。
- 实际 shell LED／充电启动回归：Full 停止硬件 pattern 并常亮；普通启动、
  开机动画交接、拔线、超时、Wi-Fi 灯和独立电池颜色仍通过。
- WCN 测试编译实际 claim／assert／reset／stop／proc 命令代码，使用 pthread
  锁及模拟 BAR，确定性覆盖 stop 先执行和 dump 先执行，以及各复位策略、
  手动 dump、掉电后 rebootwcn、重复掉电和现有恢复逻辑。ASan／UBSan 通过。
- 已用既有工作区和受控构建入口编译 ARM64 静态 mu300-keys，更新注册输入的
  二进制／源码摘要；两条内核均通过原 build-modules.sh 编译 wcn_bsp。
  原 Image 与对象树逐字节匹配，不重编未改动的 Image，不跨内核混用模块。

这些是离线回归和实际编译结果，尚未进行充满等待或无线故障的实机验收。
没有热部署、刷写或更改当前连接设备。后续按正常完整包／更新路径带入已更新
mu300-keys 和对应内核的 wcn_bsp.ko。

本轮最终任务记录（执行主机的任务编号时间与用户时区可能不同）：

- `20261116-170706-mu300-wcn-all-regression-45915`：6 项 WCN 回归通过。
- `20261116-170707-mu300-charge-led-regression-46503`：21 项 U30 LED 回归通过。
- 充电启动／按键共11项通过；另13项依赖未指定的真实 OpenWrt ucode 测试环境或
  不公开的原厂 loader，按原测试条件跳过，不计入通过数。
- `20261116-170547-mu300-charger-keys-build-45875`：静态 ARM64 程序及摘要已更新。
- `20261116-170838-mu300-wcn-618-final-63273`、
  `20261116-170945-mu300-wcn-72-final-63297`：两版最终 WCN 模块构建通过。
- `20261116-171137-mu300-charge-wcn-artifact-check-63336`：两版完整模块清单／摘要、
  BTF、对应 Image 与原对象树、编译副本与当前源码、按键程序源码／二进制摘要全部匹配。
