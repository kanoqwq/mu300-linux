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
