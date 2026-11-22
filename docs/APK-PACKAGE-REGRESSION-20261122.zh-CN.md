# APK 配套与升级迁移回归（2026-11-22）

> 本文是撤回前的审计记录。后续已按用户确定的原有软件包策略撤回此迁移，
> 见 [源码撤回和最终回归](APK-POLICY-WITHDRAWAL-20261122.zh-CN.md)。

## 结论

暂不打包。此次自编内核 APK 元数据改动还有三类共性缺口：能力声明遗漏、
用户旧 kmod 迁移后形成坏依赖、带软件源标签的 world 约束合并不完整。
既有单元测试通过不代表这些使用场景已通过。

本轮只执行离线回归和只读检查，不改设备，不部署，不追加插件专用内核
配置，不恢复官方内核假记录，不跳过 APK 依赖检查。此前临时增加的 UDP
诊断配置、检查和测试已撤回。

## 范围和证据

- MU300：正式包 `r20261122.085232-g8e7c46a4-s4a2996db`，7.2.8 / 6.18.54。
- U30 Pro：正式包 `r20261122.084301-g459ac07f-s10d5126f`。
- F50 Pro：正式包 `r20261122.084516-g459ac07f-s10d5126f`。
- E5：九款 `r20261121.145628-gfb2d3fef-sd69b19c9` 的包内配置、对应登记的
  内核产物，以及 E5 自身的实际 APK / 普通更新 / 差分回归。
- 三仓库的 `tools/stage-kernel-packages.py` 内容相同；共享运行时未改动。

软件安装矩阵使用四份 MU300/Pro 正式包实际携带的 APK 数据库和 world，
真实 ARM64 apk-tools 3.0.5，以及固件的软件源索引。仅复制包管理元数据，
不复制完整 rootfs；安装和 upgrade 使用 simulate，禁止服务脚本和提交钩子。
前后数据库和 world 摘要保持不变。没有执行真实设备安装或驱动加载。

E5 九款的能力清单从登记的实际配置、模块及 built-in 清单计算；不把这些
清单检查说成九台机器上逐一安装软件成功。

## 七轮检查

| 轮次 | 内容 | 结果 |
| --- | --- | --- |
| 1 | 11 月 20 日旧包与当前包，同一官方 kmod 的实际 APK 求解对照 | 旧数据库放行、当前数据库拒绝；实际内核同为 7.2.8，UDP 诊断配置均未启用。行为变化定位到元数据改动，而不是新近删除功能 |
| 2 | 三仓库各自执行完整 `tools/test-kernel-packages.py` | 共 36 项：34 通过、2 跳过；跳过的是 MU300/Pro 不具备的 E5 普通更新适配器测试 |
| 3 | 共享 `test_ota*.py`：生成、应用、回滚、配置保留、包替换、RPC、故障恢复等 | 共 213 项：212 通过、1 项 Windows 短路径条件跳过；这是离线测试，不是设备验收 |
| 4 | 四份正式包各做 AGH、Docker、inet-diag、ipset、WireGuard、upgrade 求解 | 24 个最终场景：12 成功、12 因依赖配套被拒绝，详见下表；不能把拒绝算成功 |
| 5 | 真实生成器和 Engine：额外旧 kmod、带源标签的 world、无关用户插件对照 | 两个缺陷复现；无关用户插件及 Wi-Fi 配置保留对照正常 |
| 6 | 使用已有私有快照、实际 apk 和三个 Wi-Fi APK 复核 dnsmasq 所有权修复 | 1 项通过：DNS 程序、服务链接、DHCP 配置和无关软件保留；没有重新删除 dnsmasq |
| 7 | 登记输入和产物配套只读检查 | E5 检查通过；Pro 登记输入存在，但返回 kernel_artifacts_reusable=false；MU300 被已取消试验留下的 Image/modules 清单不一致正确拦截，不可拿该缓存继续组包 |

第 7 轮的 MU300 缓存问题是取消试验后的本机构建状态，不是已发布 ZIP 的
新缺陷。已发布包未被覆盖；后续打包须经原构建流程重新生成一致产物，
不能改摘要绕过检查。

## 正式包的最终求解矩阵

| 操作 | MU300 7.2 | MU300 6.18 | U30 Pro | F50 Pro |
| --- | --- | --- | --- | --- |
| 安装 luci-app-adguardhome | 通过 | 通过 | 通过 | 通过 |
| 安装 luci-app-dockerman | 拒绝 | 拒绝 | 通过 | 通过 |
| 安装 kmod-inet-diag（Clashoo 所需依赖） | 拒绝 | 拒绝 | 通过 | 通过 |
| 安装 ipset | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| 安装 wireguard-tools | 拒绝 | 拒绝 | 拒绝 | 拒绝 |
| apk upgrade | 模拟通过 | 模拟通过 | 模拟通过 | 模拟通过 |

矩阵只证明依赖求解结果，不证明软件启动、联网或 upgrade 后设备正常启动。
初次 Pro upgrade 测试缺少额外软件源的缓存索引；在隔离环境补齐索引后
重新运行，upgrade 通过。该环境错误不计作固件故障。DNS 测试首次独立
启动缺少 PYTHONPATH，改用已有显式快照和 SDK apk 的测试入口后通过。

## 共性缺口

### 1. 已有功能仍可能因缺少能力记录而无法安装用户态工具

WireGuard 已存在于 MU300/Pro 的实际内核；Pro 的 xt_set、ip_set 及多种
ipset 类型也在本机内核的 modules.builtin 中。但当前人工能力表没有
对应的 `kmod-wireguard` / `kmod-ipt-ipset` 记录，APK 仍选择官方内核的
kmod，导致安装失败。这不是一律增加内核配置就能解决的问题。

E5 九款的 WireGuard 同样已配置并有内核产物，却没有对应能力记录。
MU300/E5 的部分其他功能确实不完整，例如 UDP 诊断或 xt_set；必须将
“已有功能未声明”与“内核本身未提供完整功能”分别记录。

### 2. OTA 完成不等于迁移后的依赖完整

最小复现先在旧系统添加依赖原 kernel 的用户 kmod 和消费者；旧 APK
求解通过。真实 diff 应用返回 complete，用户软件确实保留，但消费者
仍要求旧 kernel，随后安装一个无关且原本已安装的软件也会求解失败。
无关用户态插件的对照正常，因此不能用删除全部用户软件规避。

### 3. world 中的软件源标签没有按同一规则归一化

原 world 有 `kmod-tun@legacy=1.0-r1` 时，迁移会同时保留这一行和新增
本机版本的 `kmod-tun=<native-version>`。包数据库已切成本机记录，但
world 同时要求两个不同版本。共享 engine 的 world 合并对包名拆分时
没有处理 @，与其他包管理辅助函数的处理不一致。复现仅作用于隔离根。

## 日志和复现材料

私有元数据和完整输出保存在 `work/diagnostics/apk-package-audit-20261122/`，
不纳入固件或提交设备快照。关键文件：

- `existing-test-results.json`、三个 `*-native-packages.log`、`shared-ota.log`。
- `solver-matrix.json`、`pro-refreshed-matrix.json` 及各场景实际 APK 输出。
- `migration-edge-findings.json`、`migration-edges.log`。
- `compiled-feature-evidence.json`、`e5-capability-inventory.json`。
- `dnsmasq-real-transaction.log`、`input-check-results.json`。

标准入口任务日志位于 `out/build-logs/`：

- `20261122-102534-apk-package-cross-repo-regression-4937`
- `20261122-102846-apk-migration-edge-dns-regression-5677`
- `20261122-103024-apk-official-feed-package-matrix-5746`
- `20261122-103246-apk-pro-feed-refresh-control-6008`
- `20261122-103409-apk-audit-registered-input-checks-6165`
- `20261122-103542-apk-e5-capability-inventory-6183`

本轮结论是回归发现问题，不是修复完成或发布验收通过。
