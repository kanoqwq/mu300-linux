'use strict';
'require view';
'require rpc';
'require mu300.common as M';

var get=rpc.declare({object:'mu300dash',method:'apn_get',nobatch:true,expect:{'':{}}});
var change=rpc.declare({object:'mu300dash',method:'apn_set',params:['action','payload'],nobatch:true,expect:{'':{}}});
var errors={apn_unsupported:'当前蜂窝接口不支持 APN 管理',apn_invalid:'接入点参数无效',apn_missing:'接入点已不存在，请刷新',apn_unsaved:'网络或 APN 配置有未应用更改，请先保存或撤销',apn_storage:'接入点保存失败',apn_limit:'最多保存 32 个接入点',apn_duplicate:'相同 APN 和协议的接入点已存在',apn_current:'不能删除当前接入点，请先切换',apn_reload:'配置已保存，但应用失败，请刷新后重试',apn_busy:'另一项网络设置仍在执行，请稍后重试'};
function el(tag,text,cls){var n=document.createElement(tag);if(text!=null)n.textContent=text;if(cls)n.className=cls;return n;}
Object.assign(errors,{apn_identity:'无法读取当前 SIM 身份，请稍后重试',apn_bound:'此接入点仍被 SIM 使用，不能删除',apn_scope:'当前 SIM 已记住接入点，请使用按卡应用'});
function t(s){return M.translate(s);}
function proto(s){return {IP:'IPv4',IPV6:'IPv6',IPV4V6:'IPv4 / IPv6'}[s]||s;}
var CSS=`
.mud-apn .apn-panel{padding:20px;min-width:0}.mud-apn h3{margin:0;border:0;padding:0;font-size:1rem;color:var(--text)}
.mud-apn .apn-head,.mud-apn .apn-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.mud-apn .apn-head h3{flex:1}
.mud-apn .apn-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));gap:12px;margin-top:16px}
.mud-apn .apn-item{border:1px solid var(--hairline,var(--border-color,#ddd));border-radius:12px;padding:16px;min-width:0;display:flex;flex-direction:column;gap:10px}
.mud-apn .apn-item.active{border-color:var(--brand,var(--primary,#37a));background:color-mix(in srgb,var(--brand,var(--primary,#37a)) 5%,transparent)}
.mud-apn .apn-item strong,.mud-apn .apn-value{overflow-wrap:anywhere}.mud-apn .apn-actions{margin-top:auto}.mud-apn .mud-note{line-height:1.6;overflow-wrap:anywhere}
.mud-apn-editor{display:grid;gap:12px;margin-top:12px}.mud-apn-editor label{display:grid;gap:6px;min-width:0}.mud-apn-editor input,.mud-apn-editor select{width:100%;max-width:100%;box-sizing:border-box;margin:0}
.mud-apn-editor input[type=checkbox]{width:auto;justify-self:start}
.mud-apn-editor input:not([type=checkbox]),.mud-apn-editor select{min-width:0;min-height:36px;padding:7px 10px;border:1px solid var(--hairline,var(--border,#ccc));border-radius:var(--radius-base,.5rem);background:var(--surface,var(--background,#fff));color:var(--text,#222);font:inherit;line-height:1.4}
.mud-apn-editor input:not([type=checkbox]):focus,.mud-apn-editor select:focus{outline:2px solid var(--brand,var(--primary,#37a));outline-offset:1px}
.mud-dlg:has(.mud-apn-editor){max-height:calc(100dvh - 40px);overflow:auto;box-sizing:border-box}
@media(max-width:480px){.mud-apn .apn-panel{padding:14px}.mud-apn .apn-actions .mud-btn{flex:1}.mud-apn .apn-head h3{flex-basis:100%}}
`;
return view.extend({
    load:function(){return get();},
    render:function(data){
        M.injectCss();M.localizeMenu();
        this.root=el('div',null,'mud mud-apn');this.root.append(el('style',CSS));
        this.paint(data);return this.root;
    },
    refresh:function(){var self=this,seq=this.readSeq=(this.readSeq||0)+1;return get().then(function(r){if(seq===self.readSeq)self.paint(r);});},
    run:function(button,action,payload){
        var self=this;if(self.busy)return Promise.resolve();self.busy=true;self.readSeq=(self.readSeq||0)+1;M.busy(button,true);
        var toast=M.toast('正在应用…',{type:'busy',timeout:0});
        return change(action,JSON.stringify(payload)).then(function(r){
            if(!r.ok){
                var error=new Error(errors[r.error]||r.error||'接入点保存失败');
                if(r.saved)return self.refresh().catch(function(){}).then(function(){throw error;});
                throw error;
            }
            M.toast(action==='apply'||action==='auto'?'接入点已应用，正在连接；能否上网取决于 SIM 和运营商':'接入点已保存',{type:'success'});
            return self.refresh();
        }).catch(function(e){M.toast(t(errors[e.message]||e.message),{type:'error'});})
          .finally(function(){self.busy=false;toast.close();M.busy(button,false);});
    },
    edit:function(profile,button){
        var self=this;if(self.busy)return;
        var form=el('form',null,'mud-apn-editor');form.onsubmit=function(e){e.preventDefault();};
        var name=el('input'),apn=el('input'),type=el('select');
        name.type='text';apn.type='text';
        name.required=true;name.maxLength=64;apn.maxLength=100;apn.pattern='[A-Za-z0-9.\\-]+';
        apn.placeholder=t('留空使用 SIM 默认接入点');name.value=profile?profile.name:'';apn.value=profile?profile.apn:'';
        ['IPV4V6','IPV6','IP'].forEach(function(v){var o=el('option',proto(v));o.value=v;type.append(o);});type.value=profile?profile.pdptype:'IPV4V6';
        [['接入点名称',name],['APN',apn],['IP 协议',type]].forEach(function(row){var label=el('label',t(row[0]));label.append(row[1]);form.append(label);});
        var auth=el('select'),user=el('input'),password=el('input'),roaming=el('select'),attach=el('input'),clear=el('input');
        user.type='text';user.autocomplete='off';user.spellcheck=false;
        [['none','无鉴权'],['pap','PAP'],['chap','CHAP'],['papchap','PAP / CHAP']].forEach(function(v){var o=el('option',t(v[1]));o.value=v[0];auth.append(o);});
        auth.value=profile&&profile.auth||'none';user.value=profile&&profile.username||'';user.maxLength=64;
        password.type='password';password.maxLength=64;password.autocomplete='new-password';
        password.placeholder=t(profile&&profile.has_password?'留空保留原密码':'未设置密码');clear.type='checkbox';attach.type='checkbox';attach.checked=!!(profile&&profile.attach==='1');
        [['IPV4V6','IPv4 / IPv6'],['IPV6','IPv6'],['IP','IPv4']].forEach(function(v){var o=el('option',v[1]);o.value=v[0];roaming.append(o);});roaming.value=profile&&profile.roaming||type.value;
        [['鉴权方式',auth],['用户名',user],['密码',password],['清除已保存密码',clear],['漫游 IP 协议',roaming],['配置初始附着 APN（高级）',attach]].forEach(function(row){var label=el('label',t(row[0]));label.append(row[1]);form.append(label);});
        form.append(el('p',t('初始附着默认关闭；仅在运营商需要时启用。不修改 IMS 配置。'),'mud-note'));
        function ask(){
            return M.confirmBox(profile?'编辑接入点':'添加接入点','保存仅修改列表，点击切换后才会应用。',{content:form,okText:'保存'}).then(function(yes){
                if(!yes)return;
                if(!form.checkValidity() || !name.value.trim()) {M.toast('接入点参数无效',{type:'error'});return ask();}
                var p={id:profile?profile.id:'',name:name.value.trim(),apn:apn.value.trim(),pdptype:type.value,auth:auth.value,username:user.value,roaming:roaming.value,attach:attach.checked?'1':'0'};
                if(clear.checked||password.value||!(profile&&profile.has_password))p.password=clear.checked?'':password.value;
                return self.run(button,'save',p);
            });
        }
        return ask();
    },
    paint:function(data){
        var self=this;this.data=data;var old=this.root.querySelector('section');if(old)old.remove();
        var panel=el('section',null,'mud-card apn-panel'),head=el('div',null,'apn-head');this.root.append(panel);panel.append(head);
        head.append(el('h3',t('接入点 APN')));
        var refresh=el('button',t('刷新'),'mud-btn'),add=el('button',t('添加接入点'),'mud-btn');head.append(refresh,add);
        refresh.onclick=function(){if(self.busy)return;M.busy(refresh,true);self.refresh().catch(function(){M.toast('读取失败，稍后重试',{type:'error'});}).finally(function(){M.busy(refresh,false);});};
        add.onclick=function(){self.edit(null,add);};add.disabled=!data.ok;
        if(!data.ok){panel.append(el('p',t(errors[data.error]||'读取失败，稍后重试'),'mud-note'));return;}
        panel.append(el('p',t('当前配置')+'：'+(data.current.apn||t('SIM 默认'))+' · '+proto(data.current.pdptype)+' · SIM '+(data.data_sim+1),'mud-note'));
        var phase=data.connecting?'正在连接':data.connected?'已连接':data.dial&&data.dial.phase==='failed'?'连接失败':'未连接';
        panel.append(el('p',t(phase)+' · '+t(data.remembered?'按当前 SIM 记住选择':'使用全局接口配置'),'mud-note'));
        var auto=el('button',t('自动匹配当前 SIM'),'mud-btn');head.append(auto);
        auto.onclick=function(){if(self.busy)return;M.confirmBox('自动匹配当前 SIM','按运营商匹配公网 APN；未知运营商使用 SIM 默认。专网或物联卡请手动设置。',{danger:true}).then(function(yes){if(yes)return self.run(auto,'auto',{});});};
        panel.append(el('p',t('接入点应用于当前上网接口，不切换 SIM。切换会短暂中断蜂窝网络，重启后仍保留。'),'mud-note'));
        var grid=el('div',null,'apn-grid');panel.append(grid);
        if(!data.profiles.length)grid.append(el('p',t('暂无接入点，请添加'),'mud-note'));
        data.profiles.forEach(function(p){
            var item=el('article',null,'apn-item'+(p.current?' active':''));grid.append(item);
            item.append(el('strong',p.id==='automatic'&&p.name==='SIM default'?t('SIM 默认'):p.name));
            item.append(el('span',p.apn||t('SIM 默认'),'apn-value'));item.append(el('span',proto(p.pdptype),'mud-note'));
            var actions=el('div',null,'apn-actions');item.append(actions);
            var apply=el('button',t(p.current?'再次应用':'切换'),'mud-btn'),edit=el('button',t('编辑'),'mud-btn'),del=el('button',t('删除'),'mud-btn warn');actions.append(apply,edit,del);
            del.disabled=p.current;
            edit.onclick=function(){self.edit(p,edit);};
            apply.onclick=function(){if(self.busy)return;var label=el('label'),remember=el('input');remember.type='checkbox';remember.checked=true;remember.disabled=!!data.remembered;label.append(remember,document.createTextNode(' '+t('按当前 SIM 记住选择')));M.confirmBox('切换接入点？','将重新建立蜂窝连接，USB 和 Wi-Fi 不受影响。',{content:label,danger:true}).then(function(yes){if(yes)return self.run(apply,'apply',{id:p.id,remember:remember.checked});});};
            del.onclick=function(){if(self.busy)return;M.confirmBox('删除接入点？','',{content:el('p',p.name),danger:true}).then(function(yes){if(yes)return self.run(del,'delete',{id:p.id});});};
        });
    },
    handleSaveApply:null,handleSave:null,handleReset:null
});
