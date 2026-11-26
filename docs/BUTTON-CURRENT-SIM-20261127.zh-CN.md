# 快捷键从实际数据卡继续切换

列表取下一项的原算法本来就是 `(index(current)+1)%length`。缺陷在 current 的来源：
物理卡 status 和 U30 Air 快捷键直接读取 `/run/mu300-data-sim-actual`，而网页的数据
状态还会根据 netifd 正在使用的 `l3_device` 修正 actual_sim。当启动快照或保存值
与基带实际数据栈不一致时，按键会选中已在使用的卡，看起来像从第一项重新起步。

MU300／Pro 的既有 `mu300-data-sim` 增加只读 `current` 操作，复用本文件已有
`data_card()`：经 `mu300-sim-env` 与原 AT owner，显式选择槽位0后查询 SPSWDATA。
它不写保存值、不更改快照、不切卡、不重拨、不启动后台任务。返回未知或查询失败
时拒绝切换，不猜默认卡，也不用保存值回退。

- U30 Air 的两卡切换根据实时 0／1 取另一栈。
- V50 的列表循环以该数据栈在当前 `logical_cards` 中的实际物理卡源为起点。
- U30 Pro／F50 Pro 的 ucode pair／list 策略也先映射实时数据栈；灯光反馈与实际
  目标使用同一份计划。不会因旧 active_card／saved_card 再次选中自己。
- U30 Pro 的新卡源组合事务也使用实时数据栈判断是否需要中断当前 WAN。

只在用户明确触发动作时查询一次，保留现有控制通道／队列、SFUN 顺序、GPIO
事务和失败恢复；NR0 的 URC 接收规则未改变。E5 不在本次更改范围。

离线回归覆盖：保存值／快照与实际卡相反、没有 WAN 时查询、查询失败不猜卡、
每种形态逐卡循环和回绕、混搭 pair、单源无操作及原切卡／更新保留回归。
MU300 基础设置／所有形态分发及 diff 交付共 23 项通过，日志
`out/build-logs/20261126-194723-mu300-current-source-core-61198/`。
Pro 基础设置／形态迁移 45 项、物理／数据卡／更新 68 项、目标组合事务 7 项、
目标灯控 6 项、实际 ucode 分发 2 项及实时选择矩阵通过；日志
`out/build-logs/20261126-194926-pro-card-current-final-matrix-61959/`。
两机型 40,000 次连续选择复测通过，日志
`out/build-logs/20261126-195612-pro-cycle-sustained-final-8963/`。
以上为离线验证，未进行本轮真实卡槽操作。

MU300 两轮普通更新保留回归通过：
`out/build-logs/20261126-195657-mu300-current-source-update-8988/`。
