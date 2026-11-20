'use strict';
'require view';
'require rpc';
'require ui';
'require poll';
'require mu300.common as M';

var callStatus = rpc.declare({object:'unisoc-ota', method:'status', nobatch:true});
var callInspect = rpc.declare({object:'unisoc-ota', method:'inspect', nobatch:true});
var callStart = rpc.declare({object:'unisoc-ota', method:'start', params:['id'], nobatch:true});
var callRollback = rpc.declare({object:'unisoc-ota', method:'rollback', nobatch:true});
var callRollbackCheck = rpc.declare({object:'unisoc-ota', method:'rollback_check', nobatch:true});
var callDiscard = rpc.declare({object:'unisoc-ota', method:'discard_recovery', nobatch:true});
var callReboot = rpc.declare({object:'unisoc-ota', method:'reboot', nobatch:true});

var CSS = `
.mud-ota{max-width:1200px;margin:0 auto;padding:4px 0 16px}
.mud-ota .mud-ota-heading{margin:0 0 22px}.mud-ota .mud-ota-heading h2{border:0;padding:0;margin:5px 0 9px;font-size:1.5rem;letter-spacing:-.025em;color:var(--text,#222)}
.mud-ota .mud-note{margin:0;line-height:1.7;color:var(--text-muted,var(--text-light,#777));font-size:.8rem}
.mud-ota-kicker{font-size:.7rem;font-weight:650;letter-spacing:.08em;color:var(--brand,var(--primary,#2f7bf6))}
.mud-ota .mud-card{padding:22px;min-width:0}.mud-ota .mud-card:hover{border-color:var(--hairline,var(--border,#e3e6ea))}
.mud-ota-hero{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:18px}
.mud-ota-version{min-width:0;flex:1}.mud-ota-version code{display:block;margin-top:9px;padding:0;border:0;background:none;color:var(--text,#222);font-size:.85rem;line-height:1.6;overflow-wrap:anywhere}
.mud-ota-status{display:inline-flex;align-items:center;gap:7px;font-size:.76rem;font-weight:600;color:var(--success,#25985a)}
.mud-ota-status::before{content:'';width:7px;height:7px;flex:none;border-radius:50%;background:currentColor}
.mud-ota-status[data-state=pending]{color:var(--warning,#ad7400)}.mud-ota-status[data-state=running]{color:var(--brand,var(--primary,#2f7bf6))}
.mud-ota-grid{display:grid;grid-template-columns:1.1fr 1fr;gap:18px;margin-bottom:18px}
.mud-ota-card-head{display:flex;gap:12px;align-items:center;margin-bottom:18px}.mud-ota .mud-ota-card-head h3{margin:0;padding:0;border:0;font-size:1rem;color:var(--text,#222)}
.mud-ota-icon{display:inline-flex;flex:none;align-items:center;justify-content:center;width:38px;height:38px;border-radius:var(--radius-base,.5rem);color:var(--brand,var(--primary,#2f7bf6));background:var(--brand-subtle,var(--background-alt,#f4f6f8))}
.mud-ota-icon svg{width:21px;height:21px;display:block}.mud-ota .mud-btn .mud-ota-icon{width:17px;height:17px;background:none;color:inherit}.mud-ota .mud-btn .mud-ota-icon svg{width:17px;height:17px}
.mud-ota-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:20px}.mud-ota .mud-btn{min-height:38px;padding:8px 14px;line-height:1.4}
.mud-ota .mud-ota-primary,.mud-ota-modal .mud-ota-primary{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-ota .mud-ota-footnote{padding-top:16px;margin-top:18px;border-top:1px solid var(--hairline,var(--border,#e3e6ea))}
.mud-ota-space{display:flex;align-items:baseline;gap:12px;margin:3px 0 10px;flex-wrap:wrap}.mud-ota-space strong{font-size:1.65rem;font-weight:650;letter-spacing:-.03em;color:var(--text,#222)}
.mud-ota-size-note{font-size:.77rem;color:var(--text-muted,#777)}.mud-ota-activity{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.7;color:var(--text,#222);font-size:.84rem}
.mud-ota-overlay{position:fixed;inset:0;z-index:20000;display:grid;place-items:center;padding:24px;background:rgba(0,0,0,.36)}
.mud-ota-overlay[hidden]{display:none}.mud-ota-overlay .mud-card{width:min(100%,440px);box-sizing:border-box;display:flex;flex-direction:column;align-items:center;gap:16px;text-align:center;background:var(--surface,var(--background-color-high,#fff));color:var(--text,var(--text-color-high,#222))}
.mud-ota-spinner{display:inline-block;width:30px;height:30px;box-sizing:border-box;border:3px solid var(--hairline,#ddd);border-top-color:var(--brand,var(--primary,#2f7bf6));border-radius:50%;animation:mud-ota-spin .8s linear infinite}
.mud-ota .mud-btn[aria-busy=true]::after{content:'';width:13px;height:13px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:mud-ota-spin .8s linear infinite}
@keyframes mud-ota-spin{to{transform:rotate(360deg)}}
@media(prefers-reduced-motion:reduce){.mud-ota-spinner,.mud-ota .mud-btn[aria-busy=true]::after{animation-duration:2s}}
.mud-ota-modal-actions{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:22px}.mud-ota-error pre{max-height:180px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}
@media(max-width:720px){.mud-ota-grid{grid-template-columns:1fr}.mud-ota .mud-card{padding:18px}.mud-ota-hero{align-items:flex-start;flex-wrap:wrap}.mud-ota-hero>.mud-btn{margin-top:0}.mud-ota .mud-ota-heading h2{font-size:1.3rem}}
`;

function icon(kind) {
    var paths={upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 16v5h16v-5"/>',
        recovery:'<path d="M3 11a9 9 0 1 1 2.5 7M3 4v7h7M12 7v5l3 2"/>',
        power:'<path d="M12 3v8M6 5a9 9 0 1 0 12 0"/>',
        history:'<path d="M4 6h16M4 12h10M4 18h7"/>',
        trash:'<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7"/>'};
    var node=E('span', {class:'mud-ota-icon', 'aria-hidden':'true'});
    node.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">'+paths[kind]+'</svg>';
    return node;
}

return view.extend({
    load: function() {
        return Promise.all([import(L.resource('unisoc-ota/errors.js')),
            callStatus().catch(function(e) { return {load_error:e}; })]);
    },
    render: function(loaded) {
        M.injectCss(); M.localizeMenu();
        var messages=loaded[0], state=loaded[1], lang=M.uiLanguage();
        var zh=lang==='zh', tr=function(a,b,c){return zh?a:lang==='tr'?(c||b):b;};
        var error=function(r){if(r&&r.error)throw new Error(r.error);return r;};
        var detail=E('div',{class:'mud-ota-activity',role:'status','aria-live':'polite'});
        var version=E('code'), badge=E('span',{class:'mud-ota-status'});
        var size=E('strong',{},'--'), recoveryNote=E('p',{class:'mud-note'});
        function action(label,kind,cls){return E('button',{type:'button',class:'mud-btn '+(cls||''),'aria-label':label,disabled:!L.hasViewPermission()||null},[icon(kind),label]);}
        var button=action(tr('导入差异包','Import patch','Yama içe aktar'),'upload','mud-ota-primary');
        var rollback=action(tr('撤销上次补丁','Roll back last patch','Son yamayı geri al'),'recovery');
        var discard=action(tr('删除恢复副本','Delete recovery copy','Kurtarma kopyasını sil'),'trash','warn');
        var reboot=action(tr('重启设备','Reboot','Yeniden başlat'),'power');
        var busy=false, watching=false, pollError=false, rebooting=false, disposed=false, pollTick, last=state, localPhase=null, pendingAction=null;
        var workingTitle=E('strong'), workingDetail=E('p',{class:'mud-note'});
        var overlay=E('div',{class:'mud-ota-overlay',hidden:true,role:'status','aria-live':'polite'},[
            E('div',{class:'mud-card'},[E('span',{class:'mud-ota-spinner','aria-hidden':'true'}),workingTitle,workingDetail])]);
        function phaseTitle(value){return ({
            upload:tr('请选择并上传补丁…','Select and upload a patch…','Yamayı seçip yükleyin…'),
            checking:tr('正在检查补丁…','Checking patch…','Yama kontrol ediliyor…'),
            staging:tr('正在准备更新文件…','Preparing update files…','Güncelleme dosyaları hazırlanıyor…'),
            runtime:tr('正在准备更新…','Preparing update…','Güncelleme hazırlanıyor…'),
            applying:tr('正在更新系统…','Updating system…','Sistem güncelleniyor…'),
            reconnecting:tr('正在确认更新状态…','Checking update status…','Güncelleme durumu kontrol ediliyor…'),
            recovery:tr('正在检查恢复副本…','Checking recovery copy…','Kurtarma kopyası kontrol ediliyor…'),
            rollback:tr('正在恢复系统…','Restoring system…','Sistem geri yükleniyor…'),
            discard:tr('正在删除恢复副本…','Deleting recovery copy…','Kurtarma kopyası siliniyor…'),
            reboot:tr('正在重启设备…','Rebooting device…','Cihaz yeniden başlatılıyor…')
        })[value]||tr('正在准备更新…','Preparing update…','Güncelleme hazırlanıyor…');}
        function progress(r){
            var job=r.job||{}, transaction=r.transaction||{}, value=localPhase||job.phase||'applying';
            overlay.hidden=!(localPhase&&localPhase!=='upload'||job.status==='running'||(r.recovery||{}).pending);
            workingTitle.textContent=phaseTitle(value);
            var writing=!localPhase&&transaction.id===job.patch_id&&transaction.total>0&&transaction.status==='applying';
            workingDetail.textContent=writing?transaction.started+' / '+transaction.total:
                localPhase==='checking'?tr('检查完成后会展示更新内容，请确认后再应用。','Review the changes after checks finish, then confirm to apply.','Kontrol bitince değişiklikleri inceleyip uygulamayı onaylayın.'):
                tr('正在处理，请稍候。更新开始后关闭网页不会中断任务。','Please wait. Once started, the update continues even if you close this page.','Lütfen bekleyin. Başladıktan sonra sayfa kapatılsa da güncelleme sürer.');
        }
        function phase(value){if(disposed)return;localPhase=value;if(!value){show(last);return;}busy=true;progress(last);controls();detail.textContent=phaseTitle(value);badge.setAttribute('data-state','running');badge.textContent=phaseTitle(value);}
        function errorNode(e){var info=messages.explainError(e,!zh);if(/Incomplete patch inspection response/.test(info.details))info.message=tr('补丁检查结果不完整，未开始更新。请更新插件后重新检查。','Patch inspection is incomplete. No update started. Update the plugin and inspect again.','Yama kontrolü eksik. Güncelleme başlamadı. Eklentiyi güncelleyip yeniden kontrol edin.');if(/Inspect this patch|Upload differs/.test(info.details))info.message=tr('补丁检查结果已失效或文件已变化，未开始写入。请重新导入并检查。','The review is stale or the file changed. No writes started. Import and inspect it again.','Kontrol geçersiz veya dosya değişti. Yazma başlamadı. Yeniden içe aktarıp kontrol edin.');if(/There is no item named ['"]manifest\.json['"]/.test(info.details))info.message=tr('请选择解压后的 update.ota，网页不能直接导入包含电脑更新器的外层 ZIP。','Select update.ota from the extracted patch ZIP.','Açılan yama ZIP dosyasındaki update.ota dosyasını seçin.');if(/Recovery copy was deleted/.test(info.details))info.message=tr('恢复副本已删除，无法再撤销这次补丁。当前系统不受影响。','The recovery copy was deleted. This patch can no longer be undone; the current system is unaffected.','Kurtarma kopyası silindi. Son yama artık geri alınamaz.');return E('div',{class:'mud-ota-error'},[
            E('p',{},info.message),E('details',{},[E('summary',{},tr('技术详情','Technical details','Teknik ayrıntılar')),E('pre',{},info.details)])]);}
        function failed(e){if(!disposed)ui.addNotification(null,errorNode(e),'error');}
        function controls(){
            var info=last.recovery||{}, writable=!busy&&!rebooting&&!info.pending&&L.hasViewPermission();
            button.disabled=!writable;
            rollback.disabled=!writable||!(info.available===true||(!last.recovery&&last.transaction&&last.transaction.status==='complete'));
            discard.disabled=!writable||!(info.count>0);
            reboot.disabled=!writable;
            button.setAttribute('aria-busy',localPhase==='upload'||localPhase==='checking'?'true':'false');
        }
        function bytes(value){if(value==null)return '--';if(value<1024)return value+' B';var i=Math.min(3,Math.floor(Math.log(value)/Math.log(1024)));return (value/Math.pow(1024,i)).toFixed(1)+' '+['B','KiB','MiB','GiB'][i];}
        function show(r){
            if(disposed||rebooting)return;
            error(r); last=r; version.textContent=(r.identity||{}).version||'--';
            var job=r.job||{}, info=r.recovery||{};
            if(localPhase==='reconnecting')localPhase=null;
            busy=!!localPhase||job.status==='running'||info.pending===true;
            size.textContent=bytes(info.bytes);
            recoveryNote.textContent=info.available?tr('恢复副本可用于撤销上次补丁。确认更新稳定后，可删除副本释放空间。','Use this copy to undo the last patch. Delete it to reclaim space once the update is verified.','Son yamayı geri almak için bu kopyayı kullanın. Güncelleme doğrulandıktan sonra yer açmak için silebilirsiniz.'):
                info.deleted?tr('恢复副本已删除，当前系统保留。无法再撤销上次补丁。','Recovery copy deleted. The current system is retained; the last patch can no longer be undone.','Kurtarma kopyası silindi. Mevcut sistem korunur; son yama artık geri alınamaz.'):
                tr('暂无可用的恢复副本。成功应用补丁时会自动创建。','No recovery copy available. One is created automatically when a patch is applied.','Kurtarma kopyası yok. Yama uygulanırken otomatik oluşturulur.');
            badge.setAttribute('data-state',busy?'running':r.reboot_required===true?'pending':'ready');
            badge.textContent=busy?tr('正在更新','Updating','Güncelleniyor'):r.reboot_required===true?tr('待重启','Reboot required','Yeniden başlatma gerekli'):tr('系统就绪','System ready','Sistem hazır');
            if(busy){detail.textContent=phaseTitle(localPhase||job.phase||'applying');if(job.status==='running'||info.pending)watch();}
            else if(job.status==='complete'){
                detail.textContent=r.reboot_required===true?tr('操作完成。请重启使所有服务使用新文件。','Complete. Reboot to use all updated files.','İşlem tamamlandı. Yeni dosyaları kullanmak için yeniden başlatın.'):
                    r.reboot_required===false?tr('操作已完成，设备已重启。','Complete. The device has restarted.','İşlem tamamlandı. Cihaz yeniden başlatıldı.'):
                    tr('上次操作已完成。','The last operation completed.','Son işlem tamamlandı.');
            }else if(job.result&&job.result.error){detail.textContent='';detail.appendChild(errorNode(job.result.error));}
            else detail.textContent=tr('尚未应用系统补丁。','No system patches applied yet.','Henüz sistem yaması uygulanmadı.');
            controls();
            progress(r);
        }
        function stopWatch(){if(pollTick)poll.remove(pollTick);watching=false;}
        this.stopWatch=function(){disposed=true;overlay.hidden=true;stopWatch();};
        function watch(){
            if(watching||disposed||rebooting)return;watching=true;
            pollTick=function(){if(disposed||rebooting)return;return callStatus().then(function(r){show(r);pollError=false;if(!busy)stopWatch();}).catch(function(e){if(disposed||rebooting)return;if(messages.isConnectionError(e)&&busy)phase('reconnecting');else if(!pollError)failed(e);pollError=true;});};
            poll.add(pollTick,2);
        }
        function refresh(){return callStatus().then(show).catch(function(e){failed(e);watch();});}
        function operationFailed(e){
            if(disposed)return Promise.resolve();
            localPhase=null;
            if(messages.isConnectionError(e)&&pendingAction){
                phase('reconnecting');
                return callStatus().then(function(r){
                    error(r);var job=r.job||{}, transaction=r.transaction||{};
                    var id=job.patch_id||(job.result||{}).id||transaction.id;
                    var accepted=job.command===pendingAction.command&&id===pendingAction.id&&
                        (job.status==='running'||job.status==='complete');
                    localPhase=null;show(r);if(accepted){if(busy)watch();return;}
                    failed(e);
                }).catch(function(){failed(e);watch();});
            }
            phase(null);failed(e);return refresh();
        }
        function modal(title,body,confirmLabel,confirm,danger){
            if(disposed)return;
            badge.setAttribute('data-state','pending');badge.textContent=tr('等待确认','Awaiting confirmation','Onay bekleniyor');detail.textContent=title;
            ui.showModal(title,[E('div',{class:'mud mud-ota-modal'},[body,E('div',{class:'mud-ota-modal-actions'},[
                E('button',{type:'button',class:'mud-btn',click:function(){ui.hideModal();show(last);}},tr('取消','Cancel','İptal')),
                E('button',{type:'button',class:'mud-btn '+(danger?'warn':'mud-ota-primary'),click:function(){ui.hideModal();return confirm();}},confirmLabel)])])]);
        }
        button.onclick=function(){
            if(busy)return;
            phase('upload');
            return ui.uploadFile('/etc/unisoc-ota/upload.ota',button).then(function(){if(disposed)return;phase('checking');return callInspect();}).then(function(r){
                if(disposed)return;
                phase(null);error(r);var m=r.manifest;
                if(!m||typeof r.id!=='string'||!/^[a-f0-9]{64}$/.test(r.id))throw new Error('Incomplete patch inspection response');
                var text=E('div',{},[E('p',{},m.base_version+' → '+m.target_version),
                    E('p',{},m.operations.length+tr(' 个系统文件变更；保留配置、插件及用户数据。',' system file changes. Settings, extra plugins and user data are retained.',' sistem dosyası değişikliği. Ayarlar, eklentiler ve kullanıcı verileri korunur.')),
                    E('p',{class:'mud-note'},tr('仅导入可信来源的补丁。哈希校验不等同于发布者签名。','Import only trusted patches. Checksums do not authenticate the publisher.','Yalnızca güvenilir yamaları içe aktarın. Sağlama toplamı yayıncıyı doğrulamaz.'))]);
                if(m.boot)text.appendChild(E('p',{},tr('包含内核／启动镜像与配套模块更新，完成后重启生效。','Includes kernel/boot and matching modules. Reboot after completion.','Çekirdek ve eşleşen modülleri içerir. Tamamlandıktan sonra yeniden başlatın.')));
                if(!r.applicable){text.appendChild(E('p',{},tr('无法应用。冲突文件或依赖：','Cannot apply. Conflicts: ','Uygulanamaz. Çakışmalar: ')+r.conflicts.concat(r.package_conflicts||[]).join(', ')));ui.showModal(tr('无法应用补丁','Patch unavailable','Yama kullanılamıyor'),[text,E('button',{class:'mud-btn',click:ui.hideModal},tr('关闭','Close','Kapat'))]);return;}
                modal(tr('确认系统补丁','Review system patch','Sistem yamasını incele'),text,tr('应用补丁','Apply patch','Yamayı uygula'),function(){
                    if(busy)return;pendingAction={command:'apply',id:r.id};phase('queued');
                    return callStart(r.id).then(error).then(function(job){localPhase=null;show(Object.assign({},last,{job:Object.assign({command:'apply',patch_id:r.id},job)}));watch();}).catch(operationFailed);
                });
            }).catch(function(e){phase(null);detail.textContent=tr('本次补丁未导入。','This patch was not imported.','Bu yama içe aktarılmadı.');failed(e);});
        };
        rollback.onclick=function(){
            if(busy)return;phase('recovery');
            detail.textContent=tr('正在检查恢复副本…','Checking recovery copy…','Kurtarma kopyası kontrol ediliyor…');
            return callRollbackCheck().then(error).then(function(check){
                phase(null);
                if(!check.available){
                    if(check.reason==='no_update')throw new Error('No completed update to roll back');
                    if(check.reason==='recovery_deleted')throw new Error('Recovery copy was deleted');
                    ui.showModal(tr('暂不能撤销','Rollback unavailable','Geri alma kullanılamıyor'),[E('p',{},tr('以下文件在更新后有变化，未执行任何恢复写入。','These files changed after the update. No recovery writes were made.','Bu dosyalar güncellemeden sonra değişti. Geri yükleme yapılmadı.')),
                        E('div',{style:'max-height:240px;overflow:auto'},(check.conflicts||[]).slice(0,20).map(function(c){return errorNode('Rollback conflict: '+c.path+'; changed='+c.fields.join(','));})),E('button',{class:'mud-btn',click:ui.hideModal},tr('关闭','Close','Kapat'))]);return;
                }
                modal(tr('撤销上次补丁？','Roll back the last patch?','Son yama geri alınsın mı?'),E('p',{},tr('恢复副本检查通过，将恢复为更新前版本。','Recovery checks passed. Restore the previous version.','Kurtarma kontrolleri başarılı. Önceki sürüm geri yüklenecek.')),tr('撤销','Roll back','Geri al'),function(){if(busy)return;var id=(last.transaction||{}).id;pendingAction={command:'rollback',id:id};phase('queued');return callRollback().then(error).then(function(job){localPhase=null;show(Object.assign({},last,{job:Object.assign({command:'rollback',patch_id:id,phase:'rollback'},job)}));watch();}).catch(operationFailed);});
            }).catch(operationFailed);
        };
        discard.onclick=function(){
            if(busy||discard.disabled)return;
            modal(tr('删除恢复副本？','Delete the recovery copy?','Kurtarma kopyası silinsin mi?'),E('div',{},[
                E('p',{},tr('将释放约 ','Reclaim approximately ','Yaklaşık ')+bytes((last.recovery||{}).bytes)+tr(' 存储空间。',' of storage.',' depolama alanı açılır.')),
                E('p',{},tr('删除后无法撤销上次补丁。当前系统、已安装插件、配置及用户数据不会被删除。','The last patch can no longer be undone. Your current system, installed plugins, settings and user data will be kept.','Son yama artık geri alınamaz. Mevcut sistem, eklentiler, ayarlar ve kullanıcı verileri korunur.'))]),tr('确认删除','Delete recovery copy','Kurtarma kopyasını sil'),function(){
                    if(busy)return;phase('discard');
                    return callDiscard().then(error).then(function(){localPhase=null;return refresh();}).catch(operationFailed);
                },true);
        };
        reboot.onclick=function(){
            if(busy||rebooting)return;rebooting=true;phase('reboot');stopWatch();
            detail.textContent=tr('正在请求重启，请等待设备恢复连接…','Requesting reboot. Waiting for the device to reconnect…','Yeniden başlatılıyor. Cihazın bağlanması bekleniyor…');
            return callReboot().then(error).then(function(){ui.awaitReconnect();}).catch(function(e){
                if(messages.isConnectionError(e))ui.awaitReconnect();else{rebooting=false;busy=false;return operationFailed(e);}
            });
        };
        if(state.load_error)failed(state.load_error);else show(state);
        return E('div',{class:'mud mud-ota'},[E('style',{},CSS),overlay,
            E('header',{class:'mud-ota-heading'},[E('span',{class:'mud-ota-kicker'},tr('系统维护','SYSTEM MAINTENANCE','SİSTEM BAKIMI')),E('h2',{},tr('系统补丁','System patches','Sistem yamaları')),E('p',{class:'mud-note'},tr('只更新变化的系统文件，保留你的插件、配置和数据。','Update changed system files while keeping your plugins, settings and data.','Eklentileri, ayarları ve verileri koruyarak değişen sistem dosyalarını güncelleyin.'))]),
            E('section',{class:'mud-card mud-ota-hero'},[E('div',{class:'mud-ota-version'},[badge,version]),reboot]),
            E('div',{class:'mud-ota-grid'},[
                E('section',{class:'mud-card'},[E('div',{class:'mud-ota-card-head'},[icon('upload'),E('h3',{},tr('导入差异更新','Import differential update','Fark güncellemesini içe aktar'))]),
                    E('p',{class:'mud-note'},tr('使用 Diff Studio 从两版完整包生成补丁。支持系统、内核、模块、依赖及更新器自身更新。','Generate a patch from two full releases with Diff Studio. Update the system, kernel, modules, dependencies and updater.','Diff Studio ile iki tam sürümden yama oluşturun. Sistem, çekirdek, modüller, bağımlılıklar ve güncelleyici desteklenir.')),
                    E('div',{class:'mud-ota-actions'},[button]),E('p',{class:'mud-note mud-ota-footnote'},tr('网页导入 update.ota；若下载的是 ZIP，请先解压。写入前会检查补丁并保存恢复副本。','Import update.ota here. Extract downloaded ZIP files first. Patches are checked and recovery copies are saved before writes.','Buraya update.ota aktarın. ZIP dosyasını önce açın. Yazmadan önce yama kontrol edilir ve kurtarma kopyası kaydedilir.'))]),
                E('section',{class:'mud-card'},[E('div',{class:'mud-ota-card-head'},[icon('recovery'),E('h3',{},tr('恢复副本','Recovery copy','Kurtarma kopyası'))]),
                    E('div',{class:'mud-ota-space'},[size,E('span',{class:'mud-ota-size-note'},tr('占用存储空间','Storage used','Kullanılan alan'))]),recoveryNote,E('div',{class:'mud-ota-actions'},[rollback,discard])])]),
            E('section',{class:'mud-card'},[E('div',{class:'mud-ota-card-head'},[icon('history'),E('h3',{},tr('最近操作','Last operation','Son işlem'))]),detail])]);
    },
    unload:function(){if(this.stopWatch)this.stopWatch();},
    handleSaveApply:null,handleSave:null,handleReset:null
});
