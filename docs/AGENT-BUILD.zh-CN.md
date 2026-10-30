# 给 Agent 的本机编译与发布操作手册

先读本文件和 `LOCAL-BUILD.zh-CN.md`。不要寻找用户不存在的 Mac Agent。
Windows 和固件在本机编译；Apple Silicon 安装器可以使用 GitHub macOS CI
原生重编。2026-10-25 用户已明确允许 Mac 部分使用上游 CI。

## 固定环境与边界

- Windows 仓库：`D:\Git\mu300-linux-clean-tf-7.2`；维护分支：`minikano-openwrt-feats`（不得误用历史 main）。
- WSL：`Ubuntu-22.04`，用户 `kano`，仓库 `/mnt/d/Git/mu300-linux-clean-tf-7.2`。
- 唯一 Linux 工作区：`/home/kano/openwrt-build`。
- Windows Python：`C:\Python312\python.exe`，Node/Rust 使用本机已有环境。
- 两条发行线：`mu300-7.2 mu300-6.18`；各自运行时区分 MU300/F50/V50/U30 Air。
- 输入路径由 `tools/build-layout.json` 登记。缺输入先报告，不随意换旧包。
- 正式包：`release/<task>/`；日志：`out/build-logs/<task>/`。
- 不复制整份源码/rootfs，不导出 Docker volume，不迁往 C 盘，不删除缓存或旧包。
- U30 Pro 是另一个仓库；不能把它混入本次发布。

## 1. 确定需要编译的组件

先看 `git status`、最近提交和登记的交付件。安装器源码变化必须重编安装器；
内核、DTS、驱动、内核配置变化必须重编相应 Image 和完整配套模块。
只有用户态文件变化时可以复用已验证且输入未变的内核；不能仅凭文件日期复用，
也不能为了“全量打包”无意义地重编未变依赖。

WSL 中执行：

```sh
cd /mnt/d/Git/mu300-linux-clean-tf-7.2
python3 tools/build-release.py plan --workspace /home/kano/openwrt-build --profiles all
python3 tools/build-release.py check --workspace /home/kano/openwrt-build --profiles all
```

过期输入导致 check 失败是正常的：重编对应组件后重新检查，禁止改指纹绕过。
编译通过 `tools/build-release.py run --label <组件> -- <真实构建命令>`
记录日志并获取互斥锁。内核沿用 upstream/build.sh 和 upstream/build-modules.sh，复用既有 Docker volume。用 tools/build-layout.json 中匹配的两套 Image/模块，不能跨内核混搭；具体规则见 LOCAL-BUILD。

## 2. Windows 安装器，只编一次

在 Windows 本机运行下面的实际命令，但用 WSL 的 `build-release.py run`
调用 PowerShell，以保留任务日志和互斥。不要用 PowerShell 双引号提前展开
传给内层 shell 的 `$LASTEXITCODE`。

```powershell
Set-Location D:\Git\mu300-linux-clean-tf-7.2
C:\Python312\python.exe installer/build.py --adb-dir D:/Git/e5-linux/work/installer-platform-tools/platform-tools
```

`--skip-ui` 仅允许在已确认现有 Rust GUI 和前端与当前源码匹配时使用；
它仍重编 Python 后端。输出实际为 `installer/artifacts/windows/`，
不是 `windows-x64/`。检查命令退出码和 `build.json`，不能只看 exe 存在。


本机 PowerShell 可直接使用下面的带日志入口（外层单引号不可改成双引号）：

```powershell
wsl -d Ubuntu-22.04 --cd /mnt/d/Git/mu300-linux-clean-tf-7.2 --exec python3 tools/build-release.py run --workspace /home/kano/openwrt-build --label windows -- /mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe -NoProfile -Command 'Set-Location -LiteralPath D:\Git\mu300-linux-clean-tf-7.2; & C:\Python312\python.exe installer/build.py --adb-dir D:/Git/e5-linux/work/installer-platform-tools/platform-tools; exit $LASTEXITCODE'
```

## 3. Apple Silicon App：上游 CI 原生重编

现有 workflow 是 `.github/workflows/gui-macos.yml`，运行器 `macos-15`，
Rust 1.90.0、Node 22、Python 3.12。它检查 ARM64、运行安装器测试、
重编 GUI/冻结后端、签名，并运行 `macos_frozen_smoke.py`。

必须让远程构建提交包含本地所有安装器相关修改。使用独立
`build/macos-*` 分支可以触发构建；不要强推或改上游 main 来触发。
同一提交不要同时 push 触发和手动 dispatch，避免双重构建。

```sh
gh workflow run gui-macos.yml --repo sprd-linux-devs/e5-linux --ref <含当前源码的分支> -f runner=macos-15
gh run list --repo sprd-linux-devs/e5-linux --workflow gui-macos.yml
gh run view <run-id> --repo sprd-linux-devs/e5-linux --log-failed
gh run download <run-id> --repo sprd-linux-devs/e5-linux --name openwrt-installer-macos-arm64 --dir <独立下载目录>
```

2026-10-25 个人 fork 虽显示 workflow active，dispatch 仍返回 HTTP 422
“Actions has been disabled for this repository”；不能把它当成已在编译。
当时经用户许可使用 E5 组织仓库的独立 build/macos-mu300-* 构建分支承载 MU300 的完整提交；本地维护分支和两个上游 main 均未修改。必须构建 MU300 自己的源码，不能直接复用 E5 App。无权限或运行额度问题必须如实报告。

将 artifact 中的 `openwrt-installer-macos-arm64.tar.gz` 解压到
`installer/artifacts/macos-arm64`，保留执行权限和 framework 链接。
替换前检查目的目录，避免覆盖非构建文件。最后由 check 验证源码指纹及所有哈希。
不得拿旧 App 改 build.json 冒充重编，也不用 Python 入口替代 Mac App。

## 4. Diff Studio 与最终组包

正式包已经固定内置 Diff Studio。仅给旧正式包补工具时，使用 LOCAL-BUILD 中的
`build-release.py add-diff --source-release ...`，不重编固件，不复制完整 rootfs。
以下 build-ota.py 仅用于单独交付工具。

当前平台的原生安装器验证后，可复用同一份新编 GUI/后端生成 Diff Studio：

```powershell
C:\Python312\python.exe installer/build-ota.py --output installer/artifacts/diff-windows
```

已存在的完整自有目录可加 `--refresh`；有本地修改时应停止调查。
Mac Diff Studio 需要在 macOS runner 执行同一脚本，不能把 Windows EXE 改名。
此步骤不重复 npm/Rust 编译。独立工具和固件的产物路径分别报告。

所有输入通过检查后，WSL 执行：

```sh
sudo python3 tools/build-release.py package --workspace /home/kano/openwrt-build --profiles all
python3 tools/build-release.py status --workspace /home/kano/openwrt-build
```

两条内核发行线串行组包。成功的正式目录必须有 `BUILD-RUN.json`，校验 ZIP、
manifest、SHA-256 和单份 boot/rootfs。`.building-*` 不是正式包。
失败保留现场，按 LOCAL-BUILD 的 `clean-staging` 规则处理，禁止另写 worker。
打包期间不改源码；与 MU300 共用锁，不并行修改共享构建缓存。

## 交付标准

报告每个包的完整路径、构建提交、校验结果及失败项。原生 Mac 冒烟通过不等于
设备硬件验证；本次本地构建不得擅自连接或刷入用户机器。


## 在线保数据缩容工具

图形安装器的 F2FS 在线缩容使用仓库内置的静态 ARM64 小工具。
修改 installer/device/f2fs-online.c 后，在既有 WSL 工作区通过固定入口执行：

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build \
  --label online-f2fs-helper -- python3 installer/build-online-helper.py
```

构建器先跑原生 C 回归，再生成 installer/device/f2fs-online-arm64 和 build.json。
Windows/macOS 原生打包消费同一受源码哈希绑定的工具，不另找 Android Python，
不为此重编内核或复制 rootfs。源变化必须重编工具及两个原生安装器；禁止修改
指纹绕过。工作流、证据及硬件验收边界见 ONLINE-F2FS-INSTALL-20261029.zh-CN.md。


## 公共安装器子模块

installer/ 现在固定引用 https://github.com/kanoqwq/openwrt-tools 的提交。新检出先执行 git submodule update --init --recursive。共享源码、Android 及独立工具构建方法见 installer/docs/BUILD.zh-CN.md；E5/MU300 的 NV、UFS 身份和启动流程继续分开处理。请先在子模块提交，再在父仓库更新 gitlink；不要重新复制一整份安装器。

两个父仓库可通过 OPENWRT_GUI_ARTIFACT 与 OPENWRT_GUI_MACOS_ARTIFACT 复用同一份已校验交付件，无需各编译一次；首次拆分后旧 GUI 指纹应正常拒绝，不能修改旧 build.json 绕过。tools/release-index.py、tools/build-release-index.py、tools/package-diff-studio.py 保留为兼容入口，实际代码由子模块提供。


### 本机共用安装器交付件

当前统一的原生交付件在 D:/Git/e5-linux/installer/artifacts/windows 与 macos-arm64；MU300 原目录的旧交付件仅保留，不用于本轮打包。Windows/WSL 打包前显式设置下面两项（Windows 对应使用 D:/Git/e5-linux/... 路径）：

```sh
export OPENWRT_GUI_ARTIFACT=/mnt/d/Git/e5-linux/installer/artifacts/windows
export OPENWRT_GUI_MACOS_ARTIFACT=/mnt/d/Git/e5-linux/installer/artifacts/macos-arm64
```

这是已选定的共享输入，不是失败后寻找旧缓存；指纹不匹配仍应停止并重编真正变化的组件。两个子模块提交一致，原生交付件的 sources() 必须与各自检出内容一致。不要复制同一份 SDK、源码、Cargo target 或完整 runtime 到另一个构建目录凑路径。
