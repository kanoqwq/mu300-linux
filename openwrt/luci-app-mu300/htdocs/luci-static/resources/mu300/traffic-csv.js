'use strict';
'require baseclass';

return baseclass.extend({
    attach: function(view, M, currentSlot, slotName) {
        var box = view.q('csv'), busy = false, file = null;
        var t = function(s) { return M.translate(s); };
        var errors = {
            csv_invalid: 'CSV 格式或数据无效，请使用本页面导出的格式',
            csv_conflict: '每日记录与月度汇总冲突，请核对 CSV 或选择替换全部历史',
            csv_retention: '合并结果超过历史保留范围，请选择替换全部历史或减少导入记录',
            csv_backup_missing: '暂无恢复前备份',
            storage_failed: '统计数据保存失败', clock_unsynced: '系统时间尚未同步',
            traffic_unavailable: '流量统计服务暂不可用', invalid_slot: '请选择有效的 SIM 卡槽',
            invalid_sim: '请选择有效的 SIM 卡槽'
        };
        box.innerHTML = `<h3>流量备份与报表</h3>
<div class="mud-note">导出当前卡槽保留的全部每日和月度记录，字节数可用于报表。恢复不修改套餐设置。</div>
<div class="mud-tr-actions"><button class="mud-btn" data-csv="export">导出 CSV</button><button class="mud-btn" data-csv="backup">下载上次恢复前备份</button></div>
<div class="mud-tr-form">
 <div class="mud-tr-field"><label for="mud-tr-csv-file">备份文件</label><input id="mud-tr-csv-file" data-csv="file" type="file" accept=".csv,text/csv"></div>
 <div class="mud-tr-field"><label for="mud-tr-csv-mode">恢复方式</label><select id="mud-tr-csv-mode" data-csv="mode"><option value="merge">合并导入日期（相同日期覆盖）</option><option value="replace">替换当前卡全部历史</option></select></div>
</div>
<div class="mud-note">合并保留未导入的日期；替换会移除当前卡原有历史。恢复前会校验日月总量并保留一份原账本，同一文件重复恢复不会累加。</div>
<div class="mud-tr-actions"><button class="mud-btn" data-csv="restore">预览并恢复 CSV</button></div>`;
        M.localize(box);
        var q = function(k) { return box.querySelector('[data-csv="' + k + '"]'); };
        function slot() { return String(currentSlot()); }
        function blocked() { return busy || view._disposed || view._mutating || view._confirming || view.clearing; }
        function update(data) {
            box.querySelectorAll('button,input,select').forEach(function(el) { el.disabled = busy; });
            q('backup').disabled = busy || !data.csv_backup;
            q('restore').disabled = busy || !file;
        }
        function ok(result) {
            if (!result || !result.ok) throw new Error(errors[result && result.error] || '流量备份或恢复失败');
            return result;
        }
        function download(csv, name) {
            var url = URL.createObjectURL(new Blob(['\ufeff', csv], {type:'text/csv;charset=utf-8'}));
            var link = document.createElement('a'); link.href = url; link.download = name;
            document.body.append(link); link.click(); link.remove();
            setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
        }
        async function exportCsv(backup) {
            if (blocked()) return;
            var target = slot();
            if (!view.data.status || String(view.data.status.slot) !== target) { view.refresh(); return; }
            busy = true; update(view.data || {});
            try {
                var result = ok(await M.callTrafficExport(target, backup));
                if (view._disposed) return;
                download(result.csv, 'traffic-' + (backup ? 'before-restore-' : '') + 'sim' + (+target + 1) + '-' + new Date().toISOString().slice(0,10) + '.csv');
            } catch(e) { if (!view._disposed) M.toast(t(e.message), {type:'error'}); }
            finally { busy = false; update(view.data || {}); }
        }
        q('export').onclick = function() { exportCsv(false); };
        q('backup').onclick = function() { exportCsv(true); };
        q('file').onchange = function() { file = q('file').files[0] || null; update(view.data || {}); };
        q('restore').onclick = async function() {
            if (blocked() || !file) return;
            var target = slot(), picked = file, mode = q('mode').value, toast;
            if (!view.data.status || String(view.data.status.slot) !== target) { view.refresh(); return; }
            busy = true; update(view.data || {});
            try {
                if (picked.size > 65536) throw new Error(errors.csv_invalid);
                var text = await picked.text();
                var preview = ok(await M.callTrafficImport(target, text, mode, false)).preview;
                if (view._disposed) return;
                if (target !== slot()) throw new Error('查看卡槽已改变，请重新操作');
                var details = t('备份统计槽位：') + preview.source_slot + ' (IMEI' + (+preview.source_slot + 1) + ')' + '\n' +
                    t('恢复到：') + slotName(target) + '\n' +
                    t('记录范围：') + (preview.first || '--') + ' → ' + (preview.last || '--') + '\n' +
                    t('每日记录：') + preview.days + ' · ' + t('月度记录：') + preview.months + '\n' +
                    t('重合记录：') + preview.conflicts + '\n' +
                    t(mode === 'replace' ? '将替换当前卡全部历史，原账本会先备份。' : '仅覆盖导入日期，其他记录保留，原账本会先备份。');
                if (!await M.confirmBox('恢复流量记录？', details, {danger:true,okText:'恢复'})) return;
                if (view._disposed) return;
                if (target !== slot()) throw new Error('查看卡槽已改变，请重新操作');
                toast = M.toast('正在恢复流量记录…', {type:'busy',timeout:0});
                ok(await M.callTrafficImport(target, text, mode, true));
                toast.update('流量记录已恢复', 'success');
                file = null; q('file').value = '';
                await view.refresh();
            } catch(e) {
                if (!view._disposed) {
                    if (toast) toast.update(t(e.message), 'error');
                    else toast = M.toast(t(e.message), {type:'error'});
                }
            } finally {
                busy = false; update(view.data || {});
                if (toast) setTimeout(function() { toast.close(); }, 3000);
            }
        };
        update(view.data || {});
        return { get busy() { return busy; }, update:update };
    }
});
