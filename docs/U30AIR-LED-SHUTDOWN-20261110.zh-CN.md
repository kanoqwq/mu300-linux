# U30 Air 电池灯、关机反馈与熄灯期限

## 已确认的问题

- 原 `battery()` 仅在 `Charging` 且低于 10% 时选择红灯，拔线后的同一电量会变蓝。现在充电与放电共用电量颜色：低于 10% 红色、10–49% 蓝色、50% 及以上白色；充电呼吸、放电常亮，充满保持白色。熄灯超时与用户禁用选项仍有效。
- `tick_hint()` 遇到已过期、尚未消费的唤醒期限，会将下一次检查改写为十分钟后。若电池事件在期限之后、sysd 执行全组熄灯之前到达，它只熄灭电池灯，随后 sysd 可能因状态未变而跳过全组熄灯。信号事件也可能独立熄灭信号灯，留下 Wi-Fi 灯。现在过期的期限仍作为到期工作保留，直到 `tick/sleep` 清理全部灯组。
- 电源键长按达到原有三秒门限后，原流程只有普通唤醒，没有关机确认。现在先调用 LED owner 锁定电池红色硬件呼吸，再发出关机请求。晚到的充电、Wi-Fi、超时和开机动画事件均不能覆盖它；旧开机动画会退出。关机命令失败则解除锁定并恢复正常灯态。
- 真实 OpenWrt ash 回归还发现其 standalone BusyBox applet 会抢先处理 `flock -w 2 9`，但 BusyBox 不支持 `-w`，使灯控退出。固件原本已安装 util-linux 的 flock，现明确使用该包的真实入口 `/usr/bin/util-linux-flock`，不依赖 alternatives 链接；Ubuntu 保持 `/usr/bin/flock`。保留两秒等待上限及同一把锁，不绕过并发保护，rootfs 构建同时校验该依赖。

## 实现与边界

正式 OpenWrt 入口为 `mu300-sysd`，兼容 shell 入口 `mu300-buttons` 同步修正；颜色、物理写入和锁继续归 `mu300-led` 所有。SC27xx 使用既有硬件 pattern trigger；缺少硬件曲线接口时使用现有 LED timer trigger。没有增加用户态呼吸循环、常驻进程或周期轮询。

改动仅针对 U30 Air 的现有三组灯，不改变 F50/V50 的灯语义、按键门限、充电参数或任何 NV。全部文件由现有 rootfs overlay 构建路径收录，6.18/7.2 两条发行线均生效，无需重编内核或按键二进制。

## 回归

`tests/test_u30air_leds.py` 覆盖充放电颜色边界、拔线、全组超时、关机硬件曲线、晚到事件、旧动画 token、关机失败恢复；`test_device_scripts.Buttons` 同时供 shell 和 ucode 兼容测试验证确认先于关机。

`tests/test_power_idle_scheduling.py` 用真实 ucode sysd、真实 LED helper、假 sysfs 和合成电池事件复现“期限之后先收到电池事件”的竞争，检查 Wi-Fi 随全组熄灭、随后不重复派生 tick。测试不访问设备，不实际关机。离线回归不等于已经验证所有硬件批次；用户反馈的现场尚未抓取。

本轮 shell LED/按键共 33 项通过；OpenWrt ARM64 容器中的实际 ucode 服务与按键共 8 项通过。后者以明确的首次 tick 完成握手控制事件顺序，避免模拟器调度快慢改变测试场景。日志分别位于 `out/build-logs/20261109-173121-u30-led-buttons-regression-19279/` 和 `out/build-logs/20261109-173921-u30-led-sysd-native-complete-40142/`。没有实机部署、关机或 NV 写入。
