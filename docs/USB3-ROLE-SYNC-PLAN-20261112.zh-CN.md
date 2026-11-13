# UMS9620 USB3 角色与方向同步方案

状态：F50 原两级 hub 扩展坞已通过双面 5000 Mbps、多次带坞重启和直连电脑
NCM 验证，详见文末。U30 Air 已部署后续公共内核 #108 并通过电脑网络、
热点、蜂窝和充电检查；用户暂不测试扩展坞，因此带坞重启改善尚未验证。
下面 U30 Air 双面及带坞启动的历史结果来自 #100、签名提交 de0bbf36。
U30 Air 最后实测双面均枚举 5000，
主机＋外供及最终直连电脑 NCM/SSH、蜂窝 IPv4/IPv6、热点正常。
但同一 #100 首次带外供启动为 480，用户随后手动重启到 5000，
因此不能宣布带外供跨重启的间歇降速已解决，也不能直接断言是坞的缺陷。
原厂 Android 也曾复现该组合；#95 的无外供对照两次均为 5000。
#97/#98 的角色冲突硬复位策略未通过，正常构建已撤掉；已保留独立确认的
PD 生命周期与 PHY 角色交接修正。设备临时 LAN 改动已恢复，不额外重启。
范围：MU300/F50/U30 Air 的 UMS9620 主线内核适配；不直接套用 Pro/E5。

## 目标

在支持 USB3 的主机、线材与扩展坞组合上，修复角色切换／插拔后因为 PHY
状态过时而退回 USB2 的问题。保留电脑 USB 网络、OTG 网口、外部供电扩展坞、
自动与用户手选角色。USB2 设备或 USB2 线本身只能跑 480 Mbit/s，不能承诺
所有组合一律 5 Gbit/s，也不能仅凭 root hub 的 10000M 判断链路成功。

## 初始源码证据（修正前）

- `upstream/port/drivers/usb/phy/phy-sprd-ums9620-ssphy.c`：初始化按
  `is_host` 设置 TCA GCFG bit4；`sprd_ssphy_set_vbus()` 在后续角色变化时
  更新 `is_host`、USB2 ID/上下拉和眼图，却没有同步这处 TCA 配置。
- 同一驱动初始化从 AON USB_SW 读取方向并配置 TCA；当前没有 PTN38003A
  的每次 attach 重新检测流程。
- `upstream/port/drivers/usb/typec/tcpm/sc27xx-ump9620.c`：已有 TCPM
  数据角色和供电角色分离；`set_polarity` 当前不连接 SS 通道方向切换。
- 上游 issue #54 有失败和成功枚举 SuperSpeed 的记录；不能归结为全机型
  固定限速。PR #341 提供原厂 TCA/redriver 线索，但仍为未完成实机验收方案。

原厂时序必须进一步从已登记的对应机型原厂 PHY、DWC3 glue 和 DT 核实。
PR 中的行号及推断不能替代本机原厂来源核对。

## 改动设计

### 1. 保持现有角色决策

TCPM／现有 USB 管理服务仍决定 data role 与 power role。不开机强制 host，
不强制 SRC，不更改 Rp 电流，不用 10/60 秒或有没有 hub 猜测是否接电脑。
主机＋外部供电必须保持 data=host、power=sink，不能因为设置主机模式而
调用供电策略去强开 VBUS boost。

### 2. 角色、方向在链路启动前一致

为 SS PHY 增加独立的角色／方向准备操作，按原厂确认的 TCA 握手更新模式
与方向。把“设置 PHY 数据角色”与“真正输出 VBUS”分开；不能把
`otg_set_vbus(true)` 当成无副作用的角色标记 API。

初次 PHY 初始化使用已确定的目标角色；后续切换先通过既有 DWC3 角色流程
停旧链路，再完成 SS 通道准备，最后启动新的 xHCI 或 gadget。不得在活动
链路上直接改 TCA 或把 PHY shutdown/init 当作通用补救。具体挂接点需同时
核对 6.18 与 7.2 的 core/drd/runtime-PM 调用顺序，避免重复初始化和锁反转。

由 TCPM 的 polarity/attach 事件向标准 Type-C switch／mux 接口交付方向，
PHY 注册对应回调；在当前构造 connector fwnode 的路径补齐关联。若原厂
USB_SW 才是 SS 实际方向来源，应先证明它与 CC 极性关系，再决定采样方式；
不可直接把两者当成相同位值。使用的机型属性进入受版本管理的适配路径。

### 3. redriver 只操作本机真实存在的芯片

根据实际 DT compatible 和 I2C client 绑定 PTN38003A；不扫描所有地址，
不向未知芯片写寄存器。核对原厂进入／退出深度省电及方向自动检测的顺序，
在新的 attach／确实改变方向的转换窗口重新检测。该器件不是可随意翻转的
交叉开关，不能只改 redriver 而不对齐 TCA。

若启动必需的 redriver 未就绪，使用正常驱动依赖／probe defer；不是靠永久
后台轮询补救。芯片缺省、寄存器访问失败和握手超时要返回实际错误，不谎报
SS 就绪。处理失败时由既有角色路径清理本次未完成状态，不能重启整个 USB
电源链，尤其 F50 的扩展坞同时承担整机供电。

### 4. 事件及并发

只处理 attach、detach、data-role、polarity、runtime resume 等既有事件。
同一 attach 的重复通知不反复重置 redriver。用序列号取消旧事件和短临界区
串行化 PHY 状态；需要睡眠的 I2C／TCA 操作放在允许睡眠的转换上下文，
不在 IRQ 或持有 TCPM 锁时等待另一个角色回调。

仅使用原厂规定的稳定延时和有上限握手等待；不增加常驻每秒检查或无限重试。
detach/remove/suspend 清理工作与锁的顺序必须明确，避免拔线后的迟到操作。

## 实施拆分

1. 先核对并记录原厂源码／DT、两版 DWC3 调用顺序，确定合法的静默切换窗口。
2. 独立实现 SS 角色同步，先验证现有电脑网络和主机行为。
3. 再接 polarity 与确认存在的 redriver，覆盖同角色重新插拔。
4. 共享 PHY 修复进入两版内核生产源码与构建指纹；机型差异放配置，不写死
   一台机器的寄存器快照。通过后才发布，不将未验收修改混入正常包。

## 验收矩阵

| 场景 | 必须验证 |
| --- | --- |
| 直连电脑、插头两面、冷启动与热插拔 | NCM/RNDIS/ECM 现有选择保留，UDC 枚举、IP、双向流量正常；USB3 条件下检查实际 UDC speed |
| 普通 OTG 千兆网口、插头两面 | 自动／手动 host，实际网卡节点 speed=5000，双向吞吐不受 USB2 限制 |
| 带外部供电扩展坞 | host+sink 正常，网口与充电同时工作，不回灌，不中断 F50 整机电源 |
| device→host→device，host→device→host | 多轮切换和不掉电重连，方向与角色无旧状态残留 |
| 启动前接坞、启动后接坞、普通 reboot | SS hub 与网卡均按实际支持速率枚举，不依赖先进入 Android |
| 挂起／唤醒、USB2 设备／线 | 正常恢复；合法 USB2 连接不被拒绝或无限重试 |

记录每次物理设备节点的 speed、拓扑、错误与双向传输；5000 是 USB 链路
速率，不是千兆网口的应用吞吐。先保留原镜像，实机刷写按授权执行。

## 参考

- https://github.com/dikeckaan/mu300-linux/issues/54
- https://github.com/dikeckaan/mu300-linux/pull/341

## 第一阶段源码与编译结果

已只读核对既有 `mu300-kernel` volume 的 `zte-u30air` 原厂源码：
`qogirn6pro.dtsi` 的 DWC3 USB2／USB3 phandle 指向同一 ssphy；原厂
`dwc3_sprd_enable()` 初始化 PHY，`phy-sprd-usb31-qogirn6pro.c` 按
is_host 写 GCFG bit4，并从 USB_SW 写 TCA 方向，且调用 redriver USB3 模式。

当前第一阶段没有重写整个原厂时序。`port/install.py` 仅对兼容
`snps,sprd-dwc3` 且两个 legacy PHY 实为同一对象的角色转换，在旧控制器
退出之后、新控制器启动之前调用原 PHY 角色入口。失败保留错误、阻止启动
本次新角色，允许之后明确重试。原有通用／其他平台入口保留。

初始构想涉及同步 GCFG bit4，但实机原版在正常方向已经可以 host+sink 跑
5000 Mbps，不能将 bit4 断言为该现场根因，因此此项未随测试镜像部署。
原有 BC1.2 通路、Type-C 协商、boost、电流和默认角色不改，不增加周期工作。

`upstream/check-usb3.sh` 通过正式 `build-release.py run` 入口复用
`mu300-mainline-clean-tf-7-2` volume，在 6.18.54 和 7.2.8 上编译实际
`drivers/usb/dwc3/core.o` 与 `drivers/usb/phy/phy-sprd-ums9620-ssphy.o`，均通过。
这些是对象编译检查，不是 Image／完整配套模块发布，也不是实机 USB3 验收。

下一阶段须对照 U30 Air 的实际方向、TCA 和 redriver 状态；现阶段尚不能
承诺正反插、同角色重新接入和所有扩展坞都恢复 SuperSpeed。不要将本次
阶段结果标成完整 USB3 修复，也不要未经验收直接打包成用户正式更新。

## 2026-11-12 原内核基线实机采样

用户连接 U30 Air 1TB，运行 OpenWrt 25.12.5 / 7.2.8 #73。只读确认 TCPM
存在，电脑连接时 data=device、power=sink、port_type=dual、orientation=normal，
UDC current_speed=high-speed。

用户按要求接 USB3 扩展坞约15秒后接回电脑。内核日志记录 TCPM 自动切换为
host/source，xHCI 两条总线均注册，但只出现 1-1 与 1-1.3 的 high-speed hub，
没有 SuperSpeed 下游设备；接回电脑后恢复 device/sink 及 SSH 网络。
此结果复现了该连接组合的 USB3 枚举缺失，不证明 TCA 或 redriver 就是唯一根因。

设备树/I2C client 登记 6-0030，compatible 为 `sprd,ptn38003a redriver`，
未绑定驱动。电脑连接状态下只读寄存器 0x00、0x04 均 I/O 失败；可能未供电、
节点与实物差异等，尚不能断言实际芯片存在并工作。不能据节点直接强行写入。
实机没有 /dev/mem，因此本轮没有获取 TCA 寄存器回读，也未开启此接口。

私有采样保存在 `work/usb3-20261112/`，不作为生产构建输入或公开硬件验收。

### 原版正反插对照

保持同一扩展坞和线材：normal 方向出现 0bda:0411 USB3 hub、0bda:8153
网口及 0bda:0306 读卡器，实际设备 speed 均为 5000；reverse 方向仅有
480 的 USB2 hub。正常面可工作于 host+sink，不需要强制 SRC。

AON BOOT_MODE 回读随方向改变：normal=0x41d，reverse=0x1d，正好是
原厂 PHY 使用的 bit10。原有 PHY 仅首次 init 写 TCA，后续没有按这个
实时方向重新选择 SS 通道。无需新增 CC 轮询或再实现一套 Type-C owner。

### 第一版失败与恢复

7.2.8 #81 候选用现有 DWC3 转换的静默窗口刷新 TCPC／SYSMODE，等待
controller-synced ACK。实机启动和 Wi-Fi/SSH 正常，但反面仍为 480；
翻至正常面后出现 `cannot prepare combo PHY role: -110`，USB 下游未枚举。
这证明该 ACK 路径在当前状态不可用，不能继续作为修复发布。

根据明确故障关联，已恢复电脑备份的原 boot_b 和原模块目录并正常重启。
恢复刷写回读 SHA256 为
`11f93518c44f36ae3a5c950eecd6b58675a862a0c0687efb0488c5af5aeecc2a`。
启动后 boot 尾部持久日志会变化，判断当前测试镜像时需比较前48MiB而非
把日志变化误判成 boot 内容损坏。原始与候选文件、采样均保留在私有 work。

### 第二版候选

采用现有主线 `drivers/phy/freescale/phy-fsl-imx8mq-usb.c` 对同类 Synopsys
TCA 的 system-configuration 切换流程：GCFG 选择该模式，SYSMODE 先设置
TCPC_DISABLE，再改 FLIP，最后清 TCPC_DISABLE。只在新方向确实不同时
执行，并以 GEN_STATUS 的实际方向／disable 输出做有上限的就绪检查。
不再复用已失败的 TCPC ACK 请求，不重置 PHY 或切断供电。

DWC3 在旧链路退出后、启动新链路前准备方向；同角色重复请求仅当当前
AON 方向与 TCA 配置不同才重建链路，普通重复通知不触发重建。相关代码
按 UMS9620 compatible 和共享 combo PHY 限定，其他平台不采用此逻辑。
目前仍不向无法响应 I2C 读取的 redriver 写入，也不推断其一定实装。

实际 helper 的模拟寄存器测试覆盖两面、无变化不写、保留无关位、实际输出
不符合时超时、明确重试与 regmap 错误。DWC3 调用用例覆盖先准备后启动、
失败阻止新启动、重试及其他平台隔离。完整 7.2.8 Image 和202个配套模块
编译／清单校验通过。第二轮实机验证未通过，原因及恢复情况见下文。

### 第二轮失败原因与第三版待验证

第二版错误地把 TCA system-configuration 模式值设为 2；同类主线驱动的
明确宏定义为 0。实机 #82 回读实际输出 0x103 未随要求的 FLIP 改变，
返回 -110，新 USB 链路未能启动。这是候选实现错误，不能归因于扩展坞。
已回读确认恢复原 boot_b SHA256 与原模块目录，并正常重启。

第三版修正模式值为 0；测试寄存器模型同步限制为只有合法 system 模式
才能改变实际输出，避免之前过宽的模拟漏检。helper 与角色顺序测试通过，
完整 7.2.8 Image 和配套模块构建、校验完成，尚未部署，不代表硬件通过。

恢复后只读确认运行 #73，当前 reverse / host / sink，8153 网口和 USB3
hub 均为 5000 Mbps，AON BOOT_MODE=0x219。这说明原版也可能在某次
启动／插拔状态下使该方向正常；早前观察到的方向与降速关联不能解释成
所有场景都按固定一面失败。下一轮须核对双面反复切换及电脑设备模式。

### 第三版首轮实际结果

实机 #83 启动后 reverse / host / sink，网口仅 480 Mbps，尚无 helper
切换日志。随后用户翻到 normal，DWC3 退出旧链路，再执行方向同步：
`boot=0x419 flip=0 output=0x103 result=0`，RTL8153 与两个 USB3 hub
均为 5000 Mbps，host / sink 保持正常。此项只证明 normal 面可用和该次
切换未超时；reverse 面及电脑网络模式仍待验收，不得视为修复完成。

### 第三版反向复验与首次初始化漏点

再次翻回 reverse 后日志 `boot=0x19 flip=1 output=0x107 result=0`，
RTL8153 与 SS hub 均为 5000 Mbps，host/sink 保持正常。双面切换这一轮
均通过，不能由此推定首次带坞启动与电脑 USB 网络模式也通过。

源码已确认初始 controller-sync 初始化会预先写入期望方向。第三版 helper
仅比较该请求值，导致首次角色准备时提前返回，未执行已验证的 system-mode
切换。第四版将无操作条件限为已处于 system mode 且请求值、实际输出均
匹配；初始 controller-sync 值相同仍须应用一次。模拟覆盖了请求值相同但
实际输出未跟随的冷启动状态。首次带坞启动是否因此恢复仍须实机验证。

### 第四版带坞启动未通过

实机 #84 带坞重启，0.287 秒已记录 `boot=0x19 flip=1 output=0x107
result=0`，但 8.239 秒 xHCI 启动后 RTL8153 仍枚举为 480 Mbps。
所以实际输出正确本身不足以保证首次训练成功，第四版不能标为已解决。

对照 DWC3 角色路径与 PHY 的 `was_host` 可知：早期 device 准备已经
应用方向，随后 host 准备因相同方向而跳过。第五版将真实角色变化作为
重新应用 mux 切换的条件，同角色、同方向且实际输出匹配仍不写寄存器。
不改变默认角色、Type-C 或供电，不使用定时切换或重复重启恢复。
这仍是针对剩余时序差异的候选，须带坞重启与原有功能回归后才可验收。


### 第五版带坞重启仍偶发降至 USB2，保留故障现场

用户连续多次正常重启，部分为 5000 Mbps，部分为 480 Mbps。此次只读确认
运行 #85，host / sink / reverse，USB2 hub `0bda:5411` 与网口
`0bda:8153` 都是 480，两个 `0bda:0411` SS hub 均未出现。
0.311 秒已应用 reverse，7.123 秒 host 入口再次应用：
`boot=0x19 flip=1 output=0x107 result=0 role_change=1`。
所以方向输出匹配、在 host 入口重应用都不足以证明首次 USB3 训练成功。
xHCI 的 SS port02 为 `0x0a0002a0 Speed=0 Link=RxDetect PP WCE WOE`；
这次不是网口自身协商成 100M，也不是 root hub 不支持 USB3。

保留当前故障现场，未重启、未切 USB、未重新插拔。只读采样保存在私有
`work/usb3-20261112/role-entry-failing-{boot,link,tcpm}.txt`。

### 第六版候选：补齐原厂角色位与禁用输出握手，待实机验证

原厂 `zte-u30air/drivers/usb/phy/phy-sprd-usb31-qogirn6pro.c` 的
`sprd_ssphy_init()` 明确以 `is_host` 设置 GCFG bit4：host 清零，device 置位。
现有主线首次按 device 初始化，之后 `set_vbus()` 更新 is_host 和 USB2
电气配置，却未同步这项 USB3 PHY 角色位。前五版方向 helper 也保留了该位。
这是代码路径确定的遗漏，但当前内核没有该寄存器的运行时读取接口，不能
把源码推导冒充本次故障现场实测，更不能直接认定是全部偶发故障的唯一根因。

另一个未验证环节：旧 helper 连续写 disable/flip/enable，仅检查最终输出，
即使最终方向本来就匹配，也可能未确认禁用状态是否真正到达 PHY。
第六版对禁用、禁用中方向以及重新启用分别做有上限的输出确认，在禁用
期间同步原厂角色位。失败直接阻止新控制器启动，保留后续显式重试入口；
不额外重启、不加定时角色切换、不改 TCPM/VBUS 策略或 USB 速率上限。
最终日志包含前后 GCFG、host 和 PSTATE，供下一轮实机对照。

模拟测试已验证角色位、禁用超时不继续启用、双方向、重复通知不写、显式
重试和其他平台隔离；硬件结果尚待验证。


第六版完整 7.2.8 Image/202 个模块校验通过，6.18.54 和 7.2.8 的
DWC3/SSPHY 对象编译通过。试验 boot SHA256：
`a40e76e3e05441e27aa43deb2cce18675256ca78778018f9e39a1949c16f726e`。
本轮继续此前用户授权的 Linux boot_b/配套模块及带坞重启范围，已保留本轮
更新前 boot、前五版与原厂基线备份，写入后全分区回读哈希一致；已请求正常
重启，待检查首次枚举，不能据此记为硬件通过。


第六版写入后正常重启等待期间，电脑切回 CUDY_TR3000；多次重新连接
U30Air_1TB 提示网络不可用，SSH 无法到达。当前不能区分正常重启卡在退出
阶段、启动未完成或热点未启动，不得把此现象直接归因到新候选。需要用户
保持扩展坞不动并物理重启后，优先读取日志/启动版本；尚无 USB3 验证结果。


### 第六版恢复连接后的证据和第七版候选

用户已连回热点，电脑自行连接已保存的 U30Air_1TB 成功。运行 #86，
已开机12分钟，前述连接失败不能推断机器启动失败。角色位日志明确
`host=1 gcfg=0x10->0x0`，方向输出0x107成功，但SS端口仍RxDetect，网口480。
一次仅针对SS根端口的disable/enable重新探测没有恢复，原值0已恢复。

本次直接读取AON regmap，`0d14=00000001`，`0204=00000000`。
本机绑定头文件定义SYS_VBUSVALID为0x4、OTG_VBUS_VALID_PHYREG为0x1000000，
所以当前host/sink下两项内部有效输入确实均为0，不是推测。
BC1.2在device检测时调用notify_disconnect清掉这些输入；随后host入口设置
is_host、DP/DM和方向却不恢复，host分支的notify_connect又直接返回。
原厂host恢复重新phy_init会设置有效。第七版仅在host交接里补回原厂
init的HS/SS有效输入及VBUSVLDEXT，不控制外部供电、不启动新BC1.2检测。
这些写入与检测沿用dpdm_lock串行化，早于xHCI启动。

生产函数提取测试覆盖detach后host交接、host时迟到disconnect不清输入、
寄存器失败传播；原BC1.2事件测试和方向/角色测试通过。待完整编译和硬件
验证，不能仅凭这些内部信号异常就宣布已找到全部降速原因。


第七版 #87 完整编译和两条内核的PHY/DWC3对象检查通过，boot_b写入回读
SHA256 `22d5304e7f7364d89eb2f86a610bd59aacbf26a1dee3ae20f11b74e25a9634a5`。
实际带坞启动仍480。寄存器0204已恢复01000000、0d14已恢复00000005，说明
已修正内部有效输入遗漏，但该修正并不足以恢复这次SS链路，不能认定根因
已经全部定位。两次方向/角色准备均返回成功，PSTATE仍333322。

诊断中进行一次DWC3 data-role device->host往返（不请求Type-C/电源角色变更），
结果仍480；结束已恢复host，TCPM保持host/sink。之前SS根端口disable/enable
也未恢复，disable已还原0。均非永久配置或发布修复，不加入自动恢复循环。
需要同版本保持坞供电、只重插设备C口后的工作状态对照，不能继续凭寄存器
请求成功推断物理链路成功，更不能盲目重置整个PHY/控制器改变正常电脑网络。

电脑重连失败的另一个原因是Windows热点缓存未刷新；调用WlanScan后连接
已保存的U30Air_1TB成功。后续正常重启后由控制端自行扫描并重连，不需要
用户代为切Wi-Fi，不把这种连接失败误报为设备重启卡死。


### 第七版同方向物理重插对照与第八版门控候选

用户保持坞供电、重插设备端后，仍为reverse/host/sink，boot位0x19，
0204=01000000、0d14=00000005，与失败时一致。但SS hub `0bda:0411`
两个以及RTL8153均为5000 Mbps，port02为 `0x00001203 Speed=4 Link=U0
CCS PP PED`。物理重插前软件角色往返没有恢复，所以不能把“再设一次host”
当成修复。保留插拔前后日志，不认定每次反插/某款扩展坞必然失败。

再次核对原厂dwc3-sprd.c的600--631行：UNKNOWN/DCP走charging_mode，
只有SDP/CDP才请求peripheral数据路径；662--675行在PHY恢复后才通知connect
并设置DEVICE role。目前主线DWC3在0.3秒开放SS，而Type-C/PD经历数次
device/sink attach/detach，到9秒才完成host/sink，存在早开放数据链路差异。
这是有源码依据的时序假设，尚未证明远端坞具体锁存机制。

第八版仅在原有电池BC1.2策略启用的平台上，device且尚未确认SDP/CDP时
保持TCA禁用；确认数据线交接后由原notify_connect事件开放，host角色事件
直接开放，disconnect事件关闭。沿用既有dpdm_lock串行化，不新增线程/
轮询/延时切角色，不改Type-C角色或供电。不启用电池策略的F50等维持原路径。
初始化也不先短暂进入controller-sync开放态；相应helper状态比较覆盖禁用位。
离线测试覆盖未知充电器保持禁用、SDP/CDP交接开放、host+sink开放、同状态
不重复写和原有BC1.2事件回归。硬件结果待验证。


第八版#89带坞启动未通过：初期禁用输出0x10f确认到达，host开放输出0x107，
但SS端口停在RxDetect且PR复位位保持，RTL8153在两次采样中未枚举；第七版
同套硬件可在USB2枚举网卡。基于这一新增差异，已撤回门控代码和实机第八版，
恢复第七版boot/模块，完整回读哈希22d5304e...；恢复后#87网卡再次枚举为480。
失败门控源码另存私有work，不纳入发布。原方向/角色/VBUSVALID修正未撤回，
四组离线回归再次通过。

因此物理重新接入可5000而带坞重启不稳定仍未解决，不能宣布第八版根因成立。
后续需原厂Android同坞、同方向、持续供电带坞重启的实际对照，核对电源/链路
处理的硬件状态；不能继续只凭类似PHY的数字mux输出对接连猜测。


恢复#87后用户再次同方向重插，两个0bda:0411 SS hub与RTL8153再次5000，
port02为0x00001203/U0，host/sink/reverse均正确。工作时GCTL=00111006、
GUSB3PIPECTL(0)=01001102、GDBGLTSSM=9cc20026、AON002c=19、0204=01000000、
0d14=5，与失败现场读到的对应值一致；仅凭这些静态值无法继续定位首次训练
差异。完整记录在restored-host-valid-replug-5000.txt。保留该工作现场，未继续
刷写/重启，也未获得切Android的明确答复，因此未执行系统切换。


## 继续排查：只读完整快照、标准PM及PD事件证据

新增独立诊断模块 `upstream/debug/usb3-snapshot`，仅按实际DT资源读取TCA
0x00--0x34，不写任何寄存器/时钟/电源。手工临时加载，读完卸载，未加到
生产模块、启动脚本或发布清单。标准构建入口的诊断输出保存在私有work。

在#87同方向，工作快照PSTATE=00333300、失败=00333322；GCFG=0，
TCPC=7、SYSMODE=7、GEN_STATUS=107均一致。实际PHY DP/DPTX的DT phandle
分别指向31810000和31890000，此前25304000/2600c000不是这些phandle，
不得把它们的值当成该PHY证据。正确DP控制块工作/失败采样相同，DPTX c08=5。
工作与失败快照文件分别为snapshot-working-5000.txt和snapshot-boot-compare.txt。

临时把DWC3切device并允许标准runtime PM，约6秒进入suspended；恢复on和
host后仍480。原策略on已恢复，host/sink不变。该诊断未加到生产自动恢复。
对本次失效，缺少一次普通runtime suspend/resume不是足够解释。

完整TCPM日志从0.726秒起记录启动：在2.275和3.569秒接收包时分别进入
Data role mismatch -> ERROR_RECOVERY -> PORT_RESET；4.845秒收到合法SourceCaps，
4.942秒SNK_READY，6.984秒对端发起DR_SWAP，6.987秒完成DFP/host切换。
之后反复DISCOVER_IDENTITY / Sink TX No Go。已通过标准typec/data_role尝试
设备/主机往返；设备请求返回EAGAIN，未发起成功的PD往返，因此不能称为
“对端拒绝”或“已做完整协商复测”。供电始终sink。

另一个确定遗漏：运行时PMIC TYPEC_PD_CFG (0388)=1，CONSTRACT bit6为0，
但软件已有成功PD协商与SOP发送。原厂在第一次收到GoodCRC后置CONSTRACT。
现有驱动仅在显式GoodCRC FIFO帧分支置位，TX_OK完成分支没有做相同交接；
硬件自动GoodCRC时仅有TX_OK回调也被视为成功，因而漏置这项原厂标志。
当前新候选把交接统一放入已有pending TX的完成函数，仅对成功SOP设置，
明确排除Hard Reset/失败/重复完成；写入失败如实报告发送完成失败。

原厂参考为 `zte-u30air/drivers/usb/typec/tcpm/sc27xx_pd.c` 的GoodCRC分支
1179--1189与TX_OK分支1709--1712。新增实际回调测试覆盖仅TX_OK无RX帧、
GoodCRC与TX_OK同一IRQ、重复中断、Hard Reset不置位、失败路径与原角色/电源
分离回归。不能据此宣称USB3已修好，仍需带坞重启验证。


PD第九版#90试启动后，0388由00000001变成00000041，首次SOP ACK的
原厂交接标志已生效；带坞启动仍480，PSTATE/方向表现与第七版一致。
所以该修正有独立硬件验证，但没有解决全部USB3问题，不宣布根因已查全。
对应boot SHA256为8af712ac391d457fcb557bd1abfb60fcc67a66e05d3812af98c2f5576f864733。

用户确认原厂Android已开无线ADB，按此前原厂同条件对照方案，通过现有
mu300-next-boot android选Android A并正常重启，未刷Android启动分区。
已保存切换前bootloader_control、Linux trial block（均32字节且CRC有效）与
原default-boot=linux到私有work；Linux B保留第九版。当前准备读取Android
带坞启动状态，之后用已备份的Linux trial block回到B，再运行next-boot linux
恢复原默认值。原厂热点与Linux不同，新出现ZTE_91C5A2，电脑无此Wi-Fi配置，
已请求用户确认并连接原厂热点，不请求用户发送密码，不连接其他未知设备。

### 原厂实测与管理链路约束（后续结果）

用户随后明确禁止电脑连接设备热点，以保留电脑原有联网路径。此限制覆盖
之前的热点重连计划：电脑继续连接 CUDY_TR3000，使用扩展坞网口到电脑
以太网的独立管理链路访问原厂 Android 192.168.0.1:5555。有线接口已保存
原配置，临时使用无默认网关的静态地址，不能让设备 DHCP 替换电脑默认路由。

本机原厂 U30Air_SPV1.0.0B04、内核
5.4.254-android12-9-gb50b8ab9329f，在扩展坞持续连接的首次 Android 启动及
随后一次 Android 正常重启中也均只枚举 USB2 hub 0bda:5411 和 RTL8153 480。
因此“原厂一定能在相同条件下直接 USB3”并未成立，不能把此前第三方
总结中的因果结论当成本机验证结果。还需原厂物理重插后的工作状态对照。

用户授权 ADB su 后，进一步确认 host/sink 角色与原厂眼图参数。临时标准
USBDEVFS_RESET 工具先读取 USB 描述符，确认目标为指定 VID/PID 的集线器；
已检查集线器下读卡器容量为零且没有挂载可移动存储。对 USB2 上游 hub
0bda:5411 的一次复位成功，下游网口/读卡器重新枚举，但仍为 480，没有
出现 SS hub。ADB 短暂断线后已通过原有网线恢复，电脑 Wi-Fi 未改动。
此结果排除了“仅给 USB2 集线器做总线复位即可恢复”的方案，诊断工具
不加入生产启动流程，不新增周期复位或全局自动复位策略。

随后用户只物理重插设备端 C 口，原厂两个 SS hub 0bda:0411 与 RTL8153
均恢复 5000，host/sink 不变。原厂从这个工作状态再正常重启一次，未再
拔线，三个 SS 设备保持 5000。因此原厂不是每次带坞启动都失败，也不能
据一轮成功宣布它不存在问题。原厂同样需要物理重插恢复的两轮 480 和
这轮 5000 已分别保存完整证据。

原厂工作重启日志显示约 3.7 秒首次 PHY/core 初始化，4.36 秒 PHY 关闭，
5.02 秒 PD Hard Reset，10.65 秒完成对端 DR_SWAP，随后 host runtime
resume，11.29 秒 SS hub 枚举。它也先以 device 初始化，并非“开机强制
host/SRC”才有 USB3；不能为保速把所有板子的默认数据/供电角色写死。
原厂工作时仍记录 PTN38003A 未 probe，进一步说明不能把缺失该 redriver
的驱动单独当成当前板子的全部根因。

### 第十版候选：原厂 host-before-resume 生命周期（尚未实机验证）

从原厂工作状态切回 #90 后，启动日志只出现 high-speed hub/RTL8153；
PC 有线管理未恢复。用户改为 USB 直连后取回日志，确认 Linux 启动完整，
原因包含配置中 br-lan 仅有 usb0，USB 网卡没有被设为 LAN。已通过原有
next-boot 工具恢复默认 Linux。不能把管理地址不通认定为系统启动失败。

进一步对照原厂 dwc3-sprd.c 的 dwc3_sprd_otg_start_host()：先调用
usb_phy_vbus_on(ss_phy)，再 pm_runtime_get_sync(core)，最后切换/启动 host。
现有主线 __dwc3_set_mode() 则在进入 worker 时先恢复旧 device PHY/core，
之后才停止旧控制器及设置 host。这解释了为什么前述“device suspend，
先恢复 on，再切 host”的试验没有覆盖原厂的初始化顺序；仍属待验证假设。

候选只作用于本驱动已有电池/BC1.2 策略、共享 USB2/USB3 combo PHY、
实际进入 host 的过渡。在旧 gadget/xHCI 均已停止后，通过成对的完整
core_exit / core_init_for_resume 设置 host 意图并恢复 PHY/core，再启动
xHCI；保留电脑 device 路径、已处于 host 的同角色方向处理及无电池板
原有路径。不请求外部 VBUS、电源角色或 CC 变更，不加周期复位/用户态
延时切换。恢复函数包含时钟使能、reset deassert 和失败清理，不能只在
core_exit 后直接调用不恢复时钟的 core_init。

真实 role block 的离线 C 回归覆盖目标/非目标板、device/host 过渡、
已有 host、错误阻止启动及显式重试；6.18/7.2 的相关对象已编译通过。
完整候选仍在构建，未写入机器，尚不能声明修复带坞启动。

第十版 #92 已按用户授权部署，只写 boot_b 前 48 MiB；AVB footer 与上一版
相同，日志区未覆盖。前 48 MiB 回读 SHA256 为
1e044fe77e31061bacdd9881893bd6cad79ac09fea3f6a012a55395eac6b0c30。
对应原模块 bank 保存在 7.2.8.usb3-stock-host-resume-before。临时开启
lan_auto=1 后本机网卡 eth0 自动加入 br-lan，电脑保持原 Wi-Fi，通过网线
读取到了完整启动。带坞首次启动仍 480；随后蜂窝、Wi-Fi 均正常启动。

该次日志还发现候选自己的遗漏：PHY 完整初始化把 GCFG 设回 controller
sync 的 host 模式 1；准备 hook 未在恢复后再次完成 system-mode 交接，
通用 otg_set_vbus 回调直到 xHCI 启动后才把 GCFG 1 改为 0。实际时间为
7.327 秒 xHCI 开始、7.328 秒 TCA 再同步。不能据此认为完整的原厂顺序
假设已经被否定，因为候选仍含一次启动后的链路配置变更。

后续修正把恢复后的第二次 PHY 同步放回准备 hook，在 xHCI 启动前完成；
同步失败会配对关闭已恢复 core，防止重试时重复使能时钟。真实 hook
测试新增“完整初始化使准备状态失效”的模型及恢复后同步失败/重试，
不再只验证首次 set_vbus 早于 xHCI。两个内核相关对象通过，完整新版
正在编译，尚未部署。

第十一版 #93 已获授权写入并正常带坞启动。前 48 MiB 校验值
707bf8dd365e3f6e1e6d2257d9d6b3597c4126589c0ff910d32d86c0fa2b0145。
本轮 TCA 同步确实发生于 7.194956 秒，早于 7.195212 秒 xHCI 启动，
但 hub/RTL8153 仍只枚举 high-speed。首次未到 5000，因此没有执行
用户条件授权中的第二次重启。不能继续把“host 前完整恢复”当成已验证
的 USB3 降速根因或解决方案。

### 确认另一处数据状态交接遗漏（尚未验证 USB3 效果）

源码/启动日志可复现的顺序：BC1.2 work 在 0.21 秒要求
notify_disconnect，此时 PHY 未初始化而直接返回；随后 PHY init 在
0.25 秒无条件写入内部 VBUSVALID，却没有重放 BC1.2 决策。该 work
已经保存 generation/seen，正常继续采样不会再次进入初次 disconnect
分支；最终 UNKNOWN/DCP 分支也仅设置 DP/DM mux，不再清 VBUSVALID。
因此在新一代事件/拔插前，控制器可能把尚未通过数据口识别的连接视为
有效设备数据链路。原厂 glue 则在确认 SDP/CDP 后才启动 peripheral。

修正仅在 PHY 初始化返回 DWC3 前、原 dpdm_lock 下重新应用当前缓存：
设备模式仅 present 且 SDP/CDP 开放内部有效信号；未知/纯充电/拔线关闭；
host+sink 沿用主机初始化，无电池板完全不走新路径。不改 TCA 禁用位、
外部电源角色、充电检测时序，也不新增轮询。先前第八版失败的 TCA 门控
没有重新引入。

新离线 C 回归重放实际决策 helper，覆盖 detector-before-init、SDP/CDP、
UNKNOWN/DCP、拔线、host+sink 和无电池隔离；原 BC1.2 事件/超时/换代
回归及两条内核对象检查通过。完整候选正在构建，尚未刷入；这处缺陷的
源码证据不等于已经证明它导致所有带坞 480 现象。

第十二版 #94 已按用户授权刷入，前 48 MiB 校验值为
a66686d6b1725d45d3f786493529b176ca524a925bdcce0aa5213768e573f675。
启动 0.262908 秒已重新应用 `vbus invalid`，证明 detector-before-init
交接修正生效；9.147 秒 host 同步后 xHCI 正常启动，但网口仍 480，
SS root port 仍 RxDetect。首次未到 5000，没有执行条件中的第二次重启。
蜂窝 IPv4/IPv6、Wi-Fi、网线管理正常，不据此宣布 USB3 已修好。

### 本机原厂模块二进制核对与保留正确同步模式候选

当前 OpenWrt 只读提取本机 vendor_boot_a 的 vendor ramdisk，解析 newc
仅保存 USB/Type-C/PD 目标模块，未挂载写入 Android 或切系统。所有提取
模块 vermagic 均为实际 B04 内核 5.4.254-android12-9-gb50b8ab9329f。
原厂 PHY 模块 SHA256 为
40032e9a7ee26a067cb8b5c2740df80a77098f5058c11be84e81326e0757aa64。
私有证据在 stock-live-usb-modules.json 及对应反汇编文件，不作为版本白名单。

实际 sprd_ssphy_init 的 0x103c--0x1094 代码将 GCFG 保持 controller-sync=1，
按 is_host 设置 bit4；0x111c--0x118c 依据 AON 方向写 TCPC、SYSMODE 输入。
这一关键顺序与公开 GPL 源码一致，没有发现额外神秘寄存器写入。当前 helper
却要求必须处于 system=0 才可无操作，即使 stock 初始化的角色、TCPC 方向、
GEN_STATUS 实际输出均正确，也会额外手动切换。

新候选只缩小这项过度操作：已知合法 system=0 或 controller-sync=1 下，
角色位、当前模式对应的有效输入、实际输出和方向全部匹配，且没有上次错误
或实际角色变化时不写寄存器；controller-sync 检查 TCPC，而非不活动的
SYSMODE。错误/不同方向/不同角色继续走原静默窗口同步。回归加入真实
controller-sync 正确时零写入、SYSMODE 值不同仍不误判，同时保留请求正确
但输出错误必须修正、角色变化和失败重试用例。此候选尚未实机验证。

第十三版 #95 已按用户授权写入并完成两次连续正常带坞重启，boot 前
48 MiB 回读 SHA256 为
39389c37c068b9e7ba13af6dadb846c66945f22b5f17659aa818305c0c20d87d。
两个 boot_id 分别为 d2c9923e-f6ae-4383-a1b1-d87dee7ca689 和
e0f38c22-097d-4fb8-98ad-d551048d075c，均完整启动、网线管理正常，但
RTL8153 都只以 480 枚举，SS hub 未出现。未改变电脑 Wi-Fi/默认联网路径。

第一轮启动后临时加载既有只读 TCA 快照模块，读完卸载：GCFG=1，TCPC=7，
SYSMODE=7，GEN_STATUS=107，PSTATE=00333322；SS root port 为
0x0a0002a0、Speed=0、RxDetect。证明保留原厂 controller-sync 已生效，
并非仍在手动模式；它不足以单独修好该组合。没有发现内核 Oops/BUG/panic，
蜂窝 IPv4/IPv6 与 Wi-Fi 也正常。完整证据在 retain-stock-sync-{first,second}-boot.txt
及 first-settled.txt，不能把本轮列为 USB3 硬件验收通过。

原厂同坞既有失败和成功记录，加上这些结果，下一步需要另一款 USB3 坞或
无 PD 转接器的同机对照，区分设备 PHY 初始状态和当前坞的交互影响。当前
没有证明故障只在扩展坞，也没有证明所有 MU300/F50/U30 Air 同源故障已修。
尚未形成可发布 USB3 修复，实验性完整 host 重初始化仍须后续取舍，不应
把无收益额外复位直接当作正式功能发布。

### 无 PD 网卡对照（同一 #95 镜像）

用户更换不带 PD 的 USB3 网卡，维持网线管理及电脑原 Wi-Fi。热插后直连
RTL8153 `2-1` 为 5000，data=host、power=source、reverse，SS root port
为 0x00001203/U0。随后按新授权做两次正常重启，boot_id 为
953ed9b2-4530-4749-87e0-ce6855719b76 和
63319097-d647-4d36-8290-eef445c43957，均启动直接枚举 5000。
第二次随后确认蜂窝 IPv4/IPv6、Wi-Fi 正常。未刷新任何镜像。

| #95 实测连接 | 热插后 | 保持连接正常重启 1 | 正常重启 2 |
| --- | --- | --- | --- |
| 带供电、双层 Realtek hub 的原坞 | 既往同方向可 5000 | 480 | 480 |
| 不带 PD、直连 RTL8153 网卡 | 5000 | 5000 | 5000 |

这个结果支持继续检查带供电坞的启动交接，但两套外设也有 hub 拓扑/线材
差异，不能仅凭此表断言唯一原因就是 PD。下一种更严格的对照应是同一
原坞撤去外部供电，在电池供电 host/source 下比较，而非立即更改 PD 恢复
策略。当前没有再次刷写或增加重启，保留这两轮结果与正常工作状态。

用户随后授权原坞撤外供后的额外两次重启。首次换回检查时原坞 SS hub 和
RTL8153 均已 5000，但仍为 host/sink、PD 5V/1.4A，bq online/Charging，
电池 4.278V/+651mA。因此尚不满足“无外供”测试条件，未执行这两次
重启；已请求核对拔掉的是坞的 PD 线，并排除其他 USB 供电。私有文件
original-dock-unpowered-hotplug.txt 的名称代表计划条件，不能凭文件名
把仍在充电的实际采样列为无外供通过。需等待实际供电状态确认。

用户随后明确断开外供。只读确认原坞仍为同一双层 hub，但设备已变为
host/source、bq online=0、FGU Discharging，约 4.109V/-691mA，SS hub
和网口均为 5000。按已授权范围正常重启两次，boot_id 为
e2356f0f-4446-4c5a-8652-03885dbccfa7 与
24b2764e-5a24-4170-814a-0a89f10912a6；两次同一原坞均直接 5000，
仍为 host/source、没有充电，后续蜂窝 IPv4/IPv6 及 Wi-Fi 正常。
这组控制了 hub 拓扑变量，支持继续调查持续外供跨重启的角色/PD 交接。
不过它仍同时改变了电源角色和坞跨重启时是否断电，尚不能把单个协议步骤
认作已证实根因。

### 电池机型的 self-powered 能力声明遗漏与实机结果

主线 usb-connector.yaml 定义 self-powered 为设备具备自身电源。
当前 sc27xx 驱动只在 charger_policy_service 启用的电池板绑定，但软件
connector 未提供此属性；TCPM 实际读取默认 false。该区别影响
PORT_RESET 的 CC 处理以及 SNK_WAIT_CAPABILITIES 超时恢复分支。原厂
sprd_tcpm 的 PORT_RESET 直接请求 CC_OPEN，主线则仅 self-powered 时如此，
否则保留取电所需 RD/Rp；实际启动日志中已有 cc:=2 与角色不匹配恢复。

新增软件节点的 self-powered 布尔能力，保持原有 5V PDO、电流、双角色和
默认 sink 偏好，未修改 TCPM 核心状态机、定时器或写 PMIC force-CC。
无电池 F50/V50 在建立节点前已有明确不绑定检查，沿用其原始路径。
这是一项由物理电池供电实测和主线绑定定义支持的能力修正，不代表已证明
它可以恢复带供电坞 USB3。

六项 PD 回归在既有 root 构建容器通过，两版 PD/PHY/DWC3 对象编译通过，
完整新候选已构建。首次直接在非 root WSL 运行的 shell 用例因
"run as root" 失败，已在规范构建环境重新执行通过，未修改测试来绕过。

用户恢复外供并明确授权 #96 写入及两次正常带坞重启。写入 boot_b 前
48 MiB 后回读 SHA256 为
d0fb2c681224128686e5ce73fc3047a5c8d5fdcb537557d1a915b00b2b38fb0d，
完整 Image/模块清单校验通过，原 #95 boot 前缀和模块目录均保留。
两次 boot_id 分别为 d8c1cac1-0b70-4eb0-ba09-293e1d7756f9 和
1a13197e-3901-4fd9-a361-85991790ac6f；两次都是 host/sink、PD 5V/1.4A、
充电正常，但只有 USB2 hub 和 RTL8153 的 480 枚举，没有 SS hub。
第二轮稳定后蜂窝 IPv4/IPv6、Wi-Fi 和网线管理正常，未见 Oops/BUG。

TCPM 日志中 PORT_RESET 的 cc:=0 及后续超时目标 HARD_RESET_SEND
证明 self-powered 分支已生效。然而最初两次收到的有效 PD 包仍触发
Data role mismatch，之后重新协商成功，并由扩展坞请求 DR_SWAP，最终
切至 host/sink。这只能证实跨重启的协议角色交接存在恢复过程，不能
把它直接等同于 SS 枚举缺失的唯一原因。当前 set_cc(OPEN) 沿用原厂
空操作，日志中的 cc:=0 也不代表物理 CC 已真正断开。

本轮未解决带外供启动降速，不增加额外刷写或重启；当前 #96 失败现场
和临时网线管理保留。证据为私有 battery-backed-pd-{first,second}-boot.txt
及 second-settled.txt。电脑一直保留 CUDY_TR3000 联网，不切设备热点。

### 后续：原厂 PD 数据角色冲突恢复差异

已提交阶段检查点 4b902ba0（签名有效），并继续核对本机 B04 原厂驱动。
原厂 GPL `drivers/usb/typec/tcpm/sprd_tcpm.c` 的 RX handler 对相同数据角色
的协议错误调用 `sprd_hard_reset_state(port)`；主线 6.18/7.2 则直接进入
ERROR_RECOVERY。UMP9620 的 set_cc(OPEN) 为原厂空操作，因此后者记录
“请求 CC_OPEN”并不证明实际触发了 CC 断开。#96 的两次启动都有此类
有效 PD 消息触发的角色冲突，随后再次协商与数据角色切换。

下一候选恢复原厂的**错误触发**、有次数上限的 PD 恢复路径：给 tcpc_dev
新增默认关闭的选择位，仅本就限定电池板的 UMP9620 驱动置位；其他 TCPC
仍走原主线 ERROR_RECOVERY。调用主线既有 hard_reset_state，不强制写
HARD_RESET_SEND、不清重试计数，不忽略冲突消息、不更改正常角色切换、
PDO/限流或默认 USB 模式。通过 install.py 同步进入两版内核的实际 TCPM。
这是对已观察协议恢复差异的候选，不代表其与 SS 缺失的因果已经实机验证。

本轮还有一次诊断操作失误，必须与 USB3 故障分开：全表读取各 debugfs
regmap/registers 触发 MMIO external abort，内核 panic 后自动重启。pstore
明确指向 regmap_mmio_read32le -> regmap_read_debugfs；私有证据
regmap-read-reboot-pstore.txt。不是一次获授权的正常带坞测试，也不能计入
候选重启验收。设备自动恢复 #96 后蜂窝/管理恢复、网口仍 480。禁止再
遍历读取 regmap 全表，grep 过滤输出也无法阻止底层对未知 MMIO 的访问。
后续只使用已验证、限定地址且具备安全访问条件的读取；当前先做离线检查。

### 数据角色冲突恢复候选 #97：未通过并已恢复

6.18/7.2 TCPM、PD、PHY、DWC3 对象编译及回归通过；完整 #97 镜像与
202 个模块的校验通过。用户明确授权写入及两次带外供正常重启。首次
boot_id=f2225b5e-e656-418a-a534-46ea13cc74fd，完整 OpenWrt、蜂窝与
热点已启动，但没有切到 host，扩展坞网线不通；用户改为直连电脑后
USB NCM/SSH 恢复。不能把管理连接缺失解释成整机没有开机。

日志显示角色冲突确实选择 PD hard-reset；两次发送都没有 TX completion，
约 100ms 后状态机继续。第一轮重置期间仍有旧 SourceCaps 被送到 TCPM，
打断 SNK_HARD_RESET_SINK_OFF 并再次触发恢复；耗尽既有计数后进入
SNK_READY，但没有重新协商成 host/sink。此候选没有通过，未做第二次
同候选带坞验收，也不声称简单照搬原厂状态跳转即可解决 USB3。

捕获失败日志后，仅恢复这一候选相对 #96 的 boot/模块差异，保留方向、
有效状态与电池能力修正。boot 前 48 MiB 恢复回读 d0fb2c681224128686e5ce73fc3047a5c8d5fdcb537557d1a915b00b2b38fb0d，
原 #96 模块目录 202 项哈希逐一核验；#97 模块留在 usb3-stock-role-error-rejected。
本轮剩余一次正常重启用于恢复核对（直连电脑，不冒充带坞验收），已确认
#96，boot_id=7b2ad7ed-213c-46e5-a2ed-b27a82c5d876，NCM/SSH、蜂窝
IPv4/IPv6、热点正常。完整证据在 stock-role-error-first-boot-direct-usb.txt、
restore-after-stock-role-error.txt、restored-after-stock-role-error-boot.txt。

### 补齐原厂接收关闭／FIFO 与消息 ID 生命周期（待实机）

原厂 sc27xx_pd_set_rx 会先调用 reset(false)，控制 PKG_RV 中断及接收，
reset 按 RX flush -> TX flush -> RX ID clear 顺序；原厂发送／收到硬复位
还会 reset(true)，初始化也清 TX ID。当前主线适配的 set_rx 只切控制位，
收包中断始终开启；发送硬复位只写 HARD_RESET，遗漏原厂清理。

补齐上述已有硬件操作，并在 pd->lock 下串行接收门控、FIFO 操作和 IRQ：
关闭/重新启用接收先屏蔽 PKG_RV；成功启用才发布 rx_enabled。发送硬复位
前停止普通收包，清 FIFO/ID，再发出 strobe；不伪造 TX completion。关闭
接收或同次 IRQ 已收到硬复位时不再把残留帧上送 TCPM。普通 SOP 发送
保持原接收通路与 RX ID，不把正常通信当复位处理，不更改电源/角色策略。

新增实际 C 函数回归覆盖寄存器次序、生产者关闭、失败不激活/不发出复位、
普通 SOP 不清 RX，既有 IRQ 用例增加关闭接收及硬复位尾部帧隔离。
日志 20261112-131528-usb3-stock-pd-reset-tests-58800 全部通过，两版
对象编译日志 20261112-131507-usb3-stock-pd-reset-check-58778 通过。
完整候选正在构建，尚未部署。#97 的失败和新生命周期遗漏应分开报告；
不能预告这版一定恢复 SuperSpeed，也不能将之前的授权重复用于下一版刷写。

### #98 生命周期补齐实测与隔离对照

用户授权 #98 新候选首轮带外供重启，只有首次正常枚举才再重启一次。
boot 前 48 MiB 回读为
9563aff64957785e56e83832fede0afbb2de31c4e0a16f3355834a3922be9ba2；
Image/模块 204 个清单项核验通过。首次 boot_id 为
00a49c65-1c63-4d9f-a7f2-8918b5cccf07，仍未转 host，网线无管理连接。
用户直连电脑后确认完整系统、USB NCM、蜂窝 IPv4/IPv6、热点正常。
原坞首轮未正常枚举，因此没有执行条件中的第二次重启。

日志证明接收门控修正已生效：第一轮 SNK_HARD_RESET_SINK_OFF 完整等待
650ms，没有再被旧 SourceCaps 提前打断；但随后收到的 SourceCaps 仍是
远端 UFP，与本端 device 冲突，发送硬复位也仍无 TX completion。最终
没有协商成 host/sink。这是“接收生命周期问题已改善，但强制选择原厂
硬复位恢复仍未通过”的结果，不能把两者一并发布为 USB3 修复。
证据 stock-pd-reset-first-boot-direct-usb.txt。

已撤掉 UMP9620 对硬复位恢复策略的启用，并通过受版本管理的安装入口，
明确移除已准备内核树中的试验分支、前向声明和 tcpc_dev 字段。新源码
以及两版已准备树的 TCPM 都恢复原主线策略；全新源码不加这些字段。
清理函数保留显式试验应用/移除的离线回归入口，正常构建固定 enable=False，
不选择另一个旧驱动、不更改原 PHY、角色或供电路径。

下一候选只把已确认遗漏的接收/FIFO/ID 生命周期与 #96 做单独对照。
两版对象检查 20261112-132817-usb3-rx-lifecycle-only-check-58871 通过，
回归 20261112-132913-usb3-rx-lifecycle-only-tests-58899 通过。完整镜像
构建中；当前设备仍 #98，不因本地代码撤掉策略就声称机器已恢复该项。

### #99 隔离结果及 PHY 初始化前后的角色交接遗漏

按用户新授权刷入仅接收生命周期修正，boot 前 48 MiB 回读为
79687c85f4811c3c819080feece9132274827a55aac798ff99c95012e57eed21，
202 个模块与 Image 清单均核验。直连电脑正常重启为 #99，boot_id 为
30b58686-5675-4c8d-8f6e-f8f87c78ebf6，UDC configured/high-speed、
NCM/SSH、蜂窝 IPv4/IPv6、Wi-Fi 正常。早期抓取时 netifd 尚未创建 WAN，
随后稳定抓取已确认 up；不把早期 Not found 当成网络缺陷。

用户接回原外供坞后 host/sink 与充电/Full 状态正常，网口管理恢复，
但只有 480 的 USB2 hub/RTL8153，没有 SS hub。证据
rx-lifecycle-only-{first-pc-boot,pc-settled,dock-hotplug}.txt。
热插未达到 USB3 目标，没有使用条件性的第二次带坞重启。

继续核对调用链发现，本轮引入的完整 host resume 存在状态交接遗漏：
set_vbus(host) 在 PHY 未初始化时先把 is_host 设成 true；phy_init 随后
按 host 配好寄存器；最后一次 set_vbus(host) 因 is_host 已 true，判断
role_changed=false。保留 controller-sync 的无操作检查于是可能跳过
本该针对真实 device->host 转换完成的 mux 交接。不能把 init 写出的
匹配静态值当成“旧角色已完成交接”的证据。

新增 role_sync_pending，仅记录尚未完成的角色转换，跨 init 保留，
实际同步成功才清除；出错留给现有明确重试路径。同角色、同方向且
没有待交接状态仍使用原无操作检查，不增加轮询、定时器或电源动作。
真实 C 函数回归新增“PHY 关闭时记录 host -> 初始化 -> host”时序、
同步失败保留标记与成功后不再强制交接；两版对象编译通过。
完整候选构建日志 20261112-134108-usb3-pending-role-candidate-59030，
尚未实机验证。PD 采用原主线策略，失败的角色冲突硬复位策略继续关闭。

### #100 最终实机记录：双面和电脑模式通过，带坞重启仍间歇降速

用户授权写入新镜像，最多三次带外供正常重启，每轮正常枚举才继续，
最后直连电脑检查，不额外重启。实际写入 boot_b 前 48 MiB 并回读为
6c356cc9386b21a0f456a7ba33af5030c2dd4e59a769a0352dfde1be53750a54，
202 个模块和 Image 共 204 个清单项通过校验，原 #99 模块目录保留。

首轮 boot_id=43f9ce8c-d33c-44e6-9785-9125f8f08bb8，host/sink、
外供/Full、网线管理正常，但 RTL8153 为 480，没有 SS hub。8.791188秒
TCA 日志明确 role_change=1、result=0、output=0x103，证明待交接标记
修正已执行，却不足以修好这次 SS 枚举。随后稳定读取确认蜂窝 v4/v6 与
热点正常。未继续条件中的剩余两次自动重启。

之后只读发现同一 #100 的新 boot_id=48258594-2764-4803-95e7-038c19e9c5cd，
原方向下两个 0bda:0411 hub 及 0bda:8153 均为 5000。用户明确确认这次
是其手动重启；不能记成 Agent 发起的第二次重启，也不是自动崩溃恢复。
原方向 AON BOOT_MODE=0x41d/flip0，TCA output=0x103；用户将插头翻面后，
BOOT_MODE=0x1d/flip1、output=0x107，两个 SS hub 和网口仍为 5000，
同一 boot_id 未变，host/sink、外供/Full 和蜂窝/热点正常。

最后用户直连电脑，仍同一 boot_id，Type-C=device/sink，UDC configured，
实际 current_speed=high-speed（与本次线材/电脑路径的既有基线相同），
usb0 在 br-lan 上 UP/LOWER_UP，NCM/SSH 工作，蜂窝 IPv4/IPv6、热点正常，
未见新增 Oops/Call trace/Kernel panic/cannot prepare。没有额外重启。
不能把本次电脑路径表述成 USB3 设备模式 5 Gbps 已验收。

证据：pending-role-first-boot.txt、pending-role-first-settled.txt、
pending-role-before-final-orientation-check.txt、pending-role-final-flipped-orientation.txt、
pending-role-final-pc-regression.txt。测试结束只移除本轮临时添加的 eth0
LAN 成员及 unisoc_modem.usb.lan_auto=1，不覆盖其他配置、不重载网络；
回读 ports=usb0、lan_auto 不存在且 UDC 仍 configured。电脑 USB NCM 网卡
的临时地址及专用 /32 路由已移除，DHCP、自动跃点、IPv6 路由发现及原 DNS
已恢复。以太网 5 在用户报告拔线后仍显示 Up；用户随后明确要求“别管”，
因此停止修改该网口，保留其临时静态地址、DNS 和路由保护，未将其记为
已恢复。电脑 Wi-Fi 保持 CUDY_TR3000，默认 IPv4 路由仍经 192.168.10.1。


### F50 无电池实机对照：坞一起断电能到 USB3，单独重启仍降速

用户连接同一只带供电 Realtek 扩展坞，明确授权编译、刷入并测试 F50。
本机是 eMMC、没有电池策略服务，Linux boot_b 为 mmcblk0p38，根分区
mu300root 为 mmcblk0p76。不能套用上一台 U30 Air 的 UFS 节点、boot 镜像
或 Windows 临时网络配置。

先保存本机完整 64 MiB boot 和原模块，核对原 boot 前 48 MiB 摘要；使用
既有构建树，通过受版本管理的 build-release.py run 调用 build.sh 和
build-modules.sh，生成 7.2.8 #101。未改变无电池板的供电策略，未主动改写
用户的 USB 角色配置。使用本机备份生成候选，保留 init、ramdisk 元数据、命令行、持久日志
与 AVB 尾部，仅替换内核及对应模块。四项 USB3 回归、202 个模块和
Image/kernel.config 共 204 项摘要通过；刷入前后 boot 前 48 MiB 回读一致。
Android A、GPT、userdata、NV/IMEI 未改。原模块目录留存供恢复。

| 对照 | 实际网口/SS hub | 角色及方向同步 |
| --- | --- | --- |
| 原 #73 带同一外供坞 | RTL8153 480，无 SS hub | host |
| #101 保持坞供电正常重启 | 480，无 SS hub | host，boot=0x41f，flip0，output=0x103，result=0 |
| 仅拔插 F50 C 口，同方向，坞不断电 | 480，无 SS hub | host，flip0，output=0x103，result=0 |
| 仅翻转 F50 C 口，坞不断电 | 480，无 SS hub | host，boot=0x1b，flip1，output=0x107，result=0 |
| F50 C 口不动，坞完全撤去外供 10 秒后重新供电 | 两个 0bda:0411 hub、RTL8153、读卡器均 5000 | host，flip1，output=0x107，result=0 |
| 上一行成功后，供电/线缆不动，只正常重启 F50 | RTL8153 又回到 480，无 SS hub | host，flip1，output=0x107，result=0 |

全断电成功与随后软件重启失败使用同一 #101 内核、同一插头方向和同一
供电配置。它们的 TCA 同步都成功，不能再把 role_change 处理完成或输出
方向一致当成 SuperSpeed 链路已修复。证据支持“坞保留的供电/链路状态与
重新建链时序有关”，仍不能单方面证明纯粹是坞硬件缺陷或将原因全归于 PHY。
没有采用强制 host/SRC、按秒猜测电脑、循环 PHY 复位或电池策略冒用。
各轮热点、蜂窝 IPv4/IPv6 与网线管理均恢复。

构建任务：20261112-141014-f50-usb3-kernel-trial-59090；镜像与回归任务：
20261112-141157-f50-usb3-image-check-59112。私有实机记录在
work/f50-usb3-20261112 的 baseline.txt、first-boot.txt、
same-orientation-cold-boot.txt、flipped-cold-boot.txt、
dock-and-f50-cold-boot.txt、warm-reboot-after-dock-powercycle.txt。
设备备份、CID 和完整诊断不纳入公开仓库。

最终用户直连电脑，并说明此前手动选择了主机模式；用户通过手机手动改为
设备模式后，PC 出现 UsbNcm Host Device #3，DHCP 地址 192.168.77.200，
F50 为 device、UDC configured/high-speed，usb0 UP/LOWER_UP，NCM/SSH
及蜂窝 v4/v6、热点正常，未出现新增 Oops/Call trace。不能把手动主机模式下
不枚举成电脑网卡记为设备模式缺陷，也不能据此宣称已验证自动角色或电脑
设备模式 5 Gbps。用户保存的 role_auto=0 和设备模式均保留。

电脑全程保留原 Wi-Fi 和默认联网路由，本轮未修改电脑地址、DNS 或路由，
未清理原有 F50 的 lan_auto=1/eth0 LAN 成员。备份及本轮部署回执保留，
当前机器仍运行 #101、连接电脑处于设备模式。最终记录 direct-pc-final.txt。
本轮结论是“无电池 F50 可达 USB3，但该外供坞的跨重启链路问题仍复现”，
不是正式发布或全硬件验收通过。


### F50 下一候选：角色恢复不再依赖电池策略

用户确认继续实现并验证上述方案。参考代码为既有 mu300-kernel 卷中的
zte-u30air 原厂 GPL 树：dwc3-sprd.c 的 IDLE 分支要求核心已挂起，
otg_start_host() 先 usb_phy_vbus_on()、再 pm_runtime_get_sync()，
由恢复回调重新初始化 PHY/核心后才设置 host、启动 xHCI。这是同平台源码
参考，不能冒充已核对本机 F50 固件二进制与它逐字节一致。

原有 host_resume_needed() 直接返回 bc12_enabled，因此无电池 F50 被排除。
候选将其改为查询 PHY 是否已初始化，真实 device/idle 到 host 的转换统一
执行“停止旧核心 → 设置主机 PHY 意图 → 完整恢复 PHY/核心 → 再同步 mux
→ 启动 xHCI”。电池存在与 BC1.2 不决定 USB 核心的生命周期。

同时使用实际初始化状态决定是否先 core_exit，避免 role=0 但核心仍在运行
时重复使能时钟；准备失败已停掉核心后，下一次显式请求主机或设备都先
恢复核心，不允许在时钟关闭时直接启动 gadget。正常设备模式和同主机角色
的方向同步不重复做完整恢复。失败立即返回并保留可重试状态，无定时重置、
无默认 host/SRC、无 VBUS 限流或供电配置变更。

实际 C 回归覆盖：有/无电池、首次 role=0 但核心已开启、核心已关闭时的
主机/设备选择、准备/初始化/mux 失败后重试主机或改设备、同角色及其他
平台隔离。PHY 实际状态查询也覆盖 bc12_enabled 为真/假。四项 USB3、
PD 生命周期/策略清除及六项原有 U30 Air PD 回归全部通过。

6.18.54 的 DWC3/PHY 对象编译通过；7.2.8 完整 Image/配套模块编译通过。
构建任务 20261112-143511-f50-host-lifecycle-kernel-59330；回归任务
20261112-143445-f50-host-lifecycle-tests-59310；本机镜像准备任务
20261112-143717-f50-host-lifecycle-image-59353。仍属硬件候选，尚未证明
外供坞跨重启问题已经解决；保留前一轮失败和冷启动对照记录。


### 完整生命周期候选 #102：顺序已执行，跨重启降速仍未解决

用户确认继续后，本机 boot_b 前 48 MiB 回读为
bd347b097e8b6ebc8704d00a19923defea2a180a0f2b262fd869d9ab6fac4113，
202 个模块及 Image/kernel.config 清单均通过。前一版 #101 boot/模块已另存。
直连电脑启动 #102，device/configured/high-speed、NCM/SSH 正常；启动稳定后
蜂窝 IPv4/IPv6 与热点正常。没有把 netifd 尚未启动时的早期 Object not found
误记成服务持续故障。

用户接回一直供电的原坞后，boot_id=e249bcce-96a4-417e-b895-eaf3fa67ba4a，
RTL8153 仍 480，无 SS hub。13.255397 秒的日志明确记录 combo PHY/core
resumed before host controller，早于 13.256510 秒的 xHCI 启动；TCA 同步
result=0。因此本次实际执行了完整恢复，不能用“代码可能没生效”解释失败。

请求用户对坞和 F50 一起断电作对照后，观察到新 boot_id=
958a03c7-8d1e-46e9-8bf6-d0e868b23224，两个 SS hub 和 RTL8153 均 5000；
截至采样没有收到该物理操作的文字确认，不将它额外记为某种已确认复位原因。
从这个已确认 5000 的状态，保持供电和线缆正常重启 F50，boot_id=
5670d5bf-a1b6-4f7f-ae2f-113edf55a479，RTL8153 再次为 480、没有 SS hub。
13.471182 秒恢复完成，13.472358 秒 xHCI 启动，TCA result=0；网络与热点正常。
本轮停止重复重启，同一方案未解决该组合的跨重启 SuperSpeed 问题。

后续只读核对：F50 没有已注册的 Type-C 端口；DT 存在 sc27xx Type-C 和
I2C6:0x30 PTN38003A 节点但没有驱动绑定。只读访问 PTN 的 0x00、0x04
均返回 I/O error，不能据节点存在断言芯片实际焊装，也不据此盲写重驱动。
尚需本机原厂 Android 的实际角色/链路对照，不能把 U30 Air 原厂结果或
同平台 GPL 源码当成 F50 原厂实测。

证据文件：host-lifecycle-deployment.txt、host-lifecycle-first-pc-boot.txt、
host-lifecycle-pc-settled.txt、host-lifecycle-first-powered-dock.txt、
host-lifecycle-dock-powercycle.txt、host-lifecycle-warm-reboot.txt、
host-lifecycle-failure-hardware-inventory.txt、host-lifecycle-ptn-readonly.txt。


### F50 原厂对照尚未成立，已返回 OpenWrt

用户随后补充确认上一轮对坞完全断电，并授权临时切原厂 Android 对照，
确认无线 ADB/root 已开启。仅写入本机已校验 CRC 的 Android A 32 字节
misc 启动记录并回读校验，执行正常重启；没有修改 Linux 默认启动文件。
但网线管理下 192.168.0.1:5555 未连通，没有取得原厂 uname、数据角色或
USB 枚举结果，不能据此确认原厂是否完成启动或进入 OTG，更不能宣称这台
F50 原厂也已测到 480。

用户指出原厂可能需要额外 CC 触发 OTG。这个具体触发条件尚未核实；
同平台原厂源码确实根据 CC/ID 的 EXTCON_USB_HOST 事件启动 host。
有效速率对照必须先确认两边都处于数据主机且保持同一外部供电条件。
本轮未修改 CC、VBUS 或 PTN38003A 寄存器，不将假设直接变成硬件操作。

用户自行切回后，只读确认 F50 运行 #102，boot_id=
f6932602-bba1-457b-8907-259398d3cda4，默认 Linux、成功启动计数已清零，
实际 host，RTL8153 仍为 480。没有再次切系统或重启。电脑用于本轮原厂
探测的两个临时静态地址和 DNS 已撤回，恢复本轮之前的 DHCP/自动 DNS，
重新取得原租约地址；原 Wi-Fi 与默认联网路径保持不变。保留机器已有的
路由发现/忽略默认路由策略，没有套用上一台 U30 Air 的旧网络备份。


### F50 当前故障的链路和 CC/PD 只读证据

在 #102、host、RTL8153 480 的现有故障现场，xHCI USB3 port02 PORTSC 为
0x000002a0（Speed=0、RxDetect、PP），USB2 port01 为 0x00000e03
（Speed=3、U0、CCS/PP/PED）；USB3 对应端口的 state 为 not attached。
这能将当前故障定位到 SuperSpeed 链路尚未建立，不能将其描述为已连接
USB3 的网卡被限速，也不能直接据此区分本机 PHY 与坞上游端的责任。

对空闲 USB3 根端口通过标准 USBDEVFS_CONTROL 执行一次
SET_FEATURE(BH_PORT_RESET=28)，不操作 USB2 端口、供电或角色。
请求被接受；0.1、0.5、2 秒后仍为 RxDetect，网口仍 USB2 480，网线管理
正常。未加入循环复位服务，不据这次失败修改通用 USB hub 行为。

新增 manual-only usb3-cc-snapshot 诊断模块，通过已有 PMIC regmap，仅访问
原厂驱动定义的 Type-C/PD 配置和只读状态寄存器；不读 FIFO/中断清除寄存器，
不写寄存器、不改变时钟或角色。编译任务
20261112-151647-f50-usb3-cc-readonly-59530；读取结束已经卸载，未增加常驻服务。

故障时实际读取：TYPEC_EN=0x1e03、MODE=0x20d4、PD_CFG=0、
STATUS=0x60a2、DBG1=0x10f；PD_HEAD_CFG=0、PD_CFG0=0x10b、
PD_CFG1=0x130b、PD_STS0/1=0x2000。按同平台寄存器定义解码：
Type-C 为 attached sink（state=2），VBUS_OK=1，USB20_ONLY 位未置位，
Type-C 的 PD_SUPPORT 及协议控制器 CTL_EN 未启用。PD 数据包头配置处于
未启用状态，不能把其 0 值当成已协商的数据角色。

同时源码确认 F50/V50 的网页适配器直接写 DWC3 role，UMP9620 TCPM 驱动
当前被电池策略条件排除。这是已确认的路径缺口；它是否导致本坞 SS 检测
失败尚未证实。sink 电源角色本身不是错误，不能将其等同于数据 device，
也不能用强制 source/Rp 或关闭取电来尝试恢复无电池 F50。
后续候选若扩展无电池 Type-C/PD 支持，须保持取电路径、单独协商数据角色，
同步网页显式模式选择，并验证电脑设备模式、带外供 host 及两面跨重启行为；
不能只删除电池判断并复用现有双电源角色/充电器控制。

私有记录：current-ss-port-state.txt、single-ss-warm-reset-result.txt、
single-ss-warm-reset-settled.txt、cc-pd-readonly-failure-state.txt。

### 撤回 F50 PD 试验方向，保留原厂禁用节点行为

此前将 F50 未启用 PD 称为“路径缺口”缺少本机原厂行为依据，该结论撤回。
用户明确指出 F50 原厂不使用该 PD 节点；不能把 U30 Air 的电池/PD 方案直接
迁入 F50，也不能以扩展坞降速为由启用原厂禁用的控制器。

试验曾提交 b66768e5/e5350612：新增显式 sink-only PD 配置和网页后端，
仅写 Linux B boot 前 48 MiB、配套模块和三份运行时文件，全部保留备份。
#103 因强制内核命令行未接到启用参数，实际配置为 N，仍走原控制路径。
修正参数后部署 #104，带坞重启未恢复网口，条件性第二次重启没有执行。
用户改用数据线直连电脑后，只读确认 #104、配置 Y、实际 USB device，
NCM configured/high-speed，蜂窝 IPv4/IPv6 正常。

关键证据是本机 PD 节点实际 `status=disabled`，没有对应 platform device、
Type-C 端口或 TCPM 实例。新增后端因此将原有 host 请求记为 error；
这属于试验适配器对不存在端口的依赖，不能称为 PD 协商失败或原厂缺陷。
没有启用禁用节点、操作 PD 硬件或回退到强制供电路径。

源码撤回以上两笔 PD 试验，保留此前的 USB3 方向、PHY/core 恢复等独立修正。
机内恢复方案为本机已验证 #102 boot 前 48 MiB、原匹配模块及原 device-usb，
移除本轮新增的两个运行时文件，保留当前 UCI；这项实际恢复/重启另按授权执行。
恢复仅消除本轮 PD 依赖造成的角色切换障碍，不宣称解决此前的 480 Mbps 问题。

后续围绕 F50 原有供电和 USB 数据角色控制，调查 PHY/xHCI/扩展坞 SuperSpeed
链路状态；有效原厂对照仍需先确认真实 host 条件，不将无管理连接当作测速结果。
私有证据：pd-sink-forced-deployment.txt、pd-sink-forced-direct-pc.txt、
pd-sink-forced-probe-failure.txt、pd-sink-forced-dt-device.txt。

### 撤回后的本机恢复已完成验证

用户明确授权恢复后，校验设备 CID、Linux B 分区映射及当前试验镜像指纹，
仅恢复本机 #102 boot 前 48 MiB、匹配模块及原 device-usb，移除两个新增 PD
运行时文件。保留 #104 boot/模块/运行时备份及模块目录，未改 Android、
GPT、userdata、NV/IMEI 或 misc。恢复前后的 unisoc_modem 配置校验和相同。

正常重启保留用户 role_auto=1，直连电脑的 gadget 随主机策略断开；
用户接回原带外供坞后，只续租电脑该以太网口的 DHCP，恢复原管理租约，
未修改电脑 Wi-Fi、路由优先级或保存的网卡设置。
只读核对本机实际运行 #102，boot_id 为 c33fd803-982e-4e82-bf8f-df01095286c3，
实际 host；内核有效命令行无 PD 试验参数，新增文件已不存在。
boot 前 48 MiB 哈希与原件一致，运行目录的 202 个模块逐项通过原清单校验。
热点及蜂窝 IPv4、IPv6 地址和 IPv6 默认路由均正常。

本坞 RTL8153 与 USB2 hub 仍为 480，没有 SuperSpeed hub；这与原试验前
故障一致，恢复成功不代表原有带坞启动降速问题已解决。
证据：pd-trial-recovery.txt、pd-trial-recovery-settled.txt、
pd-trial-recovery-live-modules.txt、pd-trial-recovery-verification.json。

### F50 后续候选：完成初始化后退休旧角色交接标记

在恢复的 #102 现场只读确认：仍为 host，DWC3/xHCI 为 active，SS 根端口
仍停在 RxDetect；DWC3 GUSB3PIPECTL(0)=0x01001102，DT 保留原厂的
dis_u2_susphy/dis_u3_susphy 属性。不能将 SS 根总线的 10000 能力值当成
RTL8153 的实际速率。本轮没有更改电脑 Wi-Fi 或设备配置。

代码审查确认了一个独立的初始化交接问题：set_vbus(host) 在完整 PHY
恢复前保留 role_sync_pending；随后 phy_init 已按 host 重新配置并交接
TCA，但 pending 标记没有退休。恢复后的第二次 set_vbus 因此无条件再次
disable/rearm，将刚建立的 controller-sync 改成 system mode。现有 #102
日志的 gcfg=0x1->0x0 与这条路径一致。原厂 U30 Air 实机模块和现有
同平台 GPL 代码均在初始化中保留 controller-sync；本机缓存的 5.4 USB
模块也采用该方式，但尚未确认该缓存与 F50 当前 Android A 的固件版本
一致，不能冒充已完成 F50 原厂实测。

候选只在 SRAM 初始化完成且 TCA 收到 ACK、无 timeout event、无 regmap
错误后，校验当前角色、输入方向和实际输出，再清除旧 pending 标记。
匹配时不写 TCA，保留 controller-sync；不匹配时沿既有受控同步流程修正；
校验失败保留 pending/error。普通运行中的角色变化和正反插仍走原交接流程。
不改变 F50 原厂禁用的 PD 节点、供电策略、默认角色或 USB 最高速率。

离线验证：30 项 USB 管理/U30 Air PD 测试及实际 C helper 回归通过；
新增覆盖两种角色、两个方向、旧 pending/error、SRAM/ACK 未完成、读失败、
物理输出不匹配及下一次重复 set_vbus 不再写 TCA。6.18 USB 对象编译通过，
完整 7.2 Image 与 202 个配套模块编译及清单校验通过；不宣称全量 6.18
镜像已经重新构建。

构建任务：20261112-175357-f50-fresh-tca-regression-16412、
20261112-175433-f50-fresh-tca-kernel-16439、
20261112-175619-f50-fresh-tca-image-16463。
用本机 #102 备份生成的候选保留 init、ramdisk 元数据、安装参数、命令行、
48 MiB 后持久日志与末尾 4 KiB；内核/ramdisk 长度及 AVB 位置均未变化。
源码中的重复交接得到修正，能否解决本坞跨重启 480 仍须实机验证。

本轮实机结果：用户授权后写入候选 #105 的 boot_b 前 48 MiB 和配套模块，
完整备份 #102，读回哈希及全部模块清单通过；配置及角色适配器哈希不变。
首次带外供坞启动后，boot_id=822cd6c3-2cc7-4247-a853-a5ba6c488463，
host、热点及蜂窝 IPv4/IPv6 正常。管理网络在系统启动过程中恢复，未改电脑
网络配置。临时只读快照确认 GCFG=1、TCPC=3、SYSMODE=3、TC_STATUS=0x103；
重复交接确实已消除。RTL8153 仍为 480，SS PORTSC 仍为 RxDetect，故不将
此改动称为带坞启动降速的完整修复。按授权约定没有执行第二次重启。
快照模块读完立即卸载，未留下后台采样或角色恢复任务。

为避免继续引用错误缓存，又只读提取了本机 Android A 的实际 kernel 和
vendor_boot_a ramdisk 中 USB 模块。版本为
5.4.254-android12-9-g9c6342244991，2025-01-20；与机内缓存
5.4.254-gb50db5b6224c 不同。没有启动/修改 Android 或装载其模块。
该实际版本确认 GCFG 保持 controller-sync，且 dwc3-sprd 的
limit_dwc3_max_speed 在 PTN38003A 失败时把 DCFG.speed 改为 4（SuperSpeed）。
当前主线 DCFG.speed 为 5（SuperSpeedPlus）。这是一处已确认差异，尚未证明
与 host SS 未连接存在因果关系；DCFG 本身是 gadget 配置，不能直接把它
描述为已经证实的 xHCI 限速根因。

新增 manual-only usb3-stock-speed 对照工具：默认只读，必须显式 apply=1
且确认当前为 active host 才能临时改上述三个 speed 位；与 DWC3 role worker
共用互斥锁并持有 PM 引用，不做 USB reset/角色/供电操作。卸载时仅在仍为
同一 host、字段仍为本工具值时恢复原 speed 位，避免覆盖后续操作者状态。
不是开机服务，不纳入正式固件模块或默认构建。按当前 #105 编译通过，
任务 20261112-180421-f50-stock-speed-probe-16640；实际写入对照另需授权。

用户授权后的 15 秒 DCFG 对照已完成：保持 #105、同一 boot_id、host 和
原坞连接，将 DCFG 从 0x00080805 临时改为 0x00080804；三次有界采样中
RTL8153 和两个 5411 hub 全部仍为 480，没有 SS hub。之后 DCFG 已恢复
0x00080805，测试模块已卸载；配置和角色适配器摘要不变，热点、蜂窝
IPv4/IPv6 及 IPv6 默认路由正常。未重启、复位 USB、切角色或修改供电。
结论限于“单独运行时修改 gadget 速率字段不能恢复现有 host SS 链路”，
不能据此断言初始化期间的所有速率设置均无关，也不将此字段修改加入
正式固件。证据：stock-speed-trial-result.txt、stock-speed-trial-after.txt。

用户确认另有一款带供电 USB3 设备。下一步在相同内核、角色和电脑联网
路径下换坞对照；需要保留网线管理通道，另一款若没有网口则先不更换。
读取新设备首次枚举结果后，只有在用户另外授权且首次 5 Gbps、管理正常
时才做一次正常重启，比较其是否也会降速。不把换坞结果预先称为修复。

用户换上另一款带供电、带网口设备并授权一次条件性正常重启后，实机
首次上电（boot_id=03ecf857-3e8b-45ac-baf2-c04b9b28060a）识别到
0bda:8153 直接接在 2-1、5000 Mbps，没有原坞的两级 0bda:5411 hub。
GCTL=0x00111006、GUSB3PIPECTL(0)=0x01001102、DCFG=0x00080805；
TCA GCFG=1、TCPC/SYSMODE=7、TC_STATUS=0x107，SS 端口为 U0/Speed=4。
在供电和连接不动时执行了已授权的**一次**正常重启：
boot_id=607b331f-7041-4ec1-9abb-28c17076d593，仍为 5000 Mbps。
16 秒时热点/蜂窝尚未起来，33 秒时均已正常，IPv6 默认路由正常。
配置及角色适配器摘要仍未变；没有额外刷写、重启或配置调整。

另一个差异是新设备下 TYPEC_STATUS=0、DBG1=0x100，原坞失败现场则为
0x60a2/0x10f。CC 和 hub 拓扑同时改变，因此这个结果只能证明 #105 的
SuperSpeed host 路径及带此设备的正常重启可工作，不能单独归因于 5411
芯片或据此认定原坞损坏。只读 CC 快照按当前内核重新编译、读取后卸载，
没有修改 CC/PD 寄存器。
证据：alternate-powered-first-boot.txt、alternate-powered-before-warm-reboot.txt、
alternate-powered-after-one-reboot.txt、alternate-powered-settled-cc-state.txt。

再次核对上游 [issue #54](https://github.com/dikeckaan/mu300-linux/issues/54)
及 [PR #341](https://github.com/dikeckaan/mu300-linux/pull/341)：PR 自述尚未
运行于实机，其 redriver 推断及轮询方案不能替代当前 F50 的硬件证据；
没有据此写入未识别的 I2C 芯片、增加轮询或宣称已有通用修复。

### F50：插头方向的因果对照与事件恢复

另一款直连 RTL8153 翻面后，#105 同样降为 480，CC 状态仍为 0，
TCA 输出仍为 0x107；AON_BOOT_MODE=0x1b。当前连接不能仅凭这个
启动时留下的方向位决定 SS 通道。#106 新增 root 可写的暂存方向参数，
设置参数本身不写硬件；仍由现有 DWC3 host 角色流程停控制器、同步 TCA
并重新枚举，不改变 CC、PD、VBUS 或用户设置。

已授权实机 A/B/A：保持同一启动、插头、供电和网线，默认方向为 480，
暂存方向 0 后经原 host 流程变为 5000，恢复默认后再次为 480。
这证明该次故障与方向选择有关。不是把 root hub 的 10000 能力当成
实际速率，成功判据是同一物理端口 RTL8153 的 speed=5000。
证据：lane-ab-result.txt；#106 与 202 个配套模块已校验部署。

用户授权热部署的事件恢复也通过一次实测：netdev 事件发现支持 SS 的
网卡只运行在 480，有限尝试两个方向，确认 5000 后保留当前 host 会话的
RAM 选择并退出；没有常驻轮询。切到设备模式会释放自己拥有的选择。
新角色请求优先，不覆盖用户手动参数或平台自定义适配器，不写 UCI。
缓存 BOS 解析和角色并发等 35 项离线测试通过；热点及蜂窝 IPv4/IPv6
正常，配置摘要不变。该结果目前只覆盖直连 RTL8153 的一次恢复。

换回原扩展坞后仍为 480，未执行有条件的重启。只读确认它额外的
0bda:0306 实际是 USB3 读卡器（接口 08/06/50、usb-storage），不是
Billboard；两个 removable 盘容量均为 0，无介质或挂载。现有外设保护
因此跳过恢复，BOS 能力检查本身通过。新增源码仅允许所有接口为存储、
所有已注册盘均可移除且容量 0、无 holder 的空读卡器；未知状态、有介质
或其他接口仍拒绝自动重新枚举，每次操作前重新检查。
此扩展的 36 项离线测试通过（dash、bash、BusyBox ash 及实际 ucode），
任务 20261112-184803-f50-empty-reader-lane-tests-59231。

用户另行授权后已热更新单份 device-usb，保留旧脚本归档及摘要。
在原坞同一 boot_id=03858309-f2c0-439c-9f11-d3575d3ac922 的现场，
两个方向均完成有限尝试。内核日志确认 TCA 先变为 0x107，再恢复 0x103，
各次 xHCI 和 USB2 hub/网卡确实重新枚举，但未出现 SuperSpeed hub。
最终 RTL8153 仍为 480，参数恢复 -1，RAM 留下 attempted 标记，工作进程
退出、角色锁已释放；热点、蜂窝 IPv4/IPv6 正常，用户配置摘要不变。
因未满足首次 5000 的条件，没有执行授权中的正常重启。

空读卡器阻断已解除，但不能把另一款直连 RTL8153 的方向恢复成功
推广为此两级 hub 扩展坞已修复。此坞的 SS 链路未建立原因仍未确认，
也没有证据据此启用 F50 的 PD 或改变其供电策略。
实机证据：empty-reader-original-dock-result.txt、
empty-reader-original-dock-link-log.txt。

### 同一方向冷启动成功、持续供电重启失败的严格对照

用户冷上电后，#106 的原坞两个 0411 SS hub 和 RTL8153 均为 5000。
参数 -1、没有方向恢复标记或事件，属于正常首次枚举成功。
保持插头、供电和网线不变，只正常重启一次后，同一 #106 降为 480。
两次 AON_BOOT_MODE 都为 0x1b，TCA GCFG/TCPC/SYSMODE/STATUS 同为
1/7/7/0x107，主要 DWC3 配置也相同；PHY PSTATE 从 0x333300 变为
0x333322，SS 根端口从 U0 变为 RxDetect。这一轮排除了插头方向变量，
但状态差异本身仍不是完整的根因解释。

用户已持续授权本项 F50 修复的 Linux boot、匹配模块及验证重启，后续不再
逐次确认；不扩大到 Android、GPT、userdata、NV/IMEI 或 CC/PD/供电策略。
用户要求不再以无法识别此坞的 F50 原厂系统作运行时对照，遵从该限制。

### 撤回 #107 子级 shutdown 候选

最初检查到 DWC3 子驱动没有 shutdown，曾推测完整 PHY 退出缺失。
该推测遗漏父级 dwc3-of-simple 的 shutdown：它已有
of_platform_depopulate → 子设备 remove → dwc3_core_remove 的完整清理。
新增子级 shutdown 在 #107 重启时先清理并清空 drvdata，父级随后再次
remove，引发 NULL 空指针和 panic。pstore 栈直接确认了因果关系。
因此不能再把“没有完整 shutdown 清理”列为本机已确认缺陷。

已撤掉候选，安装脚本会清除已准备树中该试验的 include/驱动表项，
保留原父级的唯一清理入口。原 #106 boot 前 48 MiB 和全部匹配模块已
恢复并回读核验，配置保持一致。为避免退出 #107 时再次重复清理，恢复
时先正常解绑该子设备一次，再重启；已回到 #106，未新增关机异常。
失败 boot、pstore 和模块归档保留，不混入正式发布。

恢复后还做了一次标准 USB3 根端口 BH_PORT_RESET 对照：只允许未连接的
RxDetect SS 根端口，未触及 USB2 网卡、供电或 CC；前后状态同为 0x02a0，
未恢复 SS。该工具是手动诊断，不接入开机或周期恢复。

### 读取明确保存的主机自启动设置，提前选择数据角色

另一个已确认的启动顺序是：role_auto=1 时，initramfs 仍在约 2.8 秒绑定
NCM gadget，直到约 13 秒根文件系统服务才切到 host。新候选仅在 F50/V50、
正常启动、实际选中的根已挂载、读取到用户明确保存 role_auto=1 且没有
自定义角色适配器时，通过原角色接口提前请求 host，并跳过此次 gadget
绑定。关闭主机自启动、其他机型、安装准备和救援路径保留原有行为。
不把全机型默认角色改成 host，不修改 PD 或供电。

36 项原有 USB/PD 回归及 2 项新增实际 shell 用例通过。候选只对已安装
ramdisk 的五份 shell init 作受限修改，保留其存储和槽位逻辑；#106 内核、
现有模块、启动参数、持久日志区和 AVB 位置/尾部保持一致。已刷入并校验，
随后完成了下列实机对照。

### 提前主机启动及 RTL8153 固件的实机结果

同一 #106 内核和全部模块保持不变，只修改已安装 initramfs 的五份 init。
首次从已失败的坞状态启动仍为 480；用户将整只坞断电后，同方向冷启动为
5000，随后第一次持续供电重启仍为 5000。第二次重启时用户报告 USB 为
5000、但网卡链路未连接，无法从有线管理读取该次现场。拔插网线无效，
用户重新插拔 F50 的 C 线后恢复。此轮不能算完整成功，也不是 480 复发的
已确认记录。

另确认 rootfs 缺少 r8152 请求的 `rtl_nic/rtl8153b-2.fw`，故在公共 rootfs
构建中加入 OpenWrt 官方 `r8152-firmware` 依赖，不绑定某一固件文件摘要。
实机按正常仓库校验安装，重启后驱动回报实际加载的固件版本，缺失警告消失。
安装过程中还暴露了该机原有 dnsmasq 与 dnsmasq-full 同时登记造成的文件
所有权冲突；只重装其已选的 dnsmasq-full 恢复文件，DHCP 设置语义不变。
因此不能仅凭之后恢复正常，就断言前次网卡链路异常唯一由缺固件导致。

| 对照 | 实际外设速率及链路 |
| --- | --- |
| 原方向连续三次正常重启，坞持续供电 | 两级 SS hub、RTL8153、空读卡器均 5000；以太网 carrier=1、管理恢复 |
| C 插头翻面，其他线和供电不动 | 相同外设均 5000、carrier=1 |
| 翻面后一次正常重启 | 相同外设均 5000、carrier=1 |
| 局域网 HTTP 双向 64 MiB 数据测试 | F50 到电脑约 904 Mbps，电脑到 F50 约 780 Mbps |

以上通过记录均未依赖自动方向重试：参数为 -1，无会话恢复标记。
保存的 USB 设置摘要一致；电脑 Wi-Fi 保持原网络，没有切换到设备热点。
这些结果覆盖当前 F50 和原扩展坞，不代表所有扩展坞或 V50 已经实测。

### 主机优先启动仍保留切回电脑的 gadget

提前 host 的首版完全跳过 gadget 创建，检查发现这样会使后续手动切回
device 时没有 NCM 配置。最终路径改为复用原 descriptor 创建函数，但
host 启动时不绑定 UDC，并将 configfs 挂载保留到根文件系统。
用户请求 device 后，由 UDC add 事件或同一次角色操作的就绪检查绑定已有
配置；已绑定时不反复重绑，新 host 请求优先，RNDIS 沿用现有接口命名。
没有常驻轮询、延迟切角色或新建另一套 USB descriptor。

原 USB/PD 的 36 项回归及提前主机/设备交接的 6 项测试通过。新镜像仍只
修改五份 init，现有内核、111 个 ramdisk 模块项、存储/槽位逻辑、AVB 尾部
及持久日志区逐项保持一致。UDC 事件脚本加入 APK 和两条本地组包入口的
清单及可执行权限设置，避免仅热修机器而未进入后续构建。
新路径首次带坞启动及随后一次持续供电正常重启均为 5000、carrier=1，
已确认 configfs 中配置存在但 UDC 未绑定。用户随后直连电脑并在网页选择
device，日志确认 UDC 事件绑定已有配置；Windows NCM 取得 192.168.77.200，
该地址上的 SSH 实际连通，usb0 已加入 br-lan，UDC 为 configured。
本次电脑链路实际为 high-speed，不将其误报为设备模式 5 Gbps 测试。
热点及蜂窝均正常。用户此时已将主机自启动选项设为 0，按其新选择保留，
没有为测试结束而擅自恢复先前的 1。

### U30 Air 部署后续公共修复（2026-11-13）

用户要求把后续修复部署到 U30 Air。只读核对为同一台 UFS 1 TiB 机器、
Samsung KLUGGARHGB-B0E1、4Kn，Linux boot_b 实际为 sda38；没有沿用
F50 的 eMMC 节点或整份启动镜像。原 #100 boot 和模块、配置均已备份。

使用当前受版本管理的构建入口重新编译 7.2.8 #108 及全部 202 个模块，
通过 USB 角色、TCA、host-valid、首次 data-valid、PD reset/恢复回归。
用本机 boot 生成候选，只替换内核及原有 26 个主线 ramdisk 模块项；四份
init、UFS/槽位参数、其他 cpio 内容、AVB 尾部和持久日志区域逐项保持不变。
F50/V50 专用的提前主机和方向恢复用户态逻辑没有移植到 U30 Air。

从正常 OpenWrt 仓库安装 r8152-firmware，模拟安装与实际事务均只涉及该包，
dnsmasq 文件及 DHCP 配置摘要保持一致。只写 Linux boot_b 前 48 MiB 并更新
匹配模块，回读核验后正常重启；Android、GPT、userdata、NV/IMEI 未写入。

实际运行 #108，PC 经 NCM 地址 192.168.77.200 连通 SSH，UDC configured、
当前电脑链路 high-speed；Type-C 为 device/sink，电池报告 Charging，
热点和蜂窝均 up，202 个模块校验通过。USB、network、wireless 配置摘要
与部署前完全一致，默认 Linux 启动保持原样。

用户明确选择先不测试扩展坞，未再执行切角色、带坞重启或更换供电试验。
因此本节只确认部署及电脑侧回归，不把 F50 的成功推广为 U30 Air 的
带外供跨重启降速已经修复。


### 有电池机型的独立数据角色覆盖（2026-11-13）

当前 U30 Air 接外供扩展坞，实际为 host/sink、USB3 5000 Mbps。手动请求
原生 PD DR_SWAP 时，对端明确回复 NOT_SUPPORTED；请求没有被网页忽略，
也不是已经切换后又被自动策略覆盖。用户因此要求增加独立强制数据角色。

实现只覆盖本仓库以 charger_policy_service 识别的有电池平台（U30 Air）。
F50/V50 保持原 host/device 路径和选项，不新增 PD 节点或自动选项。

- 网页提供“自动（跟随 Type-C）／强制主机／强制设备”，实际角色单独显示。
- TCPM 驱动内的 usb_role_override 接口串行处理用户策略和 Type-C 事件，
  沿用现有 usb_role_switch owner；没有第二个异步控制器写入者。
- 强制只控制 USB 数据角色。CC、PD 电源协商、充电电流及 OTG 升压仍由
  原生 Type-C 电源路径管理；不会因为强制 host 而绕过受电端保护开启 boost。
- 自动模式使用最近一次 Type-C 协商的数据角色，而非强制前的旧快照。
  对端仍是固定 USB 设备时，强制本端 device 不能让双方建立数据连接。
- 保留原“开机应用主机”设置，电池平台文案为“开机应用强制主机模式”。
  未开启时正常启动回自动；不增加设备模式常驻启动策略。
- 设备模式复用原 gadget 配置和 UDC add 事件进行绑定；没有新轮询服务。
  旧内核缺少对应接口时明确报错，不悄悄回退到 PD DR_SWAP 或 I2C 强制供电。

内核控制器逻辑的并发事件、失败回滚、关机测试通过；两条内核（6.18.54、
7.2.8）的实际驱动对象编译通过，7.2.8 全部 Image／202 模块已编译配套。
36 项既有 USB/PD/方向测试与 11 项自动／强制、事件绑定、开机策略回归通过。
网页中英土三种语言、桌面／移动端共六组检查通过。以上为离线验证；
具体实机部署与验收结果另记，不能视为 F50/V50 硬件试验。


本轮已部署到同一台 UFS U30 Air，运行 7.2.8 #109；部署前的 boot、模块、
配置和网页文件均留有本机备份。boot 只写 Linux B 前 48 MiB，202 个配套
模块逐一校验，四份 init、26 项 ramdisk 模块替换及 AVB／日志区保留已审核。

| 当前外供扩展坞上的检查 | 结果 |
| --- | --- |
| 正常启动 | 覆盖策略 auto，协商 host/sink，有线管理恢复 |
| 强制主机 | 策略 host，实际控制器 host |
| 强制设备 | 策略 device，实际控制器 device，原 NCM gadget 成功绑定 UDC |
| 返回自动 | 策略 auto，按最新协商结果恢复 host，网卡 carrier=1、以太网 1000 Mbps |
| 全程电源状态 | Type-C 仍为 sink、dual，充电器及 FGU 均报告 Charging |
| 原设置及蜂窝 | USB／network／wireless 摘要逐项相同，蜂窝 IPv4／IPv6 正常 up |

强制 device 时固定设备型扩展坞不能提供 NCM 对端，UDC 为 not attached，
这是角色不匹配的预期结果；本轮没有把它冒充直连电脑 NCM 验收。
本次带原坞启动及切角色后，USB 网卡为 480 Mbps；它仍属于此前未解决的
带坞间歇降速现象，不能宣称这次数据覆盖功能已经解决扩展坞速率问题。
