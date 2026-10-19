# Windows 有线 ADB 的 MTP 冲突处理

MU300 系列原厂 Android gadget 的 MTP 接口可能影响 Windows 有线 ADB 稳定性。安装器在用户选定设备并检查 Android 时，自动处理该设备的 MTP 接口；纯设备列表刷新不会修改 Windows 设备状态。

- 仅 MU300/F50/V50/U30 Air 的 Windows 有线 ADB 流程启用；E5 适配器、macOS、无线 ADB 均跳过。
- 根据 USB composite 父节点的完整序列号定位唯一设备，只操作该父节点下的 USB MTP/WPD 接口。不会禁用 USB composite 父节点、ADB、网卡、串口或其他手机。
- 在具有管理权限的进程中再次检查父节点、序列号和 MTP 类型，然后按精确 InstanceId 禁用。没有权限时自动请求 Windows UAC，不增加安装器确认步骤。
- 重新查询 Windows，确认 ProblemCode 为 22 后才显示成功 toast；已有禁用状态不重复执行。操作不自动恢复 MTP。
- 枚举失败、操作失败、UAC 被拒绝或结果无法确认时，仅弹警告，继续 ADB 检查及原安装流程，不将辅助操作失败当成安装失败。
- 不修改 Android 的 USB 配置、射频、启动链或安装载荷，不引入后台轮询服务。

共享安装器的通知事件和适配器开关保留在公共源码中，启用条件明确限定为 `adapter.kind == 'mu300'`；MU300 的 Python 命令行入口使用相同辅助实现。根目录使用指南同步说明该行为。

测试覆盖：精确序列号及父子匹配、多设备隔离、重复序列号拒绝、已禁用幂等、WPD GUID/兼容 ID、无线与 E5 排除、UAC 拒绝、回读失败、失败后继续 ADB、编码参数，以及中英文 toast。离线测试不能证明所有原厂固件的物理 ADB 稳定性。
