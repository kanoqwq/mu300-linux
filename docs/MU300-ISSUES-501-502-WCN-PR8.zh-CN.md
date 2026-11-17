# MU300 校时、WCN 与基带恢复核对（2026-11-17）

## WCN：采用 PR #8 的总线保护

来源：[InsulateJustf 的 PR #8](https://github.com/kanoqwq/mu300-linux/pull/8)，
提交 `8b18b7f6fb76545bf98a743cf6766acb05cc108e`。
在本地 `72a3b21b` 基线上接入，不涉及 PR #7 的启动锁定功能。

- 自动断言及 watchdog 恢复在 `power_lock` 内检查实际上电状态并设置
  carddump；释放锁后才 dump/reset，避免恢复流程重复取得同一锁。
- 沿用 PR 的已关机跳过 runtime put、复位后清理 fail_count，以及只读
  `reset_dump_safe` 能力标志。模块加载参数不能强制把该标志设为真。
- 三种 PCIe 断言策略均使用相同保护；手动 dumpmem/rebootwcn 也先取得
  保护。手动复位完成前不释放 carddump，关闭无线后返回 ENODEV。
- 恢复不再用旧 powerdown 标志否定锁内仍上电的总线；集成式 WCN 保留
  原分支。保持用户选定的 dump/reset 策略，不启用整机自动重启。

离线验证包括 PR 自带状态机、实际 C 函数的 pthread 并发测试与
ASan/UBSan；覆盖先关闭、先保护、旧 powerdown 标志、连续复位及关机后
手动命令。6.18/7.2 分别使用既有内核缓存编译模块。离线结果不等于实机
断言恢复通过，本轮没有制造实机 WCN 故障。

## #501：默认 NTP 与代理 DNS 互相等待

[上游报告](https://github.com/dikeckaan/mu300-linux/issues/501) 的条件在本分支
存在：构建基础镜像 `mu300-openwrt-base:25.12.5` 的 `/bin/config_generate`
默认写入四个 `*.openwrt.pool.ntp.org`，本仓库此前没有覆盖。

不修改 UCI 的原服务器列表。在原 `sysntpd` 的服务管理、权限、jail 与网络
事件触发路径内接入启动程序：当最终使用的仍是原四个默认 pool 域名时，先
使用 `162.159.200.1`、`162.159.200.123`、`2606:4700:f1::1` 和
`2606:4700:f1::123` 尝试一次校时，它们是
[Cloudflare 公开的数字 NTP 端点](https://developers.cloudflare.com/time-services/ntp/usage/)。

IP 阶段不解析域名，最长 20 秒。成功后以 IP 运行正常校时服务；失败或超时
转回原域名列表解析并校时。后续网络变化触发原 sysntpd 重载时会重新选择。
自定义服务器、空列表及 DHCP 增补的服务器直接使用原列表。关闭服务仍不
启动，不改时区和主机名，不用 CFUN 重注册或未经验证的 CCLK 设置系统时间。
两阶段均由原 procd 服务管理，不增加周期守卫，也不阻塞系统启动等待校时。

测试覆盖 IP 成功、失败、真实 timeout 后启用 DNS、自定义与 DHCP 列表、
正常服务参数保留，以及真实基础镜像 sysntpd 的集成和 jail 依赖。没有更改
测试机时钟或访问公网 NTP；两条网络路径均不通时仍不能校时。
复查补齐重复 staging：已接入的服务可再次刷新源码，不重复插入 jail mount；
无法识别的基础服务仍在写入前报错。相同逻辑已按各自路径接入 Pro 和 E5。

## #502：原厂基带断言及 300 秒 dump 等待，尚未修复

[报告](https://github.com/dikeckaan/mu300-linux/issues/502) 中的 PHY 断言发生在
闭源 CP 固件。我们的 vendor 服务直接运行导入的原厂 `modem_control`，
不能把它与本仓库的 AT broker 混为一谈。

核对既存 U30 Air 原厂样本，SHA-256 为
`43a3bc8c4730b87431d4fc8d90a3652b2458ef8bc9f59772685d4016cc7819a3`。
该样本固件版本未随当时导入目录保留，不将其视作报告中 F50 的同一版本。
反汇编证据：

- `0x7244` 至 `0x7280` 读取 `persist.vendor.sys.modem.save_dump`，在
  reload、dump 都启用时创建 reset 线程；dump 为零走另一恢复分支。
- reset 线程 `0x8500` 在 `0x854c` 为单调时钟截止时间增加 `0x12c`
  （300 秒），随后调用 `pthread_cond_timedwait`。
- `0x82a8` 至 `0x82c8` 是同一条件变量的完成通知路径。

这证实原厂用户态存在 300 秒等待，不证明两次 PHY 断言的根因，也不能证明
dump 通知为何缺失。尚未核对报告机器的实际 loader、CP 固件及 dump 完成
路径，因此没有修改等待常量、伪造 dump 完成通知、屏蔽 dump、替换 CP 固件
或添加外部强制复位。本轮不能将 #502 标记为已修复。
