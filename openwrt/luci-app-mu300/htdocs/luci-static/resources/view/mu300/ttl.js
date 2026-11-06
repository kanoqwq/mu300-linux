'use strict';
'require view';
'require dom';
'require mu300.common as M';

// Page adapted from dikeckaan/mu300-linux, eab1802f (TTL view).
// Backend, lifecycle, localization and operation handling follow this tree.
var _ = M.translate;

return view.extend({
	load: function() { return M.callTtlGet(); },

	render: function(state) {
		M.injectCss();
		this.root = E('div', { 'class': 'mud' });
		this.paint(state || {});
		return this.root;
	},

	reload: function() {
		var self = this;
		return M.callTtlGet().then(function(st) { self.paint(st || {}); return st; });
	},

	/* run a ttl_set (a number from 1 to 255, or off) and show its result */
	set: function(btn, value) {
        var self = this;
        if (self._pending) return Promise.resolve();
        self._pending = true;
        var controls = Array.from(self.root.querySelectorAll('button, input')).map(function(el) {
            var wasDisabled = el.disabled;
            el.disabled = true;
            return [el, wasDisabled];
        });
        M.busy(btn, true);
        return M.callTtlSet(String(value)).then(function(r) {
            if (!r || r.ok !== 1) {
                M.toast(_("操作失败：%s").format(M.translate(r && r.error || '无法应用 TTL 设置')), { type: 'error', timeout: 6000 });
                return;
            }
            return self.reload().then(function(st) {
                if (!st || st.ok !== 1) {
                    M.toast(_('无法读取 TTL 设置'), { type: 'error' });
                    return;
                }
                M.toast(_('已保存'), { type: 'success' });
            });
        }).catch(function() {
            M.toast(_('管理连接已断开'), { type: 'error', timeout: 6000 });
        }).finally(function() {
            self._pending = false;
            M.busy(btn, false);
            controls.forEach(function(pair) { pair[0].disabled = pair[1]; });
        });
    },

	paint: function(st) {
		var self = this, ifs = st.iface || [];
		if (st.ok !== 1) {
			dom.content(this.root, E('div', { 'class': 'mud-note' }, [ M.translate(st.error || '无法读取 TTL 设置') ]));
			return;
		}

		var rule = st.backend === 'nft' ? 'nftables' : '--';
		if (st.enabled && st.mode === 'egress' && !ifs.length) rule = _('等待蜂窝接口，出现后自动应用。');
		else if (st.enabled && st.mode === 'legacy') rule = _('旧规则，请重新应用 TTL');
		var rows = [
			[ _("当前设置"), st.enabled ? _("固定为 %d").format(st.value) : _("保持不变（默认）") ],
			[ _("生效规则"), rule ]
		];
		if (st.offload === 0 || st.offload === 1)
			rows.push([ _("软件流量卸载"), st.offload ? _("已启用") : _("已禁用") ]);
		if (st.hw_paused === 1)
			rows.push([ _('硬件流量卸载'), _('固定 TTL 期间暂停') ]);
		var card = E('section', { 'class': 'mud-card', 'id': 'mud-ttl' }, [
			E('h3', {}, _("蜂窝网络 TTL")),
			E('div', { 'class': 'mud-note' },
				_("设置蜂窝出口数据包的 TTL 和 IPv6 跳数限制；仅影响蜂窝网络，不改变 Wi-Fi 或 USB 局域网。"))
		].concat(rows.map(function(r) {
			return E('div', { 'class': 'mud-r' }, [ E('span', { 'class': 'mud-k' }, [ r[0] ]), E('span', { 'class': 'mud-v' }, [ r[1] ]) ]);
		})));
		card.appendChild(E('div', { 'class': 'mud-note', 'id': 'mud-ttl-nft' },
				_("固定 TTL 可与软件流量卸载同时使用；IPv6 邻居发现等控制报文保持原值。启用期间暂停硬件流量卸载，关闭 TTL 后恢复原设置。")));

		var actions = E('div', { 'style': 'display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0' });
		[ 64, 65, 128 ].forEach(function(n) {
			var b = E('button', { 'class': 'mud-btn', 'id': 'mud-ttl-' + n }, [ _("设为 %d").format(n) ]);
			b.addEventListener('click', function() { self.set(b, n); });
			actions.appendChild(b);
		});
		var input = E('input', { 'type': 'number', 'min': '1', 'max': '255', 'step': '1', 'class': 'cbi-input-text',
			'id': 'mud-ttl-value', 'style': 'width:6em', 'placeholder': '1-255' });
		var custom = E('button', { 'class': 'mud-btn', 'id': 'mud-ttl-custom' }, _("设置"));
		custom.addEventListener('click', function() {
			var v = String(input.value).trim();
			if (!/^[1-9][0-9]{0,2}$/.test(v) || +v > 255) {
				M.toast(_("请输入 1 至 255 的整数"), { type: 'error' });
				return;
			}
			self.set(custom, +v);
		});
		actions.appendChild(input);
		actions.appendChild(custom);
		var off = E('button', { 'class': 'mud-btn warn', 'id': 'mud-ttl-off', 'disabled': st.enabled ? null : '' }, _("关闭 TTL"));
		off.addEventListener('click', function() { self.set(off, 'off'); });
		actions.appendChild(off);
		card.appendChild(actions);
		card.appendChild(E('div', { 'class': 'mud-note' }, _("设置会保留到重启后；默认关闭。")));

		dom.content(this.root, card);
	}
});
