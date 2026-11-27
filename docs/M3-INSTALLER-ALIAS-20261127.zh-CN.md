# M3 / MU5352 安装入口别名补全（2026-11-27）

用户报告 M3 在第一层机型检查通过后，被设备侧 `BOOTCHAIN-REFUSED` 拦截，
输出身份为 `MU5352 / MU5352`。共享安装器的 `model_compat.py` 原已将此身份、
UMS9620 平台映射为 U30 Air；旧启动链脚本尚未接入，确有前后判断不一致。

本轮在 MU300 和 Pro 仓库各自保留的旧载荷入口补齐同一明确别名：

- `tools/android-bootchain.sh` 及 Android 系统切换模块的逐字节副本。
- `desktop/installer.py`、主机 `tools/android-profile.sh`，返回 `u30air`。
- Linux/PowerShell 的 ADB 目标识别与旧 PowerShell 安装入口。

仅 `MU5352 / MU5352` 且平台为 `ums9620` 的身份进入此分支，不把所有名叫
M3 的设备识别成 U30 Air。原有 root、Android 槽位、成功启动标记、分区存在、
双槽大小/身份和备份/回读/恢复检查不变。原厂 Android 槽、GPT、用户数据和 NV
的写入边界不变。运行时机型继续按实际设备树识别，不改内核或板级配置。

共享安装器早已支持第一层身份，因此这次不改子模块、不更新安装器版本锁、
不重编未变化的 EXE/App/APK。正式组包会复制当前外置 Python/设备侧载荷及
新的校验清单；旧 ZIP 不会因此自动获得修复。没有扩大 Pro 固件的支持机型范围。

离线测试执行真实 shell 脚本与文件模拟分区，覆盖 M3 两种 Android 槽位的
只读计划、错误平台拒绝、损坏 misc 拒绝、已有五项启动链备份/写入/故障恢复、
脚本副本一致性，以及 CLI/GUI 入口对应的型号规则、ADB 多设备选择和前缀误匹配。
两份 PowerShell 脚本通过真实 PowerShell Parser 语法检查。

MU300：`out/build-logs/20261127-122833-mu300-m3-installer-final-68399/command.log`，
42 项中 41 项通过、1 项因测试进程为 root 而跳过。
Pro：`out/build-logs/20261127-122927-pro-installer-m3-stock-final-77320/command.log`，
54 项中 53 项通过、同一项跳过；含 F50 Pro 选卡导入回归。
没有对 M3 实机执行刷写、切槽或重启，离线通过不代表实机安装验收。
