'use strict';
'require view';
'require uci';
'require mu300.refresh as R';
'require mu300.common as M';
'require mu300.sim-radio as SimRadio';

/* MU300 状态看板 -- LuCI 落地页（menu.d 挂在 admin/home）。
 *
 * 布局：顶部驻网卡片是唯一的卡片；其余都是全宽分区（链路与流量 / 邻区 / 无线·局域网·
 * 设备·SIM / 快捷控制）。锁频、短信、AT 终端在「蜂窝」子菜单。
 *
 * 状态走 status，网速独立走无 AT 的 rates；隐藏页面停止轮询。
 * 状态默认 2 s 一轮、网速 1 s 一轮；慢档数据分段发布并保留尚未完成的旧值。
 * 邻区行内「锁定」走 lock_set cell（SFUN 重启协议栈，约半分钟断网）；锁定状态来自
 * lock_get（页面加载时取一次，锁定操作后刷新）。 */

var DEFAULT_POLL_S = 2;
var RATE_WIN = 40;

function pollSeconds(value) { return R.seconds(value); }

/* 工程口只提供 MCS/BLER；调制方式按 3GPP 常用 MCS table 1 就地换算，
 * 不为展示项增加 AT 请求。LTE 上行的 MCS 分界与下行/NR 不同。 */
function modulation(mcs, rat, uplink) {
	if (mcs == null || isNaN(Number(mcs))) return '--';
	mcs = Number(mcs);
	if (rat === 'lte' && uplink) {
		if (mcs >= 0 && mcs <= 10) return 'QPSK';
		if (mcs <= 20) return '16QAM';
		if (mcs <= 28) return '64QAM';
	} else {
		if (mcs >= 0 && mcs <= 9) return 'QPSK';
		if (mcs <= 16) return '16QAM';
		if (mcs <= 28) return '64QAM';
	}
	return '--';
}

function radioMetricRows(label, rat, cell) {
	var dlMcs = cell && cell.dl_mcs != null ? cell.dl_mcs : null;
	var ulMcs = cell && cell.ul_mcs != null ? cell.ul_mcs : null;
	var dlBler = cell && cell.dl_bler != null ? cell.dl_bler : null;
	var ulBler = cell && cell.ul_bler != null ? cell.ul_bler : null;
	var prefix = label ? label + ' ' : '';
	var pair = function(a, b, suffix) {
		return (a != null ? a + suffix : '--') + ' / ' + (b != null ? b + suffix : '--');
	};
	return '<div class="mud-r"><span class="mud-k">' + prefix + '调制方式 下/上</span>' +
		'<span class="mud-v" title="按当前 MCS 估算">' + modulation(dlMcs, rat, false) + ' / ' + modulation(ulMcs, rat, true) + '</span></div>' +
		'<div class="mud-r"><span class="mud-k">' + prefix + 'MCS 下/上</span><span class="mud-v">' + pair(dlMcs, ulMcs, '') + '</span></div>' +
		'<div class="mud-r"><span class="mud-k">' + prefix + 'BLER 下/上</span><span class="mud-v">' + pair(dlBler, ulBler, '%') + '</span></div>';
}

return view.extend({
	load: function() { return Promise.resolve(); },

	render: function() {
		M.injectCss();
		M.watchSms();
		var root = document.createElement('div');
		this._root = root;
		this._bootEl = root;
		root.className = 'mud mud-booting';
		root.innerHTML = this.html();
		M.localize(root);
		this.wire(root);
		M.simSelector(root);
		var self = this;
		var intervalMs = DEFAULT_POLL_S * 1000;
		L.resolveDefault(uci.load('unisoc_modem')).then(function() {
			intervalMs = pollSeconds(uci.get('unisoc_modem', 'main', 'home_refresh_interval')) * 1000;
		});
		this._disposed = false;
		var active = function() { return !self._disposed && root.isConnected; };
		this._stopStatus = R.poll(M.callStatus, function(st) { self.update(st || {}); },
			function() { return intervalMs; }, null, null, active);
		this._stopRates = R.poll(M.callRates, function(sample) { self.updateRates(sample); },
			function() { return 1000; }, function() { self.updateRates(null); },
			function() { self.lastNet = null; }, active);

		return root;
	},

	unload: function() {
		this._disposed = true;
		if (this._lockToast) this._lockToast.close();
		if (this._stopStatus) this._stopStatus();
		if (this._stopRates) this._stopRates();
	},

	updateRates: function(sample) {
		var delta = R.rateDelta(this.lastNet, sample);
		if (!delta) {
			this.dlHist = []; this.ulHist = [];
			M.v('spark-dl').innerHTML = ''; M.v('spark-ul').innerHTML = '';
		}
		this.lastNet = sample && sample.available ? sample : null;
		if (!this.lastNet) {
			M.set('dl', '--'); M.set('ul', '--');
			M.set('rx', '--'); M.set('tx', '--');
			return;
		}
		M.set('rx', M.fmtBytes(sample.rx)); M.set('tx', M.fmtBytes(sample.tx));
		if (!delta) {
			M.set('dl', '--'); M.set('ul', '--');
			this.dlHist = []; this.ulHist = [];
		} else {
			M.set('dl', M.fmtRate(delta.dl)); M.set('ul', M.fmtRate(delta.ul));
			this.dlHist.push(delta.dl); this.ulHist.push(delta.ul);
			if (this.dlHist.length > RATE_WIN) { this.dlHist.shift(); this.ulHist.shift(); }
		}
		var peak = Math.max(1, Math.max.apply(null, this.dlHist.concat(this.ulHist)));
		M.spark(M.v('spark-dl'), this.dlHist, 0, peak, RATE_WIN);
		M.spark(M.v('spark-ul'), this.ulHist, 0, peak, RATE_WIN);
	},

	html: function() {
		return `
<div class="mud-card mud-hero">
  <div class="mud-hero-l">
    <div style="font-size:.78rem;color:var(--text-muted,var(--text-light,#777))">
      <span class="mud-dot" id="mud-dot"></span><b id="mud-host" style="color:var(--text,#222)">--</b>
      <span id="mud-uptime"></span></div>
    <div class="mud-rat" id="mud-rat">--<span class="mud-bars" id="mud-bars"><i style="height:25%"></i><i style="height:45%"></i><i style="height:65%"></i><i style="height:85%"></i><i style="height:100%"></i></span></div>
    <div class="mud-op" id="mud-op">--</div>
    <div class="mud-cellline" id="mud-cellline"></div>
  </div>
  <div class="mud-hero-r">
    <div class="mud-rsrp" id="mud-rsrp">--</div>
    <div class="mud-chips" id="mud-metric-chips"></div>
  </div>
</div>

<div class="mud-card mud-boot-notice" id="mud-boot-notice" hidden style="padding:1rem;margin-bottom:1rem;gap:.75rem;align-items:center;flex-wrap:wrap">
  <div style="flex:1;min-width:12rem"><b>当前为临时启动 OpenWrt</b><div>是否将 OpenWrt 设为默认启动？不会立即重启或断网。</div></div>
  <button class="mud-btn" id="mud-btn-default">设为默认启动</button>
</div>
<div class="mud-card mud-body">

<div class="mud-sec">
  <h3>链路与流量</h3>
  <div class="mud-charts">
    <div class="mud-chart" style="color:var(--brand,var(--primary,#2f7bf6))">
      <div class="t"><b id="mud-dl">--</b><span>下行速率</span></div>
      <div class="c" id="mud-spark-dl"></div>
    </div>
    <div class="mud-chart" style="color:var(--success,#2FBF71)">
      <div class="t"><b id="mud-ul">--</b><span>上行速率</span></div>
      <div class="c" id="mud-spark-ul"></div>
    </div>
  </div>
  <div class="mud-kpis">
    <div class="mud-kpi"><b id="mud-rx">--</b><span>累计接收</span></div>
    <div class="mud-kpi"><b id="mud-tx">--</b><span>累计发送</span></div>
    <div class="mud-kpi"><b id="mud-tr-day">--</b><span>今日流量</span></div>
    <div class="mud-kpi"><b id="mud-tr-month">--</b><span>当月流量</span></div>
    <div class="mud-kpi"><b id="mud-tr-left">--</b><span>套餐剩余</span></div>
  </div>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">IPv4 / IPv6</span><span class="mud-v" id="mud-ip">--</span></div>
        <div class="mud-r"><span class="mud-k">APN</span><span class="mud-v" id="mud-apn">--</span></div>
        <div class="mud-r"><span class="mud-k">会话时长</span><span class="mud-v" id="mud-sess">--</span></div>
        <div class="mud-r"><span class="mud-k">注册状态</span><span class="mud-v" id="mud-reg">--</span></div>
        <div class="mud-r"><span class="mud-k">DNS</span><span class="mud-v" id="mud-dns">--</span></div>
      </div>
    </div>
    <div>
      <div class="mud-rows" id="mud-lteanchor"></div>
      <div class="mud-rows" id="mud-radio-metrics">
        <div class="mud-r"><span class="mud-k">调制方式 下/上</span><span class="mud-v">-- / --</span></div>
        <div class="mud-r"><span class="mud-k">MCS 下/上</span><span class="mud-v">-- / --</span></div>
        <div class="mud-r"><span class="mud-k">BLER 下/上</span><span class="mud-v">-- / --</span></div>
      </div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">频宽</span><span class="mud-v" id="mud-bw">--</span></div>
        <div class="mud-r"><span class="mud-k">QCI</span><span class="mud-v" id="mud-qci">--</span></div>
        <div class="mud-r"><span class="mud-k">AMBR 下/上</span><span class="mud-v" id="mud-ambr">--</span></div>
      </div>
    </div>
  </div>
</div>


<div class="mud-sec">
  <h3>无线 · 局域网 · 设备 · SIM</h3>
  <div class="mud-temp" id="mud-temps"></div>
  <div class="mud-kpis" style="margin-top:8px">
    <div class="mud-kpi"><b id="mud-cpu">--</b><span>CPU 占用</span><div class="mud-meter"><i id="mud-cpu-bar" style="background:var(--brand,var(--primary,#3b82f6))"></i></div></div>
    <div class="mud-kpi"><b id="mud-ram">--</b><span>内存 · <span class="mud-sub" id="mud-ram-sub">--</span></span><div class="mud-meter"><i id="mud-ram-bar" style="background:var(--info,#0ea5e9)"></i></div></div>
    <div class="mud-kpi"><b id="mud-disk">--</b><span>存储</span><div class="mud-meter"><i id="mud-disk-bar" style="background:var(--warning,#f59e0b)"></i></div></div>
    <div class="mud-kpi"><b id="mud-batt">--</b><span id="mud-batt-l">电源</span></div>
  </div>
  <div id="mud-freqs" class="mud-freqs"></div>
  <div class="mud-cols">
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">SSID</span><span class="mud-v" id="mud-ssid">--</span></div>
        <div class="mud-r"><span class="mud-k">信道</span><span class="mud-v" id="mud-chan">--</span></div>
        <div class="mud-r"><span class="mud-k">加密</span><span class="mud-v" id="mud-wenc">--</span></div>
        <div class="mud-r"><span class="mud-k">隐藏 SSID</span><span class="mud-v" id="mud-whid">--</span></div>
        <div class="mud-r"><span class="mud-k">国家</span><span class="mud-v" id="mud-wcountry">--</span></div>
        <div class="mud-r"><span class="mud-k">AP 状态</span><span class="mud-v" id="mud-whostapd">--</span></div>
        <div class="mud-r"><span class="mud-k">USB 网络</span><span class="mud-v" id="mud-wusb">--</span></div>
        <div class="mud-r"><span class="mud-k">连接跟踪</span><span class="mud-v" id="mud-conntrack">--</span></div>
        <div class="mud-r"><span class="mud-k">LAN 地址</span><span class="mud-v" id="mud-lanip">--</span></div>
        <div class="mud-r"><span class="mud-k">系统</span><span class="mud-v" id="mud-fwos">--</span></div>
      </div>
    </div>
    <div>
      <div class="mud-rows">
        <div class="mud-r"><span class="mud-k">设备型号</span><span class="mud-v" id="mud-model">--</span></div>
        <div class="mud-r"><span class="mud-k">调制解调器</span><span class="mud-v" id="mud-modem">--</span></div>
        <div class="mud-r"><span class="mud-k">运营商</span><span class="mud-v" id="mud-carr">--</span></div>
        <div class="mud-r"><span class="mud-k">PLMN</span><span class="mud-v" id="mud-plmn">--</span></div>
        <div class="mud-r"><span class="mud-k">IMEI</span><span class="mud-v" id="mud-imei">--</span></div>
        <div class="mud-r"><span class="mud-k">IMSI</span><span class="mud-v" id="mud-imsi">--</span></div>
        <div class="mud-r"><span class="mud-k">ICCID</span><span class="mud-v" id="mud-iccid">--</span></div>
        <div class="mud-r"><span class="mud-k">本机号码</span><span class="mud-v" id="mud-msisdn">--</span></div>
        <div class="mud-r"><span class="mud-k">模组</span><span class="mud-v" id="mud-fwmodel">--</span></div>
        <div class="mud-r"><span class="mud-k">固件</span><span class="mud-v" id="mud-fw">--</span></div>
      </div>
      <div class="mud-chiprow"><span class="mud-chip" id="mud-reveal">显示卡号信息</span></div>
    </div>
  </div>
  <div class="mud-cols mud-client-cols">
    <div>
      <div class="mud-r"><span class="mud-k">无线客户端</span><span class="mud-v" id="mud-wcl">--</span></div>
      <div id="mud-clist" style="margin-top:8px"></div>
    </div>
    <div>
      <div class="mud-r"><span class="mud-k">DHCP 租约</span><span class="mud-v" id="mud-wleases">--</span></div>
      <div id="mud-leases" style="margin-top:8px"></div>
    </div>
  </div>
</div>


<div class="mud-sec">
  <h3>快捷控制</h3>
  <div class="mud-ctl" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
    <button class="mud-btn" id="mud-btn-data">数据连接</button>
    <button class="mud-btn" id="mud-btn-radio">蜂窝射频</button>
    <button class="mud-btn" id="mud-btn-wifi">Wi-Fi 热点</button>
    <button class="mud-btn warn" id="mud-btn-modem">重启调制解调器</button>
    <button class="mud-btn warn" id="mud-btn-reboot">关机或重启</button>
    <button class="mud-btn warn" id="mud-btn-android">切换到 Android</button>
  </div>
</div>
<div class="mud-sec">
  <h3>邻区</h3>
  <div class="mud-note mud-progress" id="mud-slow-progress" role="status"></div>
  <div class="mud-scroll">
  <table class="mud-table"><thead><tr><th>制式/频段</th><th>PCI</th><th>频点</th><th>RSRP</th><th>RSRQ</th><th>SINR</th><th></th></tr></thead>
  <tbody id="mud-neigh"><tr><td colspan="7" style="color:var(--text-muted,var(--text-light,#777))">--</td></tr></tbody></table>
  </div>
</div>

</div><!-- /mud-body -->`;
	},

	wire: function(root) {
		var self = this;
		this.identShown = false;
		this.dlHist = []; this.ulHist = [];
		this.lastNet = null; this.lastCpu = null; this.lastFullTs = 0;
		this.lockedCell = '';
		/* render() 在节点挂进文档之前运行，这里相对 root 查找（挂载后 update 用全文档查找） */
		var q = function(id) { return root.querySelector('#mud-' + id); };

		/* 统一反馈：按钮转圈（M.busy）+ 顶部 toast，与锁定/短信页同一框架 */
		var act = function(op, arg, note, btn) {
			if (op === 'wifi') { if (self._wifiBusy) return; self._wifiBusy = true; btn.disabled = true; }
			M.busy(btn, true);
			M.toast(note || ('正在执行 ' + op + ' …'), { type: 'busy' });
			return L.resolveDefault(M.callAct(op, arg)).then(function(r) {
                if(op==='radio' && r && r.ok && r.id)
                    return SimRadio.wait(r,function(){return !self._disposed && root.isConnected;}).then(function(done) {
                        return done && Object.assign({},done,{op:'radio '+arg});
                    },function(error){return {ok:0,error:error.message};});
                return r;
            }).then(function(r) {
				r = r || {};
				M.busy(btn, false);
				if (self._disposed || !root.isConnected) return;
				if (op === 'wifi' && r.ok == null) {
					M.toast('热点连接可能已中断，请重新连接后确认状态', {type:'info'}); return;
				}
				M.toast(r.ok ? (r.pending ? '热点配置已保存，正在应用' : ((r.started ? '已后台执行：' : '已执行：') + (r.op || op))) : ('失败：' + (r.error || '未知错误')),
					{ type: r.ok ? 'success' : 'error' });
			}, function() { M.busy(btn, false); M.toast('调用失败', { type: 'error' }); }).finally(function() {
				if (op === 'wifi') { self._wifiBusy = false; btn.disabled = false; }
			});
		};
		/* Keep one confirmation/action in flight. Cancel, Escape and leaving
		 * this view must never turn into a control request. */
		var confirmAction = function(btn, title, body, options, op, arg, note) {
			if (self._disposed || self._quickPending || btn.disabled) return;
			self._quickPending = true;
			return M.confirmBox(title, body, options).then(function(go) {
				if (!go || self._disposed || !root.isConnected) return;
				return act(op, arg, note, btn);
			}).finally(function() { self._quickPending = false; });
		};
		q('btn-data').onclick = function() {
			var up = self.lastInfo && self.lastInfo.wan && self.lastInfo.wan.up;
			return confirmAction(this, up ? '断开数据连接' : '建立数据连接',
				up ? '将断开蜂窝数据连接，依赖此连接的设备将无法上网。' : '将建立蜂窝数据连接，可能产生流量费用。',
				{ danger: !!up }, 'data', up ? 'down' : 'up', up ? '正在断开数据连接…' : '正在拨号…');
		};
		q('btn-radio').onclick = function() {
			var on = self.lastCell && self.lastCell.cfun === 1;
			return confirmAction(this, on ? '关闭蜂窝射频' : '打开蜂窝射频',
				on ? '关闭后该卡无法通话、收发短信或上网，设置会在重启后保留。' : '将开启所选卡的射频，设置会在重启后保留。',
				{ danger: !!on }, 'radio', on ? 'off' : 'on');
		};
		q('btn-wifi').onclick = function() {
			var wifi = self.lastInfo && self.lastInfo.wifi;
			if (!wifi || wifi.available === 0 || wifi.pending || self._wifiBusy) return;
			var on = self.lastInfo && self.lastInfo.wifi && self.lastInfo.wifi.up;
			return confirmAction(this, on ? '关闭 Wi-Fi 热点' : '打开 Wi-Fi 热点',
				on ? '热点客户端会断开；若正通过此热点管理设备，需要重新连接后才能继续访问。' : '将使用已保存的配置开启 Wi-Fi 热点。',
				{ danger: !!on }, 'wifi', on ? 'off' : 'on');
		};
		q('btn-modem').onclick = function() {
			return confirmAction(this, '重启调制解调器', '蜂窝连接会中断 1-2 分钟。',
				{ danger: true }, 'modem-reset', null);
		};
		q('btn-reboot').onclick = function() {
			var btn = this;
			if (self._disposed || self._quickPending || btn.disabled) return;
			self._quickPending = true;
			return M.choiceBox('关机或重启', '请选择关机或重启，执行后所有连接都会断开。', [
				{ label: '取消', value: undefined },
				{ label: '关机', value: 'poweroff', danger: true },
				{ label: '重启', value: 'reboot', danger: true }
			]).then(function(op) {
				if ((op !== 'poweroff' && op !== 'reboot') || self._disposed || !root.isConnected) return;
				return act(op, null, op === 'poweroff' ? '正在关机…' : '正在重启…', btn);
			}).finally(function() { self._quickPending = false; });
		};
		q('btn-default').onclick = function() {
			return confirmAction(this, '设为默认启动',
				'以后开机默认进入 OpenWrt；启动失败仍保留 Android 回退。现在不会重启。',
				{ okText: '确定' }, 'os', 'linux-default');
		};
		q('btn-android').onclick = function() {
			return confirmAction(this, '切换到 Android 系统',
				'下次启动将进入 Android 并立即重启，此管理页面与蜂窝共享都会断开。\n' +
				'回到 OpenWrt：在 Android 上执行 mu300-next-boot linux 后重启；\n' +
				'或什么都不做，连续 5 次开机未完成会自动回退。',
				{ danger: true, okText: '切换并重启' }, 'os', 'android', '正在武装 Android 引导并重启…');
		};
		q('reveal').onclick = function() {
			self.identShown = !self.identShown;
			M.v('reveal').textContent = self.identShown ? '隐藏卡号信息' : '显示卡号信息';
			self.paintIdent(self.lastCell);
		};

		/* 邻区行内锁定（事件委托） */
		q('neigh').addEventListener('click', function(ev) {
			var btn = ev.target;
			if (!btn.getAttribute || !btn.getAttribute('data-lock')) return;
			var key = btn.getAttribute('data-lock');
			M.confirmBox('锁定小区 ' + key.replace(':', ' ') + '?', '协议栈会重启（SFUN），蜂窝断开约半分钟。', { danger: true })
				.then(function(go) {
				if (!go || self._disposed || self._locking) return;
				self._locking = true;
				M.busy(btn, true);
				self._lockToast = M.toast('正在确认设置结果…', { type: 'busy' });
				M.callLockSet('cell', key).then(function(r) {
					return M.waitLockJob(r, function() { return !self._disposed; });
				}).then(function() {
					if (!self._disposed) M.toast('已应用并核对模组状态', { type: 'success' });
				}).catch(function(error) {
					if (!self._disposed) M.toast('锁定失败：' + (error.message || error), { type: 'error' });
				}).finally(function() {
					self._locking = false; M.busy(btn, false);
					if (self._lockToast) self._lockToast.close();
					self.refreshLock();
				});
			});
		});
		this.refreshLock();
	},

	refreshLock: function() {
		if (this._disposed) return;
		var self = this;
		L.resolveDefault(M.callLockGet()).then(function(l) {
			if (self._disposed) return;
			self.lockedCell = (l || {}).cells || [];
			self.repaintNeigh();
		});
	},

	paintIdent: function(cell) {
		var id = cell && cell.ident;
		var mask = function(s) {
			if (!s) return '--';
			return this.identShown ? s : s.substring(0, 4) + '****' + s.substring(s.length - 3);
		}.bind(this);
		M.set('imei', mask(id && id.imei));
		M.set('imsi', mask(id && id.imsi));
		M.set('iccid', mask(id && id.iccid));
		M.set('msisdn', mask(id && id.msisdn));
		M.set('fwmodel', id ? (id.model || '--') : '--');
		M.set('fw', id ? (id.fw || '--') : '--');
	},

	repaintNeigh: function() {
		var el = M.v('neigh');
		if (el && this.lastCell) el.innerHTML = M.neighborRows(this.lastCell, this.lockedCell);
	},

	update: function(st) {
		var i = st.info || {};
		this.lastInfo = i;
		if (i.ts && this._bootEl) {
			this._bootEl.classList.remove('mud-booting');
			this._bootEl = null;
		}
		/* 快档覆盖：sig（服务小区/注册）盖在慢档缓存 c 的对应字段上 */
		this._slowCell = M.mergeCell(this._slowCell, st.cell);
		var pending = !st.cell || !!st.cell.partial || !!st.refreshing;
		var progress = M.v('slow-progress');
		if (progress) {
			progress.classList.toggle('is-loading', pending && !(st.cell && st.cell.error));
			progress.textContent = M.translate(st.cell && st.cell.error ? '读取失败，稍后重试' : pending ? '正在更新…' : '');
		}
		var c = this._slowCell;
		var s = st.sig || null;
		if (s && !s.error && (!c || !c.ts || (s.ts || 0) >= c.ts)) {
			if (c && s.partial) {
				/* 核心首包先更新驻网状态，但不让临时 CESQ 空值擦掉上一份工程信号。 */
				c = Object.assign({}, c, {
					ts: s.ts, cfun: s.cfun, reg: s.reg, reg5g: s.reg5g
				});
			} else c = c ? Object.assign({}, c, {
				ts: s.ts, cfun: s.cfun, reg: s.reg, reg5g: s.reg5g,
				sig_src: s.sig_src, sig: s.sig, lte: s.lte, nr: s.nr
			}) : s;
		}
		this.lastCell = c;
		/* 慢档数据（邻区/运营商/身份）只在整份缓存的时间戳变化时重绘 */
		var fullTs = st.cell ? [st.cell.ts, st.cell.partial || 0, st.cell.neigh_pending || 0].join(':') : '';
		var slowChanged = fullTs !== this.lastFullTs;
		this.lastFullTs = fullTs;

		M.set('host', i.host);
		M.set('uptime', i.uptime ? '已运行 ' + M.fmtUptime(i.uptime) : '');
		M.v('dot').className = 'mud-dot' + (i.modem && i.modem.alive ? ' on' : '');

		var sig = c && !c.error ? (c.sig || {}) : {};
		var rsrp = sig.rsrp, rsrq = sig.rsrq, sinr = sig.sinr;
		/* 4G 时 sig 就是 LTE 块（后端 sig_src 优先 NR > LTE > CESQ），c.lte.sinr
		 * 是同一数据源的另一个时间戳——仅作 sinr 缺失时的回退，绝不并列展示 */
		if (sinr == null && c && c.lte && !c.nr && c.lte.sinr != null) sinr = c.lte.sinr;
		var label = M.qLabel(rsrp, rsrq, sinr), score = M.qScore({ rsrp: rsrp, rsrq: rsrq, sinr: sinr });
		var col = M.qCol(label);

		var rat = '--';
		if (c && !c.error) {
			var nr = c.nr && c.nr.band ? true : false;
			var lte = c.lte && c.lte.band ? true : false;
			if (nr && lte) rat = '5G NSA';
			else if (nr) rat = '5G SA';
			else if (lte) {
				var act = (c.operator && c.operator.act) || (c.reg && c.reg.act);
				rat = (act == 13) ? '5G NSA' : (act == 11 || act == 18 || act == 19) ? '5G' : (act == 7 || act == 10) ? '4G' : (act >= 2 && act <= 6) ? '3G' : '4G';
			} else if (c.cfun === 0) rat = '无线电已关';
		}
		if (c && c.error) { rat = '无应答'; col = M.qCol('较差'); }
		var ratEl = M.v('rat');
		ratEl.firstChild.nodeValue = rat;
		ratEl.style.color = col;
		var bars = M.v('bars');
		if (bars) {
			var n = score == null ? 0 : Math.max(1, Math.round(score / 2));
			Array.prototype.forEach.call(bars.children, function(b, idx) { b.className = idx < n ? 'on' : ''; });
		}

		var oper = M.carrierName(c && c.operator);
		/* COPS 空时（重启后的过渡态），从 IMSI 前 5-6 位推导 PLMN */
		if (oper === '--' && c && c.ident && c.ident.imsi) {
			var imsi = c.ident.imsi;
			var plmn5 = imsi.substring(0, 5), plmn6 = imsi.substring(0, 6);
			oper = M.translate(M.PLMN_CN[plmn5] || M.PLMN_CN[plmn6] || plmn5);
		}
		M.set('op', oper + (score != null ? ' · 信号 ' + label + ' ' + score.toFixed(1) + ' 分' : ' · 信号 ' + label));

		var cl = [];
		if (c && c.nr && c.nr.band) cl.push('n' + c.nr.band + (c.nr.bw_mhz ? ' · ' + c.nr.bw_mhz + ' MHz' : '') + ' · PCI ' + c.nr.pci + ' · ARFCN ' + c.nr.arfcn);
		if (c && c.lte && c.lte.band) cl.push('锚点 B' + c.lte.band + ' · PCI ' + c.lte.pci + ' · EARFCN ' + c.lte.earfcn +
			(c.lte.sinr != null ? ' · SINR ' + c.lte.sinr.toFixed(1) + ' dB' : ''));
		M.v('cellline').innerHTML = cl.map(M.esc).join('<br>') || '<span style="color:var(--text-muted,var(--text-light,#777))">未驻留小区</span>';

		M.set('rsrp', '--');
		if (rsrp != null) M.v('rsrp').innerHTML = rsrp.toFixed(1) + '<small> dBm</small>';
		M.v('rsrp').style.color = col;
		M.v('metric-chips').innerHTML =
			'<span class="mud-q" style="background:color-mix(in oklab,' + col + ' 16%,transparent);color:' + col + '">' + label + '</span>' +
			(rsrq != null ? '<span class="mud-tag">RSRQ ' + rsrq.toFixed(1) + '</span>' : '') +
			(sinr != null ? '<span class="mud-tag">SINR ' + sinr.toFixed(1) + '</span>' : '');

		/* -- 链路与流量 */
		var nrk = (c && c.nr) || null;
		var radioMetrics = '';
		if (c && c.nr && c.nr.band) radioMetrics += radioMetricRows('5G', 'nr', c.nr);
		if (c && c.lte && c.lte.band) radioMetrics += radioMetricRows('4G', 'lte', c.lte);
		M.v('radio-metrics').innerHTML = radioMetrics || radioMetricRows('', 'nr', null);
		M.set('bw', nrk && nrk.bw_mhz ? nrk.bw_mhz + ' MHz' : (c && c.lte && c.lte.bw) || '--');
		var qos = c && c.qos;
		M.set('qci', qos && qos.qci != null ? qos.qci : '--');
		M.set('ambr', qos && qos.dl != null ? qos.dl + ' / ' + qos.ul + ' Mbps' : '--');

		var w = i.wan || {};
		var traffic = i.traffic && i.traffic.clock_ok && i.ts - i.traffic.updated_at < 30 ? i.traffic : null;
		M.set('tr-day', traffic ? M.fmtTrafficBytes(traffic.today_used) : '--');
		M.set('tr-month', traffic ? M.fmtTrafficBytes(traffic.month_used) : '--');
		M.set('tr-left', traffic ? (traffic.remaining == null ? M.translate('不限量') : M.fmtTrafficBytes(traffic.remaining)) : '--');
		M.v('ip').innerHTML = M.esc(w.ip4 || '--') + (w.ip6 ? '<br>' + M.esc(w.ip6) : '');
		M.set('dns', w.dns || '--');
		M.set('apn', w.apn || '--');
		M.set('sess', w.uptime ? M.fmtUptime(w.uptime) : '--');
		var regmap = { 0: '未注册', 1: '已注册', 2: '搜索中', 3: '注册被拒', 4: '未知', 5: '已注册（漫游）', 7: '仅紧急', 8: '仅紧急', 10: '已注册' };
		var reg = '--';
		if (c && c.reg) {
			reg = regmap[c.reg.stat] || ('状态 ' + c.reg.stat);
			if (c.reg.tac) reg += ' · TAC ' + c.reg.tac;
			if (c.reg5g && c.reg5g.stat == 1) reg += ' · 5G ' + regmap[c.reg5g.stat];
		} else if (c && c.error) reg = c.error;
		M.set('reg', reg);

		var anchor = (c && c.lte && c.lte.band) ? c.lte : null;
		M.v('lteanchor').innerHTML = anchor ?
			'<div class="mud-r"><span class="mud-k">' + (c.nr && c.nr.band ? 'LTE 锚点' : 'LTE 链路') + '</span><span class="mud-v">B' + M.esc(anchor.band) +
			' · RSRP ' + (anchor.rsrp != null ? anchor.rsrp.toFixed(1) : '--') +
			(anchor.sinr != null ? ' · SINR ' + anchor.sinr.toFixed(1) : '') +
			(anchor.ca ? ' · ' + M.esc(anchor.ca) : '') + '</span></div>' : '';

		/* -- 慢档分区 */
		if (slowChanged) {
			this.repaintNeigh();
			M.set('carr', oper);
			M.set('plmn', (c && c.operator && c.operator.plmn) ||
				(c && c.ident && c.ident.imsi ? c.ident.imsi.substring(0, 5) : '--'));
			this.paintIdent(c);
		}

		/* -- 无线 · 局域网 · 设备 · SIM */
		var wf = i.wifi || {};
		M.set('ssid', wf.ssid || '--');
		M.set('chan', (wf.channel || '--') + (wf.band ? '（' + wf.band + (wf.width ? ' · ' + wf.width : '') + '）' : ''));
		M.set('wenc', wf.enc || '--');
		M.set('whid', wf.hidden == 1 ? '已隐藏' : '否');
		M.set('wcountry', wf.country || '--');
		M.set('whostapd', wf.hostapd ? '运行中' : '未运行');
		M.set('wusb', (i.net && i.net.usb0 && i.net.usb0.up) ? '已连接' : '未连接');
		M.set('conntrack', i.conns != null ? i.conns + ' 条' : '--');
		M.set('lanip', (i.lan && i.lan.ip) || '--');
		M.set('wcl', (wf.clients_n != null ? wf.clients_n : '--') + ' 台');
		M.set('wleases', (i.lan ? i.lan.leases : '--') + ' 条');
		M.v('clist').innerHTML = (wf.clients || []).map(function(cl) {
			var l = cl.signal != null ? (cl.signal >= -55 ? '优秀' : cl.signal >= -67 ? '良好' : cl.signal >= -80 ? '一般' : '较差') : '未知';
			return '<div class="mud-cli"><div class="t"><b>' + M.esc(cl.host || cl.ip || cl.mac) + '</b>' +
				(cl.signal != null ? '<span style="color:' + M.qCol(l) + ';font-variant-numeric:tabular-nums">' + cl.signal + ' dBm</span>' : '') +
				'</div><div class="s">' + (cl.ip ? M.esc(cl.ip) + ' · ' : '') + M.esc(cl.mac) +
				((cl.tx || cl.rx) ? ' · ↑' + M.esc(cl.tx || '--') + ' ↓' + M.esc(cl.rx || '--') : '') +
				(cl.conn ? ' · ' + M.esc(cl.conn) : '') + '</div></div>';
		}).join('') || '';
		/* 近期 DHCP 租约：没人连着的时候这里也能看出谁来过 */
		M.v('leases').innerHTML = (i.lan && i.lan.list && i.lan.list.length)
			? i.lan.list.slice(0, 8).map(function(l) {
					return '<div class="mud-cli"><div class="t"><b>' + M.esc(l.host || l.ip || '?') + '</b>' +
						'<span class="mud-sub">' + (l.left >= 3600 ? Math.round(l.left / 3600) + ' 小时' : Math.max(0, Math.round(l.left / 60)) + ' 分') + '</span>' +
						'</div><div class="s">' + (l.ip ? M.esc(l.ip) + ' · ' : '') + M.esc(l.mac) + '</div></div>';
				}).join('')
			: '';

		var t = i.temps || {};
		M.v('temps').innerHTML = [ [ 'SoC', t.soc ], [ 'CPU', t.cpu ], [ '调制解调器', t.modem ], [ '主板', t.board ] ]
			.filter(function(x) { return x[1] != null; })
			.map(function(x) {
				var lab = x[1] >= 75 ? '较差' : x[1] >= 60 ? '一般' : '良好';
				return '<span style="color:' + M.qCol(lab) + '">' + x[0] + ' ' + x[1] + '°C</span>';
			}).join('') || '<span style="color:var(--text-muted,var(--text-light,#777))">无温度读数</span>';

		if (i.cpu && this.lastCpu && i.cpu.total != null && this.lastCpu.total != null) {
			var dt2 = i.cpu.total - this.lastCpu.total, di = i.cpu.idle - this.lastCpu.idle;
			var pct = dt2 > 0 ? Math.max(0, Math.min(100, Math.round((dt2 - di) * 100 / dt2))) : null;
			if (pct != null) {
				M.set('cpu', pct + '%');
				M.v('cpu-bar').style.width = pct + '%';
			}
		}
		this.lastCpu = i.cpu || null;
		/* 每簇一条 cur/max 频率条 */
		M.v('freqs').innerHTML = ((i.cpu && i.cpu.freqs) || []).map(function(f, n) {
			if (f.cur == null || f.max == null || !f.max) return '';
			var w = Math.max(2, Math.round(f.cur * 100 / f.max));
			return '<div class="mud-freq"><span class="mud-k">簇' + n + '</span>' +
				'<div class="mud-meter" style="flex:1;margin:4px 8px 0"><i style="width:' + w + '%;background:var(--brand,var(--primary,#3b82f6))"></i></div>' +
				'<span class="mud-v" style="flex:0 0 auto">' + (f.cur / 1000).toFixed(0) + ' <span style="opacity:.55">/ ' + (f.max / 1000).toFixed(0) + ' MHz</span></span></div>';
		}).join('');

		if (i.mem && i.mem.total_kb) {
			var used = i.mem.total_kb - i.mem.avail_kb, pct = Math.round(used * 100 / i.mem.total_kb);
			M.set('ram', pct + '%'); M.v('ram-bar').style.width = pct + '%';
			M.set('ram-sub', '共 ' + M.fmtBytes(i.mem.total_kb * 1024) + ' · 余 ' + M.fmtBytes(i.mem.avail_kb * 1024));
		}
		if (i.storage && i.storage.total_kb) {
			var pct2 = Math.round(i.storage.used_kb * 100 / i.storage.total_kb);
			M.set('disk', pct2 + '%'); M.v('disk-bar').style.width = pct2 + '%';
		}
		var p = i.power || {};
		if (p.present && p.capacity != null) {
			M.set('batt', p.capacity + '%');
			M.set('batt-l', '电源 · ' + (p.status || '') + (p.volt != null ? ' · ' + p.volt + ' V' : '') + (p.usb ? ' · USB' : ''));
		} else {
			M.set('batt', p.usb ? 'USB' : '--');
			M.set('batt-l', '电源' + (p.volt != null ? ' · ' + p.volt + ' V' : ''));
		}
		M.set('model', i.model || '--');
		M.set('fwos', i.fw || '--');
		M.set('modem', (i.modem && i.modem.alive ? '在线' : '无应答') + (i.modem && i.modem.atd ? '' : ' · AT 适配器不可用'));
		var androidBtn = M.v('btn-android');
		var boot = i.boot || {}, notice = M.v('boot-notice'), setDefault = M.v('btn-default');
		if (notice) {
			notice.hidden = boot.temporary !== true;
			notice.style.display = notice.hidden ? 'none' : 'flex';
		}
		if (setDefault) {
			setDefault.disabled = !boot.can_set_default || !!this._quickPending;
			setDefault.title = boot.can_set_default ? '' : M.translate('当前启动镜像不支持设置默认启动');
		}
		if (androidBtn) androidBtn.style.display = i.capabilities && i.capabilities.dualboot ? '' : 'none';

		var b;
		b = M.v('btn-data'); b.className = 'mud-btn' + (w.up ? ' on' : ''); b.textContent = '数据连接';
		b = M.v('btn-radio'); b.className = 'mud-btn' + (c && c.cfun === 1 ? ' on' : ''); b.textContent = '蜂窝射频';
		b = M.v('btn-wifi'); b.classList.toggle('on', !!wf.up);
		if (!this._wifiBusy) b.textContent = 'Wi-Fi 热点';
		b.disabled = !!this._wifiBusy || wf.available === 0 || !!wf.pending;
		b.title = M.translate(wf.error || (wf.pending ? '热点正在应用配置，请稍后重试' : ''));
		M.localize(this._root);
	}
});
