'use strict';
'require view';
'require rpc';
'require ui';
'require poll';

var callStatus = rpc.declare({object:'unisoc-ota', method:'status'});
var callInspect = rpc.declare({object:'unisoc-ota', method:'inspect'});
var callStart = rpc.declare({object:'unisoc-ota', method:'start', params:['id']});
var callRollback = rpc.declare({object:'unisoc-ota', method:'rollback'});
var callRollbackCheck = rpc.declare({object:'unisoc-ota', method:'rollback_check'});
var callReboot = rpc.declare({object:'unisoc-ota', method:'reboot'});

return view.extend({
    load: function() {
        return Promise.all([import(L.resource('unisoc-ota/errors.js')),
            callStatus().catch(function(e) { return {load_error:e}; })]);
    },
    render: function(loaded) {
        var messages = loaded[0], state = loaded[1];
        var zh = (document.documentElement.lang || '').toLowerCase().indexOf('zh') === 0;
        var tr = function(a,b) { return zh ? a : b; };
        var error = function(r) { if (r && r.error) throw new Error(r.error); return r; };
        var detail = E('div', {style:'margin:18px 0;overflow-wrap:anywhere'});
        var status = E('p');
        var button = E('button', {class:'cbi-button cbi-button-action', disabled:!L.hasViewPermission()}, tr('导入差异包','Import patch'));
        var rollback = E('button', {class:'cbi-button', disabled:!L.hasViewPermission(), style:'margin-inline-start:12px'}, tr('撤销上次补丁','Roll back last patch'));
        var reboot = E('button', {class:'cbi-button', disabled:!L.hasViewPermission(), style:'margin-inline-start:12px'}, tr('重启生效','Reboot'));
        var busy = false, watching = false, pollError = false, rebooting = false, pollTick;
        function errorNode(e) {
            var info = messages.explainError(e, !zh);
            return E('div', {}, [E('p', {}, info.message), E('details', {}, [
                E('summary', {}, tr('技术详情','Technical details')),
                E('pre', {style:'white-space:pre-wrap;overflow-wrap:anywhere;max-height:180px;overflow:auto'}, info.details)])]);
        }
        function show(r) {
            if (rebooting) return;
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
                    detail.textContent = r.job.status === 'complete' ? tr('操作完成。请重启使所有服务使用新文件。','Complete. Reboot to use all updated files.') : '';
                    if (r.job.result.error) detail.appendChild(errorNode(r.job.result.error));
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
                if (rebooting) return;
                return callStatus().then(function(r) { show(r); pollError=false; if (!busy) {poll.remove(tick); watching=false;} }).catch(function(e) {
                    if (rebooting) return;
                    if (!pollError) failed(e);
                    pollError=true;
                });
            }
            pollTick = tick;
            poll.add(tick, 2);
        }
        function failed(e) { ui.addNotification(null, errorNode(e), 'error'); }
        function operationFailed(e) {
            failed(e);
            // A lost reply does not prove that the detached worker stopped.
            // Read its state before enabling writes; keep watching on outage.
            return callStatus().then(show).catch(function() { watch(); });
        }
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
                        rollback.disabled=true; reboot.disabled=true;
                        return callStart(r.id).then(error).then(function() {detail.textContent=tr('正在更新，关闭网页不会中断写入。','Updating. Closing this page does not interrupt writes.'); watch();}).catch(operationFailed);
                    }}, tr('应用补丁','Apply patch'))
                ])]);
            }).catch(failed);
        };
        rollback.onclick = function() {
            if (busy) return;
            busy=true; button.disabled=true; rollback.disabled=true; reboot.disabled=true;
            detail.textContent=tr('正在检查恢复副本和更新后的文件…','Checking recovery copies and post-update files…');
            return callRollbackCheck().then(error).then(function(check) {
                busy=false; button.disabled=!L.hasViewPermission(); rollback.disabled=!L.hasViewPermission(); reboot.disabled=!L.hasViewPermission();
                detail.textContent='';
                if (!check.available) {
                    if (check.reason === 'no_update') throw new Error('No completed update to roll back');
                    var conflicts = check.conflicts || [];
                    ui.showModal(tr('暂不能撤销','Rollback unavailable'), [
                        E('p', {}, tr('以下文件在更新后有变化，未执行任何恢复写入。','The following files changed after the update. No recovery writes were made.')),
                        E('div', {style:'max-height:240px;overflow:auto'}, conflicts.slice(0,20).map(function(c) {
                            return errorNode('Rollback conflict: '+c.path+'; changed='+c.fields.join(','));
                        })), E('button', {class:'cbi-button',click:ui.hideModal}, tr('关闭','Close'))]);
                    return;
                }
                ui.showModal(tr('撤销上次补丁？','Roll back the last patch?'), [E('p', {}, tr('恢复副本和文件检查通过，将恢复为更新前版本。','Recovery copies and files passed validation. Restore the previous version?')),
                E('button', {class:'cbi-button',click:ui.hideModal}, tr('取消','Cancel')),
                E('button', {class:'cbi-button cbi-button-action',style:'margin-inline-start:12px',click:function(){
                    if (busy) return;
                    ui.hideModal(); busy=true; button.disabled=true; rollback.disabled=true; reboot.disabled=true;
                    return callRollback().then(error).then(watch).catch(operationFailed);
                }},tr('撤销','Roll back'))]);
            }).catch(function(e) { return operationFailed(e); });
        };
        reboot.onclick = function() {
            if (busy || rebooting) return;
            rebooting=true; busy=true; button.disabled=true; rollback.disabled=true; reboot.disabled=true;
            if (pollTick) poll.remove(pollTick);
            watching=false;
            detail.textContent=tr('正在请求重启，请等待设备恢复连接…','Requesting reboot. Waiting for the device to reconnect…');
            return callReboot().then(error).then(function(){ui.awaitReconnect();}).catch(function(e) {
                if (messages.isConnectionError(e)) {
                    detail.textContent=tr('连接已断开，正在等待设备恢复；尚未收到完整重启确认。','Connection lost. Waiting for the device; a complete reboot acknowledgement was not received.');
                    ui.awaitReconnect();
                } else {
                    rebooting=false; busy=false;
                    return operationFailed(e);
                }
            });
        };
        if (state.load_error) failed(state.load_error);
        else show(state);
        return E('div', {class:'cbi-map'}, [E('h2', {}, tr('系统补丁','System patches')), status,
            E('p', {}, tr('从两版完整包生成差异更新。保留用户插件、配置及数据，支持内核、模块、依赖和更新器自身升级；写入前保存恢复副本。','Differential updates from two full releases. Retains user packages, settings and data; updates kernel, modules, dependencies and the updater, with recovery copies.')),
            E('div', {style:'margin:20px 0'}, [button,rollback,reboot]), detail]);
    },
    handleSaveApply: null, handleSave: null, handleReset: null
});
