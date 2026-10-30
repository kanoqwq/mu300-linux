# 安装器已独立维护

公共仓库：https://github.com/kanoqwq/openwrt-tools

本仓库 installer/ 是固定版本的 Git submodule。新检出必须执行 git submodule update --init --recursive；不要再复制源码目录或运行旧式生成镜像同步。共享逻辑、Android 与工具链维护在子模块；固件、内核、设备端 NV/机型安装载荷仍由本仓库维护。

两家独立适配：E5 工作 NV 的保留与确认恢复不进入 MU300；两边都支持 eMMC/UFS，存储身份算法保持各自已发布记录语义。机型不在列表只显示风险确认，输入小写 yes 可以继续，不跳过分区、完整性与 NV 校验。

编译前阅读 installer/docs/BUILD.zh-CN.md；共享源码和交付件通过指纹核验。更新子模块提交后，再提交父仓库 gitlink。现有工具路径保留为薄入口，实际实现从子模块读取，不依赖相邻另一个固件仓库的源码。

本次为安装器抽取与适配整合，不写真实设备 GPT、boot、misc 或 NV。源代码与模拟/冻结程序回归不能替代后续获得授权的实机刷写验收。
