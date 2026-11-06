# SIPA 授权与队列唤醒顺序

适用两条发行线共用的 `upstream/modules/sprd_modem/sipa_v3`，运行时覆盖
MU300、F50、V50、U30 Air。Pro 属于独立仓库，不能直接复制其驱动覆盖这里。

源码确认两个同类竞态：

1. `GRANTED` 可以在 `request_resource()` 返回前完成。迟到的
   `-EINPROGRESS` 曾重新写回 `request_in_progress=true`，下次发包会等待
   已经发生过的授权通知，导致停发。
2. 唤醒路径在队列锁之外检查“队列是否已停止”。授权回调可能先看到队列运行，
   略过唤醒，发送路径随后才停止队列，再无通知可用。

现不覆盖已完成的授权；立即授权清除 RM 等待，申请错误保留错误并允许下次申请。
Ethernet 的条件检查／停发和回调唤醒共用队列锁。SIPA USB 发送也在同一锁下
重新核对 RM 状态，避免仅凭过期错误停发；分配失败和硬错误正确释放 skb，
返回 BUSY 时仍由 qdisc 保有包。保留原资源自动释放及事件驱动恢复。

`tests/test_sipa_flow_order.py` 编译实际函数，使用真实线程制造“发送已读到等待，
授权在最后停发前到达”的交错，同时覆盖 Ethernet/USB。七组用例包含提前／立即／
真正等待／失败授权，两个停发竞态和真实 USB 发送所有权。ASan/UBSan 通过。
原有发送重试、工作线程生命周期三项回归也通过。

复用已登记 `mu300-mainline-clean-tf-7-2` 卷，经固定 runner 调用原生
`upstream/build-modules.sh sprd_modem`，6.18.54 与 7.2.8 配套模块均重新编译，
`modules.sha256` 与各自未变的 Image/config 一起核验。没有复制第二份内核树。
这两条发行线本轮没有设备部署或硬件复现，源码／编译通过不等于长期实机验收。
