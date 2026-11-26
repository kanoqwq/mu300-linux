'use strict';
'require baseclass';
'require mu300.common as M';

// Both the dashboard and System page use these exact confirmations and actions.
var androidMessage = '下次启动将进入 Android 并立即重启，此管理页面与蜂窝共享都会断开。\n' +
	'回到 OpenWrt：在 Android 上执行 mu300-next-boot linux 后重启；\n' +
	'或什么都不做，连续 7 次开机未完成会自动回退。';

function finished(owner, active) {
	owner._quickPending = false;
	if (active() && owner.bootActionsDone) return owner.bootActionsDone();
}

function confirm(owner, active, button, act, title, body, options, op, arg, note) {
	if (!active() || owner._quickPending || button.disabled) return Promise.resolve();
	owner._quickPending = true;
	return M.confirmBox(title, body, options).then(function(go) {
		if (go && active()) return act(op, arg, note, button);
	}).finally(function() { return finished(owner, active); });
}

function permanent(owner, active, button, act) {
	return confirm(owner, active, button, act, '设为默认启动',
		'以后开机默认进入 OpenWrt；启动失败仍保留 Android 回退。现在不会重启。',
		{ okText: '确定' }, 'os', 'linux-default');
}

function temporary(owner, active, button, act) {
	return confirm(owner, active, button, act, '切换为临时启动',
		'将默认启动系统设为 Android，当前 OpenWrt 会话继续运行；下次重启进入 Android。',
		{ okText: '确定' }, 'os', 'linux-temporary');
}

function bind(owner, root, act, active) {
	var q = function(id) { return root.querySelector('#mud-' + id); };
	if (q('btn-reboot')) q('btn-reboot').onclick = function() {
		var button = this;
		if (!active() || owner._quickPending || button.disabled) return;
		owner._quickPending = true;
		return M.choiceBox('关机或重启', '请选择关机或重启，执行后所有连接都会断开。', [
			{ label: '取消', value: undefined },
			{ label: '关机', value: 'poweroff', danger: true },
			{ label: '重启', value: 'reboot', danger: true }
		]).then(function(op) {
			if ((op === 'poweroff' || op === 'reboot') && active())
				return act(op, null, op === 'poweroff' ? '正在关机…' : '正在重启…', button);
		}).finally(function() { return finished(owner, active); });
	};
	if (q('btn-default')) q('btn-default').onclick = function() {
		return permanent(owner, active, this, act);
	};
	if (q('btn-android')) q('btn-android').onclick = function() {
		return confirm(owner, active, this, act, '切换到 Android 系统', androidMessage,
			{ danger: true, okText: '切换并重启' }, 'os', 'android', '正在武装 Android 引导并重启…');
	};
}

return baseclass.extend({ bind: bind, permanent: permanent, temporary: temporary });
