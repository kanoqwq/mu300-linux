# 本地构建与打包规范

本机 Agent 的完整操作指南见 [AGENT-BUILD.zh-CN.md](AGENT-BUILD.zh-CN.md)。
Windows/固件在本机完成；Apple Silicon App 可按用户授权使用上游 macOS CI 原生重编，下载后仍须通过源码指纹校验。

本仓库维护分支为 `minikano-openwrt-feats`。不变更上游、分支及提交历史。
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
| Windows/macOS 安装器 | `installer/artifacts/windows`、`installer/artifacts/macos-arm64` |

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

## 仅补入 Diff Studio：快速重打包

正式包默认通过 `tools/package-diff-studio.py` 附带两个平台的 Diff Studio 与 Python 入口，
与安装器共用后端。已有已验证发布遗漏工具时，使用下面的入口，不能另写旧 worker：

```sh
python3 tools/build-release.py add-diff --workspace /home/kano/openwrt-build --profiles all \
  --source-release /absolute/repo/release/<verified-task> --label diff-tools
```

只接受 `release/` 下带已验证 BUILD-RUN.json 的正式发布。先核对源 ZIP 与哈希，
用本仓库源码指纹匹配的原生程序附加工具；boot、rootfs、Magisk 和修复工具载荷均不改。
ZIP 流式处理，不解压完整 rootfs。新工具纳入原 desktop manifest 和 CRC／SHA256 校验，
每个发布包仍只有一份 boot、一份 rootfs 和各平台一份后端；最终通过才发布新目录。
旧包不覆盖，失败现场按既有 clean-staging 规则处理。

## 中断批次中已完成的包

另一发行线中断后，无需重编或复制已经完成的 ZIP。使用固定入口单独验收发布：

```sh
python3 tools/build-release.py publish-completed --workspace /home/kano/openwrt-build \
  --profiles mu300-7.2 --source-release /absolute/repo/release/.building-<old-task> \
  --label publish-completed
```

只接受相同工作区中有失败任务记录、且所选构建步骤已完成的本地 pending 目录。
重新核对 ZIP 完整清单、CRC、SHA256、Diff Studio 和干净 Git 来源；原版本号、
提交及载荷全部保持，不把旧包冒充当前源码重编。原失败任务日志保留，新的
BUILD-RUN.json 明确列出已完成的所选发行线。未完成的 staging 不会被发布或清理。

仅导出独立 Diff Studio（两个平台后端各一份，无 boot/rootfs/ADB）：

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build --label diff-standalone -- \
  python3 tools/package-diff-studio.py --from-release /absolute/path/to/verified-release.zip \
  --output-zip /absolute/repo/release/diff-studio/OpenWrt-Diff-Studio.zip
```

工具文件逐项按发布包内 `diff-tool.json` 核验，保留 Mac App 签名内容和可执行权限，
不解压固件、不重新编译；输出 ZIP 与 SHA256，已有目标不覆盖。

## 仅刷新原生安装器和 Diff Studio

GUI／后端变更时，先按 AGENT-BUILD 规范原生重编 Windows 和 Apple Silicon 交付件，然后执行：

```sh
python3 tools/build-release.py refresh-installers --workspace /home/kano/openwrt-build \
  --profiles all --source-release /absolute/repo/release/<verified-task> --label native-installer
```

该入口验证原发布及当前原生程序指纹，流式替换两平台安装器、共享后端、Diff Studio 和各层清单，
移除旧运行环境中已淘汰的受管文件；不改 boot、rootfs、Magisk 载荷及系统版本。
新 ZIP 后缀 `-installer-g<提交>` 标记宿主工具修订，原发布不覆盖。
仅有外部 Python 入口变化且原生后端未变时，仍使用下面的轻量入口。

## 仅修复外部安装器 Python 入口

当变更只涉及 `desktop/installer.py` 的外部入口逻辑时，可流式修补已验证发布，
不重新构建固件或未变化的原生后端：

```sh
python3 tools/build-release.py repair-installer --workspace /home/kano/openwrt-build \
  --profiles mu300-7.2 --source-release /absolute/repo/release/<verified-task> --label installer-entry-fix
```

新包同步替换根目录和 desktop 的入口及清单；boot、rootfs、Magisk 内容保持相同。
此入口不用于修补内置后端、驱动或任意其他脚本。此类依赖改变仍应走对应构建。
发布前通过实际冻结程序加载包内入口、CLI 和 GUI Adapter 的无设备测试，不能只测试 hello。


## 公共安装器子模块

installer/ 现在固定引用 https://github.com/kanoqwq/openwrt-tools 的提交。新检出先执行 git submodule update --init --recursive。共享源码、Android 及独立工具构建方法见 installer/docs/BUILD.zh-CN.md；E5/MU300 的 NV、UFS 身份和启动流程继续分开处理。请先在子模块提交，再在父仓库更新 gitlink；不要重新复制一整份安装器。

两个父仓库可通过 OPENWRT_GUI_ARTIFACT 与 OPENWRT_GUI_MACOS_ARTIFACT 复用同一份已校验交付件，无需各编译一次；首次拆分后旧 GUI 指纹应正常拒绝，不能修改旧 build.json 绕过。tools/release-index.py、tools/build-release-index.py、tools/package-diff-studio.py 保留为兼容入口，实际代码由子模块提供。


## 根目录 Android 安装器

完整打包及 refresh-installers 均由公共 installer/package_android.py 校验已签名 ARM64 release APK 的源码、ABI 与哈希，发布包根目录固定放 OpenWrt-Tools.apk，构建清单为 docs/GUI-BUILD-android.json，使用说明为 docs/ANDROID-INSTALLER.md。不要把 APK 塞进 tools 或再复制一份。

本机与两个桌面交付件一样复用 E5 的 Android 构建：WSL 设置 OPENWRT_GUI_ANDROID_ARTIFACT=/mnt/d/Git/e5-linux/installer/artifacts/android；Windows 使用 D:/Git/e5-linux/installer/artifacts/android。过期时只重编 Android，不修改 build.json。

Mac 原生构建使用公共安装器仓库的 macos.yml，手动 dispatch 即可；不再在 E5 仓库新建 build/macos-* 快照分支。下载后仍核对公共 sources()。
