# 纯 IPv6 承载、闲置 SDIO 与 SA 信号读取

本轮核对并修正上游 MU300 main 的三个 issue，按本分支现有框架实现，未直接
合入 main 的补丁。没有连接设备、热部署、切卡、刷写或重启。此前 SIPA 修正和
安装器子模块工作继续保留，不属于这三项改动。

## #495：纯 IPv6 承载

来源：https://github.com/dikeckaan/mu300-linux/issues/495 。报告提供了 CID 1
的 CGCONTRDP/CGPADDR 原始返回：IPv6 地址以 16 字节点分形式表示，地址与掩码
合并时是 32 字节；开头可以全零，只在接口标识中非零。不能按 IPv4 首四字节
判断无地址，也不能把这个接口标识当成完整的公网 /64。

- 新增共享 `pdp-address.sh`，拨号和现有事件刷新使用同一解析器，合并同 CID
  的 IPv4/IPv6 记录，转换点分 IPv6 DNS。解析完整成功后才提交变量，不执行
  AT 返回里的任何文本。未知地址、非法 DNS 或不连续 IPv4 掩码不更新连接。
- 拨号保持 CGACT、CGCONTRDP、CGDATA 的现有顺序。网络只授予 IPv6 时，输出
  运行时实际类型 IPV6，不改持久 APN。netifd 不再要求 IP= 非空，也不安装
  空 IPv4 地址和默认路由，仍由 RA 获取公网前缀、地址与默认路由。
- CGEV/renew 继续由现有事件 owner 处理，NR0 是唯一 URC 入口。IPv4 → IPv6
  时先成功报告，再移除旧 IPv4；IPv6 → IPv4 保留原有先添加再清理方式。
  看守读取有效的纯 IPv6 授予时不再因缺少 IPv4 反复 renew/重拨，未知返回
  也不当作明确断线。不新增定时轮询、重置射频或竞争端口的 reader。
- APN 页面、协议选项、校验和 CGDCONT/初始 APN 配置一起支持 IPV6。
- MU300 和 Pro 使用同一用户态修正。E5 的 ModemManager 拨号实现不同，未把
  MU300 拨号脚本塞给它。

边界：本项修复原生 IPv6 拨号和维持连接。它不等于提供 464XLAT 的 IPv4 服务；
IPv4 字面地址及只提供 IPv4 的应用仍需 CLAT。没有把报告中的固定 NAT64 前缀、
tayga 示例、定时重拨或静态公网地址带进系统，也没有宣称这些应用已可用。
本轮发布路径是 OpenWrt/netifd，不把旧 systemd 示例称为已验收发行版。

## #385：闲置 Wi-Fi SDIO

来源：https://github.com/dikeckaan/mu300-linux/issues/385 。上游评论明确指出
报告中的 7.2.8 旧构建来自本分支。设备为 F50 存储版，实际 Wi-Fi 是 PCIe，
闲置的 `sdio_wifi` 控制器仍被启动并不断探测，引起命令中断和寄存器日志。

在共享准备脚本调用小型 `sdhci_hosts.py`：仅对 machine compatible 为
`sprd,ums9620`、节点 `sprd,name=sdio_wifi` 的组合，在分配/注册 host 前返回
ENODEV。没有关闭 SDHCI 驱动或屏蔽中断日志。eMMC、TF、未知控制器继续原流程。
6.18/7.2 共用此入口，已准备的缓存也会获得修正。Pro 的 AIC SDIO 和其他平台
不适用，不修改 Pro/E5 的存储适配器。

## #471：SA 信号

来源：https://github.com/dikeckaan/mu300-linux/issues/471 。信号字段参考
[3GPP TS 27.007 / ETSI V17.10.0，8.69](https://www.etsi.org/deliver/etsi_ts/127000_127099/127007/17.10.00_60/ts_127007v171000p.pdf)。
CESQ 后三字段是 NR SS-RSRQ、SS-RSRP、SS-SINR，255 表示未知；LTE 字段在 SA
可以为 255。状态 8 的紧急业务语义不能为显示信号而改成普通注册。

- 三仓库公共 cell 收集器补齐九字段 CESQ，正确区别 LTE/NR、未知和边界值。
- 保留原注册状态。仅对 CFUN=1 且状态 8 具有有效 NR AcT/TAC/CID 的响应
  放行只读工程信息查询，邻区和 QoS 使用同一查询条件；不因此放行拨号、
  漫游或射频操作。无服务、缺少小区、旧制式紧急状态不增加工程 AT 查询。
- 同时修正原 `reg_fragment` 的 C5GREG 前缀去除，避免将命令名前缀误作状态。

## 回归

运行命令必须通过各仓库 `tools/build-release.py run`，使用现有工作区。

- `tests/test_pdp_ipv6.py`：实际解析器、fetch_addr 和 up_locked，覆盖接口标识、
  点分 DNS、同 CID 多条记录、不同 CID、坏地址/掩码/DNS，以及实际拨号顺序。
- `tests/test_cellular_mobility.py`：原有移动性/双卡/重试用例，加纯 IPv6 setup、
  IPv4 → IPv6、稳定无 IPv4 不重复更新、看守不重拨。
- `tests/test_cell_signal.py`：三个仓库生产函数，九/六字段、未知/边界值、
  C5GREG JSON、状态 8 不伪造注册、无服务和射频关闭不增加查询。
- `tests/sdhci_hosts.py <prepared-kernel>`：仅复制一个实际 SDHCI 源文件到临时
  夹具，验证准备适配幂等，并编译生产 guard 跑 15 组平台/控制器组合；
  ASan/UBSan；分别使用既有 6.18.54、7.2.8 准备源，不修改原缓存。
- 既有 APN、看板数据与分阶段收集回归；Pro 原生检查入口及目标根核对清单
  已登记新解析器/测试。

离线回归不代表纯 IPv6 运营商实测或硬件中断验收；本轮没有重编完整 Image、
模块或发布包，不能把旧发行包当作已经包含此修正。

最终记录（仓库各自的 out/build-logs/）：

- MU300：`20261114-081915-ipv6-sdio-sa-final-regression-71700/command.log`，
  58 项中 55 通过、3 项因本机缺少 ucode runtime 跳过。
- Pro：`20261114-081732-ipv6-sa-pro-final-65509/command.log`，
  同样 55 通过、3 跳过。
- E5：`20261114-081658-sa-signal-e5-65095/command.log`，4 项通过。
- MU300 ARM 容器 APN：`20261114-081822-apn-v6-ucode-71678/command.log`，
  实际 ucode/uci 模块累计 92 项检查通过。
- MU300 7.2：`20261114-081217-sdhci-unused-hosts-50460/command.log`；
  6.18：`20261114-081357-sdhci-618-unused-hosts-52223/command.log`；
  两份实际准备源的 guard 各 15 组情况通过，原缓存保持只读。

真实拨号函数夹具最初遗漏 radio_say stub，导致测试失败；补齐隔离夹具后
最终回归通过，没有为通过测试更改生产行为或放宽校验。
