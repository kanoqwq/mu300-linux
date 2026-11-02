# U30 Pro 第一屏与开机界面交接

用户报告：原厂第一屏后有一段黑屏，再出现原生 U30 Pro 开机界面。

## 已确认的源码问题

原生 NV3002C 驱动在 probe 中申请 reset 输出高、AVDD 和 BL_EN 输出低；
即使最后因背光驱动未就绪而 deferred probe，也已经切断显示。注册的 fbdev
客户端还会在原生 KMS 界面启动前提交零填充画面。屏幕服务此前 START=95，
且运行循环先 collect_all()，再提交第一帧。

这些路径能够主动清空／熄灭第一屏；不能据此断言用户看到的全部空白都发生
在这些阶段。原厂启动器、bootstrap/kexec 和面板复位期间的实际交接仍需实屏确认。

## 正式实现

- 面板 probe 使用 GPIOD_ASIS 保留固件 GPIO 状态；不再注册自动提交黑帧的
  fbdev 客户端。本机生产界面使用 KMS，息屏与唤醒使用 connector DPMS，
  去掉了冗余的 fb0 unblank 写入。
- 首次真正的 KMS enable 时才取得 GPIO 输出方向，暂时关闭 BL_EN，执行原有
  原厂 reset/init 时序，写入完整画面后再开灯。不是跳过面板初始化；GPIO 失败
  会明确报错。正常息屏、唤醒的 display-off/sleep-in/reset/init 流程保留。
- u30pro-panel START=14，在硬件节点与 CP 服务初始化之后异步启动；不等待
  注册、拨号或 feed。启动时先提交原生开机画面，再读取运行时状态。
- 正式 root 组装启用新的 S14 链接，并清理该服务自己遗留的 S95 链接。
  不更改其他服务的 AT 顺序，不添加固定等待时间，不移植其他机型屏幕代码。

## 构建与离线验证

通过统一 build-release.py 入口增量编译 6.12.94-u30pro-p1 的 Image／模块及
原生屏幕 ELF，已完成 sources、finish 和 test-u30pro-root.py。校验包括实际
目标 ELF 依赖、服务 shell、原生来源哈希与 root 一致性，以及启动链接迁移、
重复组装、冲突项保护、procd 异步启动回归。测试不打开真实 DRM／AT／NV。

源码与本地适配哈希在 devices/u30pro/adaptations.json；内核和运行时构建回执
位于登记的共享工作区。单机部署备份与日志位于 work/u30pro-runtime/display-handoff，
它们不参与正式发布输入。未经实屏确认，不宣称已实现全程无黑帧。

## 本机试验结果

用户已明确授权应用并重启。写入前备份当前 Linux A boot 与屏幕／模块文件，
传输后及写入后校验 SHA-256；260 个运行时文件校验通过，Android B boot 的
SHA-256 保持不变。未写 GPT、userdata 或 NV。

正常重启后 boot_id 已改变，原生屏幕 RPC 恢复，启动链接为唯一的
S14u30pro-panel。用户实屏确认：**黑屏已消失**。后续独立的息屏／唤醒 RPC
复测连接超时，尚未执行该轮操作，不能将它记为本轮实机验收通过。
现有原厂 reset/init 与 DPMS 路径保留，离线目标根验证通过。
