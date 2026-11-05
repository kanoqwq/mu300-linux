'use strict';
'require view';
'require rpc';
'require poll';
'require mu300.common as M';
'require mu300.sim-radio as SimRadio';

var get=rpc.declare({object:'mu300dash',method:'data_sim_get',nobatch:true,expect:{'':{}}});
var getV50=rpc.declare({object:'mu300dash',method:'v50_sim_get',nobatch:true,expect:{'':{}}});
var start=rpc.declare({object:'mu300dash',method:'data_sim_set',params:['target'],nobatch:true,expect:{'':{}}});
var status=rpc.declare({object:'mu300dash',method:'data_sim_status',params:['id'],nobatch:true,expect:{'':{}}});
var errors={dual_sim_unavailable:'当前配置不支持双卡切换',invalid_sim:'SIM 卡槽无效',modem_busy:'另一项模组操作正在执行',sim_read_failed:'无法核验当前上网卡',sim_not_ready:'目标 SIM 尚未就绪',data_disconnect_failed:'旧数据连接未停止，已取消切换',sim_switch_failed_restored:'切换失败，已恢复原上网卡',sim_rollback_failed:'切换失败且恢复不完整，请检查 SIM 和信号',storage_failed:'设置保存失败'};
errors['Enable the target SIM radio before switching']='请先开启目标 SIM 的射频，再切换上网卡';
errors.dual_boot_required='双卡尚未共同初始化，请重启后检查状态';
Object.assign(errors,{v50_board_unknown:'无法识别 V50 硬件版本，未更改选卡',v50_card_unsupported:'此功能仅适用于 V50',v50_card_unavailable:'此硬件版本没有所选 SIM',v50_mux_unavailable:'SIM 选择驱动尚未就绪，请更新内核并重启',v50_card_power_failed:'SIM 电源切换未完成',v50_mux_write_failed:'SIM 硬件选择失败'});
function message(s){return M.translate(errors[s]||s);}
function el(tag,text,cls) { var n=document.createElement(tag); if(text!=null)n.textContent=text; if(cls)n.className=cls; return n; }
function card(n) { return n===0||n===1 ? 'SIM '+(n+1) : M.translate('未连接'); }
var CSS = `
.mud-sim-page .mud-sim-panel{padding:20px;min-width:0}
.mud-sim-page .mud-sim-panel h3{margin:0 0 10px;padding:0;border:0;font-size:1rem;font-weight:600;color:var(--text,#222)}
.mud-sim-page .mud-sim-intro{margin:0 0 16px;line-height:1.65}
.mud-sim-page .mud-data-sim{display:grid;gap:10px;margin:0;padding:14px;border:1px solid var(--hairline,var(--border-color,#ddd));border-radius:10px;min-width:0}
.mud-sim-page .mud-data-sim + .mud-data-sim{margin-top:16px}
.mud-sim-page .mud-data-sim-controls{display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px}
.mud-sim-page .mud-data-sim-controls label{flex:1;min-width:110px;font-weight:600;margin:0;color:var(--text,#222)}
.mud-sim-page .mud-data-sim-controls input[type="checkbox"]{width:18px;height:18px;min-height:0;margin:0;flex:none;accent-color:var(--brand,var(--primary,#2f7bf6))}
.mud-sim-page .mud-data-sim-controls select{width:auto;min-width:100px;max-width:100%;margin:0}
.mud-sim-page .mud-data-sim-controls .mud-btn{margin:0;max-width:100%;white-space:normal}
.mud-sim-page .mud-sim-status{display:flex;flex-wrap:wrap;gap:6px 18px;padding:10px 0 0;border-top:1px solid var(--hairline,var(--border-color,#ddd));font-size:.8rem;line-height:1.6;color:var(--text-muted,#777)}
.mud-sim-page .mud-sim-status span{min-width:0;overflow-wrap:anywhere}
.mud-sim-page .mud-note{line-height:1.65;overflow-wrap:anywhere}
.mud-sim-page .mud-data-sim .mud-note{margin:0}
@media(max-width:480px){.mud-sim-page .mud-sim-panel{padding:16px}.mud-sim-page .mud-data-sim{padding:12px}.mud-sim-page .mud-data-sim-controls label{flex-basis:100%}.mud-sim-page .mud-data-sim-controls select{flex:1;min-width:0}.mud-sim-page .mud-data-sim-controls .mud-btn{flex:1}.mud-sim-page .mud-sim-status{flex-direction:column;gap:3px}}
`;
return view.extend({
    load:function() { return Promise.all([M.callSimGet(),get(),getV50().catch(function(){return {};})]); },
    render:function(data) {
        M.injectCss(); M.localizeMenu();
        var self=this, root=this.root=el('div',null,'mud mud-sim-page');
        root.append(el('style',CSS));
        var panel=el('section',null,'mud-card mud-sim-panel'); root.append(panel);
        panel.append(el('h3',M.translate('SIM 卡管理')));
        panel.append(el('p',M.translate('切换上网卡后，看板、锁定和 AT 的查看卡槽会同步；仍可手动查看另一张卡。'),'mud-note mud-sim-intro'));
        var controls=el('div',null,'mud-data-sim'); panel.append(controls);
        var actual=el('span'), saved=el('span'), connection=el('span'), select=el('select'), button=el('button',M.translate('切换上网卡'),'mud-btn');
        select.id='mud-data-sim-select'; button.type='button';
        [0,1].forEach(function(n) { var o=el('option',card(n)); o.value=n; select.append(o); });
        var row=el('div',null,'mud-data-sim-controls');
        var label=el('label',M.translate('默认上网卡')); label.htmlFor=select.id;
        row.append(label,select,button); controls.append(row);
        var summary=el('div',null,'mud-sim-status'); summary.append(actual,connection,saved); controls.append(summary);
        controls.append(el('p',M.translate('切换会短暂中断蜂窝网络，成功后作为开机默认上网卡；不会切换 USB 或 Wi-Fi。'),'mud-note'));
        controls.append(el('p',M.translate('欠费或无信号不影响选卡；数据连接失败不会自动切回。'),'mud-note'));
        var last=data[1];
        function paint(r,initial) {
            last=r;
            button.disabled=data[0].slots!==2 || !r.ok || r.ready===false || !!self.busy;
            actual.textContent=M.translate('当前上网卡')+'：'+card(r.actual_sim);
            saved.textContent=M.translate('开机默认')+'：'+card(r.saved_sim);
            connection.textContent=M.translate('数据连接')+'：'+M.translate(r.data_connected?'已连接':'未连接');
            if(initial)select.value=r.saved_sim===1?'1':'0';
        }
        paint(data[1],true);
        var v50=data[2]||{}, physicalSelect=null, physicalButton=null, physicalNote=null;
        var pairSelects=[],pairButton=null;
        function physicalName(n) {
            return M.translate(n===3?'外置 SIM':'内置 SIM')+(n===3?'':' '+(n+1));
        }
        function paintV50(r,initial) {
            v50=r;
            if(!physicalButton)return;
            physicalButton.disabled=!r.ready||!!self.busy;
            physicalSelect.disabled=!r.ready||!!self.busy;
            if(pairButton) {
                pairButton.disabled=!r.ready||!!self.busy;
                pairSelects.forEach(function(select,slot){
                    select.disabled=!r.ready||!!self.busy;
                    if(initial) {
                        var saved=(r.saved_routes||[])[slot],current=saved==null?(r.logical_cards||[])[slot]:saved;
                        if(current!=null&&Array.from(select.options).some(function(o){return o.value===String(current);}))select.value=String(current);
                    }
                });
            }
            if(initial) {
                var current=r.saved_card==null?r.active_card:r.saved_card;
                if(current!=null)physicalSelect.value=String(current);
            }
            var layouts={0:'3内置卡',1:'3内置卡 + 1插拔卡',2:'2内置卡 + 1插拔卡',3:'2内置卡'};
            physicalNote.textContent=M.translate(layouts[r.board_id]||'SIM')+' · '+M.translate('当前上网卡')+'：'+
                (r.active_card==null?M.translate('未连接'):physicalName(r.active_card));
            if(!r.ready)physicalNote.textContent+=' · '+message('v50_mux_unavailable');
        }
        if(v50.supported) {
            // Physical card IDs must never be sent to SPACTCARD or used as
            // AT/cache slot indices. eSIM3 shares SIM2; pSIM shares SIM1.
            controls.hidden=true;
            var physicalBox=el('div',null,'mud-data-sim');panel.append(physicalBox);
            physicalBox.append(el('h3',M.translate('V50 物理卡选择')));
            var physicalRow=el('div',null,'mud-data-sim-controls');physicalBox.append(physicalRow);
            physicalSelect=el('select');physicalSelect.id='mud-v50-card';
            (v50.cards||[]).forEach(function(n){var o=el('option',physicalName(n));o.value=String(n);physicalSelect.append(o);});
            var physicalLabel=el('label',M.translate('默认上网卡'));physicalLabel.htmlFor=physicalSelect.id;
            physicalButton=el('button',M.translate('切换上网卡'),'mud-btn');physicalButton.type='button';
            physicalRow.append(physicalLabel,physicalSelect,physicalButton);
            physicalNote=el('p',null,'mud-note');physicalBox.append(physicalNote);
            physicalBox.append(el('p',M.translate('内置 SIM 3 与内置 SIM 2 共用逻辑卡槽 SIM2；外置 SIM 与内置 SIM 1 共用 SIM1。射频、锁定及 AT 查看仍按两个逻辑卡槽控制。'),'mud-note'));
            if((v50.cards||[]).length>2) {
                var pairBox=el('div',null,'mud-data-sim');physicalBox.append(pairBox);
                pairBox.append(el('h3',M.translate('卡源组合')));
                pairBox.append(el('p',M.translate('分别选择两个逻辑栈接入的卡源，重启后保留。卡源选择不改变默认上网卡或射频开关。'),'mud-note'));
                [0,1].forEach(function(slot){
                    var row=el('div',null,'mud-data-sim-controls'),label=el('label','IMEI'+(slot+1)+' · '+M.translate('卡源'));
                    var select=el('select');select.id='mud-v50-source'+slot;label.htmlFor=select.id;
                    (v50.cards||[]).filter(function(id){return (id===0||id===3?0:1)===slot;}).forEach(function(id){
                        var option=el('option',physicalName(id));option.value=String(id);select.append(option);
                    });
                    row.append(label,select);pairBox.append(row);pairSelects.push(select);
                });
                pairButton=el('button',M.translate('应用卡源组合'),'mud-btn');pairButton.type='button';pairButton.id='mud-v50-pair-apply';pairBox.append(pairButton);
                pairButton.onclick=function(){
                    if(self.busy)return;
                    var first=Number(pairSelects[0].value),second=Number(pairSelects[1].value);
                    self.busy=true;paintV50(v50,false);
                    M.confirmBox('应用卡源组合？','IMEI1: '+physicalName(first)+' · IMEI2: '+physicalName(second),{danger:true}).then(function(yes){
                        if(!yes||!root.isConnected)return;
                        var toast=M.toast('正在应用卡源组合…',{type:'busy',timeout:0});
                        return start('v50-pair-'+first+'-'+second).then(function(r){
                            if(!r.ok||!r.id)throw new Error(r.error||'网络设置提交失败');return wait(r.id);
                        }).then(function(){M.toast('卡源组合已保存',{type:'success'});})
                          .catch(function(e){if(root.isConnected)M.toast(message(e.message),{type:'error'});})
                          .finally(function(){toast.close();});
                    }).finally(function(){
                        self.busy=false;
                        if(root.isConnected)getV50().then(function(r){if(root.isConnected)paintV50(r,true);}).catch(function(){paintV50(v50,false);});
                    });
                };
            }
            paintV50(v50,true);
            physicalButton.onclick=function(){
                if(self.busy)return;
                var target=Number(physicalSelect.value);self.busy=true;
                M.confirmBox('切换上网卡？',physicalName(target),{danger:true}).then(function(yes){
                    if(!yes||!root.isConnected)return;
                    physicalButton.disabled=true;physicalSelect.disabled=true;
                    var toast=M.toast('正在切换上网卡…',{type:'busy',timeout:0});
                    return start('v50-card-'+target).then(function(r){
                        if(!r.ok||!r.id)throw new Error(r.error||'网络设置提交失败');return wait(r.id);
                    }).then(function(){M.selectViewedSlot(target===0||target===3?'0':'1');M.toast('上网卡切换完成',{type:'success'});})
                      .catch(function(e){if(root.isConnected)M.toast(message(e.message),{type:'error'});})
                      .finally(function(){toast.close();});
                }).finally(function(){
                    self.busy=false;
                    if(root.isConnected)getV50().then(function(r){if(root.isConnected)paintV50(r,true);}).catch(function(){paintV50(v50,false);});
                });
            };
        } else if(v50.error)panel.append(el('p',message(v50.error),'mud-note'));
        button.disabled=data[0].slots!==2 || !data[1].ok || data[1].ready===false;
        select.disabled=data[0].slots!==2 || !data[1].ok;
        if(button.disabled)panel.append(el('p',message(data[1].ready===false && data[0].slots===2?'dual_boot_required':'dual_sim_unavailable'),'mud-note'));
        function wait(id) {
            return new Promise(function(resolve,reject) {
                self.cancelWait=function(){reject(new Error('页面已关闭'));};
                var deadline=performance.now()+240000;
                function next() {
                    if(!root.isConnected)return reject(new Error('页面已关闭'));
                    if(performance.now()>deadline)return reject(new Error('应用超时，请刷新确认模组状态'));
                    status(id).then(function(r) {
                        if(r.id!==id || r.state==='error')return reject(new Error(r.error||'网络设置执行失败'));
                        if(r.state==='done' && r.ok)return resolve(r);
                        self.timer=setTimeout(next,1000);
                    }).catch(reject);
                }
                next();
            });
        }
        button.onclick=function() {
            if(self.busy)return;
            var target=select.value; self.busy=true;
            M.confirmBox('切换上网卡？',M.translate('目标卡槽')+'：'+card(Number(target)),{danger:true}).then(function(yes) {
                if(!yes || !root.isConnected)return;
                M.busy(button,true); select.disabled=true;
                var toast=M.toast('正在切换上网卡…',{type:'busy',timeout:0});
                return start(target).then(function(r) {
                    if(!r.ok || !r.id)throw new Error(r.error||'网络设置提交失败');
                    return wait(r.id);
                }).then(function() { M.selectViewedSlot(target); M.toast('上网卡切换完成',{type:'success'}); })
                  .catch(function(e) { if(root.isConnected)M.toast(message(e.message),{type:'error'}); })
                  .finally(function() { toast.close(); M.busy(button,false); select.disabled=false; if(root.isConnected)return get().then(function(r){if(root.isConnected)paint(r,true);}).catch(function(){}); });
            }).finally(function(){self.busy=false; paint(last,false);});
        };
        this.refresh=function() {
            if(document.hidden||!root.isConnected||self.busy||self.inflight)return Promise.resolve();
            self.inflight=true;
            return Promise.all([get().then(function(r){if(root.isConnected)paint(r,false);}),
                physicalButton?getV50().then(function(r){if(root.isConnected)paintV50(r,false);}):Promise.resolve()])
                .catch(function(){}).finally(function(){self.inflight=false;});
        };
        poll.add(this.refresh,2);
        var radioPanel=el('section',null,'mud-card mud-sim-panel');root.append(radioPanel);
        radioPanel.style.marginTop='16px';
        this.radios=SimRadio.attach(radioPanel,{dsds:true});
        this.refreshRadios=function(){return self.radios.refresh();};poll.add(this.refreshRadios,2);
        return root;
    },
    unload:function(){poll.remove(this.refresh);poll.remove(this.refreshRadios);if(this.radios)this.radios.dispose(); clearTimeout(this.timer); if(this.cancelWait)this.cancelWait();},
    handleSaveApply:null,handleSave:null,handleReset:null
});
