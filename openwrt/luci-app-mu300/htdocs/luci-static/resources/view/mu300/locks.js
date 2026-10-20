'use strict';
'require view';
'require mu300.refresh as R';
'require mu300.common as M';
'require mu300.auto-mode as AutoMode';

/* 网络锁定 -- 模式 / 频段 / 小区 / EN-DC，全部经 ubus mu300dash lock_set -> 后端
 * mu300-dash-lock（编码按 ufi_tools 权威实现），应用后 SFUN 重启协议栈并落盘，
 * 开机由插件自己的 procd 服务在 AT 适配器就绪后回放，不依赖平台拨号脚本。
 *
 * 当前驻网 hero 每 2 秒执行一次独立的实时 AT 快照，不读取蜂窝缓存；运营商与
 * 邻区等低频元数据独立轮询 cells，按阶段显示，不阻塞实时信号。
 * 邻区表每行带「锁定」按钮，与主页共用 M.neighborRows。 */

var MODES = [ [ 'auto', '自动' ], [ '4g', '仅 4G' ], [ 'sa', '5G SA' ], [ 'nsa', '5G NSA' ] ];

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		this._root = root;
		this._disposed = false;
		this._timers = [];
		root.className = 'mud mud-locks';
		root.innerHTML = `
<!-- 与主页同一套 hero 结构：mud-hero-l/mud-hero-r 让手机端媒体查询统一生效
     （信息块在上，RSRP 行左对齐、芯片右对齐），桌面端保持 RSRP 块右对齐 -->
<div class="mud-card mud-hero">
  <div class="mud-hero-l">
    <div class="mud-lock-heading" style="font-size:.78rem;color:var(--text-muted,var(--text-light,#777))">
      <span class="mud-heading-label">当前驻网</span>
      <span class="mud-progress mud-lock-progress" id="mud-serving-progress" role="status"></span>
    </div>
    <div style="font-size:1.25rem;font-weight:700;margin-top:2px" id="mud-srv-rat">--</div>
    <div class="mud-cellline" id="mud-srv"></div>
  </div>
  <div class="mud-hero-r">
    <div class="mud-rsrp" id="mud-srv-rsrp" style="font-size:1.9rem">--</div>
    <div class="mud-chips" id="mud-srv-chips"></div>
  </div>
</div>

<div class="mud-sec">
  <h3 class="mud-lock-heading"><span class="mud-heading-label">网络模式 · EN-DC</span><span class="mud-progress mud-lock-progress" id="mud-lock-progress" role="status"></span></h3>
  <div class="mud-ctl" id="mud-lock-modes" style="grid-template-columns:repeat(4,1fr);max-width:520px"></div>
  <div id="mud-auto-mode" hidden></div>
  <div class="mud-ctl" style="margin-top:7px;grid-template-columns:1fr 1fr;max-width:340px">
    <button class="mud-btn" id="mud-lock-endc">EN-DC</button>
    <button class="mud-btn" id="mud-lock-refresh">刷新锁定状态</button>
  </div>
  <div class="mud-ctl" style="margin-top:7px;max-width:340px">
    <button class="mud-btn" id="mud-lock-auto-apply">开机自动应用</button>
  </div>
  <div class="mud-note">关闭后只停止下次开机回放，已保存的网络、VoLTE 和 VoNR 设置不会被删除。</div>
</div>

<div class="mud-sec">
  <h3>VoLTE · VoNR</h3>
  <div class="mud-note">VoLTE 用于 4G 通话，VoNR 用于 5G SA 通话。设置按当前查看的 SIM 保存，并跟随“开机自动应用”。</div>
  ${['volte','vonr'].map(function(kind) {
    var name = {volte:'VoLTE',vonr:'VoNR'}[kind];
    return '<div class="mud-r mud-voice-row"><span class="mud-k">'+name+'</span>'+
      '<span class="mud-v" id="mud-lock-'+kind+'-state">--</span>'+
      '<button type="button" class="mud-btn mud-voice-switch" data-voice="'+kind+'" role="switch" aria-label="'+name+'" aria-checked="false" disabled>'+
      '<span class="mud-voice-track" aria-hidden="true"></span></button></div>';
  }).join('')}
  <div class="mud-note">关闭 VoLTE 或 VoNR 不等于屏蔽全部来电和短信，传统网络业务仍可能到达。</div>
</div>

<div class="mud-sec">
  <h3>频段锁定</h3>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">NR 频段</span><span class="mud-v" id="mud-lock-nrline">--</span></div>
      </div>
      <div class="mud-chiprow" id="mud-lock-nr"></div>
    </div>
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">LTE 频段</span><span class="mud-v" id="mud-lock-lteline">--</span></div>
      </div>
      <div class="mud-chiprow" id="mud-lock-lte"></div>
    </div>
  </div>
  <div class="mud-ctl" style="margin-top:8px;max-width:360px">
    <button class="mud-btn" id="mud-lock-nr-apply">应用 NR 频段</button>
    <button class="mud-btn" id="mud-lock-lte-apply">应用 LTE 频段</button>
  </div>
</div>

<div class="mud-sec">
  <h3 class="mud-lock-heading"><span class="mud-heading-label">邻区与小区锁定</span><span class="mud-progress mud-lock-progress" id="mud-neighbor-progress" role="status"></span></h3>
  <div id="mud-lockedcells"></div>
  <div class="mud-ctl" style="max-width:400px;margin-bottom:8px">
    <button class="mud-btn" id="mud-lock-cell">锁定当前服务小区</button>
    <button class="mud-btn warn" id="mud-lock-cell-off">解除小区锁定</button>
  </div>
  <details class="mud-manual-cell">
    <summary>手动输入小区</summary>
    <form id="mud-lock-manual-form" novalidate>
      <div class="mud-manual-cell-fields">
        <label><span>制式</span><select id="mud-lock-manual-rat"><option value="lte">LTE (4G)</option><option value="nr">NR (5G)</option></select></label>
        <label><span>频点（EARFCN / NR-ARFCN）</span><input id="mud-lock-manual-frequency" type="text" inputmode="numeric" autocomplete="off" required></label>
        <label><span>PCI</span><input id="mud-lock-manual-pci" type="text" inputmode="numeric" autocomplete="off" required></label>
        <button type="submit" class="mud-btn" id="mud-lock-manual-apply">锁定指定小区</button>
      </div>
      <div class="mud-note">LTE：EARFCN 0–262143，PCI 0–503；NR：NR-ARFCN 1–3279165，PCI 0–1007。</div>
      <div class="mud-note">应用于当前查看的 SIM，无需小区出现在邻区列表中。请输入频点编号，不是 MHz 频率。</div>
    </form>
  </details>
  <div class="mud-scroll">
  <table class="mud-table"><thead><tr><th>制式/频段</th><th>PCI</th><th>频点</th><th>RSRP</th><th>RSRQ</th><th>SINR</th><th></th></tr></thead>
  <tbody id="mud-neigh"><tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">--</td></tr></tbody></table>
  </div>
  <div class="mud-note">应用后协议栈重启（SFUN），蜂窝会短暂断开；设置会持久保存，并在启用“开机自动应用”时由插件于 AT 就绪后回放。接入平台的射频前钩子时可无重启回放。频段全不选再点应用 = 恢复自动。</div>
</div>

<div class="mud-sec">
  <h3>清除全部蜂窝锁定</h3>
  <div class="mud-note">按当前查看 SIM 恢复自动模式和频段、解除小区锁定并开启 EN-DC；只删除当前卡的已保存锁定，保留两张卡的开机自动应用开关。</div>
  <div class="mud-ctl" style="margin-top:10px;max-width:400px">
    <button class="mud-btn warn" id="mud-lock-reset">清除当前 SIM 全部锁定</button>
  </div>
  <div class="mud-note">注意：部分模组的频段或小区锁定由双卡共享（F50 的 NR 频段已确认共享），清除也会影响另一张卡。另一张卡的保存配置不会删除，其开机回放可能再次改变共享设置。清除会短暂中断蜂窝连接；频段以指令确认成功为准，其他设置回读通过后才删除当前卡的保存项。</div>
</div>`;
		M.localize(root);
		this.wire(root);
		M.simSelector(root);
		return root;
	},

	wire: function(root) {
		var self = this;
		this.lockSel = { nr: {}, lte: {} };
		this.lockCand = { nr: [], lte: [] };
		this.Q = function(id) { return root.querySelector('#mud-' + id); };

		/* 主题化确认框替代浏览器 confirm；确认后再进入实际执行 */
		var apply = function(kind, val, what, opts) {
			opts = opts || {};
			var slot = M.selectedSlot();
			opts.slot = slot;
			return M.confirmBox(opts.title || ('应用「' + what + '」？'),
				opts.message || (opts.noSfun ? '' : '协议栈会重启（SFUN），蜂窝断开约半分钟。'),
				{ danger: !opts.noSfun, okText: opts.okText || '应用' })
				.then(function(go) {
			if (self._disposed || !go || !root.isConnected) return;
			if (M.selectedSlot() !== slot) { self.note('查看卡槽已改变，请重新确认操作', 'info'); return; }
			applyNow(kind, val, what, opts); });
		};
		var applyNow = function(kind, val, what, opts) {
			if (self._applying) { self.note('另一项网络设置仍在执行，请稍后重试', 'info'); return; }
			self._applying = true;
			var btn = opts.btn;
			if (opts.optimistic) opts.optimistic();   /* 按钮立刻切到目标态，回读负责校正 */
			M.busy(btn, true);   /* 在 optimistic 之后：它可能重置按钮的 className */
			self.note('正在后台应用 ' + what + ' …' + (opts.noSfun ? '' : '（SFUN 重启 + 重新驻网，约半分钟）'), 'busy');
			var run = function() {
				if (opts.prepare && (self._disposed || M.selectedSlot() !== opts.slot))
					throw new Error(M.translate('查看卡槽已改变，请重新确认操作'));
				return M.callLockSet(kind, val);
			};
			// Save only the compatibility option after confirmation; keep the
			// existing selected-SIM job and AT/replay ordering unchanged.
			var operation = opts.prepare ? Promise.resolve().then(opts.prepare).then(run) : run();
			operation.then(function(r) {
			if (self._disposed) return;
				r = r || {};
				if (!r.ok) {
					self._applying = false;
					M.busy(btn, false); self.paintAutoMode();
					self.note('失败：' + (r.error || '未知错误'), 'error');
					self.refresh();
					return;
				}
				self.note('正在确认设置结果…', 'busy');
				return self.readback(r, btn);
			}, function(error) { self._applying = false; M.busy(btn, false); self.paintAutoMode(); self.note(error && error.message || '调用失败', 'error'); });
		};

		this.autoMode = AutoMode.attach(this.Q('auto-mode'), function(btn, prepare) {
			apply('mode', 'auto', M.translate('自动模式兼容性'), {btn:btn, prepare:prepare});
		});

		this.Q('lock-modes').innerHTML = MODES.map(function(m) {
			return '<button class="mud-btn" data-mode="' + m[0] + '">' + M.esc(M.translate(m[1])) + '</button>';
		}).join('');
		root.querySelectorAll('#mud-lock-modes .mud-btn').forEach(function(b) {
			b.onclick = function() {
				var m = b.getAttribute('data-mode');
				if (m === (self.lastLock && self.lastLock.mode && self.lastLock.mode.label)) return;
				var btn = b;
				apply('mode', m, '网络模式：' + b.textContent, { btn: btn, optimistic: function() {
					Array.prototype.forEach.call(self.Q('lock-modes').querySelectorAll('.mud-btn'), function(x) {
						x.className = x === btn ? 'mud-btn on' : 'mud-btn';
					});
				} });
			};
		});
		this.Q('lock-endc').onclick = function() {
			var on = self.lastLock && self.lastLock.endc === '1';
			var btn = this;
			apply('endc', on ? 'off' : 'on', on ? '关闭 EN-DC（NSA 锚点）' : '开启 EN-DC（NSA 锚点）',
				{ btn: btn, noSfun: true, optimistic: function() {
					btn.className = 'mud-btn' + (on ? '' : ' on');
					btn.textContent = on ? 'EN-DC' : 'EN-DC ✓';
				} });
		};
		this.Q('lock-auto-apply').onclick = function() {
			var on = !self.lastLock || self.lastLock.auto_apply !== 0;
			var btn = this;
			apply('auto_apply', on ? 'off' : 'on', on ? '关闭开机自动应用' : '开启开机自动应用',
				{ btn: btn, noSfun: true, optimistic: function() {
					btn.className = 'mud-btn' + (on ? '' : ' on');
					btn.textContent = on ? '开机自动应用' : '开机自动应用 ✓';
				} });
		};
		root.querySelectorAll('[data-voice]').forEach(function(btn) {
			btn.onclick = function() {
				var kind = btn.dataset.voice, state = (self.lastLock || {})[kind];
				if (self._applying || btn.disabled || !state || typeof state.enabled !== 'boolean') return;
				var val = state.enabled ? 'off' : 'on';
				var name = {volte:'VoLTE',vonr:'VoNR'}[kind];
				apply(kind, val, M.translate(val === 'on' ? '开启' : '关闭')+' '+name,
					{btn:btn,noSfun:true});
			};
		});
		this.Q('lock-refresh').onclick = function() {
			if (self.autoMode) self.autoMode.reload();
			var btn = this;
			M.busy(btn, true);
			self.note('正在直读调制解调器（最多几秒）…', 'busy');
			L.resolveDefault(M.callLockFresh('1')).then(function(l) {
			if (self._disposed) return;
				M.busy(btn, false); self.paintAutoMode();
				self.lastLock = l || {};
				self.paint();
				self.note('已刷新', 'success');
				var nb = self.Q('neigh');
				if (nb && self.lastCell) nb.innerHTML = M.neighborRows(self.neighborCell || self.lastCell, self.lastLock.cells || []);
			}, function() { M.busy(btn, false); self.paintAutoMode(); self.note('刷新失败', 'error'); });
			self.loadServing();
		};

		this.chipRow = function(el, rat, cand) {
			el.innerHTML = cand.map(function(b) {
				return '<span class="mud-chip" data-rat="' + rat + '" data-b="' + b + '">' + (rat === 'nr' ? 'n' : 'B') + b + '</span>';
			}).join('');
			/* chips 重建后恢复选中态 */
			Array.prototype.forEach.call(el.children, function(ch) {
				var b = ch.getAttribute('data-b');
				if (self.lockSel[rat][b]) ch.className = 'mud-chip on';
			});
		};
		[ 'nr', 'lte' ].forEach(function(rat) {
			self.Q('lock-' + rat + '-apply').disabled = true;
			self.Q('lock-' + rat).textContent = M.translate('等待模组能力数据');
		});
		/* 事件委托绑在容器上：paint() 依模组能力重建 chips 后点击依然有效 */
		[ 'nr', 'lte' ].forEach(function(rat) {
			self.Q('lock-' + rat).addEventListener('click', function(ev) {
				var ch = ev.target;
				if (!ch.getAttribute || !ch.getAttribute('data-b')) return;
				var b = ch.getAttribute('data-b');
				self.lockSel[rat][b] = !self.lockSel[rat][b];
				ch.className = self.lockSel[rat][b] ? 'mud-chip on' : 'mud-chip';
			});
		});
		var selBands = function(rat) {
			return Object.keys(self.lockSel[rat]).filter(function(b) { return self.lockSel[rat][b]; })
				.map(Number).sort(function(a, b) { return a - b; });
		};
		this.Q('lock-nr-apply').onclick = function() {
			var sel = selBands('nr');
			if (!sel.length) return apply('nr', '', 'NR 频段：恢复自动', { btn: this });
			apply('nr', sel.join(','), 'NR 频段锁定：n' + sel.join(' n'), { btn: this });
		};
		this.Q('lock-lte-apply').onclick = function() {
			var sel = selBands('lte');
			if (!sel.length) return apply('lte', '', 'LTE 频段：恢复自动', { btn: this });
			apply('lte', sel.join(','), 'LTE 频段锁定：B' + sel.join(' B'), { btn: this });
		};
		this.Q('lock-cell').onclick = function() { apply('cell', 'auto', '锁定当前服务小区', { btn: this }); };
		this.Q('lock-cell-off').onclick = function() { apply('cell', 'off', '解除小区锁定', { btn: this }); };

		this.Q('lock-manual-form').onsubmit = function(ev) {
			ev.preventDefault();
			if (self._disposed || self._applying || self._manualConfirm) return;
			var rat = self.Q('lock-manual-rat').value;
			var frequency = self.Q('lock-manual-frequency').value.trim();
			var pci = self.Q('lock-manual-pci').value.trim();
			var nr = rat === 'nr';
			if ((rat !== 'lte' && !nr) || !/^[0-9]{1,7}$/.test(frequency) || !/^[0-9]{1,4}$/.test(pci) ||
				Number(frequency) < (nr ? 1 : 0) || Number(frequency) > (nr ? 3279165 : 262143) ||
				Number(pci) > (nr ? 1007 : 503)) {
				self.note('请输入范围内的十进制频点和 PCI', 'error');
				return;
			}
			var value = rat + ':' + Number(frequency) + ',' + Number(pci);
			self._manualConfirm = true;
			return apply('cell', value, M.translate('手动锁定小区') + ' ' + (nr ? 'NR' : 'LTE') +
				' / ' + Number(frequency) + ' / PCI ' + Number(pci), {btn:self.Q('lock-manual-apply')})
				.finally(function() { self._manualConfirm = false; });
		};

		this.Q('lock-reset').onclick = function() {
			apply('reset', 'auto', '清除当前 SIM 全部锁定', {
				btn: this, okText: '确认清除',
				title: M.translate('清除 SIM %s 的全部锁定？').replace('%s', Number(M.selectedSlot()) + 1),
				message: '恢复自动模式和频段、解除 LTE/NR 小区锁定并开启 EN-DC。双卡共享的模组设置也会影响另一张卡（F50 的 NR 频段共享）。只清除当前卡的保存项，保留另一张卡配置和开机自动应用开关；另一张卡的回放可能重新改变共享设置。蜂窝会短暂断开；频段不要求回读一致，其他设置仍需回读确认。'
			});
		};

		/* 已锁定小区表的解锁按钮（委托） */
		this.Q('lockedcells').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-unlock')) return;
			var rat = btn.getAttribute('data-unlock');
			M.confirmBox('解除 ' + rat.toUpperCase() + ' 的小区锁定', '协议栈会重启（SFUN），约半分钟。', { danger: true })
				.then(function(go) {
			if (self._disposed) return;
				if (!go) return;
				if (self._applying) return;
				self._applying = true;
				M.busy(btn, true);
			self.note('正在解除 ' + rat.toUpperCase() + ' 小区锁定…', 'busy');
			L.resolveDefault(M.callLockSet('cell', 'off-' + rat)).then(function(r) {
			if (self._disposed) return;
				r = r || {};
					self.note('正在确认设置结果…', 'busy');
					self.readback(r, btn);
				});
			});
		});

		/* 邻区行内锁定（事件委托，与主页一致） */
		this.Q('neigh').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-lock')) return;
			var key = btn.getAttribute('data-lock');
			M.confirmBox('锁定小区 ' + key.replace(':', ' ') + '?', '协议栈会重启（SFUN），蜂窝断开约半分钟。', { danger: true })
				.then(function(go) {
			if (self._disposed) return;
				if (!go) return;
				if (self._applying) return;
				self._applying = true;
				M.busy(btn, true);
			self.note('正在后台锁定 ' + key + ' …', 'busy');
			L.resolveDefault(M.callLockSet('cell', key)).then(function(r) {
			if (self._disposed) return;
				r = r || {};
					self.note('正在确认设置结果…', 'busy');
					self.readback(r, btn);
				});
			});
		});

		this.loading('serving', true);
		this.loading('neighbor', true);
		this.loading('lock', true);
		this.refresh();
		var active = function() { return !self._disposed && root.isConnected; };
		this._stopMeta = R.poll(function() { return self.loadServingMeta(); }, function() {}, function() { return 2000; }, null, null, active);
		this._stopSignal = R.poll(function() { return self.loadServing(); }, function() {}, function() { return 2000; }, null, null, active);
	},

	/* 统一反馈：所有提示走顶部 toast（M.toast），进行中的用 busy 自带转圈；
	 * 同一时间只保留一条（新提示顶掉旧提示，进度→结果一路更新不堆叠）。 */
	note: function(txt, type) {
		if (this._disposed) return;
		if (this._toast) this._toast.close();
		this._toast = M.toast(txt, { type: type || 'info' });
	},

	unload: function() {
		this._disposed = true;
		if (this._stopMeta) this._stopMeta();
		if (this._stopSignal) this._stopSignal();
		(this._timers || []).forEach(clearTimeout);
		if (this._toast) this._toast.close();
	},

	later: function(fn, ms) {
		var self = this;
		if (this._disposed) return;
		var timer = setTimeout(function() {
			self._timers = self._timers.filter(function(t) { return t !== timer; });
			if (!self._disposed) fn();
		}, ms);
		this._timers.push(timer);
		return timer;
	},

	loading: function(part, pending, failed) {
		var el = this.Q(part + '-progress');
		if (!el || this._disposed) return;
		el.classList.toggle('is-loading', !!pending && !failed);
		el.textContent = M.translate(failed ? '读取失败，稍后重试' : pending ? '正在更新…' : '');
		el.title = el.textContent;
	},

	loadServingMeta: function() {
		if (this._disposed) return Promise.resolve();
		if (this._metaRequest) return this._metaRequest;
		var self = this;
		this._metaRequest = M.callCells().then(function(st) {
			if (self._disposed || (self._root && !self._root.isConnected) || document.hidden) return;
			st = st || {};
			self.servingMeta = M.mergeCell(self.servingMeta, st.cell) || {};
			if (st.cell && !st.cell.neigh_pending && !st.cell.error) self.neighborCell = st.cell;
			// A cheap cached core paints immediately while the live lane completes.
			var signal = self.liveSignal || st.sig;
			if (signal && signal.partial && self.servingMeta.ts)
				signal = { ts: signal.ts, cfun: signal.cfun, reg: signal.reg, reg5g: signal.reg5g };
			self.paintServing(Object.assign({}, self.servingMeta, signal || {}));
			self.loading('neighbor', !st.cell || !!st.cell.neigh_pending || !!st.refreshing,
				!!(st.cell && st.cell.error));
		}, function() { self.loading('neighbor', false, true); }).finally(function() {
			self._metaRequest = null;
		});
		return this._metaRequest;
	},

	/* Always a newly completed AT round; never replace the 2 s live hero
	 * with the full-cache engineering fields while a live request is pending. */
	loadServing: function() {
		if (this._disposed) return Promise.resolve();
		if (this._signalRequest) return this._signalRequest;
		var self = this;
		this.loading('serving', true);
		this._signalRequest = M.callSignal().then(function(live) {
			if (self._disposed || (self._root && !self._root.isConnected) || document.hidden) return;
			if (!live || live.error || !live.ts) {
				self.loading('serving', false, true); return;
			}
			self.liveSignal = live;
			self.paintServing(Object.assign({}, self.servingMeta || {}, live));
			self.loading('serving', false);
		}, function() { self.loading('serving', false, true); }).finally(function() {
			self._signalRequest = null;
		});
		return this._signalRequest;
	},

	paintServing: function(c) {
		var e = this.Q('srv'); if (!e || this._disposed) return;
		this.lastCell = c;
		if (!c || !c.ts || c.error) {
			this.Q('srv-rat').textContent = c && c.error ? c.error : M.translate('暂无驻网数据');
			e.innerHTML = '';
			return;
		}
		var ratTxt = (c.nr && c.nr.band) ? ((c.lte && c.lte.band) ? '5G NSA' : '5G SA') : 'LTE';
		var sig = c.sig || {}, label = M.qLabel(sig.rsrp, sig.rsrq, sig.sinr);
		var operName = M.carrierName(c.operator);
			if (operName === '--' && c.ident && c.ident.imsi)
				operName = M.translate(M.PLMN_CN[c.ident.imsi.substring(0, 5)] || c.ident.imsi.substring(0, 5));
			this.Q('srv-rat').textContent = ratTxt + ' · ' + operName;
		this.Q('srv-rat').style.color = M.qCol(label);
		var rsrpEl = this.Q('srv-rsrp');
		rsrpEl.textContent = '--';
		if (sig.rsrp != null) { rsrpEl.innerHTML = sig.rsrp.toFixed(1) + '<small> dBm</small>'; rsrpEl.style.color = M.qCol(label); }
		this.Q('srv-chips').innerHTML =
			(sig.rsrq != null ? '<span class="mud-tag">RSRQ ' + sig.rsrq.toFixed(1) + '</span>' : '') +
			(sig.sinr != null ? '<span class="mud-tag">SINR ' + sig.sinr.toFixed(1) + '</span>' : '');
		var rows = [];
		if (c.nr && c.nr.band) rows.push([ 'NR 服务小区', 'n' + c.nr.band + ' · PCI ' + c.nr.pci + ' · ARFCN ' + c.nr.arfcn + (c.nr.bw_mhz ? ' · ' + c.nr.bw_mhz + ' MHz' : '') ]);
		if (c.lte && c.lte.band) rows.push([ 'LTE 锚点', 'B' + c.lte.band + ' · PCI ' + c.lte.pci + ' · EARFCN ' + c.lte.earfcn ]);
		e.innerHTML = rows.map(function(x) {
			return '<div class="mud-srvline"><span class="k">' + M.esc(M.translate(x[0])) + '</span><span class="v">' + M.esc(x[1]) + '</span></div>';
		}).join('');
		var nb = this.Q('neigh');
		if (nb) nb.innerHTML = M.neighborRows(this.neighborCell || c, (this.lastLock || {}).cells || []);
	},

	/* Job completion is independent of browser/device clock skew. */
	readback: function(operation, btn) {
		var self = this;
		return M.waitLockJob(operation, function() { return !self._disposed; }).finally(function() {
			// The operation is finished; a slow display refresh must not keep its
			// button spinning after the completion toast has already appeared.
			self._applying = false;
			M.busy(btn, false); self.paintAutoMode();
		}).then(function() {
			if (self._disposed) return;
			self.note(operation.kind === 'auto_apply' ? '开机自动应用设置已保存' :
				operation.kind === 'reset' ? '当前 SIM 的全部锁定已清除，已核对并清理保存配置' :
				(operation.kind === 'lte' || operation.kind === 'nr') ? '频段设置已应用，实际频段以模组回读为准' : '已应用并核对模组状态', 'success');
			return self.refresh();
		}).catch(function(error) {
			if (self._disposed) return;
			self.note('失败：' + (error.message || error), 'error');
			return self.refresh();
		});
	},
	refresh: function() {
		if (this._disposed) return;
		var self = this;
		return L.resolveDefault(M.callLockGet()).then(function(l) {
			if (self._disposed) return;
			l = l || {};
			/* 空结果多半是 rpcd 高并发下的一次瞬时失败（实测同请求重发即好）：轻量重试 */
			if (!l.mode && !l.error && (self._retry = (self._retry || 0) + 1) <= 3)
				return self.later(function() { self.refresh(); }, 1800);
			self._retry = 0;
			self.loading('lock', false, !l.mode || !!l.error);
			self.lastLock = l;
			self.paint();
			/* 锁定状态回来了，把邻区表的“已锁定”标记也刷新一下 */
			var nb = self.Q('neigh');
			if (nb && self.lastCell) nb.innerHTML = M.neighborRows(self.neighborCell || self.lastCell, self.lastLock.cells || []);
		});
	},

	paintVoiceSwitches: function(l) {
		var self = this;
		['volte','vonr'].forEach(function(kind) {
			var state = l[kind] || {}, known = typeof state.enabled === 'boolean';
			var text = M.translate(known ? (state.enabled ? '已开启' : '已关闭') : '状态暂不可读，请刷新后重试');
			var status = self.Q('lock-'+kind+'-state');
			if (status) status.textContent = text;
			var box = self._root;
			if (box) box.querySelectorAll('[data-voice="'+kind+'"]').forEach(function(btn) {
				btn.className = 'mud-btn mud-voice-switch'+(known && state.enabled ? ' on' : '');
				btn.setAttribute('aria-checked', String(known && state.enabled));
				btn.setAttribute('aria-label', kind === 'volte' ? 'VoLTE' : 'VoNR');
				btn.disabled = !!self._applying || !known;
			});
		});
	},

	paintAutoMode: function() {
		if (this.autoMode) this.autoMode.update((this.lastLock || {}).mode, !!this._applying);
	},

	paint: function() {
		this.paintAutoMode();
		var l = this.lastLock || {};
		var self = this;
		this.paintVoiceSwitches(l);
		var setR = function(id, txt) { var e = self.Q(id); if (e) e.textContent = (txt == null || txt === '') ? '--' : txt; };
		if (l.error) { setR('lock-nrline', l.error); setR('lock-lteline', ''); return; }
		var MODE_TXT = { auto: '自动（5G/4G）', '4g': '仅 4G', sa: '仅 5G SA', nsa: '仅 5G NSA' };
		var cur = this.Q('lock-modes');
		if (cur) Array.prototype.forEach.call(cur.querySelectorAll('.mud-btn'), function(b) {
			b.classList.toggle('on', b.getAttribute('data-mode') === (l.mode && l.mode.label));
		});
		var eb = this.Q('lock-endc');
		if (eb) { eb.className = 'mud-btn' + (l.endc === '1' ? ' on' : ''); eb.textContent = l.endc === '1' ? 'EN-DC' : 'EN-DC'; }
		var ab = this.Q('lock-auto-apply'), autoApply = l.auto_apply !== 0;
		if (ab) { ab.className = 'mud-btn' + (autoApply ? ' on' : ''); ab.textContent = M.translate(autoApply ? '开机自动应用 ✓' : '开机自动应用'); }

		/* Never expose speculative selectable bands before backend capabilities. */
		var caps = l.caps || {};
		/* 已锁定小区独立表：多小区都列出来，每个 RAT 一个解锁按钮 */
		var lc = self.Q('lockedcells');
		if (lc) {
			var cells = l.cells || [];
			lc.innerHTML = cells.length
				? '<div class="mud-note" style="margin:0 0 4px">' + M.esc(M.translate('已锁定小区')) + '</div><table class="mud-table"><tbody>' +
					cells.map(function(k) {
						var parts = k.split(':'), rat = parts[0], fp = (parts[1] || '').split(',');
						return '<tr><td>' + (rat == 'nr' ? 'NR' : 'LTE') + '</td><td>' + M.esc(fp[0] || '?') + '</td>' +
							'<td>' + M.esc(fp[1] || '?') + '</td>' +
							'<td><button class="mud-lockbtn" data-unlock="' + rat + '">' + M.esc(M.translate('解锁 ')) + (rat == 'nr' ? 'NR' : 'LTE') + '</button></td></tr>';
					}).join('') + '</tbody></table>'
				: '';
		}
		[ 'nr', 'lte' ].forEach(function(rat) {
			var rawCaps = (l.write_caps || caps)[rat] || '';
			var capList = /^[1-9][0-9]*(,[1-9][0-9]*)*$/.test(rawCaps) ? rawCaps.split(',').map(Number) : [];
			self.Q('lock-' + rat + '-apply').disabled = !capList.length;
			if (!capList.length) {
				self.lockCand[rat] = []; self.lockSel[rat] = {};
				self.Q('lock-' + rat).textContent = M.translate('等待模组能力数据');
				setR('lock-' + rat + 'line', '--'); return;
			}
			if (capList.length) {
				capList.sort(function(a, b) { return a - b; });
				self.lockCand[rat] = capList;
				var box = self.Q('lock-' + rat);
				if (box) self.chipRow(box, rat, capList);
			}
			var locked = (l[rat] && l[rat].locked) || '';
			var arr = locked ? locked.split(',').map(Number) : [];
			var isAuto = arr.length === 0 || self.lockCand[rat].every(function(b) { return arr.indexOf(b) >= 0; });
			setR('lock-' + rat + 'line', M.translate(isAuto
				? '自动（支持 ' + self.lockCand[rat].length + ' 个）'
				: '已锁 ' + arr.length + ' 个：' + (rat === 'nr' ? 'n' : 'B') + arr.join(' ' + (rat === 'nr' ? 'n' : 'B'))));
			var box = self.Q('lock-' + rat);
			if (box) Array.prototype.forEach.call(box.children, function(ch) {
				var b = ch.getAttribute('data-b');
				var on = !isAuto && arr.indexOf(Number(b)) >= 0;
				self.lockSel[rat][b] = on;
				ch.className = on ? 'mud-chip on' : 'mud-chip';
			});
		});
	}
});
