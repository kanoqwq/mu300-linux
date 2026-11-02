# 屏幕数据连接泄漏导致 LuCI 和信号显示异常

## 实机确认

2026-11-02 通过 USB 网络只读检查当前 U30 Pro：运行内核
`6.12.94-u30pro-p1`，开机约两小时。`ubusd` PID 479 已占 1023 个 FD，
软限制 1024；`u30pro-ui-feed` PID 2651 占 1008 个 FD。ubus CLI 无法建立
新连接，LuCI dispatcher 的 `ubus` 为 null，调用 session.get 时抛出异常。

原生 `u30-modemd` 和 MU300 AT owner 仍运行。蜂窝接口已有 IP 和默认路由，
RX/TX 计数持续增加。因此这次“无信号”首先是管理／屏幕数据获取失效，
不能据此判断基带已崩溃，更不能通过重置射频或基带处理这个总线泄漏。

## 原因和修改

屏幕 feed 每 3 秒（息屏 15 秒）调用共享 `dash.info()` / `dash.status()`。
这两个快照入口内部连接 ubus，却没有明确释放；在常驻 ucode 进程中，
不能依赖请求进程退出或 GC 及时回收资源。

- 快照允许借用调用者的连接／UCI cursor。屏幕 feed 将自己已有的长连接
  和配置 cursor 传入，不再逐轮新建连接。
- RPC/CLI 等不传连接的调用仍自行连接，在正常返回和异常路径均明确
  disconnect；不关闭借入的连接，保持原接口默认参数兼容。
- 不提高文件描述符上限，不定期重启进程，不改 LuCI 的 session 校验来
  隐藏总线故障，也不触碰基带加载、AT 顺序、NV 或用户网络配置。

两个生产文件已经在 `tools/u30pro-package.py sources` 的复制范围内；
`tools/test-u30pro-root.py` 逐字节检查这两个源与构建根是否一致。
无需重编内核／模块或原生面板二进制。

## 回归

`tests/u30pro_bus_lifetime.py` 在无网络、无硬件挂载的 ARM64 OpenWrt 容器
中启动真实 ubusd，客户端 FD 上限只设 64。覆盖 128 轮自建连接、128 轮
借入连接和配置、异常时的连接归属，以及去掉 disconnect 的失败对照。
第一轮连接初始化 uloop 的 epoll 和唤醒管道，所以测量在预热之后开始。
另有现有快照测试核对网页输出、SIM/APN、设备身份、温度与电池字段。

现场恢复只需原子替换这两个 ucode 文件，再结束旧 feed 进程，让 procd
按原配置重启它。关闭旧进程会释放积压的客户端连接；不需要重启 ubusd、
机器或基带。是否完成部署及恢复结果需以对应实机检查记录为准。

## 本轮授权部署结果

用户明确允许热部署并恢复屏幕数据服务后，已备份原来的两个脚本，
逐份上传、校验 SHA-256 和目标 ucode 编译，再同目录原子替换。
只向旧 feed PID 2651 发 TERM，由原 procd 配置启动新 PID 27563。

- ubusd PID 479 保持不变，FD 从 1023 降至 32。
- 新 feed FD 为 17，后续复查保持不变，`u30pro.panel.status.feed_ok=true`。
- 实际登录和概览页 HTTP 200，不再出现 dispatcher 的空对象异常。
- WAN up=true，原 IP／连接 uptime 保留；信号缓存读到 NR n28、RSRP 约 -85 dBm。
- boot_id 未变，基带加载器 PID 1076 未变；未触碰 NV／IMEI。

现有快照 17 项测试通过；低 FD 上限下真实 ubusd 的连接生命周期和
失败对照 4 项通过。脚本源码和发布验证入口已更新，尚未制作新发布包。
本机备份及部署收据保存在忽略目录 `work/u30pro-runtime/ubus-lifetime/`。
