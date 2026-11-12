# UMS9620 USB3 角色与方向同步方案

状态：方向/角色同步可在插拔后双面达到 5000 Mbps，带坞启动仍会降至480。
第八版 TCA 数据门控试验已因失败撤回，当前设备运行 #96。
原坞撤外供后两次启动均 5000；恢复外供后的新候选两次启动仍 480。
电池 self-powered 声明已生效，但没有解决带外供启动降速；保留失败现场。
本文件不代表 USB3 问题已经修复。
范围：MU300/F50/U30 Air 的 UMS9620 主线内核适配；不直接套用 Pro/E5。

## 目标

在支持 USB3 的主机、线材与扩展坞组合上，修复角色切换／插拔后因为 PHY
状态过时而退回 USB2 的问题。保留电脑 USB 网络、OTG 网口、外部供电扩展坞、
自动与用户手选角色。USB2 设备或 USB2 线本身只能跑 480 Mbit/s，不能承诺
所有组合一律 5 Gbit/s，也不能仅凭 root hub 的 10000M 判断链路成功。

## 当前源码证据

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
