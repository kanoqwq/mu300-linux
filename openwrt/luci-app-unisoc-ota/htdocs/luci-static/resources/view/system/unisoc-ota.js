'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

var callStatus = rpc.declare({object:'unisoc-ota', method:'status'});
var callInspect = rpc.declare({object:'unisoc-ota', method:'inspect'});
var callStart = rpc.declare({object:'unisoc-ota', method:'start', params:['id']});
var callRollback = rpc.declare({object:'unisoc-ota', method:'rollback'});
var callReboot = rpc.declare({object:'unisoc-ota', method:'reboot'});

return view.extend({
    load: function() { return callStatus(); },
    render: function(state) {
        var zh = (document.documentElement.lang || '').toLowerCase().indexOf('zh') === 0;
        var tr = function(a,b) { return zh ? a : b; };
        var error = function(r) { if (r && r.error) throw new Error(r.error); return r; };
        var detail = E('div', {style:'margin:18px 0;overflow-wrap:anywhere'});
        var status = E('p');
        var button = E('button', {class:'cbi-button cbi-button-action', disabled:!L.hasViewPermission()}, tr('导入差异包','Import patch'));
        var rollback = E('button', {class:'cbi-button', disabled:!L.hasViewPermission(), style:'margin-inline-start:12px'}, tr('撤销上次补丁','Roll back last patch'));
        var reboot = E('button', {class:'cbi-button', disabled:!L.hasViewPermission(), style:'margin-inline-start:12px'}, tr('重启生效','Reboot'));
        var busy = false, watching = false;
        function show(r) {
            error(r);
            var identity = r.identity || {};
            status.textContent = tr('当前版本：','Current version: ') + (identity.version || '--');
            if (r.job && r.job.status === 'running') {
                busy = true;
                detail.textContent = tr('正在应用补丁：','Applying patch: ') + (r.transaction ? r.transaction.started + ' / ' + r.transaction.total : tr('准备中','Preparing'));
                watch();
            }
            else {
                busy = false;
                if (r.job && r.job.result) {
                    detail.textContent = r.job.result.error || (r.job.status === 'complete' ? tr('操作完成。请重启使所有服务使用新文件。','Complete. Reboot to use all updated files.') : '');
                }
            }
            button.disabled = busy || !L.hasViewPermission();
            rollback.disabled = busy || !L.hasViewPermission() || !(r.transaction && r.transaction.status === 'complete');
            reboot.disabled = busy || !L.hasViewPermission();
        }
        function watch() {
            if (watching) return;
            watching = true;
            function tick() {
                return callStatus().then(function(r) { show(r); if (!busy) {poll.remove(tick); watching=false;} });
            }
            poll.add(tick, 2);
        }
        function failed(e) { ui.addNotification(null, E('p', {}, e.message || String(e)), 'error'); }
        button.onclick = function() {
            if (busy) return;
            return ui.uploadFile('/etc/unisoc-ota/upload.ota', button).then(function() { return callInspect(); }).then(function(r) {
                error(r);
                var m = r.manifest;
                var text = E('div', {}, [E('p', {}, m.base_version + ' → ' + m.target_version),
                    E('p', {}, m.operations.length + tr(' 个系统文件变更；保留配置、插件及用户数据。',' system file changes. Settings, extra plugins and user data are retained.')),
                    E('p', {}, tr('仅导入可信来源的补丁。哈希校验不等同于发布者签名。','Import only trusted patches. Checksums do not authenticate the publisher.'))]);
                if (m.boot) text.appendChild(E('p', {}, tr('包含内核／启动镜像与配套模块更新，完成后重启生效。','Includes kernel/boot and matching module updates. Reboot after completion.')));
                if (!r.applicable) {
                    text.appendChild(E('p', {}, tr('无法应用。冲突文件或依赖：','Cannot apply. File/dependency conflicts: ') + r.conflicts.concat(r.package_conflicts || []).join(', ') + tr('；所需空间：','; required space: ') + r.required_bytes));
                }
                ui.showModal(tr('确认系统补丁','Review system patch'), [text, E('div', {class:'right'}, [
                    E('button', {class:'cbi-button', click:ui.hideModal}, tr('取消','Cancel')),
                    E('button', {class:'cbi-button cbi-button-action', disabled:!r.applicable, style:'margin-inline-start:12px', click:function() {
                        ui.hideModal(); busy=true; button.disabled=true;
                        callStart(r.id).then(error).then(function() {detail.textContent=tr('正在更新，关闭网页不会中断写入。','Updating. Closing this page does not interrupt writes.'); watch();}).catch(failed);
                    }}, tr('应用补丁','Apply patch'))
                ])]);
            }).catch(failed);
        };
        rollback.onclick = function() {
            ui.showModal(tr('撤销上次补丁？','Roll back the last patch?'), [E('p', {}, tr('已修改的系统文件会恢复为更新前版本。存在本地冲突时会停止。','Changed system files will return to their previous versions. Local conflicts stop rollback.')),
                E('button', {class:'cbi-button',click:ui.hideModal}, tr('取消','Cancel')),
                E('button', {class:'cbi-button cbi-button-action',style:'margin-inline-start:12px',click:function(){ui.hideModal();callRollback().then(error).then(watch).catch(failed);}},tr('撤销','Roll back'))]);
        };
        reboot.onclick = function() { callReboot().then(error).then(function(){ui.awaitReconnect();}).catch(failed); };
        show(state);
        return E('div', {class:'cbi-map'}, [E('h2', {}, tr('系统补丁','System patches')), status,
            E('p', {}, tr('从两版完整包生成差异更新。保留用户插件、配置及数据，支持内核、模块、依赖和更新器自身升级；写入前保存恢复副本。','Differential updates from two full releases. Retains user packages, settings and data; updates kernel, modules, dependencies and the updater, with recovery copies.')),
            E('div', {style:'margin:20px 0'}, [button,rollback,reboot]), detail]);
    },
    handleSaveApply: null, handleSave: null, handleReset: null
});
