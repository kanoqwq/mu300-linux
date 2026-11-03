# U30 Pro 独立移植记录

## 2026-11-03：插拔外部电源亮屏（事件驱动）

屏幕进程订阅 `NETLINK_KOBJECT_UEVENT` 的内核组播，并把非阻塞 socket
接入现有 `poll()`。只接受内核发来的 `power_supply` / 本机
`u30pro-charger-master` / `ONLINE=0|1` 变化；不依赖 feeder、充电电流、
`Charging` 状态或周期电池刷新，不新增定时器/轮询。订阅后读取一次在线
状态作为基线；接电、拔电边沿调用原有 `wake()`，不解除 PIN/软锁。
满电、限充、温控停充、同状态重复通知不会亮屏或延长息屏时间。

原来把 `Charging -> Full/Not charging` 当作拔电提示的判断已经移除，避免
电源没拔却显示断电。事件丢失的 `ENOBUFS` 错误只触发一次状态重新读取，
不是定时兜底。socket 创建失败会明确记录，屏幕状态的
`power_events_ready` 可只读核对是否订阅成功。

`tests/u30pro_power_events.py` 的8项离线用例覆盖启动基线、双向插拔、锁屏
保留、无 feeder、满电/限充、重复/无效/非内核消息及真正的内核 socket。
与切页、电源菜单、频宽合计19项回归通过；消息模拟不等于实际插拔验收。
随后沿用用户本轮屏幕热修授权更新实机，仅重启屏幕服务；回读二进制摘要、
触摸/按键/feed状态均正常，并将屏幕进程 socket inode 与内核 netlink 表
交叉核对，确认仅一个 protocol=15、group=1 的事件订阅。整机 boot ID、
基带进程和屏幕持久配置未变；实际充电器插拔亮屏效果仍待用户观察。

## 2026-11-03：快速连续切页的动画重入

已在真实屏幕 C 源码的主机模拟器复现：240 ms 动画结束前 `page` 仍指向
旧页；第二次滑动会取消未提交的动画，连续翻页键会重置它，导致反复播放
同一对页面。修复后，新相对切页先完成上一笔目标，再从该页继续；新触摸
同时重建目标页的命中区域，避免动画中途没有按钮区域或操作到旧页。
实体翻页键接管尚未松开的拖动时，会消费该旧触摸剩余事件，防止重复提交。
普通动画仍为 240 ms，没有增加延时或屏蔽连续翻页。

`tests/u30pro_navigation.py` 使用原生产 UI 和虚拟时钟，覆盖双向连续按键、
双向连续滑动、反向及取消拖动、动画中点按、按键接管拖动、普通动画只提交
一次；已接入 `tools/test-u30pro-native.sh`。改动来源摘要登记在
`devices/u30pro/adaptations.json`，原生二进制仍由
`tools/u30pro-runtime.py` 编译、`tools/u30pro-package.py` 收入正式根。
这是离线复现与回归，不代表已授权部署或完成本轮实屏验收。

## 来源和范围

本仓库从用户维护的 `kanoqwq/mu300-linux` **minikano-openwrt-feats**
分支 `29061dc8` 创建，工作分支是 `u30pro-bringup`，不是 MU300 main。
原 MU300、E5、旧 U30Pro 迁移目录均不修改。

硬件源码最初来自用户提供的干净参考仓库 `33333s/U30pro-openwrt`：
`7bcb40ac769ebcdeae0fd550c7a637d3d6dae5fc`；现已核对并更新到
`df3b44fce648105aa785204571f8ad6317a95959`，保留逐文件适配记录。
`devices/u30pro/reference.json` 逐文件记录 Git blob、SHA-256 和执行权限；
本地适配另记在 `adaptations.json`，不能把修改后的源伪称为上游原件。
保留其 MIT 版权和驱动文件自身的 SPDX 标记。

`native/` 只收入原生驱动覆盖层、配置、屏幕、基带/电源服务及离线测试所需源码，
不含整棵 Linux、固件、NV、设备备份、SDK、旧网页、SPD/fastboot 刷写工具、
原仓库的双槽系统升级器和 reserve2 设置存储。

## 接入边界

| 功能 | 采用的实现 | 当前状态 |
| --- | --- | --- |
| 公共软件框架 | MU300 维护分支的看板、流量、短信/网络控制和公共工具 | 已作为基底，平台适配进行中 |
| 内核/设备树 | U30 Pro 原生 Linux 6.12.94；本机 stock DTB | 已授权更新并启动；Image 和整套模块配对 |
| Wi-Fi | AIC8800 SDIO + 标准 netifd/wpad | 不使用 SC2355 驱动或固件 SAE 桥 |
| 机身屏幕 | U30 Pro 自身 `u30pro-panel`，240×240 | 已编译；数据和操作接 MU300 后端 |
| CP 加载/NV | `u30-modemd`、`u30pro-nvsyncd`、`u30pro-refnotifyd` | 保留平台所有权，未执行 NV 操作 |
| AT/拨号 | MU300 ucode/C 桥：nr2/nr0、nr5/nr3 两组 owner | 保留 SPACTCARD，不启动参考 atd/celld；已实机恢复 IPv4/IPv6 |
| CPU/电源 | UMS9632 cpufreq/ASE/SCU、SGM41513、UMP965x FGU | 公共调压配置接本机两域 SML ABI；原厂零偏移和表读取已实测，非零偏移未测 |
| 启动/持久层 | 本机另一槽三镜像；MU300 mu300root 容器 /openwrt | 不采用 u30prodata、reserve2 或参考固定扇区 |
| 安装器 | 固定 openwrt-tools，包内 U30 Pro 启动容器 adapter | 本机模板、备份、回读，misc 最后提交；A/B 和失败恢复已做文件模拟 |
| Diff OTA | MU300 公共引擎和插件 | 配套三镜像事务已接入，离线更新/恢复/撤销通过，待本轮硬件验收 |

表中的“已收入源码”不代表已接通运行路径或完成实机验证。

## 已完成的离线检查

2026-11-02：547 个导入源文件已核对，原厂三镜像的来源、大小和哈希通过。
内核配置解析及必需驱动断言通过。USB MUSB trace 的 `__assign_str` 已适配
Linux 6.12 的单参数接口，原始/修改后哈希及原因均留在 adaptations.json。

第一轮合计 **89 项通过**：槽位/几何及导入边界 11 项、AT broker 的 PTY
通信 9 项、拨号/注册/错误回复/重连/附加承载 51 项、关机充电 18 项。
测试编译器使用与原生工具相同的 Clang 系列；未连接真实基带或充电芯片。
日志和结果位于本仓库 `out/build-logs/20261101-180451-u30pro-native-regression-38140/`。
这些是离线测试，不是新仓库安装器、完整镜像或实机启动的验收。

## 必须完成的启动改造

1. 解析真实 Android 槽，Linux 使用另一槽。没有证据时不得默认 Android A。
2. BSP 从本机当前 Android 槽提取、按需同步并回读；不使用另一台机器的镜像。
3. U30 Pro 原生 `boot`、`vendor_boot`、`init_boot` 配套生成；保留本机 DTB/
   bootconfig。不能套用 MU300 的单 boot 载荷。
4. 写入窗口由本机 GPT 的起点/大小生成。参考仓库曾使用的固定 LBA、
   userdata 前 8 GiB、A/B 更新窗口不得进入本仓库的正式安装路径。
5. 持久层严格采用 MU300 的 `mu300root` 容器和 `/openwrt`，安装、缩容、
   卸载算法不改成原 U30 Pro 的布局；不继承 reserve2 或清空 overlay 的规则。
6. 初始试启动先保证 USB 控制台/日志和看门狗，随后接入 CP、无线、
   充电和屏幕，最后验证完整管理功能。首版实机已完成画面、触摸、USB、蜂窝和热点联调；
   本轮安装器改动仍需单独授权的实机验收。

## 源码与构建检查

读取 `tools/build-layout.json`，复用本机已存在的
`/home/kano/openwrt-build/sources/u30pro-linux-6.12.94` 与
`objects/u30pro/release`，不另克隆/解压内核，不复制完整 rootfs。
旧目录仅作显式登记的输入位置，不能调用其失败安装流程或生成 worker。

```sh
python3 tools/u30pro-build.py plan
python3 tools/u30pro-build.py check
```

`plan` 只读展示路径；`check` 核对导入来源和登记输入，缺失时报明具体项。
首版是硬件待验收的 preview，不是已经实机通过的正式固件。发布入口
`python3 tools/build-release.py package` 使用共享工作区锁和日志；不得直接
运行原仓库的流水线。具体阶段和已知限制见 LOCAL-BUILD 及包内 START-HERE。

新增离线验收覆盖：三镜像实际 DTB/bootconfig 保留、Android 两个槽方向、
中途写坏 vendor_boot 后恢复并重试、目标根的 ucode 编译、屏幕动态链接、
服务语法、命名 NV 映射及合成电池的限充启停。没有连接或写入真实硬件。

离线安装需要在根切换前读取 F2FS 的安装请求，因此 F2FS 为内建，不沿用
参考库的可加载模块配置。root 同样包含 MU300 的 wifi-scripts、SIPA fw4
补丁、DFS 能力检查、账号迁移及用户插件基线；不打入发行版其他内核的模块。
MU5358 的 AT 线路保留 MU300/E5 的 SPACTCARD 选卡前缀。nr0..2 / nr3..5 的物理分组并不代表固件已选中对应 SIM；两组新开通道的 SPACTCARD 回读均可为 0。早期移植删前缀的推断已被实机证据否定并修正。

运行时 NV 通知服务保留原格式、双份读回和校验，不再绑定参考机的 LBA；
以本机命名分区校验 PARTNAME、容器长度和 GPT 起点，当前进程内禁止几何变化。
启动前还核对 NV 分区属于已确认的内部盘。没有将任何 NV 内容打进包。

首版 Magisk 直接刷入未接通三镜像构造，因此明确拒绝，避免旧单 boot 工具写坏
启动链；TF 仍可从解压后的安装器安装。电压偏移等硬件专有控制仍待后续接通。

## 实机边界

初始阶段只做源码移植和离线检查；后续经用户明确授权进行了记录中的热部署和启动。
U30 Pro 实机仍默认只读；新的刷写、重启、
分区或试启动须获得用户对具体操作的授权。任何情况下不改 IMEI，
不擅自修改 NV，也不触碰原 Android 数据。禁止 fastboot 和 SPD。

## 2026-11-02 插卡、充电模式启动和 IPv6 后续回归

以 MU300/E5 维护分支的基带框架为核心，不改变其选卡协议、AT 串行所有权、初始化/回放/重连逻辑。

- **已确认并热修复：部分辅助核启动被当成完整 CP 启动。** 本次命令行为 `modem=shutdown sprdboot.mode=charger`，PM SYS 控制口可开，但 CH/NR 控制口返回 ENODEV。旧加载器只看 sctl_pm，跳过加载后 AT 永久不可用。现在只在 PM/CH/NR 三个控制通道均就绪时跳过加载；探测不开 AT、不读事件、不发送命令。8 种就绪组合用生产函数编译测试。
- **已确认并热修复：错误移除了 SPACTCARD。** 实机两个通道组的 `SPACTCARD?` 都返回 0；物理分组并不等于已选 SIM。恢复 MU300/E5 的原始带前缀请求，仅校验请求与逻辑 owner 的分组匹配。不加另一套 RIL/AT 所有者。
- **已确认并热修复：URC 就绪标记/日志路径不匹配。** U30 Pro 的 NR0/NR3 为数据与 URC 共用的持久 owner，ready 标记和日志跟随 at2/at5。双卡初始化按这些 owner 检查；IPv6/bearer 监视器使用 sim-env 导出的路径。移除等错误文件时每秒 5 次创建 sleep 的 shell 循环，交给 tail -F 阻塞等待。
- **IPv6 证据：** 基带 CGCONTRDP 的 `::1` 是占位符，不是可用公网地址。主动 RS 后实收来自 fe80::2 的 RA，内核取得 2409 开头公网 /64。把 netifd 的 accept_ra=2 放在重新启用 addrconf 之前，避免首次 RS 在路由器禁收 RA 配置下启动；修正后重拨已自动取得新的公网地址、RA 路由，netifd 的 IPv6 地址/路由字段同时更新，IPv6 ping 2400:3200::1 两包均成功。
- **已确认并修复：ip-full 的 alternatives 未物化。** 包文件存在，但 /sbin/ip 仍指向 BusyBox，两个 ip monitor 子进程均启动失败。组包现按包声明优先级处理 alternatives，不运行服务脚本；实机两个 netlink monitor 均存活，12 秒内所有 IPv6 监视进程含子进程累计 CPU ticks 增量均为 0。
- **已确认并修复：热重载残留的 AT 锁元数据。** 死亡 owner 的 pid 被清理但 until 文件残留，导致 rmdir 失败。客户端回收/退出同时清理自有 deadline；守护进程正常退出释放自身锁。新增真实 FIFO 回归，不接触 tty。
- 原始 runtimenv1/2 SHA-256 与热修前一致；NV 通知进程始终 writeback=off。不改 IMEI/原始 NV。
- 本机 CUDY/电脑管理网络未修改。固件与内核未刷写，未重启整机。热修备份/证据存于 work/u30pro-runtime/cp-startup-fix、sim-scope-fix、ipv6-idle-fix。


屏幕壁纸页保留原生 U3LW/JPEG/RPC 协议，切换到共享看板的主题令牌与确认框，补齐中文/英语/土耳其语。24 种浏览器布局与图片转换、上传失败重试、取消/确认移除、设置保存流程通过；已热部署，无需重启。原生第三方插件协议不变。测试为隔离 RPC，不把浏览器模拟操作当成设备写入验收。

## 2026-11-02 调压接入、屏幕插件与 SDIO 启动修补

- UMS9632 为两组 CPU 策略 policy0（0–5 核）与 policy6（6–7 核），不能套用 UMS9620 三域参数。公共调压后端/网页按明确平台选择域数。SML 采用 DHTB 完整性与调用链/指令算法识别，不使用固件 SHA 白名单。主、备 SML 都要通过；不写 SML、不从用户态直接执行 SMC。
- 调频驱动只在启动时初始化一次，CPU/SCU 配套模块不允许卸载或解绑。保留原生 ASE、温控、SCU 和 boost 行为。新 Image 与整套模块已编译、经授权应用并正常启动。实机两域均为 0 偏移、原厂表可读、supported=true；未做非零偏移或长期稳定性试验。
- 原作者 screen-data 插件用精确字符串读取 desired_up。适配器先前输出带空格的 JSON，会误判关闭。已保留其紧凑布尔字段，状态来自公共框架的用户主动断开标记和 WAN 禁用配置，不把临时重拨当成主动关网。原版插件脚本在隔离容器验证连接、临时断开、主动关闭及 WAN 禁用；CLI 仍只调用 MU300 mobile-data。
- 壁纸菜单补充独立 zh-cn/tr LMO；PO 源位于 devices/u30pro/po。目录由官方 LuCI po2lmo/hash 函数生成，实机 luci.core 载入全局目录后返回“屏幕壁纸”，不是仅在当前页面替换菜单 DOM。
- 屏幕运营商名称复用维护看板的大陆 PLMN/别名表，独立展示模块不改变 COPS 解析或 AT 次序。15 个用例覆盖移动、联通、电信、广电、铁通、未知及国外/自定义名称。当前卡缓存为 46000，屏幕投影为“中国移动”。
- 无线已是 HE80/5 GHz/CN，手机曾协商 1080.6 Mbps 双流；低于 150 Mbps 的明确限制是 SDIO 当时仅 50 MHz、4 bit。原仓库有 SDR104/208 MHz 启动设置，但本次移植漏接。现由 S08u30pro-factory 在 S10boot/kmodloader 前设置；必须使用 START=08，START=8 会生成排序错误的 S8。修补接本机 SDIO 参数，不改 eMMC 时钟或基带流程，不继承旧 /mnt/data 的布局。
- 热重载 AIC BSP 时先出现固件探测 -110，随后在 mmc_rescan/__device_attach_driver 发生空指针；正常重启未完成，用户物理重启恢复。保留完整日志，不据此关闭 208 MHz，也不宣称已修复 AIC 的热卸载失败路径。以后此项在开机前配置，不热卸载 AIC 模块。
- 修正启动次序后实机已确认 clock=208000000、SDR104，热点恢复，AX/80 MHz/CN 保留，IPv4/IPv6 正常。按用户要求未再跑吞吐压力测试，因此不声称达到 600 Mbps。证据在 work/u30pro-runtime/sdio208-carrier-menu。
- 本轮始终 NV writeback=off，原始 runtimenv1/2 哈希不变。新安装未配置通知写回时默认 off，保留用户显式选择。

安装器本轮范围与离线验证见 [U30PRO-INSTALLER-20261102.zh-CN.md](U30PRO-INSTALLER-20261102.zh-CN.md)。

board_id 版本、物理 SIM mux 及公共切卡事务适配见
[U30PRO-SIM-TOPOLOGY-20261103.zh-CN.md](U30PRO-SIM-TOPOLOGY-20261103.zh-CN.md)。

## 2026-11-03 屏幕输入、充电来源及原生 OTG

### 已部署与已确认

- 快速连续翻页不再取消尚未提交的目标页；新的按键、触摸先完成上一个目标，再按新页计算。保留原有动画时长，不用输入节流掩盖状态错误。
- 插拔供电亮屏由 `NETLINK_KOBJECT_UEVENT` 驱动，仅接收内核发出的 `u30pro-charger-master` ONLINE 边沿。启动基线、重复通知及充满状态变化不亮屏，不新增轮询定时器。
- SGM41513 的充电来源改为读取本机 USB2 PHY 的 BC1.2 结果，而非用 PN0 的 PSEL 位猜测 SDP。原厂 DT 表为 SDP 输入 500 mA、DCP 输入 3000 mA、CDP 输入 1500 mA；保留各自充电电流表、PD、JEITA、用户限充和 CV 规则，不统一强制大电流。
- 上述屏幕和 BC1.2 版本已获授权部署。本机用户反馈 A-C/C-C 充电正常；此前临时记录因设备缺少 nohup 未启动，因此没有该次插拔的实际功率/BC1.2 事件曲线，不能将软件表值当成实测电流。用户随后补充 OTG 不可用，OTG 不算通过。

### OTG 确认缺口及修补

在该机运行配置中确认只编译了 MUSB gadget，没有 host/dual-role；同时导入源码把 Type-C DRP 分别强制改成 SNK 和 sink-only。这些限制在 BC1.2 修补前已存在。网页又只检查 DWC3 的 usb_role 文件，不能控制本机 MUSB，因此 USB 模式灰色。

本机原厂派生运行 DT 的 Type-C 为 DRP、preferred sink，USB/PD 使用 `use_pdhub_c2c`，VBUS supply 连接本机 SGM OTG regulator。现在：

- 配置启用原生 MUSB dual-role；恢复 Type-C/TCPM 的 DT 双角色能力。修正此前未编译过的主机代码在 Linux 6.12 上的 unaligned 头及可选 audio-offload 头引用。
- 机型模块清单接入常见 RTL8152、ASIX、CDC 网卡及 USB 存储驱动；本根没有通用 USB modalias 热加载钩子，仅编译模块而不加载仍无法识别外设。组包校验同时核对清单、模块及 depmod 依赖。
- MUSB 提供只读 `dual_role_capable`，网页依据实际运行内核判断能力，旧 gadget-only 内核继续明确显示不支持，不能靠存在 Type-C 目录假报能力。
- 新增 `u30pro-usb-role`，只使用 Type-C class 的 `port_type` 控制自动/设备/主机；TCPM 和 MUSB 继续负责识别、VBUS 及 extcon。不使用 U30 Air/F50 的固定 I2C 寄存器路径，不强行对已有外部 VBUS 开升压。
- 网页对 U30 Pro 显示自动、设备、主机三项及“开机应用所选 USB 角色”。默认自动；未勾选持久化的临时选择下次开机恢复自动。其他平台保留原行为。当前没有额外提供未经验证的“主机＋外部供电”强制选项。

双角色 Image 与配套完整模块已在登记缓存编译成功。31 项 USB 后端回归及 6 组语言/视口浏览器用例通过；后者同时覆盖持久化、角色应用、错误和旧内核能力。**经用户“接回来吧，允许刷入”授权后已部署并重启；随后用户确认自动/主机模式“一切正常一切可用”，本轮 OTG 实测通过。未据此声称所有充电器与扩展坞组合均兼容。**

完整原生回归 116 项通过；最后一次根检查核对 45 项当前源码/适配文件、主机模块加载清单与依赖，并通过目标环境语法、服务、双卡映射、屏幕及充电控制检查。当前测试 boot 保留本机可用 ramdisk，与配套 245 个模块的摘要在上述诊断目录 `readiness.json`；它不是可给其他设备直接刷入的通用启动包。

本次实机确认 Linux A / Android B，刷入前比较当前 kernel/ramdisk 与本机捕获记录一致。只更新 Linux A boot、245 个配套模块及六项 USB 运行时文件；原 boot、模块和 USB 配置/程序留存电脑。写入后 boot 全分区即时回读通过，Android boot 与两槽 vendor_boot/init_boot 摘要未变。重启后双角色能力为 1，默认策略 auto，电脑连接时为 device/sink；258 项模块及元数据摘要逐项通过，常用 USB 外设驱动已加载，蜂窝 IPv4/IPv6、5 GHz 热点、屏幕和 NCM 管理恢复。只清理本轮设备端 staging 和旧模块目录，电脑备份保留。记录位于 `work/u30pro-runtime/otg-adaptation/deploy-20261103-074441/`。

物理复测后日志确认两种 RTL8153 网卡实例完成枚举并出现 carrier on，扩展坞上的 USB Mass Storage 被识别，切回自动并接电脑后恢复 device/NCM；同一 boot 内蜂窝 IPv4/IPv6 与热点仍正常。网卡驱动另有可选 `rtl8153a-4.fw`、`rtl8153b-2.fw` 缺失提示：当前无补丁固件也实际连通，记录为独立的固件收录缺口，不将它说成已修复或 OTG 失败。原始日志和用户结果存于上述目录 `physical-validation.json`。

只读资料已保存到 `work/u30pro-runtime/otg-adaptation/`，包括运行 FDT/DTS、配置、电源/USB/调节器状态、完整 dmesg、模块和当前 Linux A boot，`capture.json` 记录摘要。设备离开后可继续编译，不需要重复采集。该目录是诊断与本机测试镜像保存位置，不是正式生产输入。

本机 boot 的 48 MiB 位置以后含早期诊断日志，启动后整分区哈希会变化。已离线比较确认当前 boot 的 kernel/ramdisk 与已部署 BC1.2 版本相同，变化仅在该日志尾部；不要以启动后的整分区摘要变化判断内核损坏。刷写前即时回读仍须精确校验。

## NFC 配网与功能菜单（2026-11-03）

本机只读确认 FM11NT08x 标签在 I2C4/0x57，原有 WSC 内容匹配当前热点，
但原生工具此前没有接入正式构建。现已添加静态工具编译、一次性启动服务、
无线配置/netdev 事件同步及屏幕“功能 → NFC”持久化开关。
关闭后保持空 NDEF，热点变更不重新启用；相同内容不重复写 EEPROM。
这不是新增轮询、基带服务或门禁读卡功能。机制、测试和实机验收边界见
[NFC 适配记录](U30PRO-NFC-20261103.zh-CN.md)。
