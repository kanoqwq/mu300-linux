# U30 Air 充电开关通知与呼吸灯延迟

## 实机故障记录

2026-11-22，只读检查用户的 U30 Air。安装版本为
`mu300-r20261122.065604-gc0b0addb-sff0c6757`，Linux 7.2.8 #120，
boot ID `29d3a824-f4b4-4c64-a9b9-b2de011fb3ac`。
现场保存在 `work/diagnostics/u30air-led-live-20261122/`。

- uptime 114.68：BQ online=1、status=Charging，FGU 4%、status=Charging、
  current_now=212000；红灯 trigger=none，`.battery-applied=red:steady:1`。
- uptime 149.85：充电状态仍正常，红灯已经为 pattern，保存状态为
  `red:breathe:1`。期间没有部署、写 sysfs、重启或改配置。
- 用户随后确认实际红灯已经开始呼吸。

因此本次不是灯完全不能呼吸，而是充电状态变化后灯保留旧状态，直到原有
30 秒安全检查才更新。此前清除 SC27xx TYPE 位的修复保留，不能因这次
延迟就撤销已经生效的硬件模式修复。

## 修复

U30 Air 的 SGM41513 使用 BQ256xx 驱动且没有独立 charger IRQ。
原有 `POWER_SUPPLY_PROP_CHARGE_TYPE` setter 成功修改硬件和 `chg_off`
后没有调用 `power_supply_changed()`；已核对当前内核的 power_supply
核心和 sysfs store，它们不会替这个 setter 自动发送通知。

`0029-power-bq256xx-notify-charge-switch.patch` 在成功写入并更新
`chg_off` 后发布通知。失败直接返回，不改变保存状态、不发布成功事件。
已有 power_supply/extcon 事件接收及 sysd 的 500 毫秒合并调度负责更新灯。
不加新轮询，不改变充电限值、用户停充选择、USB/PD 或 OTG 策略。

## 离线验证

1. 对修复前实际准备的驱动 switch case 编译执行，成功写入没有通知的断言
   失败；日志 `20261122-073117-u30air-charge-event-confirm-baseline-68089`。
2. 两套实际驱动的同一代码提取测试均通过：各执行 100 轮暂停/涓流/快充，
   600 次成功或失败写入，成功事件 300 次；验证事件发生时保存状态已更新。
3. 7.2.8 和 6.18.54 的完整 Image、配套模块编译通过；日志
   `20261122-073152-u30air-charge-event-dual-kernel-68111`。
4. `test_u30air_leds.py` 21 项通过；`test_charge_boot.py` 8 项中 7 项通过、
   1 项按运行条件跳过。
5. 在已有 ARM64 OpenWrt 测试镜像执行实际充电 ucode：
   `test_charge_control.py` 12 项通过；日志
   `20261122-074152-u30air-charge-notify-policy-regression-85516`。
6. 实际 sysd/uloop 调度 6 项通过，新增用例从稳亮状态收到充电事件，
   在 30 秒兜底之前转入呼吸，并检查没有重复调度；日志
   `20261122-074550-u30air-charge-notify-final-event-matrix-86213`。

测试环境初次选错镜像架构、镜像没有 Python 或缺少测试所需的 util-linux
flock，均已修正测试依赖后重跑；不把这些环境错误当成固件故障或通过记录。
测试容器使用私有传感器、GPIO/命令替身，不操作真实设备。

## 交付状态

以上是离线回归和编译结果，不是新候选的实机验收。现场没有部署新候选。
06:56 的两个旧 ZIP 不包含本次 0029 通知补丁，后续需要通过正式打包入口
收录新的 Image、模块及其 APK 元数据，不能只换 boot 而遗留旧内核包身份。
