# 本地构建与打包规范

本仓库维护分支为 `clean-tf-7.2`。不变更上游、分支及提交历史。
本地任务统一走受版本控制的 `tools/build-release.py`；不得复制/执行旧的
`work/` 日期 worker，或通过字符串替换拼接另一份发布流程。

## 固定目录

| 内容 | 位置 |
| --- | --- |
| Linux 构建工作区 | 本机既有 `/home/kano/openwrt-build`，不迁移 C 盘 |
| 内核/SDK 编译缓存 | 原有 WSL 目录或 Docker volume，不新建重复源码树 |
| 输入登记 | `tools/build-layout.json`，另一台机器用 `--config` 显式指定 |
| 打包 staging | 工作区 `package-work/mu300-release/<series>/assembly` |
| 正式发布 | 仓库 `release/<task>/` |
| 任务日志/状态 | `out/build-logs/<task>/`、`out/build-state/latest.json` |
| 临时文件 | 工作区 `tmp/mu300-release-*`，单步结束自动删除 |
| Windows/macOS 安装器 | `installer/artifacts/windows-x64`、`installer/artifacts/macos-arm64` |

登记表显式引用当前已经存在的输入，包括相邻 `mu300-linux/work` 的固件/工具，
以及本仓库旧安装包中的维护工具。它们尚未迁移，所以不能当成垃圾删除。
路径记录不意味着另复制一份；缺失时报错，不会随机寻找旧包替代。

## 使用方式

在 WSL 内进入当前仓库，使用当前 Linux 用户的 `~/openwrt-build`；sudo 时必须
明确 `--workspace /home/kano/openwrt-build`。当前支持两条内核发行线：
`mu300-7.2`、`mu300-6.18`；共享固件继续在运行时区分 MU300/F50/V50/U30 Air，
不为每台机器重复构建同一份镜像。

```sh
python3 tools/build-release.py plan --profiles all
python3 tools/build-release.py check --profiles all
python3 tools/build-release.py package --profiles all
python3 tools/build-release.py status
```

`plan/check` 只读，验证固件/工具入口、内核与完整模块配套、Wi-Fi/block-mount
缓存，以及 Windows/macOS 安装器源码指纹。输入过期即停止，不启动编译或复制 root。
内核产物配套校验不能证明当前内核源码已经被编译；仍需核对变更范围。

编译有变更的依赖或内核时，使用固定日志与互斥入口，显式设置原构建器参数：

```sh
python3 tools/build-release.py run --label wifi -- sh openwrt/build-hostapd.sh
python3 tools/build-release.py run --label block -- sh openwrt/build-block-mount.sh
# 内核：run --label mu300-kernel -- <现有构建命令>
```

内核继续复用现有 Docker volume 和 `upstream/build.sh` / `build-modules.sh`，
禁止临时克隆或导出另一份内核树。Windows 和 Apple Silicon 安装器在对应原生
平台运行 `python installer/build.py`，各编译一次供两条内核发行线共用。

## 组装与清理边界

组装复用原 `tools/build-openwrt-tf-magisk.sh`，不改 Magisk/桌面安装流程。
新增的 `MU300_PACKAGE_WORK` 和 `MU300_ROOTFS_OUT_DIR` 仅控制中间文件位置；
未设置时保留原脚本默认行为。现在 rootfs、boot、ramdisk、release JSON、
解压 staging 均能放入固定任务目录，成功后一起清理，不散落根目录。

原 rootfs 构建仍需安装软件包、编译少量辅助程序；不重新编译内核或 GUI。
Wi-Fi/block-mount 先验证缓存，现有构建器命中缓存即复用。
完成 ZIP CRC、原 manifest 文件校验、单份 boot/rootfs 校验及复制 SHA-256
核对后，清理本机型 staging。全部发行线通过且源码未变，才把隐藏的
`.building-<task>` 目录改名为正式目录，写入 `BUILD-RUN.json`。

失败保留现场，状态为 failed；隐藏目录不是发布包。确认不再需要现场后：

```sh
python3 tools/build-release.py clean-staging --profiles mu300-7.2
```

只删除固定目录内带匹配所有权标记的 staging，拒绝符号链接、越界和仍有挂载
的目录。源码、对象、下载缓存、设备备份、当前安装器和旧发布包不自动清理。
历史遗留缓存应单独盘点处理，禁止例行 `docker system prune` 或删除 volume。

E5 与 MU300 工具共用工作区 `locks/openwrt-release.lock`，防止同时读写共享
SDK/源码缓存；staging 与日志按仓库分开。旧脚本直接执行无法受此锁保护，
所以本地构建必须经过固定入口。CI 的独立 runner 不受本规范改变。
