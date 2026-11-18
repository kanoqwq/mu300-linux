# 正常启动跳过串口等待与临时救援网络

2026-11-19，按维护者要求应用于 MU300、U30 Pro/F50 Pro、E5 全系列的 OpenWrt 根文件系统。

## 行为

`openwrt/overlay/lib/preinit/31_unisoc_fast_boot` 在普通启动时：

- 跳过 OpenWrt 的串口按键等待，不再初始化临时救援网络。
- 不清空已建立的早期 USB 网络地址；随后按原有 netifd/dnsmasq 路径接管。
- 不改变驱动、基带、USB 角色、充电模式、用户配置或 Android 自动回退计数。

带 `failsafe=` 参数启动时，该脚本不覆盖框架函数，保留 OpenWrt 原有的显式救援启动和网络处理。因此普通启动不再提供串口按键进入救援的时间窗，显式救援路径仍保留。

思路参考 MU300 main `833616a6c03a7c0245300c5946d98ec98b09bb94` 的 `31_mu300_failsafe_wait`，本实现增加显式救援分支，并避免普通启动清掉早期 USB 地址；不直接采用上游无条件禁止救援网络的设置。

E5 原有 `01_e5_boot_wait` 将等待缩到 1 秒，本次由新策略取代并删除。MU300 和 Pro 原先使用 OpenWrt 默认 4 秒窗口；不能声称三个系列都节省固定 4 秒。

## 交付

文件位于三个仓库各自已有的公共 OpenWrt overlay 中。MU300 完整包的必需文件清单、Pro 已组装根的源码比对清单、E5 根文件系统的复制后校验均要求该文件存在。普通更新和差分更新按固件文件交付，用户配置不放入这个目录；无需更新内核、模块或安装器。

`tools/test-preinit-fast-boot.py` 在现有 OpenWrt 25.12.5 ARM64 基底上读取实际 `10_indicate_preinit`、`30_failsafe_wait`、`99_10_run_init`；仅替换测试用 cmdline、临时文件路径和网络 syscall。正常启动对照必须先观察到原始倒计时和网络动作，再验证新策略全部跳过；显式救援则要求保留原网络动作。测试不操作真实网络或全局 failsafe 标记。

同一测试还通过实际 OTA compare/Engine 连续执行首次引入、后续更新，检查无线、网络、射频配置保留；普通更新分别执行 MU300/Pro 的实际 `apply_one`、E5 的实际 `keep_config`。无设备的最小交付测试不构建无关的安装器 bootstrap。

复用已存在的 OpenWrt 基底镜像执行：

```sh
# 通过本仓库 tools/build-release.py run 的固定入口调用：
env UNISOC_PREINIT_IMAGE=mu300-openwrt-base:25.12.5 python3 tools/test-preinit-fast-boot.py
```

这里的镜像名称仅是维护机显式选择的测试基底，不是固件运行依赖或适配门槛，不下载或复制完整根文件系统。

## 验证记录

三仓库各 4 项专项测试通过；MU300 原启动回归 19 项、Pro 原启动回归 12 项也通过。未实机启动 Pro 或 E5。

当前 F50 的前一轮日志：preinit 1.624 → 6.857 秒，约 5.23 秒；本轮：2.132 → 2.761 秒，约 0.63 秒。本机一次性采样记录：热点 14.20 秒、蜂窝 14.71 秒、HTTP 7.03 秒、RPC 5.93 秒，均从内核启动计时，采样是首次观察上界。这些单次结果不能当作所有型号或每次启动的固定耗时。

实机再次检查 AP-ENABLED、WAN、DNS、ping、LuCI 登录正常，USB 网卡保持 5000 Mbps，四份用户配置摘要不变。一次性采样服务和程序已移除；诊断及热部署回执保留于 MU300 仓库忽略路径 `work/boot-speed-20261119/`。本轮未重新打包或修改 Android 回退代码。
