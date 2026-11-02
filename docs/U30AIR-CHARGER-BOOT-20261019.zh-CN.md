# U30 Air 上电自启动与关机充电（2026-10-19）

## 行为与边界

网页“设备管理 → 充电管理”增加“上电自启动”，保存于
`mu300-charge.main.power_on`。默认关闭，与 E5 的开关语义一致：

- 关闭：LK 标记为 charger 的启动停在充电状态；电池灯沿用现有颜色、
  呼吸和插拔延迟策略。长按电源键 1.5 秒继续启动；拔掉充电器，确认
  断开持续 500 ms 后关机，不进入残缺的 OpenWrt。
- 开启：charger 启动直接继续进入 OpenWrt。
- 电源键产生的 normal 启动不等待；F50、V50 不进入此门控。
- 保留已保存的开关值。旧客户端只提交充电阈值时不覆盖上电自启动。
  开关中英土三语言齐全。

只在 OpenWrt 的 `S09mu300-charge-mode` 启动路径等待，排序在
`S09mu300-vendor` 前。正常路径不加 sleep，不改 AT owner、握手、按卡
回放、射频开关或拨号顺序。关机充电时加载现有 IPC/PM 看门狗及温控模块，
推迟用户态 CP 加载、热点与网络初始化。没有关闭温控、休眠或降低硬件功能。

复用 `mu300-keys` 增加 `-c`，通过 poll 等待电源键和内核 kobject
power_supply 事件，没有周期查询、后台 SOC 定时器。只认支持 KEY_POWER
的输入设备，不响应 Wi-Fi 键；事件仅用来触发重新读取实际 online 节点。
收到 ENOBUFS 会重读供电状态；短暂断线有 500 ms 去抖。输入/供电硬件
不可用时明确记录错误并继续正常启动，避免无法开机。

拔线关机前同步文件系统，先只读重挂根文件系统、再重挂 userdata 镜像
的 backing mount，通过已有 next-boot rearm 保留用户启动选择，最后使用
内核 poweroff 路径，包含 FGU shutdown 保存。不会发 reboot，也不跳过 sync。

## “充电启动后无基带”的原厂依据

从连接的 U30 Air 导入目录只读取得本机原厂 `modem_control`。
该导入目录没有保留完整 build.prop，以下 SHA-256 用来记录当时取得的样本，
不是生产代码的固件版本白名单。不把 E5 或 F50 的加载器当作 U30 Air 版本依据：

`43a3bc8c4730b87431d4fc8d90a3652b2458ef8bc9f59772685d4016cc7819a3`

反汇编确认：

- `0xc650` 的 modem_has_been_boot 先检查已运行的 PM 通道。
- `0xcf0c` 保存这个结果，随后与 `modem=shutdown` 条件合并。
  PM 已在线不代表 NR/CH 已加载，但该结果会跳过初次加载。
- `0xcf50` 正常初次加载调用传入 63，包含 PM 和 CP/CH 的操作。

U30 专用 `mu300-modem-coldboot` 只在 charger＋modem=shutdown、且 CP
的 stty_nr1 SBUF 尚未就绪时准备 RAM 私有副本。spipe_open 的驱动实现会
在 SBUF 不就绪时立即返回 ENODEV；`dd count=0` 仅开关文件，不读写 AT，
且执行时 AT owner 尚未启动。其他错误不会被当作“CP 离线”。

私有副本仅有两处已验证修改：

| 文件偏移 | 原指令 | 副本指令 | 目的 |
| --- | --- | --- | --- |
| 0xcf0c | mov w19,w0 | mov w19,wzr | 不用 PM 已启动推断 CP 已启动 |
| 0xcf50 | mov w0,63 | mov w0,34 | 仅 LOAD_CP / LOAD_CH，保留运行中的 PM |

上述历史样本修改后的 SHA-256 是（仅作回归测试的结果基准）：

`9e3490f222a312e649dd5273df216b87ca9880a621ea68a0a73df0aa6b37c2ae`

副本在 `/run/mu300/coldboot`，只绑定到 Android chroot 的独立 tmp 目录，
没有修改原可执行文件、Android 分区、固件或 NV。执行文件仍名为
modem_control，满足驱动的 task comm 检查。CP 已就绪、正常启动及其他
机型继续使用原始加载器。

2026-11-02 已取消整文件哈希白名单和固定补丁偏移。静态 ARM64 辅助程序
`modem-loader-patch` 根据 ELF 映射、实际指令与调用关系定位上述路径；
检查 PM 谓词、cmdline 解析、shutdown 条件，以及三个加载上下文的停止／启动
配对和位掩码。固件版本字节、代码地址、文件布局变化不再导致直接拒绝。
支持已核实指令 ABI 的寄存器、栈偏移及 NOP 差异；不承诺任意编译器生成的
未知 ABI。无法唯一证明加载逻辑时仍明确报告识别失败，不向未知地址写指令。
详细边界与新增回归见 [U30AIR-MODEM-LOADER-ABI-20261102.zh-CN.md](U30AIR-MODEM-LOADER-ABI-20261102.zh-CN.md)。

## 验证及尚未验证的内容

- 97 项相关回归通过，包括充电门控、真实原厂文件副本校验、电源键短/长按、
  其他实体按键隔离、U30 冷/热切卡及 LED 逻辑。
- 11 项真实 OpenWrt ucode 充电后端测试通过；中英土、亮暗色和窄屏/桌面
  实际页面检查、14 项翻译回归通过。
- AArch64 静态按键程序编译通过，复用已有 WSL 交叉工具链；没有复制源码树。
  打包检查会拒绝缺少 charger-event 功能的旧 mu300-keys 二进制。
- 已对当前 U30 Air 热部署，部署前复核模型与 eMMC 身份，原文件备份在
  `/root/update-backups/u30air-charge-boot-20261019`。保留原充电阈值和暂停状态，
  仅为缺失的 power_on 加入默认值。没有重启、断网或修改当前射频。
- 实机新 RPC 能读到 power_on=false，按键/内核事件监听正常阻塞，外部限时
  结束后退出；正常启动路径选择原始 modem_control。这些只能证明接口可用。
- **尚未实测**：真正关机充电后拔线是否关机、长按是否启动并恢复基带、
  长时间关机充电的 PM 看门狗握手，以及该私有加载副本的实际冷启动结果。
  当前普通启动的成功不能替代这些物理条件验证。

## V50 用户改装卡槽说明

用户将两颗嵌入式 SIM 中的一颗改装成插槽。软件仍依据 board_id 和 GPIO
物理路由编号识别，不能根据运营商猜接线，也不能因为改装就给 board_id=3
凭空增加原厂 physical ID 3。此次没有增加运营商绑定或修改其他机型切卡。
