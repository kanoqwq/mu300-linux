'use strict';
'require view';
'require rpc';
'require poll';
'require mu300.common as M';
'require mu300.cpu-cores as Cores';
'require mu300.cpu-idle as Idle';

var get = rpc.declare({ object: 'mu300dash', method: 'cpu_get', expect: { '': {} } });
var apply = rpc.declare({ object: 'mu300dash', method: 'cpu_apply', params: ['payload'], expect: { '': {} } });
var getVoltage = rpc.declare({ object: 'mu300dash', method: 'cpu_voltage_get', expect: { '': {} } });
var saveVoltage = rpc.declare({ object: 'mu300dash', method: 'cpu_voltage_save', params: ['payload'], expect: { '': {} } });
function node(tag, text, cls) {
    var e = document.createElement(tag); if (text != null) e.textContent = text;
    if (cls) e.className = cls; return e;
}
function mhz(n) { return n == null ? '--' : (n / 1000) + ' MHz'; }
function degrees(n) { return n == null ? '--' : (n / 1000).toFixed(1) + ' °C'; }
function select(values, value, label) {
    var e = node('select');
    values.forEach(function(v) { var o = node('option', label ? label(v) : v); o.value = v; e.appendChild(o); });
    e.value = value; return e;
}
function field(name, input) { var e = node('label', null, 'mud-cpu-field'); e.append(node('span', M.translate(name)), input); return e; }
function heading(title, badge, description) {
    var head = node('div', null, 'mud-cpu-heading'), copy = node('div');
    copy.append(node('h3', M.translate(title)));
    if (description) copy.append(node('p', M.translate(description), 'mud-note'));
    head.append(copy, node('span', M.translate(badge), 'mud-cpu-badge'));
    return head;
}
function clusterTitle(cpus) {
    var head = node('div', null, 'mud-cpu-cluster-head');
    head.append(node('h4', 'CPU ' + cpus.trim().replace(/\s+/g, ', ')));
    return head;
}

var CSS = `
.mud-cpu{max-width:1200px;margin:0 auto;padding:4px 0 12px}
.mud-cpu .mud-cpu-title{margin:0 0 20px;padding:0 2px}
.mud-cpu .mud-cpu-title h2{margin:0 0 8px;padding:0;border:0;font-size:1.5rem;font-weight:650;letter-spacing:-.03em;color:var(--text,#222)}
.mud-cpu .mud-note{margin:0;color:var(--text-muted,#777);font-size:.77rem;line-height:1.65}
.mud-cpu .mud-cpu-section{padding:22px;margin:0 0 20px;overflow-wrap:anywhere}
.mud-cpu .mud-cpu-section:hover{border-color:var(--hairline,var(--border,#e3e6ea))}
.mud-cpu-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:20px}
.mud-cpu-heading>div{min-width:0;flex:1}
.mud-cpu .mud-cpu-heading h3{margin:0 0 6px;padding:0;border:0;font-size:1rem;font-weight:650;letter-spacing:0;color:var(--text,#222)}
.mud-cpu-badge{flex-shrink:0;border:1px solid var(--hairline,#ddd);border-radius:99px;padding:4px 10px;color:var(--text-muted,#777);background:var(--surface-sunken,rgba(127,127,127,.05));font-size:.7rem;line-height:1.5}
.mud-cpu-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:14px;align-items:start}
.mud-cpu-cluster{min-width:0;padding:18px;border:1px solid var(--hairline,#ddd);border-radius:12px;background:color-mix(in srgb,var(--surface-sunken,#f4f5f7) 55%,var(--surface,#fff))}
.mud-cpu-cluster-head{display:flex;align-items:center;gap:10px;margin-bottom:18px}
.mud-cpu-cluster-head::before{content:'';width:8px;height:8px;border-radius:3px;background:var(--brand,#2f7bf6);opacity:.8;flex-shrink:0}
.mud-cpu .mud-cpu-cluster-head h4{font-size:.85rem;font-weight:650;letter-spacing:.015em;margin:0;padding:0;border:0;color:var(--text,#222)}
.mud-cpu-metric{display:flex;flex-direction:column;gap:4px;margin:0 0 20px}
.mud-cpu-metric>span{font-size:.72rem;color:var(--text-muted,#777)}
.mud-cpu-metric>div{display:flex;align-items:baseline;gap:7px;font-variant-numeric:tabular-nums;line-height:1.2}
.mud-cpu-metric strong{font-size:1.8rem;font-weight:650;letter-spacing:-.04em;color:var(--text,#222)}
.mud-cpu-metric small{font-size:.78rem;color:var(--text-muted,#777)}
.mud-cpu-field{display:flex;flex-direction:column;gap:7px;margin:0 0 14px;min-width:0;font-size:.75rem;color:var(--text-muted,#777)}
.mud-cpu .mud-cpu-field select,.mud-cpu .mud-cpu-field input{width:100%;max-width:none;min-width:0;box-sizing:border-box;margin:0;min-height:40px;padding:8px 10px;border:1px solid var(--hairline,#ddd);border-radius:8px;background:var(--surface,#fff);color:var(--text,#222);box-shadow:none;font-size:.84rem;font-variant-numeric:tabular-nums}
.mud-cpu .mud-cpu-field :is(input,select):focus-visible,.mud-cpu-table>button:focus-visible{outline:2px solid var(--brand,#2f7bf6);outline-offset:2px}
.mud-cpu .mud-cpu-field :disabled{opacity:.5;cursor:not-allowed}
.mud-cpu-presets{display:flex;flex-wrap:wrap;gap:6px;margin:-3px 0 14px}
.mud-cpu .mud-cpu-presets .mud-btn{flex:1;padding:5px 8px;min-height:32px;font-size:.7rem;white-space:nowrap;font-variant-numeric:tabular-nums}
.mud-cpu .mud-cpu-preset-note{margin:0 0 16px;font-size:.7rem}
.mud-dlg .mud-cpu-confirm-values{margin:12px 0 0;white-space:normal;font-variant-numeric:tabular-nums}
.mud-cpu-confirm-values>div{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 16px;padding:7px 0;border-top:1px solid var(--hairline,#ddd)}
.mud-cpu-confirm-values strong{color:var(--text,#222);white-space:nowrap}
.mud-cpu-range{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
.mud-cpu .mud-cpu-meta{font-size:.65rem;line-height:1.6;color:var(--text-subtle,var(--text-muted,#888));padding-top:10px;border-top:1px solid var(--hairline,#ddd);overflow-wrap:anywhere}
.mud-cpu-actions{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:flex-end;padding-top:18px;margin-top:18px;border-top:1px solid var(--hairline,#ddd)}
.mud-cpu .mud-cpu-actions .mud-btn{min-height:38px;padding:8px 16px;line-height:1.5;white-space:normal;text-align:center}
.mud-cpu-persist{display:flex;gap:10px;align-items:flex-start;flex:1;min-width:min(100%,240px);margin:0;color:var(--text,#222);cursor:pointer}
.mud-cpu-persist input{margin:3px 0 0;flex-shrink:0;accent-color:var(--brand,#2f7bf6)}
.mud-cpu-persist>span{display:flex;flex-direction:column;gap:3px;max-width:620px;font-size:.8rem}
.mud-cpu-persist .mud-note{font-size:.7rem}
.mud-cpu-table{border-top:1px solid var(--hairline,#ddd);margin-top:6px;padding-top:12px}
.mud-cpu-table>button{display:flex;align-items:center;justify-content:space-between;gap:12px;width:100%;cursor:pointer;font-size:.72rem;color:var(--text-muted,#777);line-height:1.6;padding:2px 0;border:0;border-radius:0;background:transparent;box-shadow:none;text-align:left;white-space:normal}
.mud-cpu-table>button::after{content:'↗';color:var(--brand,#2f7bf6);flex-shrink:0}
.mud-dlg .mud-cpu-voltage-scroll{max-height:min(55vh,420px);overflow:auto;overscroll-behavior:contain;white-space:normal;margin-top:12px}
.mud-dlg .mud-cpu-voltage-table{width:100%;margin:0;border-collapse:collapse;font-size:.78rem;font-variant-numeric:tabular-nums;table-layout:fixed}
.mud-dlg .mud-cpu-voltage-table :is(td,th){padding:8px 10px;border:0;border-bottom:1px solid var(--hairline,#ddd);background:transparent;color:var(--text,#222);overflow-wrap:anywhere;text-align:left}
.mud-dlg .mud-cpu-voltage-table th{position:sticky;top:0;background:var(--surface,#fff);font-weight:600}
.mud-dlg .mud-cpu-voltage-table :is(td,th):last-child{text-align:right}
.mud-cpu .mud-cpu-status{flex:1;margin:0;min-width:min(100%,220px);font-size:.73rem;line-height:1.6;color:var(--success,#368364)}
.mud-cpu .mud-cpu-status[data-pending="true"]{color:var(--warning,#ad7420)}
.mud-cpu-status::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor;margin-right:7px;vertical-align:1px}
.mud-cpu .mud-cpu-warning{margin-top:16px;padding:12px 14px;border-left:2px solid var(--warning,#ad7420);border-radius:0 6px 6px 0;background:color-mix(in srgb,var(--warning,#ad7420) 5%,var(--surface,#fff));font-size:.7rem}
.mud-cpu-thermal-trips{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,200px),1fr));gap:14px;margin-top:16px}
.mud-cpu-thermal-readonly{display:block;padding:10px 0;color:var(--text,#222);font-size:.9rem;font-variant-numeric:tabular-nums}
.mud-cpu-thermal-cooling{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}
.mud-cpu-sensors{margin-top:20px;border-top:1px solid var(--hairline,#ddd);padding-top:16px}
.mud-cpu-sensors>summary{cursor:pointer;font-size:.8rem;color:var(--text,#222)}
.mud-cpu-sensor-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,215px),1fr));gap:8px 20px;margin-top:14px}
.mud-cpu-sensor{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--hairline,#ddd);font-size:.75rem;color:var(--text-muted,#777)}
.mud-cpu-sensor strong{flex-shrink:0;color:var(--text,#222);font-variant-numeric:tabular-nums}
@media(max-width:600px){.mud-cpu .mud-cpu-section{padding:16px}.mud-cpu-heading{flex-wrap:wrap;gap:8px;margin-bottom:16px}.mud-cpu-heading>div{flex-basis:100%}.mud-cpu-cluster{padding:16px}.mud-cpu-grid{grid-template-columns:minmax(0,1fr)}.mud-cpu-actions{gap:12px}.mud-cpu-actions>.mud-cpu-persist,.mud-cpu-actions>.mud-cpu-status{flex-basis:100%}.mud-cpu .mud-cpu-actions .mud-btn{flex:1}.mud-cpu .mud-cpu-title h2{font-size:1.3rem}}
`;

return view.extend({
    load: function() { return Promise.all([get(), getVoltage().catch(function() { return {}; })]).then(function(r) { r[0].voltage = r[1]; return r[0]; }); },
    render: function(data) {
        M.injectCss(); M.localizeMenu();
        var self = this, root = this.root = node('div', null, 'mud mud-cpu');
        root.appendChild(node('style', CSS));
        var title = node('div', null, 'mud-cpu-title');
        title.append(node('h2', M.translate('CPU 设置')), node('p', M.translate('频率、电压、温控分别保存。频率和温控即时生效，电压重启后生效。'), 'mud-note'));
        root.append(title,Cores.render(),Idle.render());
        var frequency = node('section', null, 'mud-card mud-cpu-section');
        frequency.append(heading('频率与调速策略', '即时生效'));
        var grid = node('div', null, 'mud-cpu-grid'); frequency.append(grid); root.append(frequency);
        this.inputs = [];
        (data.policies || []).forEach(function(p) {
            var card = node('div', null, 'mud-cpu-cluster'), current = node('strong', p.current == null ? '--' : p.current / 1000, 'mud-cpu-current'); current.dataset.policy = p.id;
            var metric = node('div', null, 'mud-cpu-metric'), value = node('div'); value.append(current, node('small', 'MHz'));
            metric.append(node('span', M.translate('当前频率')), value);
            card.append(clusterTitle(p.cpus), metric);
            var gov = select(p.governors, p.governor), lo, hi;
            if (p.frequencies.length) { lo = select(p.frequencies, p.min, mhz); hi = select(p.frequencies, p.max, mhz); }
            else { lo = node('input'); hi = node('input'); [lo, hi].forEach(function(e) { e.type = 'number'; e.min = p.hardware_min; e.max = p.hardware_max; e.step = 1; }); lo.value = p.min; hi.value = p.max; }
            [gov, lo, hi].forEach(function(e) { e.disabled = !p.writable; });
            var range = node('div', null, 'mud-cpu-range');
            range.append(field(p.frequencies.length ? '最低频率' : '最低频率（kHz）', lo), field(p.frequencies.length ? '最高频率' : '最高频率（kHz）', hi));
            card.append(field('调速器', gov), range);
            card.append(node('div', p.driver + ' · ' + mhz(p.hardware_min) + ' – ' + mhz(p.hardware_max), 'mud-cpu-meta'));
            grid.append(card); if (p.writable) self.inputs.push({ p: p, gov: gov, lo: lo, hi: hi });
        });
        if (!(data.policies || []).length) frequency.append(node('div', M.translate('CPU 调频驱动尚未就绪'), 'mud-note'));
        var thermalSection = node('section', null, 'mud-card mud-cpu-section');
        thermalSection.append(heading('温控管理', '即时生效', '按实际温区调整被动阈值；临界保护与回差只读，保留内核降频绑定。'));
        this.thermalInputs = [];
        this.thermalLive = [];
        (data.zones || []).filter(function(z) { return z.trips.length; }).forEach(function(z) {
            var card = node('div', null, 'mud-cpu-cluster'), head = node('div', null, 'mud-cpu-heading');
            var copy = node('div'), temp = node('strong', degrees(z.temp), 'mud-cpu-thermal-readonly');
            copy.append(node('h4', z.name), node('p', M.translate(z.mode === 'disabled' ? '温区已禁用' : '内核温控') + ' · ' + (z.policy || '--'), 'mud-note'));
            head.append(copy, temp); card.append(head);
            var live = { name: z.name, temp: temp, cooling: [] }; self.thermalLive.push(live);
            var trips = node('div', null, 'mud-cpu-thermal-trips');
            z.trips.forEach(function(t) {
                var title = t.type === 'passive' ? (t.throttles_cpu ? '降频触发温度' : '被动温控起点') : t.type === 'critical' ? '临界保护温度' : t.type === 'hot' ? '高温保护温度' : '只读阈值';
                var input;
                if (t.writable) {
                    input = node('input'); input.type = 'number'; input.min = z.minimum / 1000; input.max = z.maximum / 1000; input.step = z.step / 1000; input.value = t.temp / 1000;
                    self.thermalInputs.push({ zone: z.name, trip: t.id, input: input });
                } else input = node('span', degrees(t.temp), 'mud-cpu-thermal-readonly');
                var row = field(title, input);
                row.append(node('small', '#' + t.id + ' · ' + M.translate('回差') + ' ' + degrees(t.hysteresis) + (t.writable ? ' · ' + input.min + '–' + input.max + ' °C' : ' · ' + M.translate('只读')), 'mud-note'));
                trips.append(row);
            });
            card.append(trips);
            var devices = node('div', null, 'mud-cpu-thermal-cooling');
            z.cooling.forEach(function(c) {
                var badge = node('span', c.type + ' · ' + c.state + '/' + c.max_state, 'mud-cpu-badge');
                badge.title = M.translate('降温等级（当前 / 最大）'); devices.append(badge); live.cooling.push({ id: c.id, node: badge });
            });
            card.append(devices); thermalSection.append(card);
        });
        if (!self.thermalInputs.length) thermalSection.append(node('p', M.translate('当前内核没有可写的 CPU 被动温控阈值，仅显示实际传感器。'), 'mud-note'));
        var sensors = node('details', null, 'mud-cpu-sensors'), sensorGrid = node('div', null, 'mud-cpu-sensor-grid');
        sensors.append(node('summary', M.translate('温度传感器') + ' · ' + (data.zones || []).filter(function(z) { return !z.trips.length; }).length));
        (data.zones || []).filter(function(z) { return !z.trips.length; }).forEach(function(z) {
            var row = node('div', null, 'mud-cpu-sensor'), temp = node('strong', degrees(z.temp));
            row.append(node('span', z.name), temp); sensorGrid.append(row); self.thermalLive.push({ name: z.name, temp: temp, cooling: [] });
        });
        sensors.append(sensorGrid); thermalSection.append(sensors);
        thermalSection.append(node('p', M.translate('提高阈值可能增加温度；此处不会关闭内核临界保护或设备紧急保护。'), 'mud-note mud-cpu-warning'));
        function controlsFor(scope, target, enabled) {
            var isThermal = scope === 'thermal';
            target.dataset.scope = scope;
            var controls = node('div', null, 'mud-cpu-actions'), persist = node('input');
            persist.type = 'checkbox'; persist.checked = !!(isThermal ? data.thermal_persist : data.persist);
            if (isThermal) self.thermalPersist = persist; else self.persist = persist;
            var label = node('label', null, 'mud-cpu-persist'), copy = node('span');
            copy.append(node('span', M.translate('开机自动应用')), node('span', M.translate('仅控制本区域；未勾选时只应用本次，并取消本区域的开机设置。'), 'mud-note'));
            label.append(persist, copy);
            var save = node('button', M.translate(isThermal ? '应用温控设置' : '应用频率设置'), 'mud-btn on');
            var reset = node('button', M.translate('恢复启动基线'), 'mud-btn');
            save.disabled = reset.disabled = persist.disabled = !enabled;
            controls.append(label, reset, save); target.append(controls);
            function reflect(r) {
                // Never replace unsaved edits or persistence choices in another section.
                if (isThermal) {
                    (r.zones || []).forEach(function(z) { self.thermalInputs.forEach(function(x) { if (x.zone === z.name) z.trips.forEach(function(t) { if (x.trip === t.id) x.input.value = t.temp / 1000; }); }); });
                    persist.checked = !!r.thermal_persist;
                } else {
                    (r.policies || []).forEach(function(p) { self.inputs.forEach(function(x) { if (x.p.id === p.id) { x.p = p; x.gov.value = p.governor; x.lo.value = p.min; x.hi.value = p.max; } }); });
                    persist.checked = !!r.persist;
                }
            }
            function submit(defaults) {
                if (self.busy) return;
                var payload = { scope: scope, persist: persist.checked };
                if (defaults) payload = { scope: scope, reset: true };
                else if (isThermal) {
                    if (self.thermalInputs.some(function(x) { return x.input.value.trim() === '' || !x.input.checkValidity(); })) { M.toast('温控阈值超出保护范围', { type: 'error' }); return; }
                    payload.thermal = self.thermalInputs.map(function(x) { return { zone: x.zone, trip: x.trip, temp: Math.round(Number(x.input.value) * 1000) }; });
                } else {
                    payload.changes = self.inputs.map(function(x) { return { id: x.p.id, cpus: x.p.cpus, governor: x.gov.value, min: Number(x.lo.value), max: Number(x.hi.value) }; });
                    if (self.inputs.some(function(x) { return x.lo.value.trim() === '' || x.hi.value.trim() === ''; }) ||
                        payload.changes.some(function(c) { return !Number.isInteger(c.min) || !Number.isInteger(c.max) || c.min > c.max; })) { M.toast('CPU 配置无效', { type: 'error' }); return; }
                }
                var title = defaults ? (isThermal ? '恢复温控启动基线？' : '恢复频率启动基线？') : (isThermal ? '应用温控设置？' : '应用频率设置？');
                var message = defaults ? (isThermal ? '仅恢复本次启动记录的温控阈值并取消温控开机应用；不修改频率或电压设置。' : '仅恢复本次启动记录的频率并取消频率开机应用；不修改温控或电压设置。') :
                    (isThermal ? '温控阈值将立即应用；提高阈值可能增加温度。不修改频率或电压设置。' : '降低频率可能降低吞吐量；提高最低频率可能增加功耗和温度。不修改温控或电压设置。');
                self.busy = true;
                M.confirmBox(title, message).then(function(yes) {
                    if (!yes) return;
                    var button = defaults ? reset : save; M.busy(button, true);
                    var toast = M.toast(isThermal ? '正在应用温控设置…' : '正在应用频率设置…', { type: 'busy', timeout: 0 });
                    return apply(JSON.stringify(payload)).then(function(r) {
                        if (!r.ok) throw new Error(r.rollback === false ? 'CPU 设置失败且回滚不完整，请检查实际状态' : r.error || 'CPU 配置无效');
                        reflect(r); M.toast(isThermal ? '温控设置已应用' : '频率设置已应用', { type: 'success' });
                    }).catch(function(e) { M.toast(M.translate(e.message), { type: 'error' }); }).finally(function() { toast.close(); M.busy(button, false); });
                }).finally(function() { self.busy = false; });
            }
            save.onclick = function() { submit(false); };
            reset.onclick = function() { submit(true); };
        }
        controlsFor('frequency', frequency, !!this.inputs.length);
        controlsFor('thermal', thermalSection, !!this.thermalInputs.length);
        var voltage = data.voltage || {}, section = node('section', null, 'mud-card mud-cpu-section');
        section.append(heading('CPU 电压偏移', '重启生效', '按 CPU 簇设置，步进 3.125 mV。偏移会作用于固件对应的电压表；不增加超频档位。'));
        section.append(node('p', M.translate('预设仅填入小幅偏移，不会直接保存；非零偏移仍需验证稳定性，不保证每颗芯片都安全。'), 'mud-note mud-cpu-preset-note'));
        section.dataset.scope = 'voltage';
        root.append(section, thermalSection);
        var voltageGrid = node('div', null, 'mud-cpu-grid'), offsets = [], pending = node('p', null, 'mud-cpu-status'); pending.setAttribute('role', 'status');
        function updatePending(r) { pending.dataset.pending = String(!!r.pending); pending.textContent = M.translate(r.pending ? '已保存，将在下次重启时应用' : '当前电压偏移与保存值一致'); }
        updatePending(voltage);
        pending.hidden = !(voltage.domains || []).length;
        if (!voltage.supported) section.append(node('p', M.translate('当前内核或固件不支持电压调整'), 'mud-note'));
        if (voltage.recovery) section.append(node('p', M.translate(voltage.recovery === 'invalid_boot_record' ? '电压启动记录损坏，已暂停自动应用；设置仍保留，重新保存可重试。' : '上次电压设置未完成启动确认，已使用默认电压；设置仍保留，重新保存可重试。'), 'mud-note'));
        (voltage.domains || []).forEach(function(d) {
            var card = node('div', null, 'mud-cpu-cluster'), input = node('input');
            input.type = 'number'; input.min = -50; input.max = 25; input.step = 3.125;
            input.value = ((voltage.offsets || [0, 0, 0])[d.id] || 0) / 1000;
            input.disabled = !voltage.supported;
            var metric = node('div', null, 'mud-cpu-metric'), value = node('div');
            value.append(node('strong', d.offset / 1000), node('small', 'mV'));
            metric.append(node('span', M.translate('当前偏移')), value);
            card.append(clusterTitle(d.cpus), metric, field('下次启动偏移（mV）', input));
            var presets = node('div', null, 'mud-cpu-presets');
            presets.setAttribute('role', 'group'); presets.setAttribute('aria-label', M.translate('偏移预设'));
            var presetButtons = [0, -3.125, -6.25].map(function(offset) {
                var button = node('button', offset === 0 ? M.translate('默认电压') : offset + ' mV', 'mud-btn');
                button.type = 'button'; button.disabled = !voltage.supported;
                button.onclick = function() { input.value = offset; updatePresets(); };
                presets.append(button); return { button: button, offset: offset };
            });
            function updatePresets() {
                presetButtons.forEach(function(p) {
                    var selected = input.value.trim() !== '' && Number(input.value) === p.offset;
                    p.button.classList.toggle('on', selected); p.button.setAttribute('aria-pressed', String(selected));
                });
            }
            input.addEventListener('input', updatePresets); updatePresets(); card.append(presets);
            var tableAction = node('div', null, 'mud-cpu-table'), tableButton = node('button', M.translate('当前固件电压表'));
            tableButton.type = 'button'; tableButton.setAttribute('aria-haspopup', 'dialog');
            tableButton.onclick = function() {
                if (self.tableOpen) return;
                self.tableOpen = true;
                var scroll = node('div', null, 'mud-cpu-voltage-scroll'), table = node('table', null, 'mud-cpu-voltage-table');
                var head = node('thead'), headers = node('tr'), body = node('tbody');
                ['MHz', 'mV'].forEach(function(unit) { var cell = node('th', unit); cell.scope = 'col'; headers.append(cell); });
                head.append(headers);
                (d.table || []).forEach(function(pair) { var row = node('tr'); row.append(node('td', pair.khz / 1000), node('td', pair.uv / 1000)); body.append(row); });
                table.append(head, body); scroll.append(table);
                M.alertBox(M.translate('当前固件电压表') + ' · CPU ' + d.cpus.trim().replace(/\s+/g, ', '), '', { content: scroll }).finally(function() {
                    self.tableOpen = false;
                    if (tableButton.isConnected) tableButton.focus({ preventScroll: true });
                });
            };
            tableAction.append(tableButton); card.append(tableAction); voltageGrid.append(card); offsets.push({ id: d.id, cpus: d.cpus, active: d.offset, input: input, updatePresets: updatePresets });
        });
        var save = node('button', M.translate('保存电压设置'), 'mud-btn on'), reset = node('button', M.translate('恢复默认电压'), 'mud-btn');
        save.disabled = !voltage.supported;
        var actions = node('div', null, 'mud-cpu-actions'); actions.append(pending, save, reset);
        section.append(voltageGrid, actions, node('p', M.translate('调压可能导致死机或数据丢失，请从小幅调整开始。完成启动确认后，直接断电会保留设置；首次应用未完成确认时暂停重试。保存不会立即调压或自动重启。'), 'mud-note mud-cpu-warning'));
        function submitVoltage(defaults) {
            if (self.busy) return;
            var count = voltage.domain_count || (voltage.offsets || [0, 0, 0]).length;
            var values = Array(count).fill(0);
            if (!defaults) offsets.forEach(function(x) { values[x.id] = x.input.value.trim() === '' ? NaN : Number(x.input.value) * 1000; });
            if (values.some(function(v) { return !Number.isInteger(v) || v < -50000 || v > 25000 || v % 3125; })) { M.toast(M.translate('CPU 电压配置无效'), { type: 'error' }); return; }
            var confirmation = node('div', null, 'mud-cpu-confirm-values');
            confirmation.append(node('p', M.translate('当前偏移 → 下次启动偏移')));
            offsets.forEach(function(x) {
                var row = node('div');
                row.append(node('span', 'CPU ' + x.cpus.trim().replace(/\s+/g, ', ')), node('strong', (x.active / 1000) + ' → ' + (values[x.id] / 1000) + ' mV'));
                confirmation.append(row);
            });
            self.busy = true;
            M.confirmBox(defaults ? '恢复默认电压？' : '保存电压设置？', '请核对各簇偏移。非零偏移可能导致死机或数据损坏，预设也不保证稳定。确认仅保存配置，下次重启生效，不会立即调压。', { content: confirmation, danger: values.some(function(v) { return v !== 0; }), okText: '确认保存' }).then(function(yes) {
                if (!yes) return;
                var target = defaults ? reset : save; M.busy(target, true);
                var toast = M.toast('正在保存电压设置…', { type: 'busy', timeout: 0 });
                return saveVoltage(JSON.stringify({ offsets: values })).then(function(r) {
                    if (!r.ok) throw new Error(r.error || 'CPU 电压配置无效');
                    offsets.forEach(function(x) { x.input.value = r.offsets[x.id] / 1000; x.updatePresets(); });
                    updatePending(r); M.toast(M.translate('电压设置已保存，重启后生效'), { type: 'success' });
                }).catch(function(e) { M.toast(M.translate(e.message), { type: 'error' }); }).finally(function() { toast.close(); M.busy(target, false); });
            }).finally(function() { self.busy = false; });
        }
        save.onclick = function() { submitVoltage(false); };
        reset.onclick = function() { submitVoltage(true); };
        this.refresh = function() {
            if (document.hidden || !root.isConnected || self.busy || self.inflight) return Promise.resolve();
            self.inflight = true;
            return get().then(function(r) {
                if (!root.isConnected) return;
                (r.policies || []).forEach(function(p) { var e = root.querySelector('[data-policy="' + p.id + '"]'); if (e) e.textContent = p.current == null ? '--' : p.current / 1000; });
                self.thermalLive.forEach(function(x) {
                    var z = (r.zones || []).find(function(z) { return z.name === x.name; });
                    x.temp.textContent = degrees(z ? z.temp : null);
                    x.cooling.forEach(function(entry) {
                        var c = z && z.cooling.find(function(c) { return c.id === entry.id; });
                        entry.node.textContent = c ? c.type + ' · ' + c.state + '/' + c.max_state : '--';
                    });
                });
            }).catch(function() {}).finally(function() { self.inflight = false; });
        };
        poll.add(this.refresh, 2);
        return root;
    },
    unload: function() { poll.remove(this.refresh); },
    handleSaveApply: null, handleSave: null, handleReset: null
});
