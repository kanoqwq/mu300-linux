# U30 Pro OpenWrt（MU300 维护分支基底）

这是独立的 U30 Pro 源码移植仓库，基底为 `kanoqwq/mu300-linux` 的
**minikano-openwrt-feats** 分支 `29061dc8`。当前工作分支：`u30pro-bringup`。

采用 MU300 的公共看板、管理与工具框架；U30 Pro 的 Linux 6.12.94、
AIC8800 SDIO、240×240 原生屏幕及基带/电源驱动来自其干净参考仓库。
原 MU300、E5 和用户提供的参考目录均不修改。

**当前为首版 preview，完整包见 release/；硬件尚待验收。**
使用 MU300 的 mu300root 分区布局和安装流程，只替换 U30 Pro 驱动、原生圆屏
及启动容器适配。不能直接刷包内未绑定的 boot-linux.img。

- [移植范围、已做内容、剩余工作和实机边界](docs/U30PRO-PORT.zh-CN.md)
- [本机构建目录与检查入口](docs/LOCAL-BUILD.zh-CN.md)
- [Agent 构建指导](docs/AGENT-BUILD.zh-CN.md)
- [U30 Pro 明确的平台配置](devices/u30pro/profile.json)
- [逐文件硬件源码来源](devices/u30pro/reference.json)

安装器继续通过 `installer/` Git submodule 固定引用
[openwrt-tools](https://github.com/kanoqwq/openwrt-tools)，不复制 SDK 或完整安装器镜像：

```sh
git submodule update --init --recursive
python3 tools/build-release.py plan
python3 tools/build-release.py check
```

检查及编译在 WSL 的现有 `/home/kano/openwrt-build` 工作区进行。
`check` 不刷写设备；编译/离线测试必须通过 `build-release.py run` 获取工作区锁并记录日志。
硬件试启动、分区和刷写需单独获得用户授权；不使用 fastboot 或 SPD。

U30 Pro 专用源保留原作者版权；内核驱动按各文件自身的 SPDX 许可证使用。
