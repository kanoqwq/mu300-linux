# U30 Pro 公共安装器接入（2026-11-02）

## 决定和证据

保留本机 Android v4 的 boot、vendor_boot、init_boot 容器。系统仍为 MU300 的
mu300root 分区和 /openwrt 容器，不把 rootfs 放入 vendor_boot，也不采用参考仓库的
reserve2、固定扇区或双槽根系统升级器。

证据为本机 MU5358-20260203 抓取的三镜像：boot 为 64 MiB、vendor_boot 为
100 MiB、init_boot 为 8 MiB；内核、厂商 ramdisk/DTB/bootconfig、Android 通用
ramdisk 分开。此前将新内核和启动内容配套写入后，实机已进入 OpenWrt，用户确认
画面与触摸正常。此证据不是本轮新安装/更新/卸载流程的实机验收。

## 实现边界

公共 UI、日志、无线/有线 ADB、SSH、分区计算、在线保数据缩容、安装记录和卸载
流程继续复用 openwrt-tools。desktop/manifest.json 的 platform=u30pro 显式选择原生
启动适配；其他 MU300、E5 包不启用该适配。机型不匹配仍可输入 yes 明确确认。
启动布局不同不会偷偷切换为另一套硬件实现。

- 初装/重装：动态确定 Android 和另一 Linux 槽；读取本机模板，保留本机 DTB 和
  bootconfig，生成一致的 boot/vendor_boot ramdisk，并清空另一槽 init_boot 的
  Android /init。DTBO 和 BSP 使用本机当前槽，备份并回读，最后提交 misc。
- 临时启动：正常路径仅检查三镜像是否配套、修改 misc；明确修复时先备份另一槽
  的启动容器并重建，再回读并试启动。界面列出实际修复目标。
- OpenWrt 下完整更新：SSH 读取并校验当前 Linux 槽模板，通过同一编码器生成
  vendor_boot/init_boot，在持久系统盘暂存和备份。配套写入失败恢复原容器、根及 misc。
- Diff OTA：补丁只携带一份规范 boot 输入；设备端保留本机配置生成 guard/final
  的配套容器，不在 ZIP 重复塞入三个通用 boot。事务记录包含分区、大小、SHA 和
  恢复文件；更新、断电恢复与撤销都处理配套容器。恢复副本沿用引擎原清理规则。
- 卸载归还空间：维护镜像在存储发现后、挂载系统前进入公共离线卸载器；同时将
  相同维护 ramdisk 放入 vendor_boot，保留原 Android 槽。完成后恢复另一槽 Android
  的 vendor_boot/init_boot/DTBO 及 boot，避免留下 Linux 启动组件。
- Android 挂载前 F2FS 扩容：原生 Android v4 独立 ramdisk 路径使用当前 Android
  槽 init_boot，保留其 kernel/头/参数，仅临时添加 Magisk pre-mount hook；完成或
  失败均按原件 SHA 恢复。仍需实际固件支持原有 Magisk 挂载前工具，不伪造支持。

未加入固件文件哈希白名单；哈希用于本次传输、备份、并发变化和恢复校验。
容器格式、片段表或边界不符会在写入前停止，不能把未知固件结构当已适配。
AVB 元数据容器保留不等于重新签名；本机既有启动链行为不作额外放宽。

## 离线结果及限制

- 公共安装器完整回归：610 项执行，608 通过，2 项因环境条件跳过。
  日志：out/build-logs/20261102-140029-u30pro-installer-acceptance-77679/command.log。
- 前端 TypeScript/Vite 构建通过，10 项协议测试通过；复用已有 node_modules，不复制缓存。
- tests/u30pro_boot_adapter.py：10 项文件模拟通过，包含两种槽位方向、部分写入恢复、
  misc/BCB 失败恢复、卸载配套容器恢复/重试及真实 SSH 更新 shell 函数。
- installer/tests/test_u30pro_boot.py：覆盖动态槽位、三镜像一致性、损坏输入、SSH
  二进制传输、卸载启动标记、OTA 更新/恢复/撤销及用户插件保留。

本轮不连接或操作实机，不修改用户 NV/IMEI，不重启设备。尚需用户授权后的实机
初装/更新/卸载和断连恢复验收，特别是当前固件 Magisk init_boot 的挂载前扩容。
未将离线测试称作硬件通过；本轮没有生成新的 EXE/APK/完整包。

## 生产构建接入

共享编码器唯一源为 installer/runtime/luci-app-unisoc-ota/root/usr/lib/unisoc-ota/boot.py。
完整包将其复制为 desktop/boot-runtime.py 并纳入 manifest 校验及包身份；设备端由
现有 stage-ota.py 收入。桌面、Android 和 SSH 使用同一共享后端，没有移动端专用
刷写分叉。CLI 通过有包上下文的后端入口加载，避免相对导入在打包后失效。
原生安装器改动先提交到子模块，再在本仓库固定 gitlink；E5/MU300 参考仓库不修改。
新源码要求重建匹配的安装器交付件，旧 artifact 的源码指纹检查不可绕过。
