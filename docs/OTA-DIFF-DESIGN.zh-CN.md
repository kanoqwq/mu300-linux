# 差异 OTA 协议 2

协议标识为 `unisoc-diff-ota-2`。归档仅包含 `manifest.json` 与 `blobs/<sha256>`；所有系统、boot、执行环境的重复内容共用相同 blob。

`operations` 保存路径、旧/新描述符及显式合并方式。配置默认值采用 `default`，账号采用 `accounts`，APK world 采用 `world`，包数据库采用 `apk-db`，包脚本采用 `apk-scripts`，发行身份采用 `release`。
`boot` 指向完整新 Linux boot 的 blob；不是旧版协议只提示更换内核而不写入的文件补丁。
`runtime` 是独立 RAM 根的文件索引；`busybox` 与 `bootstrap` 负责旧系统的 SSH 引导，不依赖其已安装的 Python。

设备事务先建立实际设备计划，保留额外插件拥有的文件，对 APK 记录按包名合并，保留用户账号和已有配置。
将恢复环境、实际旧文件和 boot 写入持久化备份后才进入 applying 状态。每次文件变更有 fsync 和内容回读，日志只更新已开始的数量，避免 O(n²) 写入。
boot-v4 绑定动态 Linux 槽位、保留当前命令行和角色记录，使用标准库 LZ4/CPIO 处理追加恢复钩子。启动日志区不覆盖。
事务失败恢复根文件与原 Linux boot，成功则保留最近一次恢复副本供手动撤销。RAM 执行环境与 RPC 保持在根文件更新之外。

启动恢复钩子仅在 pending 日志存在时启动恢复解释器；正常启动不运行 Python、不启动额外守护进程或轮询。
内核与根文件可更新，不校验“必须同内核版本”。仍校验设备系列、来源发行版本、归档路径/长度/哈希、空间、互斥和动态槽位角色。

实现入口：`installer/backend/ota_package.py`、`ota_runtime.py`、`ota_desktop.py` 与公共 LuCI 插件的 `engine.py`、`boot.py`、`rpc.py`、`proxy.sh`。
离线回归在 `installer/tests/test_ota.py`、`test_ota_full.py`、`ota_release_migration.py`。实机掉电不是离线回归可以证明的能力。
