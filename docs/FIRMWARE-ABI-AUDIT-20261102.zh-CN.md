# 固件版本与哈希门槛核查

用户要求：原厂固件版本众多，不得以整文件固定哈希作为兼容性门槛。

## U30 Pro 实际运行路径

- `devices/u30pro/overlay/etc/init.d/mu300-vendor` 启动原生 `u30-modemd`，
  使用本机固件槽、命名分区和配置映射，不调用 MU300 的 Android chroot
  `modem_control` 补丁器。原生基带加载源码未发现固定固件哈希白名单。
- `devices/u30pro/overlay/opt/mu300/bin/cpu-voltage-platform` 对两个本机 SML
  分区执行 `sml-voltage-check --domains 2`；识别 UMS9632 的指令与 SMC
  参数协议，不使用完整文件哈希决定是否支持。此次保留原生实现。
- SML 中 DHTB 自带摘要的动态校验是容器完整性验证，与某个已知版本比哈希
  不同。构建和导入的来源清单也只是核对输入未被调换，并非支持机型白名单。

## 遗留基底清理

基底目录还保留 MU300 专用 `mu300-modem-coldboot` 的旧哈希补丁器。
它不适用于 U30 Pro；`tools/u30pro-package.py` 现在明确不把它留在
U30 Pro 系统内，避免误走另一平台的加载路径。U30 Pro 仍使用原生 CP
服务，不添加第二个加载器或哈希失败后的回退。

继承的、被 U30 Pro overlay 替换的通用 CPU adapter 也去掉已知哈希捷径，
统一使用指令识别，避免后续维护误把固件版本当成兼容性条件。

本次只修改仓库及离线测试，没有写实机、NV、IMEI 或重启设备。
