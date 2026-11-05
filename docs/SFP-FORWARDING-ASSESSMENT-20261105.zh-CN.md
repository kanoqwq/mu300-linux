# MU300 / U30 Air 的 SFP 与 300 Mbps 报告核查

前半部分为静态核查；随后用户连接 U30 Air 并授权核查，实机结论见下一节。
初次核查未改生产代码或持久配置。随后用户授权修复，已接入三个仓库并热部署 U30 Air；未验证 300 Mbps 吞吐上限。

## 后续实机核查：软件 flowtable 存在确定接入缺口

设备为用户的 U30 Air UFS，运行 `7.2.8` / `mu300-r20261027.200255-g0043a5d8-sc0b45b4c`，
已运行约 43 小时，Wi-Fi 为 5 GHz 信道 149 / VHT80，蜂窝使用 `sipa_eth0`。
软件 offload=1，硬件 offload=0，TTL 未启用；不是“编译没开 flowtable”。

原 flowtable 设备仅有 `br-lan`、`docker0`、`sipa_eth0`；LAN 下实际端口为
`usb0`、`wlan0`。通过只读 ctnetlink dump 查询状态位，基线未见 OFFLOAD。
随后用电脑绑定 USB 接口建立到公开 HTTPS 服务的正常 TCP/TLS 连接，确认
TCP ESTABLISHED、已双向通信且有 NAT，仍未 OFFLOAD。这一步只传握手/请求级
流量，没有使用不受控大文件测速或改电脑默认路由。

短时独立 tracefs instance/kretprobe 捕捉到了建表失败：保存的环形记录中
`nft_flow_route` 1641 次返回 `-2`（ENOENT），未进入 flow_offload_alloc/add。
7.2 的 [nf_flow_table_path.c](https://github.com/torvalds/linux/blob/v7.2/net/netfilter/nf_flow_table_path.c)
在实际网卡路径不在 flowtable hook 列表时返回失败；不同于较老版本的宽松退回行为。

短对照仅向已有 flowtable 临时加入 `wlan0`、`usb0`，保留 counter：

- 两条新建受控 TCP/TLS 连接都出现 `IPS_OFFLOAD`，状态值从 414 变为 16798。
- 同期手机已有连接也开始进入软件 offload，没有安装 SFP/SFE 或修改内核。
- 移除临时设备后核对 flowtable 与原始文本完全一致，保持软件 offload=1、TTL 不变。
  所有本轮 tracefs instance 和 kprobe 均已移除，WAN、热点仍正常。

这确认当前这台 7.2.8 包的 fw4 设备发现存在缺口。修复方向应优先补齐网桥实际
成员和正确的动态蜂窝 l3_device，替换当前只补固定 sipa_eth0 的逻辑；不能另堆一套
硬编码设备补丁。6.18/E5/Pro 的内核版本和路径不同，需要分别检查，不能把这台
机器的结论直接当成全机型硬件验收。

用户说明当前信号弱，本次没有验证修复后的最高吞吐，也没有证明 EDMA 反压、
全部速率问题或 300→700 Mbps 的因果关系。实机证据保存在忽略目录
`work/sfp-audit-20261105/`，不作为生产构建输入。

## 静态源码核对

- 两条当前内核缓存 `/home/kano/openwrt-build/kernels/mu300-{7.2.8,6.18.54}/kernel.config`
  都开启 `CONFIG_NF_FLOW_TABLE=y`、`CONFIG_NF_FLOW_TABLE_INET=y`、`CONFIG_NFT_FLOW_OFFLOAD=y`。
  正式源配置 `upstream/mu300-mainline.config:280-282` 同样开启。
- `openwrt/overlay/etc/uci-defaults/90-mu300:70-73` 默认启用软件 flow offload，
  硬件 flow offload 关闭。不能把当前实现概括为所有报文都必然走完整 Netfilter 慢路径。
  这些是构建/默认配置证据，不是群友当前连接已经命中的证据。
- 用户启用 TTL/hoplimit 改写时，`ttl-engine` 会保存并关闭 fw4 offload，
  因为快路径绕过相应 postrouting 改写；关闭该功能时恢复自己关闭的 offload。
  本轮不改变用户选择或此行为。
- 6.18/7.2 当前没有移植原厂 `sfp_core`；其 SIPA 网卡有标准网络栈/GRO 收包路径。
  SIPA 数据通路存在不等于已打通原厂完整的 AP 转发卸载。

## 原厂 GPL 源核对

原始 Enceka 仓库本次公开读取返回 404，读取了标明镜像来源的
[dikeckaan/zte-ums9620-kernel-5.4.254 b50db5b6224c11db42a246e8fcdaad1e1d59f478](https://github.com/dikeckaan/zte-ums9620-kernel-5.4.254/tree/b50db5b6224c11db42a246e8fcdaad1e1d59f478)。

- SFP 实际目录是 `drivers/soc/sprd/sfp/`；Makefile 将其编为 `sfp_core.o`。
- [Kconfig](https://github.com/dikeckaan/zte-ums9620-kernel-5.4.254/blob/b50db5b6224c11db42a246e8fcdaad1e1d59f478/drivers/soc/sprd/sfp/Kconfig)
  区分 `SPRD_SFP_SUPPORT` 和 `SPRD_IPA_SUPPORT` 的硬件路径，后者默认关闭。
  仅有 SFP 模块不证明硬件转发已启用。
- 该快照 `net/core/dev.c` 未发现 SFP/fastpath hook；公开代码索引中的
  `soft_fastpath_process` 引用在 SFP 自身实现、头文件及测试。报告声称的 dev.c
  补丁不能用这份源码直接验证，仍需对应固件的实际实现。GPL 镜像不能替代其设备日志。
- 留存的 F50 B09 `kernel/device.config` 是 SFP=m、SPRD_IPA_SUPPORT 未启用。
  这不是对所有 U30 Air 批次原厂固件的硬件卸载状态判断。

## 分析边界与下一步

[Linux flowtable 文档](https://docs.kernel.org/networking/nf_flowtable.html)明确描述了现有软件
快路径和硬件卸载的区别。[Shortcut-FE 原说明](https://github.com/waau/qualcomm-sfe/blob/master/README)
也属于内核软件转发引擎，不能称为硬直通或保证移植后到 700 Mbps。

25,000 PPS 按每包 1500 字节换算约 300 Mbps，只是算术对应，不证明 CPU 达到瓶颈；
一次 EDMA 解除流控耗时也不证明加速命中率或整条链路极限。缺少群友测速期间的
每核软中断、频率、报文大小、flowtable 实际命中、无线聚合/重传和前后对照。

先区分本机 iperf3/HTTP 与蜂窝到 Wi-Fi 的转发；前者并不经过同一 FORWARD/NAT
路径。对转发现场先核实 fw4 实际生成的 flowtable 设备、conntrack OFFLOAD、
TTL/SQM 等配置及 CPU/无线队列负载，再确定是现有快路径未生效、无线瓶颈还是
软件转发本身需要进一步优化。不根据本报告直接新增 SFP/SFE 或保证吞吐。

## 已落地的公共修复与回归

- MU300（7.2/6.18）、E5 全机型及 U30 Pro/F50 Pro 使用同一份
  `tools/stage-flow-offload.py` 源码转换：按当前防火墙 zone、网桥/VLAN 实际成员和
  活跃逻辑网络的 `l3_device` 生成软件 flowtable 端口，不固定某一个 SIPA 编号。
- 删除 MU300/Pro 原 `fw4-sipa-offload.patch`，由新实现完整替换；Pro 的增量构建
  显式迁移旧补丁记录。E5 的全新根构建和公共机型根构建均已接入。
- 不更改软件/hardware offload 的用户配置，不绕过 TTL 对加速的控制，不新增常驻
  服务、轮询或内核 hook。已有的硬件加速探测路径保留。
- 检查了现有 MU300 两版内核源码/配置：7.2 的实际端口缺失会使建流返回 ENOENT；
  6.18、E5 6.18、Pro 6.12 的旧路径处理不同。公共发现逻辑有缺口不等于已证明
  每一款旧内核设备均完全不能加速，不能把 U30 Air 7.2 的实测外推为全部硬件验收。
- 每仓库 `openwrt/tests/flow-offload.py` 的 10 项实际目标 ucode 测试通过，包括
  完整 fw4 模块编译、桥/VLAN、动态 PDP、双栈网络、去重、关闭加速、硬件路径、
  拓扑错误和旧实现迁移；每仓库 9 项 TTL 回归通过。E5 另使用真实公共 base 的
  fw4 文件验证转换，Pro 使用实际组装根完成 `test-u30pro-root.py`。

U30 Air 正式热部署后，`fw4 check` 通过，软件 flowtable 为
`docker0/sipa_eth0/usb0/wlan0`。两个新建 IPv4 TCP/TLS 连接均有 `IPS_OFFLOAD`，
同期手机 97/98 条连接进入软件 offload；单独的 IPv6 HTTP 请求也收到应答并有
`IPS_OFFLOAD`。这些是命中验证，不是带宽或省电提升测量。

Wi-Fi 正常 reload 及 down/up 重建均完成，后者 ifindex 从 25 变为 26，已配置
端口仍在 flowtable，USB 转发重新建流再次命中。内核 nft_flowtable 的
NETDEV_REGISTER/UNREGISTER 按名称重新挂接端口，不额外增加重连守护程序。
热点恢复原 SSID、信道及带宽；防火墙、网络和无线配置哈希与部署前一致，蜂窝
仍 up。本轮未重启基带或改 AT/NV。手机在热点重建后的无线重连与峰值吞吐未另做
人工验收；其重建前的无线命中有实测证据。

结论：先交付和观察现有 fw4 软件快路径修复。若之后确认连接已命中，转发负载
仍使 CPU 饱和，再评估 SFP 等其他引擎；没有证据支持现在把 Android 5.4 SFP
直接移植为必需依赖。设备本机 iperf3/HTTP 与蜂窝 FORWARD 转发不是同一条路径，
本修复不承诺解决本机测速服务或无线本身的所有速率波动。

## 用户要求恢复实机基线

上述热部署验收结束后，用户为后续 diff 更新明确要求撤回实机补丁。已将 U30 Air 的
fw4.uc 恢复为部署前备份，SHA-256 为
19446671105fe885fdd850e14a7884b93dcdee821737c322c17ca1f6513abec3，
fw4 check/reload 通过；原 flowtable、网络/无线/防火墙配置与基线一致，蜂窝仍 up。
仓库源码修复保留，等待后续正式包或 diff 交付。此恢复是用户明确要求，并非发现
故障后的推测性回退。记录在 work/sfp-audit-20261105/user-requested-restore.json。
