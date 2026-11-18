'use strict';
'require baseclass';
'require rpc';

var get = rpc.declare({object:'mu300dash', method:'traffic_policy_get', params:['slot'], expect:{'':{}}, nobatch:true});
var set = rpc.declare({object:'mu300dash', method:'traffic_policy_set', params:['payload'], expect:{'':{}}, nobatch:true});
var run = rpc.declare({object:'mu300dash', method:'traffic_policy_run', params:['confirm'], expect:{'':{}}, nobatch:true});

return baseclass.extend({
 attach: function(view, M, selected) {
    var host = view.root.querySelector('#mud-tr-controls'), epoch = 0, disposed = false;
    var slot = null, data = {}, usage = {}, dirty = {quota:false, schedule:false}, busy = false, pending = false;
    host.innerHTML = `
<div class="mud-tr-grid">
 <section class="mud-card">
  <h3>达量断网</h3>
  <label class="mud-tr-toggle"><input id="mud-quota-enabled" type="checkbox"><span>启用当前卡保护</span></label>
  <div class="mud-tr-field"><label for="mud-quota-limit">断网线（GB）</label><input id="mud-quota-limit" type="number" min="0.001" max="100000" step="0.001"></div>
  <div class="mud-tr-line"><span>本周期已用</span><b id="mud-quota-used">--</b></div>
  <div class="mud-tr-line"><span>距离断网</span><b id="mud-quota-remaining">--</b></div>
  <div class="mud-tr-line"><span>状态</span><span id="mud-quota-state">--</span></div>
  <div class="mud-tr-line"><span>下次自动恢复</span><span id="mud-quota-reset">--</span></div>
  <div class="mud-note">按当前查看的 SIM 保存，使用上方套餐周期与计量方式。达到断网线时停用蜂窝 WAN，所有依赖此出口的设备断网；不关闭射频。关闭保护、周期重置或切换到未达量的上网卡后恢复。</div>
  <div class="mud-note">约每 10 秒检查一次，可能有少量超出；本地统计与运营商计费可能不同。</div>
  <div class="mud-tr-actions"><button class="mud-btn on" id="mud-quota-save" disabled>保存保护设置</button></div>
 </section>
 <section class="mud-card">
  <h3>定时重启网络</h3>
  <label class="mud-tr-toggle"><input id="mud-schedule-enabled" type="checkbox"><span>启用每日定时重启</span></label>
  <div class="mud-tr-field"><label for="mud-schedule-time">每天执行时间</label><input id="mud-schedule-time" type="time" value="07:00" required></div>
  <div class="mud-tr-line"><span>本地时间</span><span id="mud-schedule-now">--</span></div>
  <div class="mud-tr-line"><span>上次执行</span><span id="mud-schedule-last">--</span></div>
  <div class="mud-tr-line"><span>下次执行</span><span id="mud-schedule-next">--</span></div>
  <div class="mud-note">全局设置，每次对当前上网卡执行与锁频后相同的蜂窝网络栈重启，不以是否处于 5G 为条件。不重启系统或局域网。射频、WAN 被手动关闭或达量保护生效时跳过。</div>
  <div class="mud-note">网络会短暂中断。停机错过的任务不补执行；请先校准设备时间。</div>
  <div class="mud-tr-actions"><button class="mud-btn on" id="mud-schedule-save" disabled>保存定时设置</button><button class="mud-btn" id="mud-schedule-run">立即重启一次</button></div>
 </section>
</div>`;
    var q = function(id) { return host.querySelector('#mud-' + id); };
    M.localize(host);
    var results = {running:'正在重启网络…', done:'已完成', radio_off:'已跳过：射频关闭',
      wan_disabled:'已跳过：WAN 已关闭', quota_blocked:'已跳过：达量保护生效',
      missed:'已跳过：错过执行时间', failed:'重启失败'};
    function stamp(n) { return n ? new Date(n * 1000).toLocaleString() : '--'; }
    function paint() {
      var c = data.quota || {}, over = c.enabled && usage.clock_ok && usage.cycle_used >= c.limit_gb * 1e9;
      q('quota-used').textContent = M.fmtTrafficBytes(usage.cycle_used);
      q('quota-remaining').textContent = c.enabled && Number.isFinite(usage.cycle_used) ? M.fmtTrafficBytes(Math.max(0, c.limit_gb * 1e9 - usage.cycle_used)) : '--';
      q('quota-reset').textContent = c.enabled ? usage.cycle_end || '--' : '--';
      var state = !data.ok ? '管理服务暂不可用' : !c.enabled ? '未启用' : data.runtime && data.runtime.error ? '保护检查失败' : !usage.clock_ok ? '系统时间尚未同步' :
        over ? (data.runtime && data.runtime.blocked && String(data.runtime.active_slot) === slot ? 'WAN 已停用' : '已达到断网线') : '正常';
      q('quota-state').textContent = M.translate(state);
      q('quota-state').title = data.runtime && data.runtime.error || '';
      q('schedule-now').textContent = data.local_time || '--';
      q('schedule-next').textContent = data.next_time || '--';
      q('schedule-last').textContent = data.last ? (data.last_time || stamp(data.last.at)) + ' · ' + M.translate(results[data.last.result] || '重启失败') : M.translate('暂无记录');
      q('schedule-last').title = data.last && data.last.detail || '';
    }
    function refresh() {
      if (disposed) return;
      var current = String(selected());
      if (slot !== current) {
        epoch++; pending = false; slot = current; dirty.quota = false; data = {};
        q('quota-enabled').checked = false; q('quota-limit').value = '';
        q('quota-save').disabled = q('schedule-save').disabled = true;
      }
      if (pending) return;
      var id = ++epoch;
      pending = true;
      get(current).then(function(r) {
        if (disposed || id !== epoch || String(selected()) !== current) return;
        data = r || {};
        if (data.ok && String(data.slot) === current) {
          if (!dirty.quota) { q('quota-enabled').checked = data.quota.enabled; q('quota-limit').value = data.quota.limit_gb || ''; }
          if (!dirty.schedule) { q('schedule-enabled').checked = data.schedule.enabled; q('schedule-time').value = data.schedule.time; }
        }
        q('quota-save').disabled = q('schedule-save').disabled = !data.ok || busy;
        paint();
      }).catch(function() { if (!disposed && id === epoch) { data = {}; paint(); } }).finally(function() { if (id === epoch) pending = false; });
    }
    ['quota','schedule'].forEach(function(kind) {
      host.querySelectorAll('[id^="mud-' + kind + '-"]').forEach(function(input) {
        if (input.tagName === 'INPUT') input.addEventListener('input', function() { dirty[kind] = true; });
      });
      q(kind + '-save').onclick = function() {
        if (busy || slot !== String(selected()) || !data.ok) return;
        var p = {};
        if (kind === 'quota') {
          var n = Number(q('quota-limit').value);
          p.quota = {enabled:q('quota-enabled').checked, limit_gb:n};
          if (!Number.isFinite(n) || n < 0 || n > 100000 || p.quota.enabled && n <= 0) { M.toast('配置无效', {type:'error'}); return; }
        } else {
          p.schedule = {enabled:q('schedule-enabled').checked, time:q('schedule-time').value};
          if (!/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(p.schedule.time)) { M.toast('配置无效', {type:'error'}); return; }
        }
        var savedSlot = slot;
        perform(q(kind + '-save'), function() { return set(JSON.stringify({slot:savedSlot, config:p})); }, function() {
          if (kind === 'schedule' || savedSlot === String(selected())) dirty[kind] = false;
        });
      };
    });
    function perform(button, action, accepted, message) {
      busy = true; M.busy(button, true);
      Promise.resolve().then(action).then(function(r) {
        if (!r || !r.ok) throw new Error(r && r.error || 'operation_failed');
        if (accepted) accepted();
        if (!disposed) M.toast(message || '已保存', {type:'success'});
      }).catch(function(e) {
        if (!disposed) M.toast(M.translate(e.message === 'invalid_config' ? '配置无效' : e.message === 'operation_busy' ? '另一项网络设置仍在执行，请稍后重试' : '操作失败') + (e.message === 'invalid_config' || e.message === 'operation_busy' ? '' : ': ' + e.message), {type:'error'});
      }).finally(function() { busy = false; M.busy(button, false); refresh(); });
    }
    q('schedule-run').onclick = function() {
      if (busy) return;
      busy = true;
      M.confirmBox('立即重启网络？', '这会短暂中断当前上网卡的蜂窝网络，不重启系统或局域网。', {okText:'立即重启一次'}).then(function(yes) {
        if (yes && !disposed) perform(q('schedule-run'), function() { return run(true); }, null, '重启任务已提交');
        else busy = false;
      }).catch(function() { busy = false; });
    };
    return { update: function(d) { usage = d.status || {}; paint(); refresh(); },
      dispose: function() { disposed = true; epoch++; } };
 }
});
