# Android → OpenWrt 网页插件 misc 写入复查

## 2026-10-30：v7 移除机型限制

两仓库 JS 插件内容一致，不再读取型号或平台作白名单判断，避免新增机型、
定制固件改名导致切换被拒绝。根据备用槽 boot 中的启动参数识别 E5/MU300
镜像格式，Android 当前槽仍来自 `ro.boot.slot_suffix`，支持 A/B 双向。

保留镜像头、范围、AVB footer、安装/卸载待办、misc CRC 及回读校验；
仅写 misc 偏移 2048 的 32 字节，不挂载 OpenWrt，不修改 boot/GPT/默认启动策略。
去掉名称限制不表示替设备安装 OpenWrt，也不表示已验证任意硬件的启动兼容性。

新增回归覆盖未知型号、新平台、名称与镜像系列不一致时按实际镜像识别，
以及未知型号携带原厂、损坏或待安装镜像时拒绝写入。未执行实机切换。
下方为 2026-10-16 历史记录，其中旧机型限制已被本节取代。

## 2026-10-16 历史复查

日期：2026-10-16。开发诊断及验证报告；没有对设备执行命令，没有编译或打包。

## 已确认的问题

用户提供的 `OP系统切换插件.js` 为 v2，MU300 仓库内同名功能也是 v2；E5 包内为 v3。三者都有相同的 `printfArg` 多重转义问题。

JavaScript 源码把每个字节编码成两条反斜杠后跟 `xNN` 的运行时字符串。交给 shell 的 `printf` 后，输出的是字面文本 `\x5f`，不是一个 `0x5f` 字节。32 字节的目标块变成 **128 字节文本**。旧写入命令没有 `count=32`，所以会覆盖 misc 的 **2048～2175**，超出应写的 **2048～2079**。旧插件回读后虽提示失败，但没有保存或恢复原内容。

在临时文件/管道中执行旧表达式得到 128 字节，开头为 `5c 78 35 66`（文本 `\x5f`）。这不是偏移 2048 或 A/B 元数据位置错误：两仓库均使用该偏移，slot A 元数据在控制块内 12，slot B 在 14，CRC32 在 28～31，均与原插件所用位置一致。没有失败设备的读数，不能据此断言所有切换失败都是同一原因。

检查本地 `UFI-TOOLS` 的 `requests.js`、`RootShell.kt`：命令经 JSON 传入 root socket，没有额外的反斜杠解码；API 的 `success` 表示 socket 调用成功，不能当作 shell 退出码或 misc 写入成功。root socket 会在用户命令之后追加结束标记。

另有三个风险：用 misc 中“下次启动槽”兜底推测当前 Android 槽；只检查读取长度、不验证原 BCAB/CRC 和目标镜像；E5 v3 将内部独立分区路径及 root 挂载写入作为通用切换步骤，不适用于 TF、镜像和 MU300 容器布局。

## 修正范围

两仓库的网页插件现共享相同 v4 内容：

- E5：`openwrt/bundle/【插件】mu300系统切换器.js`，原打包路径仍将其复制为 `extras/android/openwrt-system-switch-plugin.js`。
- MU300：`android/magisk/webui-plugins/mu300-system-switch.js`。
- 用户下载目录中的原文件同步修正；原版保留在同目录 `OP-system-switcher.v2.backup.js`。

操作语义为 **选择下次启动已安装的 OpenWrt，保持原默认启动策略**。不挂载/修改根文件系统、不写 boot、GPT 或启动链、不自动同步启动链。永久默认启动由已运行 OpenWrt 的原有设置完成；E5 v3 的 Android 侧 root 挂载改默认逻辑已移除。

当前槽必须来自 Android `getprop`。按仓库现有机型限制检查型号：E5/T551M/VIPME M8 只支持解锁后的 Android A；MU300/F50/U30Air 支持 Android A/B，V50 要求 MU3351、UMS9620 匹配。没有增加 FM_U20。

写入前验证原控制块格式、CRC、Android successful 状态、verity 状态和待启动优先级，验证相应家族 boot v4、镜像范围及 AVB footer，拒绝待完成的安装/卸载镜像、相同 Android 镜像。检查只读镜像头与尾部，不读取/挂载 OpenWrt 根分区。

二进制编码改用八进制转义的 `printf '%b'`，先写普通临时文件，核对长度和内容后，再限长写 32 字节。一次设备侧锁覆盖重新核对当前槽、原 misc、目标镜像头/尾、备份、写入、刷新及回读。每次写入保留 misc 前 4 KiB 和原控制块，便于排查旧插件超范围写入。写入或回读失败会恢复原 32 字节并验证；恢复失败明确禁止重启。原本已损坏的控制块会停止，不根据其他机器或包内模板猜测修复。

所有命令封装在子 shell，避免 `set -e`、`exit` 和 trap 影响宿主结束标记；只有回读验证后输出的独立 `SWITCH_VERIFIED` 才能触发重启，socket API 返回成功本身不能触发。成功后锁定再次启动按钮，提示预期断连与必要时手动重启。

## 验证

两仓库运行各自的 `system-switch-plugin.cjs`，**各 13 项全部通过**（WSL 的 Node、Bash、BusyBox）。覆盖：

1. IEEE CRC 标准值、独立 Python zlib 计算的 A/B 启动块、保留 Android 状态及设备保留字段。
2. 错误 CRC/格式/版本、未成功启动、verity、待启动优先级、丢失实际槽位、未知机型、V50 平台错误、E5 B、相同 Android 镜像。
3. 临时普通文件上的实际编码和写入：E5 A、MU300 A/B，仅 32 字节改变，misc 其余字节保持一致，4 KiB 备份一致。
4. 部分写入、回读不符、回滚失败、设备锁、槽位改变、并发 misc/目标镜像变更。
5. 旧写法实际输出 128 字节文本；root socket 命令成功/失败后宿主结束标记仍可执行，锁正常释放。
6. UI 重复点击互斥、API 成功但 shell 失败时不重启、只有已验证提交才重启、预期断连不报安装失败。

命令：

```sh
# E5
node openwrt/tests/system-switch-plugin.cjs
# MU300
node tests/system-switch-plugin.cjs
```

Windows 可执行纯 JavaScript/模拟宿主测试，5 项 POSIX 文件操作测试跳过；完整文件操作检查在 WSL 上执行。另对现有 E5/M8/T551M 三份 boot 及 MU300 `boot-mainline-7.2.img`、`boot-linux-slotb-tf.img` 只读检查，五份均可识别。没有修改这些发布载荷。

**未在实机或真实 Android WebView 中验证切换。** 已被旧插件覆盖的 misc 需要读取现场证据并结合该设备写坏前的真实备份恢复，不能通过新版插件自动重建；当前仍在运行的 Android 不代表 misc 没有损坏。
