# U30 Pro 本地构建规范

本仓库从用户的 MU300 `minikano-openwrt-feats` 分支创建，当前 `u30pro-bringup`。
这里构建 U30 Pro，不是 MU300/F50/V50/U30 Air；不能使用它们的 Image、模块、
SC2355 固件或单 boot 安装布局。硬件代码来自 [移植记录](U30PRO-PORT.zh-CN.md)。

## 固定环境

| 内容 | 路径 |
| --- | --- |
| Windows 源码 | D:/Git/u30pro_openwrt/mu300-linux |
| WSL 源码 | /mnt/d/Git/u30pro_openwrt/mu300-linux |
| Linux 工作区 | /home/kano/openwrt-build |
| 已有 Linux 6.12.94 源码 | 工作区 sources/u30pro-linux-6.12.94 |
| 已有独立对象缓存 | 工作区 objects/u30pro/release |
| 输入登记 | tools/build-layout.json |
| 日志 / 状态 | out/build-logs/、out/build-state/ |
| 后续正式交付 | release/ |

stock 三镜像和公开构建输入归档暂时仍在 `D:/Git/U30pro-openwrt` 的原路径，
**只读引用**，不执行旧迁移代码、不重新解压整份输入、不删除那个目录。
缺失输入必须报出，不能从 MU300 或 E5 包里补一个相似内核/根。
登记的现成对象缓存不等于新源已编译；修改驱动后要重编 Image 和完整模块。

## 检查与离线测试

在 WSL Ubuntu-22.04 执行：

```sh
cd /mnt/d/Git/u30pro_openwrt/mu300-linux
python3 tools/build-release.py plan
python3 tools/build-release.py check
python3 tools/build-release.py run --label u30pro-native-regression -- \
  taskset -c 14,15 nice -n 19 ionice -c 3 sh tools/test-u30pro-native.sh
```

`check` 校验源文件来源、显式输入、原厂三镜像哈希和 Android 槽来源；不访问实机。
测试使用合成 PTY 和临时目录，不连接真实 AT 端口或 NV。

内核编译入口（需要重编时再运行）：

```sh
python3 tools/build-release.py run --label u30pro-kernel -- \
  taskset -c 14,15 nice -n 19 ionice -c 3 python3 tools/u30pro-build.py kernel --jobs 2
```

内核 builder 在登记的现有源/对象树增量构建，使用 U30 Pro 配置断言。
不会寻找原作者 `/home/sunyi` 路径，不新克隆 Linux，也不创建第二份根文件系统。
`--configure-only` 可仅解析配置，但仍修改登记的配置/准备树，必须经过 `run`。
不要直接运行 native 参考目录中的历史 builder 或尝试它未导入的旧流水线。

## 发布状态

U30 Pro 采用 MU300 mu300root 容器，根文件系统内是 /openwrt，绝不采用参考库
的独立 overlay 或固定扇区。动态三镜像只适配启动硬件，原 Android 槽不写入。

准备依赖后的固定根 staging 分阶段操作：

```sh
python3 tools/build-release.py run --label runtime -- python3 tools/u30pro-runtime.py
python3 tools/build-release.py run --label sources -- python3 tools/u30pro-package.py sources
python3 tools/build-release.py run --label root -- python3 tools/u30pro-package.py finish
python3 tools/build-release.py run --label native-boot-test -- python3 tests/u30pro_boot_adapter.py
python3 tools/build-release.py run --label target-root-test -- python3 tools/test-u30pro-root.py
python3 tools/build-release.py package --label u30pro-preview
```

首次且 staging 不存在时，先在 runner 中运行 bootstrap、dependencies；不能在准备根
上重新解包基础归档。sources 仅刷新本工具所管理的源/模块；finish 物化配置和服务。
失败的 package staging 保留供检查，不得手动覆盖。发布只包含一个 rootfs 和一个
规范 boot，实际设备三镜像在电脑端使用本机模板构造。

Windows/Android/macOS 安装器复用登记目录下同一份经源码指纹和文件摘要校验的
产物；变化才重编，绝不调用旧目录的构建 worker。首版限制见包内 START-HERE。

不迁移到 C 盘，不重复 SDK/node_modules/Cargo/根文件系统，不自动删除缓存、
旧包、设备备份或失败 staging。Mac 安装器需原生 macOS CI 时再使用已获授权的
上游 CI，不寻找用户不存在的 Mac Agent。
