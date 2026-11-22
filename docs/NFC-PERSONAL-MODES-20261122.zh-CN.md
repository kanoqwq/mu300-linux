# NFC 个人网络模式与 F50 Pro 双击（2026-11-22）

## 现场与修复边界

只读检查 F50 Pro，版本 `f50pro-r20261121.191636-g7fcb9347-sf56946fa`。
`/etc/mu300/model=f50pro`，平台为 `u30pro`；NFC 开启、选中热点为纯
`sae`，旧控制器结果为 `unsupported_encryption`，因而主动清空标签。
双击设置为 `wifi-band`，但共享设备探测返回平台名 u30pro，按键服务读错
产品配置。证据在 Pro 仓库 `work/diagnostics/f50pro-button-nfc-20261122/`。

修正 F50 Pro 按键服务的产品选择，不改原生输入、250ms 双击窗口、单击或
长按动作。NFC 不再用精确字符串白名单排除 SAE、OWE 和合法的加密后缀。
Pro 与 MU300 使用字节相同的 `usr/share/ucode/mu300/nfc-wifi.uc` 编码器；
GPIO/I2C、锁、RF 开关和原生硬件写入仍由各平台原有 owner 管理。

覆盖开放、WEP open/shared、WPA/WPA2/WPA3 Personal、混合及 SAE 扩展
模式、OWE，以及 CCMP/AES/GCMP/256 位和 TKIP 组合后缀。WEP 使用当前
选择的 key1–key4；隐藏标志和原始加密字符串保留在额外的 Wi-Fi 文本记录。
未知模式也保留配置信息，不再因为模式名称不在列表就发布空 NDEF。
企业认证按用户最新要求不适配；不读取或分享 AP 侧 RADIUS 密钥。

密码仅走 stdin，不进 argv、状态或错误日志。编码完成且通过边界检查后才
访问标签。相同内容免写，NFC 关闭状态与自定义 URL/文本继续保留。
没有新增轮询、驻留 Python、无线扫描、Wi-Fi 配置修改或安全降级。

## 手机自动连接边界

WSC 使用协议定义的认证字段；其传统认证 ID 没有 SAE/OWE 项，不自行
发明数值。Android NFC 导入 PSK/open 凭据后，Wi-Fi 配置层可增加 SAE/OWE
安全参数，实际使用取决于手机功能与策略。附加文本保留真实 SAE/OWE 模式，
不把 AP 改为 WPA2/open。标签写入、手机发现标签、手机成功入网是三项
不同验收，不能用编码测试承诺所有手机或隐藏/WEP 网络都能自动连接。

依据：
- Android NFC 读取实现：
  https://android.googlesource.com/platform/packages/modules/Nfc/+/refs/heads/main/NfcNci/src/com/android/nfc/NfcWifiProtectedSetup.java
- Android 安全参数自动升级及设备支持检查：
  https://android.googlesource.com/platform/packages/modules/Wifi/+/refs/heads/main/service/java/com/android/server/wifi/WifiConfigurationUtil.java

## 六组离线回归

1. 原始 WPA2 凭据字段对照与目标 ucode 编码：5 项测试，58 个有效认证/
   加密组合、短 SAE 密码、UTF-8、转义、隐藏标志、容量和非法输入；
   另连续 40 次更换凭据，确认不复用旧密码。
2. Pro 原生 C 写入/控制：23 项，实际 C 工具、私有 EEPROM、F50 reset
   恢复、同内容免写、写入中断、读写错误；真实目标 ucode -> 真实共享
   控制器 -> 原生写入器的六模式端到端测试，并核对 UCI 字节不变。
3. NFC RPC 契约 7 项、浏览器 18 个语言/主题/尺寸组合；F50 按键事件
   3 项及配置/动作 19 项。测试使用私有 RPC/输入替身，不是实机按键验收。
4. Pro 继承的 MU300 标签 owner 16 项，覆盖真实 shell/ucode 路径的
   GPIO 释放、RF 关闭、改密、重新开启、URL/文本、排队事件和故障恢复。
5. MU300/V50 相同 16 项通过，核对 V50 DT GPIO 与 U30 Air 分别选择；
   不因共用编码器合并硬件访问路径。没有控制实机 GPIO。
6. 真实共享差分生成器/执行器：mu300、u30pro、f50pro 三个 profile，
   首次新增编码器、后续更新、两次回退和再次更新，用户无线/NFC设置及数据
   保持原样。标准 refresh-sources 已执行，F50 Pro 构建根中的六份源码及
   原生 ELF 与本次源文件/编译产物逐一比对通过。

日志在 Pro 仓库：
- `20261122-080254-nfc-personal-all-modes-regression-89933`（RPC、浏览器、按键及原生编译）
- `20261122-081214-nfc-complete-private-io-matrix-94006`（Pro owner 前15项；最后一项测试入口缺少新编码器路径，随后补齐测试依赖）
- `20261122-081540-nfc-cross-platform-final-94030`（Pro 排队事件重跑、MU300完整16项、最终58组合及Pro23项）
- `20261122-082107-nfc-ota-ordinary-delivery-verification-96615`（实际差分/回退与交付文件）

测试镜像原先缺少 host Python、od 或小数 sleep，已在私有测试环境补齐；
未用放宽生产检查或修改真实硬件行为来通过测试。曾出现的 shell 引号错误
也已在完整目标 shell 回归前修正。

## 交付状态

新文件位于两仓库既有固件 overlay；完整镜像、普通更新和差分沿用统一的
来源/文件清单。Pro 根检查已加入新编码器比对，原生 C 适配摘要和编译回执
已更新。没有修改安装器、版本锁、内核或硬件供电策略。
本轮尚未热部署或生成新 ZIP，手机碰触及实际入网待验收。
