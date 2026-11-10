# 无线配置保留调查（2026-11-10）

差异更新的固定版公共 OTA 引擎保留现存 `/etc/config/wireless`；新增文件的
default 合并也不覆盖用户无线配置。普通更新和 Android 保配置更新保留
`etc/config` 的现存内容。本次没有收紧 OTA 校验或改变本地修改备份规则。

已复现的另一条覆盖路径是开机 `openwrt-wifi-config`：旧安装缺少
`wifi-configured` 标记时，会忽略现存 UCI，导入旧 Android 热点密码或随机
生成密码。现在已有 AP、STA、mesh、禁用的自定义热点均保留，只有未初始化
的禁用 OpenWrt 模板才首次配置。已有配置读失败时停止初始化，不覆盖文件，
不提交用户尚未保存的设置。

回归使用真实 OTA 引擎、真实普通／Android 更新函数和隔离 UCI 下的真实
启动脚本，覆盖特殊字符密码、缺失标记、不同角色／加密、读取失败及首次
初始化。测试入口：`tests/test_wifi_diff_preserve.py`、
`tests/test_android_upgrade_config.py`、`tests/test_user_packages.py`、
`tests/test_wifi_config_preserve.py` 和 `tests/test_wifi_channels.py`。
这不等同于实机升级验收；尚无证据确认独立的 LuCI 保存问题。

## U30 Air 扫描／AP 与 STA 热切换边界

已检查跟踪源码 `upstream/modules/sprd_wlan_combo/` 中的 cfg80211、
SC2355 scan/report 及 AP/STA 生命周期路径。已有信号分析页面禁用扫描的
规则不等于客户端模式扫描已修复。报告尚缺少卡死时的驱动堆栈和对应固件
状态，当前不能确认根因。

按用户允许的范围，本轮暂不改驱动、不触发实机扫描或角色热切换，不增加
强制复位、不禁用正常热点，也不宣称已支持无故障热切换。需后续在可恢复
的实机上捕获故障现场再继续。
