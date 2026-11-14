# 显式选择实际数据卡后恢复拨号

U30 Pro 的用户现场确认：保存的 data_sim=0、实际数据卡=1，卡1 absent、
卡2 ready；WAN 被空卡1留在 unavailable / NO_DEVICE。明确选择卡2、恢复
协议可用状态后，WAN 在 sipa_eth8 上取得双栈地址、路由和 DNS。

MU300 的共用 mu300-data-sim 存在相同分支：目标与实际卡相同则只保存设置，
不会恢复等待旧保存卡的 WAN。现在仅在保存目标改变、WAN 未连接且未拨号、
autostart=true 时，解除旧协议不可用状态并走现有 ifup。普通异卡切换的成功
分支也复用这条恢复路径。连接中、拨号中及手动 ifdown 的同卡操作不重启 WAN。
不会按卡存在性擅自换默认卡，不修改 GPIO、AT owner、SPACTCARD、RF 顺序或 URC。

正式源为 rootfs/overlay/opt/mu300/bin/mu300-data-sim，两条内核发行线共用；
不需要内核改动。tests/test_data_sim.py 在生产脚本上覆盖双向保存／实际卡不一致、
协议不可用、ifdown 清除 autostart、手动断网、失败恢复及原有单待策略。
本轮 18 项离线回归全部通过，日志：
out/build-logs/20261114-144927-data-sim-adopt-regression-42136/command.log。
本仓库未做实机部署或联网验收，历史发布包不因源码修改而自动更新。
