# MU300 AUDCP 启动内存预留

MU300 固件只负责在内核早期预留音频共享区和 DSP 镜像空间，不包含 SIP 插件、
音频模块或 DSP 镜像，也不启动 DSP。未安装插件时同样保留这块内存。

## 依据与范围

2026-11-18 只读核对 U30 Air UFS：已安装 SIP r15，DSP 文件存在，但没有音频
预留或 ALSA 声卡。其 `/audio-mem-mgr` 的 `sprd,ddr32-dma` 为
`af700000+200000`，`sprd,cmdaddr` 为 `af980000+400`，与此前 F50 试验一致。
F50 曾通过本机 vendor_boot 预留及插件加载验证；这不等于其他机型已完成实机验收。

内核从实际设备树读取 DDR32／command 地址，把 command 末端向上对齐至 1 MiB，
再保留 UMS9620 AUDCP 镜像所需的 6 MiB。上述布局得到 `af700000-afffffff`，
共 9 MiB。若设备树使用其他合法位置，计算结果随之改变，不按机型名或固件版本
选择一组固定物理地址。范围必须处于实际 RAM 内并满足 DDR32 约束。

## 交付路径

`upstream/port/audio_memory.py` 由共同的 `upstream/port/install.py` 调用，
把早期钩子安装到 `drivers/of/fdt.c`。在标准 DT 保留区和 FDT `/memreserve/`
处理之后、页分配器启动之前执行 `memblock_reserve()`；与已有保留区合并，
不释放或搬移它们。缺少音频节点、其他 SoC 或非法布局不会扩大预留范围。

6.18 与 7.2 共用这一生成路径。变化位于 Image，完整安装和普通内核更新沿用现有
内核 bundle；差分更新通过现有 kernel delta 交付。不改 vendor_boot、GPT、
NV 或 Android 槽，也不引入插件安装时写分区的步骤。需要重新编译 Image 和匹配
模块后才能交付，旧内核不会因仅更新 rootfs 自动获得预留。

## 验证

`tests/test_audio_memory.py` 编译并执行实际预留函数的 FDT／memblock 桩测试，
覆盖合法和变化布局、旧预留合并、非法边界、非目标 SoC、内存不足及安装幂等性。
`tests/check_audio_memory_kernels.py` 使用既有准备树分别编译两条线的真实
`drivers/of/fdt.o`；它不是完整 Image 或实机验证。

更新后实机应在内核日志出现 `MU300: reserved AUDCP memory ...`，并在
`/proc/iomem` 中看到相应的 reserved 范围。之后由 SIP 插件完成驱动和 DSP
初始化；不把离线编译或预留成功等同于通话音频验收。
