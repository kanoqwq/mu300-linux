# eSIM 管理（实体 eUICC / lpac）

入口为「蜂窝 → eSIM 管理」。管理插在实际 SIM 卡槽内的 eUICC；贴片／内置 SIM
这个硬件名称本身不代表 eUICC，须成功读取 EID。普通 SIM 不会被转换成 eSIM。

## 功能与边界

- 显式选择现有逻辑 SIM，并显示当前卡源名称及对应 IMEI 序号；不修改卡板映射。
- 读取 EID、卡片版本／空间及配置列表，下载、启用、停用、重命名、删除配置。
- 激活码支持粘贴及本地二维码图片识别；确认码可选。下载不会自动启用新配置，
  不传 IMEI。删除需先停用；启停、删除和下载需输入小写 yes。
- 提供待处理运营商通知的计数与发送入口；发送成功才清除对应通知。
- 简体中文、英语、土耳其语，适应 LuCI Bootstrap／Aurora 及移动布局。

后端只走已经存在的 AT owner／队列，每条命令显式选择目标 SIM。不会读取串口，
不新增 NR0 读者或按 SIM 去监听不同 URC 通道。默认 AT+CSIM；高级选项可明确
选择 CCHO/CGLA，不在失败后偷偷换通道或重置基带。

每次 lpac 调用分配独立 ISD-R 逻辑通道并在结束时关闭。不使用 CPIN 拦截空卡，
因为没有启用 USIM profile 的 eUICC 仍可能允许访问管理应用。只开放固定的 ISD-R
应用，没有向网页暴露任意 APDU、任意 AT、清空卡片或 IMEI 写入接口。

任务按需运行，没有常驻采集进程。网页只在任务活跃时查询 RAM 状态；浏览器先
生成任务 ID，再提交动作。超时／刷新只恢复原任务查询，不重发下载或删除。
修改后回读确认；已提交但回读失败会明确要求重新读取，不宣称成功或自动重试。

MU300 V50 和 Pro 的物理卡源事务与 eUICC 任务互斥，避免 APDU 中途更换卡片。
不改变原有射频锁、PID／父进程所有权或正常命令顺序。记录仅放在私有 RAM 目录，
不保存激活码、确认码或 APDU 原文。HTTPS 验证服务器证书，不自动降级 HTTP。

## 来源与 lpac 修正

协议实现来自 [estkme-group/lpac](https://github.com/estkme-group/lpac) v2.3.0，
按其 OpenWrt 包源码的 SHA-256 校验，不把第三方 Windows 参考程序作为固件输入。

2.3.0 的 stdio APDU connect、logic_channel_open、transmit 三处把成功返回值
反向判断，错误分支另有空指针清理问题。跟踪的 `openwrt/lpac-stdio.patch` 通过 OpenWrt 包补丁栈应用修正，
`patch-lpac.py --verify` 在 prepare 和 compile 后均核对结果，防止重新准备源码时丢失修补。
不匹配的源码会停止。发布使用独立 r1001 APK，不能用未修补的 r1 代替。

二维码识别使用 jsQR 1.4.0，源码包下载时核验 npm SHA-512 integrity，脚本和
许可证随页面提供，不依赖外部 CDN。lpac 的许可证和源码来源沿用上游包；本仓库
保留具体补丁及构建入口，供同版本重建。

## 构建

三个仓库共享同一份 lpac 构建脚本及 APK，不复制 SDK、内核或 rootfs。
`tools/build-layout.json` 中的 lpac_apks 指向 `{workspace}/artifacts/lpac`。
原有 e5-openwrt-src SDK volume 同时供 hostapd、lpac 使用，两者共用 SDK 锁。

在现有 Linux 工作区，先通过本仓库受锁入口执行：

```sh
python3 tools/build-release.py run --workspace /home/kano/openwrt-build \
  --label lpac -- sh openwrt/build-lpac.sh /home/kano/openwrt-build/artifacts/lpac
```

然后按本仓库正常 check／package 或 Pro build 流程操作。Pro 的公开 build 入口会
自动准备该依赖；单独构建根文件系统时可用 UNISOC_LPAC_APKS 显式选择登记目录。
版本、源码指纹及 APK 摘要不一致必须重建，不允许修改收据接受旧包。

根文件系统组装会安装 r1001，并实际执行仅列举驱动的 lpac driver list，确认
APDU／HTTP stdio 都存在；此检查不访问卡片。准备根的收据还记录实际二进制摘要，
E5／Pro 的根检查核对页面、RPC、ACL、菜单和二维码脚本。

## 已做与待做

2026-11-10 离线：用核验过的 2.3.0 源码及同一补丁编译 Windows 参考程序，
模拟卡驱动完成读取、启停、重命名、删除与通知回读测试。三个仓库真实网页及各自
common.js 已检查三种语言、三种主题和三种宽度，并测试 yes、二维码、权限及超时
恢复。构建收据的过期／篡改及缺驱动检查另有测试。

2026-11-10 ARM64：现有 SDK 编译 r1001，在生产基础镜像中运行实际 ARM64 lpac，
30 项离线回归通过，包括模拟卡读取、配置修改、通知、确认预览和真实进程锁；没有
把模拟卡修改当作真实卡验证。E5 屏幕的任务适配另外验证卡身份、选择项和断线恢复。

同日 U30 Pro（board_id=2，卡槽 2／逻辑栈 1）只读实测：通过已有 NR5 owner 队列，
每条命令显式 SPACTCARD=1，读到 9eSIM v0 的 EID 尾号 8072、卡固件 4.2.0、
可用空间 453669 字节，已有一个已启用的 RedteaGO / RTG_Android 配置（ICCID 尾号 5373）。
探测在控制端运行同源修补 lpac，通过 SSH 调用现有队列；未上传或安装软件，未下载、
启停或删除真实 profile，未修改卡源、NV 或 IMEI。运营商下载和切换后的联网仍待实测。

## E5 信息屏及第三方来源

E5 信息屏内置 eSIM 管理应用；网页、屏幕共用任务、身份检查、进度和互斥锁。
支持卡片／配置详情、选择安全元件、配置增删改及启停、下载预览和取消、通知管理。
缺依赖时提示安装完整固件，不从屏幕后端偷偷安装包。切换配置不会自动重启基带；
若模组信息尚未更新，由用户通过 SIM 管理按原有流程刷新。无活跃任务时不轮询卡片。

屏幕 UI 及写入进度解析参考：
- [XiaoMengXinX/e5-webapp-esim](https://github.com/XiaoMengXinX/e5-webapp-esim/tree/11b10358c0c140357cea33697530b277babd0f8c)
- [XiaoMengXinX/luci-app-e5-esim](https://github.com/XiaoMengXinX/luci-app-e5-esim/tree/6fb6b3189df8981b40b720f985fb36500103d460)

两者采用 MIT，版权为 2026 Enceka <enceka@yeah.net>。完整许可证随应用安装，
固定来源见屏幕目录 ORIGIN.json。适配保留现有 AT 管理，不导入独立串口读者、
后台安装器或未确认的射频重置逻辑。简体中文、英语、土耳其语随源码提供。

验证入口：`openwrt/tests/euicc.py`（30 项协议／任务）、`euicc-screen.py`（8 项屏幕适配）、
`euicc-build.py`（5 项构建收据）、`euicc-browser.py`（每仓库 27 种布局／语言／主题，
另含确认、二维码、权限、断线、SE 选择、下载预览与取消）。E5 屏幕浏览器测试为
`devices/common/infoscreen/tests/esim-frontend.cjs`；生产 ucode 已通过 ARM64 编译检查。
这些测试使用模拟卡／RPC；真实卡的读信息证据与未验证的写操作已在上文区分。

## GSMA TLS 信任根（2026-11-10 修复）

真实 U30 Pro 下载停在 `es9p_initiate_authentication`，任务返回 `server_tls`；
OpenSSL 明确返回无法找到本地颁发者。设备时钟正确，服务器证书由生产
GSM Association - RSP2 Root CI1 签发。这次失败发生在服务器 TLS 握手，未进入写卡。

普通 Web CA bundle 不含该 eSIM 根。现随源码提供公开 GSMA 根证书，来源、固定版本
及 DER 指纹记录在 `usr/share/unisoc-euicc/ca/README.md`。仅在 eUICC HTTP 上下文
中扩展系统 CA，保留域名、证书链、有效期与用途校验，不改变系统信任库，也不自动信任
测试根／服务器自己提供的根。根文件缺失或损坏返回 `server_ca`；组装审计核对证书
和来源文件，不能漏进成品。下载失败还会通过顶部 toast 提示，不只改变状态行。

参考项目的 GnuTLS/libcurl shim 用于其 mbedTLS 兼容问题；本实现使用 Python/OpenSSL
stdio HTTP，不加载该 shim。实际设备 Python 3.13.15 / OpenSSL 3.5.9，在内存中加载
该根后，通过相同 TLSConnection 完成目标服务器的 TLS 1.2 认证握手；没有重试激活码、
发送下载请求或改动 profile。完整下载及安装仍需用户自行提交后的真实验证。

## 下载预览确认的 stdio 边界（2026-11-10）

修复 CA 后，实机可以读取到配置元数据，确认后任务却以 `lpac_failed` 结束，最后阶段
为清理用的 `es9p_cancel_session`。lpac 2.3.0 的 `src/applet/profile/download.c`
通过 `getchar()` 消费一个确认字符；后端此前写入 `y\n`，遗留换行会被后续 APDU
stdio 的 `afgets()` 当作空 JSON 回复。因此确认／取消只发送一个 `y`／`n` 字节。

修正了原先按整行读确认的模拟子进程：按单字节读取后立即执行下一条 APDU JSON
交换。旧实现能复现同样的 `lpac_failed`，修复后确认与取消均通过。33 项协议测试中
Windows 参考 lpac 的读写回归也通过，两个 Linux flock 用例在 Windows 跳过；这不是
真实运营商 profile 安装成功的证明。激活码由用户自行重提，不自动重放失败的下载。

## 大量配置的列表界面

参考 MIT 项目 luci-app-e5-esim 的配置表格与“管理”入口，将逐配置大卡片改为紧凑行。
桌面列出名称、运营商、ICCID、状态、管理；手机展示为紧凑行，长名称省略并保留完整提示。
点击管理后选择启停、重命名或删除；启用中的配置不提供删除，修改仍经原有身份核对与确认。

默认每页 10 条，可选 20／50 条，支持按名称、运营商、ICCID 搜索及状态筛选。
搜索、翻页只处理已经读取的列表，不触发 AT、卡片读取、后台轮询或修改卡片；切卡／SE
重置筛选上下文，刷新保留页码并按剩余结果收敛。新增文字和可访问名称均有中英土三语。
`openwrt/tests/euicc-list-browser.py` 以 125 个模拟配置测试三种语言与三种屏宽，覆盖
分页边界、筛选、土语大小写、长名称、启用配置删除保护，以及管理弹窗期间切卡的拦截。
