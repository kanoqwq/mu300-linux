# MU300 / Pro 蓝牙网页和数据网关

页面位于「服务 → Bluetooth」，使用 BlueZ 5 的 D-Bus API；BLE 和经典蓝牙
由同一个后台进程管理。网页状态使用登录会话校验的 SSE，后台依赖 BlueZ 的
设备、配对和特征值事件，不在空闲时反复运行 bluetoothctl。

## 功能和运行边界

- 蓝牙默认开机关闭，只有「开机开启蓝牙」会触发启动时上电；已保存的网关
  配置不会绕过该选项。手动开启只对本次开机有效，单独重启网页后台不会
  关闭手动开启的蓝牙。手动关闭会暂停网关扫描、重连、转发和代理，保留配置。
- 本机名称旁的「保存蓝牙设置」独立保存名称、控制器和开机选项，不校验
  尚未填写完整的网关表单。蓝牙关闭或控制器尚未注册时也可保存名称；下次
  开启时应用。重复保存会重试此前未成功应用的名称。
- 手动扫描持续 30 秒，只结束本进程拥有的扫描会话。网关默认关闭，启用后
  才持续扫描或按设定的扫描／暂停窗口工作；可选 BLE、经典或双模。
- 支持配对确认、PIN / Passkey、连接、断开、信任、移除。允许发现使用
  BlueZ 的 120 秒计时器，需要用户在网页确认配对，不自动接受陌生设备。
- GATT 通过当前设备的实际服务和特征查询 UUID，支持读取、写入和通知。
  可配置数值解码，未知厂商私有协议以原始数据转发，不冒称已支持专有传感器。
- 扫描列表每台设备可展开「广播数据」，查看厂商 ID（十六进制／十进制、
  已知厂商名称）、厂商／服务数据原始十六进制和字节数、设备报告的服务 UUID、
  地址类型、RSSI、TxPower、Appearance、Flags 及控制器提供的其他 AD 字段。
  不将未提供的字段填成 0；缓存值与本次后台启动后收到更新的时间分开显示。
  这不是完整空中报文，也不反推 AD 原始顺序。展开／复制／导出均不启动扫描、
  不连接或配对；保留展开和未变化数据节点，RSSI 更新不会重建已选中的数据。
  厂商／服务数据可复制为广播实验页支持的 `ID=HEX` 格式，HTTP 下提供可选中
  的文本框，HTTPS 可使用剪贴板。JSON 导出包含已报告字段。
- 经典 SPP 使用 BlueZ 的 SDP 查询和 RFCOMM 工具，不自行实现蓝牙协议栈。
  网页可发送十六进制数据、显示接收事件；可选 TCP 桥接指定本机或 LAN IP。
  TCP 不提供额外认证，也不添加防火墙规则，仅应绑定可信 LAN；端口 0 不监听。
- MQTT 使用长连接、TLS 默认系统证书校验。HTTP webhook 使用 POST JSON，
  可配置 Bearer 令牌，不跟随重定向转送凭据。两路转发使用独立有界队列，
  任一路故障不阻塞另一条；队满丢弃最旧数据，并显示丢弃和失败计数。
- 自动解析 BTHome v2（含 AES-CCM 加密）、Theengs Decoder 2.5.0 支持的
  广播设备。提供温湿度、电量、门磁、运动、按钮等测量或事件实体；不支持的
  私有格式继续保留原始数据，不将“扫描到／已配对”当成型号已适配。
- Home Assistant MQTT discovery 支持 sensor、binary_sensor、event 和显式
  配置的 button／switch／number；包含设备名称、房间、型号、在线状态。
  Node-RED 可直接接 MQTT in / HTTP in。HA 重启时重新发布发现资料和当前值，
  不重放旧按钮事件；MQTT 遗嘱显示网关离线。
- MQTT 双向控制只允许已配置的动作：GATT 读写、串口发送、连接和断开。
  不接收任意系统命令或 AT 命令。保留的 MQTT 控制消息不执行；JSON 请求 ID
  在当前进程的有界缓存内去重。写入被受理不代表设备状态已改变；只有可读
  特征的回读才发布控制状态，串口及仅写特征显示“已发送，尚未确认”。
- 设备目录支持名称、房间、厂商、型号、BTHome 密钥、自动重连和离线阈值。
  最近广播与最近测量分别计时。BTHome 事件设备不按无广播时间判离线。
  BLE 重新连接后恢复订阅；SPP 在明确启用时退避重连。网页主动断开会暂停
  对该设备的本轮重连，直到重新连接、重新应用配置或服务重启。
- 自定义模板和完整配置均可导入导出；模板仅包含经过校验的数据，不运行脚本。
  配置备份含密钥，需要写权限；不复制控制器地址、固件、BlueZ 配对资料。
- 「BLE 广播实验」支持保存 32 条 stub 定义，包含实验标签、独立广播名称、
  服务 UUID、厂商数据和服务数据，以及 20–10240 ms 的间隔和 0–65535 秒运行
  时长。默认 1000 ms、60 秒；0 秒为手动停止。支持新建、复制、编辑、删除、
  单条启停及停止全部实验广播；编辑／删除运行中的定义前必须先停止。
  使用现有 D-Bus 连接导出 `LEAdvertisement1`，类型固定为 `broadcast`，
  不注册 GATT 服务或实际功能。广播内容按传统 31 字节校验，名称按 29 个
  UTF-8 字节校验。BlueZ 的硬件实例上限决定能同时启动多少条，控制器管理
  地址，多条定义不等于多个独立 MAC。不会取消其他应用的广播。
  该功能默认不运行，保存不自动上电或启动；关蓝牙、切控制器或重启后台均
  结束实验会话。超时由 BlueZ 处理，状态由 Release／D-Bus 信号驱动。定义
  存放 `advertisements.json`，跟随现有配置备份／升级保留，恢复不自动续播。
- 蓝牙关闭、网关关闭是明确用户选择，不通过重新加载 Wi-Fi、重置整个 WCN
  或修改省电策略恢复失败。未知初始化错误会直接返回网页。

## 配置与接口

广播定义接口：`advertising_list` 为只读；`advertising_save`、
`advertising_delete`、`advertising_start`、`advertising_stop` 要求写权限。
它们只处理实验广播，不向设备发送 AT 指令，也不改配对和扫描策略。
广播定义采用 BlueZ 5.83 的
[LEAdvertisement1](https://github.com/bluez/bluez/blob/5.83/doc/org.bluez.LEAdvertisement.rst)
和 [LEAdvertisingManager1](https://github.com/bluez/bluez/blob/5.83/doc/org.bluez.LEAdvertisingManager.rst)
接口。广播名与本机 Alias 独立。注册超时属于结果待确认，保留停止入口，
不能把可能仍在运行的广播当作已清理。

2026-11-17 在 F50、Linux 7.2.8、BlueZ 5.83 上核对到 16 个可用实例：
两条实验定义同时注册、3／5 秒超时 Release、单条停止和删除均通过；
已清理临时定义，用户配置、蓝牙名称和电源状态未变。这证明本机控制器的
注册与释放流程，不替代独立手机接收验证，也不代表 Pro 硬件已经实测。

配置和密钥存放 `/etc/unisoc-bluetooth/`，目录 0700，JSON 文件 0600；状态
接口不返回密码和令牌。留空保留旧密钥，清除需要明确勾选。
BlueZ 配对资料由发行版存放在 `/etc/bluetooth/keys/`。
普通整包更新和 OTA 差分均收录服务、页面、权限及依赖；整包保留上述配置目录。

MQTT 事件主题是 `<前缀>/<无冒号设备地址>/events`，默认前缀
`unisoc/bluetooth`。HTTP 收到相同事件，例如：

```json
{"seq":12,"time":1794900000.0,"type":"gatt","data":{"address":"02:11:22:33:44:55","service":"0000180f-0000-1000-8000-00805f9b34fb","characteristic":"00002a19-0000-1000-8000-00805f9b34fb","hex":"64"}}
```

`device` 是发现、RSSI、广播资料和连接状态事件，`gatt` 是特征值，
`serial` 的 `data.hex` 是原始串口数据。事件序号只在一次服务运行中递增，
服务重启后 `event_id` 改变，网页会重置游标，不漏掉新事件。

LuCI 的登录 Cookie 可能仅覆盖 `/cgi-bin/luci`，同级 CGI 收不到它。
网页通过 Fetch 流式读取 SSE，在 POST JSON 请求体中传递当前 LuCI 会话；
会话不进入 URL，服务端仍检查 RPC 读权限并定期复核，匿名访问返回 403。

认证后的 LuCI 使用 `unisoc-bluetooth` RPC 对象，参数为一个 `request` JSON
字符串。只读权限仅提供 `status` / `gatt_list`；发起扫描、配对、读取设备、
写入、订阅和串口发送均要求写权限。没有匿名控制 HTTP 端点。
配对和传输初始化是后台任务，通过 `jobs` / SSE 显示结果，避免长时间占用 rpcd。

### MQTT 控制示例

先在网页配置控制项 `lamp`，开启网关和 MQTT，再向
`unisoc/bluetooth/commands/lamp/set` 发布非保留消息：

```json
{"id":"request-001","value":"ON"}
```

结果位于 `unisoc/bluetooth/commands/lamp/result`，包含 `ok`、`id`、
`acknowledged` 和 `confirmed`。回读得到的状态才进入 `/state`。
HA 原生按钮／开关／数值实体也可以发送 `PRESS`／`ON`／`OFF`／整数文本；
程序控制建议使用 JSON 请求 ID。MQTT 服务端应为该网关配置独立账号及主题权限。
上报策略只节流连续数值和 RSSI；连接变化及离散按钮事件不被普通节流吞掉。

### 原生 Home Assistant 蓝牙代理

通过 ESPHome Native API 接入 HA，不需要自定义 HA 集成。与网页共用同一个
BlueZ 接收者和控制器，不启动另一套 HCI 读者。代理默认关闭，监听默认是
`127.0.0.1:6053`；供 HA 使用时明确选择设备自己的 LAN IP。使用 Noise
NNpsk0 加密认证，必须设置 32 字节 Base64 密钥，不提供明文退路。
在 HA 添加 ESPHome，填写设备地址、端口与加密密钥；LAN 上通过 mDNS 宣告。

广播从 BlueZ 已解析属性重建为 AD 字段，按 100 ms 窗口有界合批；并非独立
HCI 抓包器，无法保留 BlueZ 未提供的每个原始无线包。完整字段无法放入
62 字节时不截断加密数据字段。主动连接默认关闭，开启须填写设备白名单，
支持 v3 连接、服务发现、特征及描述符读写和通知。最大连接数是软件上限，
实际并发能力仍取决于控制器。只释放本代理建立的连接；不会接管网页／其他
程序正在使用的连接，也不远程自动配对、清缓存或重置控制器。

已用官方 `aioesphomeapi 46.30.1` 在本地假控制器上验证加密握手、广播、
主动连接、服务 UUID、GATT 读写和通知；这不是 HA 整机或具体家居设备实测。

### 模板文件格式

```json
{"format":1,"kind":"unisoc-bluetooth-templates","templates":[
  {"id":"my-sensor","name":"My sensor","decoder":"auto","subscriptions":[
    {"service":"180f","characteristic":"2a19","codec":"u8","name":"Battery","unit":"%"}
  ]}
]}
```

可选 `commands` 与网页控制项字段一致；应用时绑定所选设备并生成独立 ID。
内置 BTHome、标准电量和原始广播模板不可被导入文件替换。
第三方模板只有与真实设备协议一致才可控制，不推断私有写入命令。

### 构建与第三方许可

Theengs Decoder 2.5.0 的必要源码位于 `tools/ble-decoder/vendor/theengs/`，
保留 GPL-3.0-or-later 和 ArduinoJson MIT 声明；它作为独立 JSON-line
程序运行，没有 Python ABI 或内核模块 ABI 绑定。源码来源和原包摘要在
`tools/ble-decoder/PROVENANCE.json`。固件同时携带该程序的对应源码归档和 COPYING。

先经构建 runner 执行 `tools/ble-decoder/build.py --output <登记目录>`，需
`aarch64-linux-gnu-g++`。两仓库在 `tools/build-layout.json` 登记
`bluetooth_decoder={workspace}/artifacts/bluetooth-decoder`；共享完全相同的
源码／二进制校验结果。打包遇到过期输入会要求重编，不能拿旧包顶替。
普通更新与 diff 均由同一 root staging 收录程序、页面、模板、依赖和权限。
新增的系统依赖是发行版 `python3-cryptography`、`python3-zeroconf`；
NoiseProtocol 的纯 Python wheel 固定摘要，许可证随文件保留。

## 平台实现与依据

MU300：现有 `sprdbt_tty` 通过 PCIe WCN 提供 `/dev/ttyBT0`，已有
`mu300-bt-init.c` 实现原厂 PSKey / RF / enable 命令，再由 btattach 接 H4。
仅操作厂商名为 `bluetooth` 的那一个 rfkill，不使用影响全部控制器的
`rfkill block bluetooth`。地址保留现有配置；没有旧地址时生成并持久保存一个
本机随机地址，不从不存在的 machine-id 派生所有机器都相同的地址。

Pro：明确使用 `u30pro-aic-uart`，不会运行 Marlin 初始化器。现有 AIC BSP
负责 SDIO 固件和共享电源位图；新增独立 rfkill 调用同一个
`aicbsp_set_subsys(AIC_BLUETOOTH, …)`，不自行改组合芯片 GPIO。
现有 BSP 参数是 UART、1500000 baud、硬件流控；已在 U30 Pro 验证 ttyS0
初始化和 H4 注册。F50 Pro 尚未完成本轮实机验证，不能仅凭共享 BSP 推定。

## 可复现检查

`tests/test_bluetooth_gateway.py` 使用私有 D-Bus、临时目录、本地 MQTT / HTTP
服务器及 PTY：覆盖配对确认、GATT 通知、扫描代次、事件游标、密钥处理、
队列隔离、MQTT / HA / HTTP 传输、串口二进制和交付权限。
这些是离线检查，不代表板载蓝牙已经完成实机验证。

## 2026-11-17：U30 Pro 热部署与内核验证

目标是 U30 Pro / MU5358，原固件 `r20261114.144530-gc23a2e60-s11df1a36`，
Linux `6.12.94-u30pro-p1`。原运行内核未启用 CONFIG_BT；已先只读备份并
校验两槽 boot、原模块及配置，再按用户本轮授权更新 Linux A 槽 boot 前
48 MiB／AVB footer 和 253 个配套模块、正常重启。使用原 ramdisk、安装
参数及保留尾部，Android B 的 boot 哈希逐字节保持，未改 GPT、userdata
或 NV／IMEI。新 Image 的 SHA-256 为
`eca0a9c61a99c7f238ee0ba67c3a7f4d55c20fe11866eb4371790a82a95a7360`，
已核对登记对象目录的 Image、config、源码 overlay 收据和全部模块。

本轮实机结果：

- `aic_bt_rfkill` 通过现有 BSP 子系统位图供电，ttyS0 以 1500000 baud、
  硬件流控、UART H4 注册 hci0；蓝牙开启和关闭均通过。
- BLE 扫描 6 秒发现 5 台设备，其中 2 台提供厂商数据，4 台提供服务数据。
  经典扫描可启动和停止，本轮没有发现新的经典设备，不冒称完成配对验证。
- 控制器报告 6 个广播实例；两条自定义广播同时注册、3／5 秒超时 Release、
  单条启停通过。临时定义已删除。尚未用另一台手机核对空中广播接收。
- 名称保存、实际 Alias 应用及恢复通过；控制页、广播详情／实验脚本与当前
  源文件一致，匿名 SSE 返回 403，默认自启动和网关均保持关闭。
- 测试结束蓝牙恢复 Powered=false；WAN、USB NCM、wlan0 AP 及原 36 信道／
  80 MHz 保持正常，管理员密码和主 APK 数据库未变。

用户态采用官方签名索引验证的独立测试运行环境，位于
`/opt/unisoc-bluetooth/runtime`，避免改动该旧固件的系统依赖；私有启动包装器
只在本机使用，不是正式源码输入。正式构建仍用 `stage-bluetooth.py` 和发行版
依赖交付。GATT／SPP 实际外设收发、真实配对、HA 整机及长期待机仍待验证。
原件、试验收据及未脱敏诊断仅保存在本机 `work/bluetooth-pro-20261117/`。

## 2026-11-17：F50 热部署记录

实机为 MU300 系列 F50，OpenWrt 25.12.5、7.2.8，运行原有匹配的
`sprdbt_tty.ko` 和本机 PSKey / RF 配置；本轮未刷 boot 或重启。
BlueZ 5.83 能注册 UART `hci0`，双模能力可读，控制器能开启并恢复关闭。
BLE 与经典扫描均可启动并在 30 秒后自动停止。用户打开手机的蓝牙设置页后，
经典扫描发现 2 台设备，BLE 扫描发现 3 台（其中 2 台使用随机地址），
网页收到实时发现事件。本轮没有发起配对；GATT 通知和 SPP 收发仍未完成
硬件验收。开关循环已核对，最后恢复关闭。

实机补齐两个边界：厂商 rfkill 已开启时，HCI 自己仍可能软阻止，须仅解除
所选控制器的阻止；解除后内核/BlueZ 可能已启动同一次电源转换，Busy 必须
等待实际 Powered 事件确认，不能直接当作成功或反复发送开关命令。

该机原有 dnsmasq 与 dnsmasq-full 并存，常规 apk 事务会连带移除旧包。
本轮测试将 BlueZ 用户态环境装入独立 `/opt/unisoc-bluetooth/runtime`，
由官方签名索引及包摘要验证，不修改主软件包数据库，也不关闭签名校验。
该私有测试目录不是正式构建输入；正式固件仍走受版本控制的依赖安装路径。
当前机器的旧内核没有 btusb，因此没有保留该机不适用的 USB 蓝牙自动加载项。

已验证网页登录、RPC 方法和认证后的事件流，匿名事件流访问返回 403。
测试后蓝牙、自动开启和网关均关闭；Wi-Fi 接口/信道、USB 主机角色、管理员
密码以及主软件包数据库保持原样。硬件报告和原文件备份仅留本机 work 目录。

网页将控制器、扫描、配对与转发状态分别显示；异步请求受理不算操作成功，
以任务结果为准。CN/EN/TR 均提供可读提示，原始错误与事件数据留在可展开的
技术详情中；操作提示在原位置替换，不累计弹出相同通知。

依赖 wheel 版本、来源、SHA-256 及许可证位于
`openwrt/overlay/usr/lib/unisoc-bluetooth/vendor/`。接口参考：
[BlueZ Adapter API](https://bluez.readthedocs.io/en/latest/adapter-api/)、
[GATT API](https://bluez.readthedocs.io/en/latest/gatt-api/)、
[Agent API](https://bluez.readthedocs.io/en/latest/agent-api/)。


### LuCI 事件连接与并发（2026-11-18）

网页事件改走 uhttpd 原生 ubus SSE `/ubus/subscribe/unisoc-bluetooth-events`，
由一个 ucode 服务转发私有控制套接字的事件。不再用长期占用 CGI 请求槽的 Python CGI；
旧 CGI 地址立即返回 410，提示刷新。多标签页不应堵住登录或其他 CGI。
订阅使用 Bearer 会话与 `:subscribe` ACL；服务器每 30 秒结束订阅租约，重新认证后续接，
注销或权限撤销不能永久保留事件流。页面隐藏时断开，恢复时重新订阅并刷新快照；
没有订阅者时不连接私有事件源。蓝牙依旧默认关闭，自动开启设置不变。

`tests/test_bluetooth_http.py` 在真实 uhttpd/rpcd/ubus/ucode 容器中验证八路订阅、
CGI 和登录并发、未授权拒绝、会话撤销和无订阅时停止取事件；
`tests/test_bluetooth_events_ui.js` 验证页面隐藏、重连竞态和异常流清理。
这是离线服务回归，不等于控制器实机验收。
