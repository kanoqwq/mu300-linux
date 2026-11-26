# 临时启动满一分钟自动设为永久（2026-11-26）

## 行为与交付

- Pro、MU300、E5 的正常 OpenWrt 临时启动，在开机运行满 60 秒后自动保存为永久启动；不需要打开网页，不自动重启。已经永久启动时直接退出。
- 使用 ucode 的一次性 uloop 截止定时器，挂入已有 S99 启动成功服务。没有独立轮询、常驻重试或新的 rc.d 链接；升级保留旧 rc.d 时也能生效。
- 实际保存沿用各平台 next-boot 程序及其共享锁、misc 写入校验和事务恢复。没有改动原有失败次数、回退策略或 Linux/Android 槽位判断。
- 本次开机中，用户明确选择 Android 或临时启动时，该选择优先，不会被定时任务改回去。并发时由同一启动锁串行化；自动任务写入失败则保留原状态，不反复写入。
- U30 Pro 关机充电不会启动 S99；退出充电画面并正常开机后另等 60 秒，不把长时间关机充电算作正常启动时间。E5 RAM 试运行镜像继续禁止设为永久，failsafe 不触发。
- 系统切换页面在进入后的 65 秒做一次只读状态刷新，页面退出时取消；看板沿用已有刷新。没有新增后台查询循环。
- worker、ucode 模块、平台启动脚本和 next-boot 都在镜像拥有的路径。复用现有 rootfs staging 和普通更新路径；构建检查补入关键启动输入，差分更新无需手动启用服务。没有改写用户 Wi-Fi、射频、插件配置。

## 六轮离线回归

1. ARM64 真实 ucode/uloop：正常 uptime 的截止时间、充电转正常开机、缺失/异常 uptime；真实等待一分钟，三仓库各执行一次保存，随后永久状态再次启动 worker 立即退出。
2. 真实 next-boot 事务：Linux A/B 两种槽位、默认值缺失、临时转永久、重复调用；校验 32 字节启动记录 CRC，记录范围以外的数据不变。
3. 手动选择与故障恢复：自动任务之前/之后手动选 Android、锁竞争、持久化失败恢复 misc 和配置、失败后可再次正常执行。未知意图及 E5 RAM 试运行不自动提升。
4. 实际启动脚本与真实控制入口：用 procd 注册桩检查已有 S99 的单次子任务且无 respawn，E5 原有试启动标记清理保留；ARM64 执行实际 rpcd action → ucode → 平台 helper，覆盖忙、失败恢复和两槽。不是 PID1 实机启动验收。
5. 三仓库实际差分生成器与执行器：首次引入、后续更新、撤销后续更新；普通更新分别走 MU300/Pro 实际配置迁移及 E5 实际配置归档片段。旧版没有新增模块目录时仍能交付，保留启动选择、Wi-Fi、射频和插件/壁纸数据。E5 使用 4 MiB 合成 ext4，不复制完整构建根。
6. Chromium 执行真实页面模块：自动保存后的单次状态刷新、无写操作、无循环刷新、退出页面取消；既有启动设置 9 项回归、E5 交付审计 14 项通过。

测试：`test_boot_auto_default.py`（5 项事务、4 项目标端定时测试）、`boot_auto_browser.py`、`boot_auto_delivery.py`；Pro/MU300 在 `tests/`，E5 在 `openwrt/tests/`。可通过重复 `--repo` 指定多仓库；定时/交付脚本默认测试所在仓库，浏览器脚本须显式传 `--repo`。交付回归需 Linux、mkfs.ext4 和 dissect.extfs；Windows linked worktree 可先用原生 Git 导出 HEAD 基线：`boot_auto_delivery.py --repo <仓库> --export-baseline <JSON>`，再在 Linux 测试时显式传 `--baseline <JSON>`。

本轮没有实机部署、重启或刷写，也没有重新打包；上述是文件隔离、目标架构用户空间及真实浏览器的离线结果，不能替代各机型 Android → OpenWrt 的实际开机验收。

本机最终目标端日志（Pro 仓库 `out/build-logs/`）：`20261126-143648-auto-boot-final-deadline-81834/command.log`、`20261126-143532-auto-boot-final-rpc-runtime-79836/command.log`；三仓库交付矩阵：`20261126-143852-auto-boot-final-delivery-81870/command.log`。
