# U30 Pro 独立移植记录

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
