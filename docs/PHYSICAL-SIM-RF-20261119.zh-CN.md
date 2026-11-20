# 内置／外置卡切换的原厂核对与射频完成确认

日期：2026-11-19。此项修改不重启调制解调器，不改 GPIO 拓扑、卡源编号、
射频权限、单／双待策略、AT owner 或自动事件入口。

## 实际原厂证据

| 产品 | 本机已有原厂资料 | 核对的方法 |
| --- | --- | --- |
| V50 / MU3351 | V50_V1.0.0B15，Android 13，20250527.235207 | UniSubscriptionInfoUpdater 的 dealWithFourSimSwitch / dealWithThreeSimSwitch / handleMessage；VendorTelephonyManager.setProtocolStack |
| U30 Pro / MU5358 | U30Pro_AIV1.0.0B05，Android 15，20260528.145811 | VendorSubInfoUpdater 的 dealWithTwoEsimAndOnePsimSwitch；VendorTelephonyManager.setProtocolStack |
| F50 Pro / MU3356 | F50ProV1.0.0B22，Android 15，20260321.130936 | VendorSubInfoUpdater 的 dealWithTwoEsimAndTwoPsimSwitch / dealWithTwoPsimSwitch / handleMessage；VendorTelephonyManager.setProtocolStack |

V50 资料提取记录位于既有调查目录 `work/v50-card-topology/stock-evidence.json`；
Pro 拓扑依据分别在 `devices/u30pro/board-topology.json` 和
`devices/f50pro/board-topology.json`。这些路径记录证据来源，不是构建输入。

三款实际 `vendor-telephony-framework.jar` 的 SHA-256（仅用于证据追溯，
绝不用于限制运行版本）：

- V50：`b5189f6e42a4f8b6d97f0bdffc82074498a572af68e04eff752ac74c6031bef7`
- U30 Pro：`431b1b803be54752b2a775bfce72243a8287b6a36f286af64bb69daa657482b8`
- F50 Pro：`131411217d088d325457738a5bb8dec49c92309e8d4761707d89654cd09890a4`

实际二进制中的 setProtocolStack(false) 是先按槽位 SPACTCARD 选择上下文，
SFUN=5 关闭协议栈，再 SFUN=3 关闭 SIM。切 GPIO 后，卡源切换方法投递
延迟 5000 ms 的事件；setProtocolStack(true) 是 SFUN=2、随后 SFUN=4。
原厂还按有卡状态和单／双待策略决定是否启用目标、关闭另一栈。
MU3356 外置卡 1/2 的物理编号与逻辑栈交叉，沿用独立拓扑，不套 MU5358 表。
原厂 Manager 还在操作结束后选回 SPACTCARD=0；当前实现每条控制命令都显式
选择目标上下文，继续使用各操作原有 owner／队列，不照抄全局上下文复位，
也不将控制命令搬到唯一 URC 入口 NR0。

## 确认的源码缺口与修复

现有物理卡源事务的关栈、SIM 上下电、GPIO、5 秒延迟和配置回放顺序与
上述基础流程一致，没有发现普遍漏发 SFUN=4。确认的缺口在完成判断：
SFUN=4 最终返回 OK 后，没有读取 CFUN，便继续逻辑选卡、保存新卡源。

离线模拟“SIM 已就绪、SFUN=4 返回 OK，但 CFUN 仍为 0”，旧代码在三款
模型上均返回成功、保存新卡源；模拟延迟开启时也没有等待完成。
这证明事务会错误报告完成，尚不证明用户报告的实机为何停在 CFUN=0。

修复在原 RF mutex 和原按槽位选择的 AT owner 内，SFUN=4 后限次查询
CFUN，只有 CFUN=1 才继续。等待沿用现有 mobile-data 的 30 次启动检查
预算，正常即时就绪只增加一次查询；静默／无法解析立即失败。不重发复位
序列，不等待信号格数或注册成功，弱信号／欠费不阻止已就绪卡的选择。
若射频未恢复，调用原事务回退旧卡源；回退失败仍保留 incomplete 标记并
明确报错，不保存成功或假装 WAN 已恢复。

关闭射频的卡源配置以及开机阶段仍由原策略处理，不擅自开启射频。物理
换卡还一并清除旧卡的 C5GREG 快照（slotN-nr.json），避免沿用旧 NR 注册状态；
不清短信、流量、APN、锁频设置或用户数据。

## 验证边界

`tests/test_physical_sim_rf.py` 运行真实 shell 切卡事务，外部 AT/GPIO/
netifd 为离线夹具：覆盖延迟 RF 就绪、接受请求但未开启、失败回退、内外
双向切换。原有机型测试继续覆盖 GPIO／逻辑栈映射、手动关闭 WAN、单／
双待、首次 SIM2 启用、关闭射频的卡源配置和开机不提前开启 RF。

这轮未连接设备、未切实体卡、未刷写。用户实机反馈的根因和各批次卡片
兼容性仍须用故障机日志验证，不能将这些离线测试称为实机修复确认。

本轮结果：MU300 原有 V50 的 30 项通过，新增 3 项在修正夹具执行方式后通过；
Pro 的新增用例与相关原有用例共 13 项通过，均覆盖 dash、bash、BusyBox ash。
日志保存在各仓库 out/build-logs：MU300 的
20261119-101010-physical-sim-rf-regression-54051（原有 30 项通过，新增夹具权限错误）、
20261119-101354-physical-sim-rf-final-71588（新增 3 项通过）；Pro 的
20261119-101450-physical-sim-rf-pro-final-73756（13 项通过）。
