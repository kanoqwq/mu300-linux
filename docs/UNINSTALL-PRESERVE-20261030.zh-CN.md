# 卸载保数据扩容与日志恢复（2026-10-30）

## 已确认的问题

F50 删除原安装记录后，安装器走当前 GPT、备用 Linux boot 和 ext4 安装标记的核验路径。实机 ext4 为 clean，错误计数 0，但带 needs_recovery；内部 JBD2 的 start 非零。用户正常从 OpenWrt 切回 Android 后该标志仍存在，执行 journal_only 后才通过检查。不能再建议反复切系统，也不能由这个标志认定文件系统损坏。

旧卸载代码只允许未加密、直接挂载的 ext4/F2FS 离线扩容，把 FBE＋metadata 加密也当成必须格式化，属于能力遗漏。

## 原厂证据

本次只读调查 F50：`ZTE/MU300/MU300:13/TP1A.220624.014/20250120.095956:user/release-keys`。userdata 为 mmcblk0p75，OpenWrt 为 mmcblk0p76，Android A；实现不固定这些节点或槽位。Android /data 来自 dm-39，FBE file 加密，单一 slave 指向 userdata，映射完整覆盖 userdata。

- libfs_mgr.so SHA256：`f72b6ccc247dd3f217cfaa3eaba7f790a4c9c1e150a58d8fcc7f9369ae907ce6`。
- resize.f2fs SHA256：`803e7d01e16f975a604ee652e891cac6b05e41676946c9db81cee3b550b58a84`。

ARM64 只读反汇编：libfs_mgr.so 的 0x38c80 函数内，0x38e0c 起检查 f2fs，执行 fsck 后在 0x38ef0 比较挂载点 /data，0x38f10 起组织 `/system/bin/resize.f2fs` 与实际块设备参数。vold 内也有同源逻辑。

AOSP Android 13 通用源码本身不能证明此厂商扩展，不能直接用通用源码替代本机证据：

- https://android.googlesource.com/platform/system/core/+/refs/heads/android13-release/fs_mgr/fs_mgr.cpp
- https://android.googlesource.com/platform/external/f2fs-tools/+/refs/heads/android13-release/fsck/main.c

## 实现边界

1. 无原记录的卸载检查先验证 GPT、Linux boot、未挂载且无 holders 的目标、可读且匹配的已提交安装标记。若只有日志待回放，取得 CID/ADB 设备锁，重新检查 GPT 和目标身份，自动调用 Android e2fsck 的 journal_only。不会挂载 OpenWrt，不执行完整修复，不清标志伪造成功。完成后重新读取所有证据。
2. FBE＋metadata F2FS 新增 `grow-android-f2fs`，不走未加密离线 resize 分支。计划和启动前都核对单层完整映射、原厂 manager/resizer 二进制及哈希。
3. 离线 initramfs 只将已证明属于 OpenWrt 的相邻空间归还 userdata。该分支不会对加密原分区执行 fsck、resize、mount、mkfs，不导出密钥，不碰 NV、metadata、super 或当前 Android boot。回到 Android 后让原厂通过解密映射执行挂载前扩容。
4. 必须核对 Android 分区容量、映射容量、文件系统容量及原 UUID 才报告完成。容量短缺保留续接状态，重试只重启 Android 原厂扩容，不重放 GPT 删除或自动格式化。
5. 未加密直接 ext4/F2FS 的现有离线扩容保持；旧式 FDE、非完整或多层映射、未知布局或缺少原厂能力证据时仍需明确提示和用户单独授权清空。不能把在线 F2FS 缩容 ioctl 反向当成在线扩容接口。
6. eMMC/UFS 共用现有动态磁盘拓扑与 512-byte 对外单位，GPT 内使用实际逻辑扇区；Android A/B 均动态确定，没有加入机型硬编码。

## 验证范围

连接 F50 实际运行新源码生成无记录卸载计划：`wipe=false`、`grow-android-f2fs`，userdata 约 15.2695 GiB → 23.2695 GiB，归还 8 GiB。只生成计划，未执行卸载或写 GPT。

日志恢复、异常布局、错误状态、错误归属、GPT 变化、恢复后标志未消失、加密映射和原厂工具、容量不足续接及 A/B 离线 worker 回归使用模拟传输或临时普通文件，不称作全机型硬件验收。

F50 的完整免擦卸载及回 Android 后最终扩容仍需用户执行新版安装器验收。其他机型和 UFS 的完整加密扩容未在本轮实机执行。原厂工具存在的检查用于选择保数据尝试路径，最终成功由实际 UUID/容量核验决定，不能用字符串存在替代硬件验收。


## 后续实机发现：一次性 bootloader 请求被误判成 Recovery

F50 的确认卸载已完成备份，但原 misc 的 command 是 bootonce-bootloader，Recovery 载荷和 stage 为空。旧判断仅检查 command 的 32 字节是否全零，把它误报为 Recovery 待执行。

现在按 C 字符串语义识别 command；真正的 Recovery、未知命令或带 Recovery/OTA 载荷的请求仍然阻止操作。用户确认卸载、备份完成且备用启动镜像写入核验后，允许交接无 Recovery/OTA 载荷的一次性 bootloader 命令：对比完整 2048 字节 BCB 的原值或已处理值，仅将前 32 字节 command 清零并回读。其余 BCB、A/B 控制、userdata、NV 不在这一步修改。未确认卸载时不执行清理。

BCB 原值继续保存在已有校验过的 misc 备份中，恢复启动准备允许识别原值或已处理值，旧失败事务可继续。最终回 Android 的核验也使用同一语义，避免在最后阶段再次误报。临时普通文件回归检查其他字节及 A/B 控制完全不变，Recovery/未知请求不被覆盖。


## 2026-10-30：原厂存在 resizer 不等于已完成扩容

用户 F50 的实际续接日志确认，卸载后的 GPT 与单层解密映射都已达到
24,985,468,928 字节，但 F2FS 仍为 16,395,534,336 字节，缺少 8 GiB。
原 UUID 和数据仍可读取。不能把这一状态报成卸载完成，也不能靠反复重启
保证原厂自动扩容。先前仅凭 ELF 中存在 resize.f2fs 路径推断执行能力不充分。

修正后的 grow-android-f2fs 在实际容量不足时安排一次挂载前维护：

- 使用本机 Magisk 工具生成临时 Android ramdisk，核对原 kernel、命令行及
  OS/header 字段没有被替换。缺少这些工具时在计划阶段说明无法提供该路径。
- 保持原 Android 槽。不能用另一槽启动 Android 来偷换这一步：另一槽的
  logical system/vendor 可能缺失或版本不同。该路径会临时修改原 Android
  boot，计划说明已明确这一点；电脑与设备各保留校验过的原件。
- Magisk overlay.d 在 post-fs、late-fs 之前安装临时的 fsck/resize 包装器。
  只接受属于本机 userdata 的完整解密映射，核对 GPT、存储身份、原 UUID、
  目标范围及未挂载状态，再调用本机原生 fsck/resize；不导出密钥。
- F2FS 的 -t 参数按实际逻辑扇区换算，兼容 512/4096 字节；sysfs/GPT
  公共接口的单位仍为 512 字节。E5 的 UFS 路径携带同一身份读取 helper。
- post-fs-data 保存实际工具输出并恢复原 Android boot，回读成功后才重启。
  若返回过程中中断，电脑仅在原件与已安装维护镜像的精确哈希匹配时续接恢复。
  boot 写入失败会当场尝试恢复已校验原件；无法确认时保留错误和备份。
- 不修改 misc、GPT、NV 或 metadata；不在线强改已挂载的 F2FS，不执行格式化。
  最后仍要求原 UUID、分区、解密映射和文件系统容量全部匹配才完成卸载。
- 成功后删除本次设备侧维护镜像与解包临时文件。失败保留日志；电脑安装备份
  按原事务保留，不会自动删除。不会无限重复后台重启或无限累积恢复目录。

验证边界：本轮对实机只读取状态、在普通临时文件中生成并检查了维护启动镜像，
没有刷入该镜像、重启或执行实际扩容。模拟回归覆盖挂载拒绝、错误 UUID/映射、
fsck/resize 失败、工具未真正扩容、512/4K 参数、Android A/B 和启动恢复。
这不是 F50 或全机型免擦扩容的实机验收；尤其不能将原厂工具存在或测试通过
表述为已完成设备扩容。现有已扩大的 GPT 和数据不应为了重试而重新缩容/格式化。


## F50 实机完成验收（2026-10-30）

用户明确要求手动完成后打包，本轮已实际执行上述挂载前维护。使用原卸载事务
204eceecb5ef49d3bad7b4cb332dd137 的已校验备份和 GPT 快照，在 CID 与原 UUID
匹配、Android A、GPT 已恢复的前提下执行；没有再次删除/改写 GPT，没有格式化。

原厂 fsck/resize 实际通过 dm-39 执行并回报 GROWTH_VERIFIED。本机 dm 的实际
逻辑扇区为 4096 字节，工具目标为 6,099,968 个逻辑扇区，不能传 512-byte 计数。
返回正常 Android 后确认：

- userdata 分区、解密映射、F2FS 均为 24,985,468,928 字节（约 23.2695 GiB）。
- 原 F2FS 为 16,395,534,336 字节，实际归还并扩入 8,589,934,592 字节（8 GiB）。
- UUID 不变，原 Android boot 回读与备份一致，维护 ramdisk 已退出。
- 原卸载事务已完成；设备侧临时 boot、解包文件和脚本已清理，电脑原备份保留。

这验证的是该台 F50 的 eMMC＋4K 解密映射路径，不代表其他固件或 UFS 的硬件验收。
