'use strict';
'require view';
'require rpc';
'require poll';
'require mu300.common as M';
'require mu300.charge as Charge';

var history = rpc.declare({ object: 'unisoc-battery-history', method: 'history', params: ['range', 'points'], expect: {'': {}}, nobatch: true });
var ranges = [['full', '自上次充满'], ['charge', '自上次充电'], ['24h', '24 小时'], ['7d', '7 天']];
function el(tag, cls, text) {
	var node = document.createElement(tag);
	if (cls) node.className = cls;
	if (text != null) node.textContent = text;
	return node;
}
function duration(seconds) {
	if (!Number.isFinite(seconds) || seconds < 0) return '—';
	return Math.floor(seconds / 3600) + M.translate('小时') + ' ' + Math.floor(seconds % 3600 / 60) + M.translate('分钟');
}
function stamp(t, short) {
	return new Date(t * 1000).toLocaleString(M.uiLanguage() === 'zh' ? 'zh-CN' : M.uiLanguage(), short ? {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'} : undefined);
}
function number(n, digits) { return Number.isFinite(n) ? n.toFixed(digits) : '—'; }
function state(s) { return M.translate({D:'使用电池',C:'正在充电',F:'已充满',P:'已连接电源，未充电'}[s] || '正在读取…'); }

return view.extend({
	load: function() { return Promise.resolve(); },
	render: function() {
		M.injectCss(); M.localizeMenu();
		this._disposed = false; this._range = 'full'; this._generation = 0;
		var root = this.root = el('div', 'mud mud-battery');
		root.innerHTML = `<style>
.mud-battery{--battery-green:var(--success,#22a474);--battery-drain:var(--warning,#c28a22)}
.mud-battery h2{margin:0 0 16px;font-size:1.25rem}.mud-battery h3{margin:0;font-size:.9rem}
.mud-battery-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:14px}
.mud-battery .mud-card{min-width:0}.mud-battery-label{display:flex;align-items:center;gap:8px;font-size:.8rem;color:var(--text-muted,var(--text-light,#777))}
.mud-battery-label svg{color:var(--battery-green);width:17px;height:17px;flex:none}.mud-battery-value{font-size:1.65rem;font-weight:700;margin:12px 0 8px;line-height:1.25;overflow-wrap:anywhere}.mud-battery-small{font-size:.75rem;color:var(--text-muted,var(--text-light,#777));line-height:1.6}
.mud-battery-meter{height:7px;border-radius:8px;background:var(--hairline,var(--border,#ddd));overflow:hidden}.mud-battery-meter>span{display:block;height:100%;background:var(--battery-green);transition:width .3s}
.mud-battery-head{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:14px}.mud-battery-ranges{display:flex;flex-wrap:wrap;gap:6px}.mud-battery-ranges .mud-btn{font-size:.75rem;min-height:32px;padding:5px 10px}.mud-battery-ranges .mud-btn[aria-pressed=true]{background:var(--brand,var(--primary,#2f7bf6));border-color:var(--brand,var(--primary,#2f7bf6));color:var(--on-brand,#fff)}
.mud-battery-chart{position:relative;min-height:280px}.mud-battery-chart svg{display:block;width:100%;height:280px;touch-action:pan-y;overflow:visible}.mud-battery-chart text{fill:var(--text-muted,var(--text-light,#777));font:11px sans-serif}.mud-battery-chart .grid{stroke:var(--hairline,var(--border,#ddd));stroke-width:1}.mud-battery-chart .charge-line{stroke:var(--battery-green);fill:none;stroke-width:2}.mud-battery-chart .power-line{stroke:var(--battery-drain);fill:none;stroke-width:1.5}.mud-battery-chart .cursor{stroke:var(--text-muted,#777);stroke-dasharray:3 3}.mud-battery-tip{min-height:23px;font-size:.78rem;color:var(--text-muted,var(--text-light,#777));margin-bottom:8px;overflow-wrap:anywhere}.mud-battery-legend{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0;font-size:.72rem;color:var(--text-muted,var(--text-light,#777))}.mud-battery-legend span:before{content:'';display:inline-block;width:9px;height:9px;border-radius:3px;margin-right:6px;background:var(--battery-green);opacity:.6}.mud-battery-legend span:nth-child(2):before{background:var(--text-muted,#999)}.mud-battery-legend span:nth-child(3):before{background:var(--battery-drain)}
.mud-battery-status{display:flex;align-items:center;gap:8px;min-height:24px;margin:10px 0;font-size:.8rem;color:var(--text-muted,var(--text-light,#777))}.mud-battery-status[aria-busy=true]:before{content:'';width:12px;height:12px;border:2px solid var(--hairline,#ccc);border-top-color:var(--brand,#2f7bf6);border-radius:50%;animation:mud-battery-spin .8s linear infinite}@keyframes mud-battery-spin{to{transform:rotate(360deg)}}
.mud-battery .mud-device-head{display:flex;justify-content:space-between;align-items:center;gap:10px}.mud-battery .mud-device-field{display:flex;flex-direction:column;gap:7px;margin:12px 0}.mud-battery .mud-device-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.mud-battery .mud-device-toggle{display:flex;align-items:center;gap:9px;font-size:.82rem;cursor:pointer}.mud-battery .mud-device-field label{font-size:.8rem;color:var(--text-muted,#777)}
@media(max-width:1000px){.mud-battery-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:440px){.mud-battery-grid{gap:10px}.mud-battery-value{font-size:1.3rem}.mud-battery-ranges{width:100%}.mud-battery-ranges .mud-btn{flex:1 0 40%}.mud-battery .mud-device-actions .mud-btn{flex:1 0 45%;white-space:normal}}@media(prefers-reduced-motion:reduce){.mud-battery-meter>span{transition:none}}
</style>`;
		root.appendChild(el('h2', '', M.translate('电池与电量')));
		var grid = el('div', 'mud-battery-grid'); this.cards = {};
		[['capacity','当前电量','battery'],['state','充电状态','plug'],['time','电池使用时间','clock'],['estimate','预估续航','clock']].forEach(function(row) {
			var card = el('section','mud-card'), label = el('div','mud-battery-label');
			label.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true">' + (row[2] === 'battery' ? '<rect x="3" y="7" width="17" height="10" rx="2"/><path d="M22 10v4M6 10v4M9 10v4"/>' : row[2] === 'plug' ? '<path d="M9 3v5m6-5v5M7 8h10v4a5 5 0 0 1-10 0zM12 17v4"/>' : '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>') + '</svg>';
			label.appendChild(document.createTextNode(M.translate(row[1]))); card.appendChild(label);
			var value = el('div','mud-battery-value','—'), sub = el('div','mud-battery-small','—');card.append(value,sub);
			this.cards[row[0]] = {card:card,value:value,sub:sub}; grid.appendChild(card);
		}, this);
		var meter = el('div','mud-battery-meter'); this.meter = el('span'); this.meter.style.width='0%'; meter.appendChild(this.meter);this.cards.capacity.card.appendChild(meter);
		root.appendChild(grid);
		this.notice = el('div','mud-battery-status',M.translate('正在读取…'));this.notice.setAttribute('role','status');this.notice.setAttribute('aria-live','polite');root.appendChild(this.notice);
		var chartCard=el('section','mud-card'), head=el('div','mud-battery-head');head.appendChild(el('h3','',M.translate('电池历史')));
		var buttons=el('div','mud-battery-ranges');buttons.setAttribute('role','group');buttons.setAttribute('aria-label',M.translate('时间区间'));
		ranges.forEach(function(row){var b=el('button','mud-btn',M.translate(row[1]));b.type='button';b.dataset.range=row[0];b.setAttribute('aria-pressed',String(row[0]===this._range));b.addEventListener('click',function(){if(this._range===row[0])return;this._range=row[0];this._generation++;buttons.querySelectorAll('button').forEach(function(x){x.setAttribute('aria-pressed',String(x.dataset.range===row[0]));});this.refresh();}.bind(this));buttons.appendChild(b);},this);
		head.appendChild(buttons);chartCard.appendChild(head);
		this.tip=el('div','mud-battery-tip','—');chartCard.appendChild(this.tip);this.chart=el('div','mud-battery-chart');chartCard.appendChild(this.chart);
		var legend=el('div','mud-battery-legend');['正在充电或已充满','已连接电源，未充电','电池放电功率'].forEach(function(s){legend.appendChild(el('span','',M.translate(s)));});chartCard.appendChild(legend);
		chartCard.appendChild(el('div','mud-battery-small',M.translate('每分钟记录，保留最近 10 天；关机或缺失时段留空。功率和电流为电池净值，不是充电器输入值。')));root.appendChild(chartCard);
		Charge.mount(root);
		this._poll = this.refresh.bind(this);poll.add(this._poll,10);this.refresh();
		return root;
	},
	refresh: function() {
		if(this._disposed)return Promise.resolve();
		if(this._pending){this._queued=true;return this._pending;}
		var generation=this._generation, range=this._range;
		this.notice.setAttribute('aria-busy','true');this.notice.textContent=M.translate('正在读取…');
		this._pending=history(range,240).then(function(s){
			if(this._disposed||generation!==this._generation)return;
			if(s.error)throw new Error(s.error);
			if(s.present!==true){this.notice.textContent=M.translate('未检测到电池');this.chart.replaceChildren();return;}
			this.paint(s);this.notice.textContent=s.first==null?M.translate('暂无历史记录，采集后自动显示'):M.translate('已更新')+' · '+stamp(s.now,true);
		}.bind(this)).catch(function(){if(!this._disposed&&generation===this._generation)this.notice.textContent=M.translate('电池记录读取失败，请稍后重试');}.bind(this)).finally(function(){this._pending=null;if(this._disposed)return;this.notice.setAttribute('aria-busy','false');if(this._queued){this._queued=false;this.refresh();}}.bind(this));
		return this._pending;
	},
	paint: function(s) {
		var c=s.current||{}, pct=Number.isFinite(c.pct)?c.pct:null;
		this.cards.capacity.value.textContent=pct==null?'—':pct+'%';this.cards.capacity.sub.textContent=Number.isFinite(c.temp)?number(c.temp/10,1)+' °C':'—';this.meter.style.width=(pct==null?0:Math.max(0,Math.min(100,pct)))+'%';
		this.cards.state.value.textContent=state(c.st);this.cards.state.sub.textContent=number(c.mw==null?null:Math.abs(c.mw)/1000,2)+' W · '+(Number.isFinite(c.ma)&&c.ma>0?'+':'')+number(c.ma,0)+' mA · '+number(c.mv==null?null:c.mv/1000,2)+' V';
		this.cards.time.value.textContent=duration(s.stats&&s.stats.on_battery_s);this.cards.time.sub.textContent=M.translate((ranges.find(function(x){return x[0]===s.range;})||ranges[0])[1]);
		this.cards.estimate.value.textContent=duration(s.estimate_s);this.cards.estimate.sub.textContent=M.translate(c.online?'连接电源时不估算续航':s.estimate_s==null?'放电记录不足，暂不估算':'按最近放电速度估算');
		this.draw(s);
	},
	draw: function(s) {
		var width=Math.max(280,this.chart.clientWidth||900), top=24, base=182, pbase=232, n=s.pct.length, span=s.t1-s.t0;
		var svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 '+width+' 280');svg.setAttribute('preserveAspectRatio','none');svg.setAttribute('tabindex','0');svg.setAttribute('role','img');svg.setAttribute('aria-label',M.translate('电池历史'));
		function node(name,attrs,text){var x=document.createElementNS(svg.namespaceURI,name);Object.keys(attrs).forEach(function(k){x.setAttribute(k,String(attrs[k]));});if(text!=null)x.textContent=text;svg.appendChild(x);return x;}
		var x=function(i){return span>0?Math.max(0,Math.min(width,(s.t0+i*s.step-s.t0)*width/span)):0;};
		[0,50,100].forEach(function(v){var y=base-v*(base-top)/100;node('line',{x1:0,x2:width,y1:y,y2:y,class:'grid'});node('text',{x:3,y:y-4},v+'%');});
		var maxPower=Math.max(1000,Math.ceil(Math.max.apply(null,s.mw.filter(Number.isFinite).concat([0]))/1000)*1000);
		function paths(values,y,cls,area){var segment=[];function flush(){if(!segment.length)return;var d='M'+segment.map(function(p){return p.join(',');}).join(' L');if(area){var a=d+' L'+segment[segment.length-1][0]+','+base+' L'+segment[0][0]+','+base+' Z';node('path',{d:a,fill:'var(--battery-green)',opacity:'.12'});}node('path',{d:d,class:cls});segment=[];}values.forEach(function(v,i){if(!Number.isFinite(v)){flush();return;}segment.push([x(i),y(v)]);});flush();}
		for(var i=0;i<n;i++){if(!Number.isFinite(s.pct[i]))continue;var st=s.st[i];if(st==='C'||st==='F'||st==='P')node('rect',{x:x(i),y:top,width:Math.max(0,x(i+1)-x(i)+.1),height:base-top,fill:st==='P'?'var(--text-muted,#888)':'var(--battery-green)',opacity:'.10'});}
		paths(s.pct,function(v){return base-v*(base-top)/100;},'charge-line',true);paths(s.mw,function(v){return pbase-v*32/maxPower;},'power-line',false);
		node('line',{x1:0,x2:width,y1:pbase,y2:pbase,class:'grid'});node('text',{x:3,y:200},number(maxPower/1000,1)+' W');
		var ticks=width<600?2:4;for(var j=0;j<=ticks;j++){var t=s.t0+span*j/ticks;node('text',{x:j*width/ticks,y:268,'text-anchor':j===0?'start':j===ticks?'end':'middle'},stamp(t,true));}
		var cursor=node('line',{x1:0,x2:0,y1:top,y2:pbase,class:'cursor',visibility:'hidden'}), selected=0;
		var show=function(i){if(!n)return;selected=Math.max(0,Math.min(n-1,i));cursor.setAttribute('x1',x(selected));cursor.setAttribute('x2',x(selected));cursor.setAttribute('visibility','visible');this.tip.textContent=stamp(s.t0+selected*s.step,false)+' · '+(Number.isFinite(s.pct[selected])?s.pct[selected]+'%':'—')+' · '+(s.st[selected]?state(s.st[selected]):M.translate('暂无记录'))+(Number.isFinite(s.mw[selected])?' · '+number(s.mw[selected]/1000,2)+' W':'');}.bind(this);
		svg.addEventListener('pointermove',function(e){var box=svg.getBoundingClientRect();show(Math.round((e.clientX-box.left)/box.width*span/Math.max(1,s.step)));});svg.addEventListener('keydown',function(e){if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();show(selected+(e.key==='ArrowRight'?1:-1));}});
		this.chart.replaceChildren(svg);this.tip.textContent=s.first==null?M.translate('暂无历史记录，采集后自动显示'):stamp(s.t0,true)+' — '+stamp(s.t1,true);
	},
	unload: function(){this._disposed=true;this._generation++;if(this._poll)poll.remove(this._poll);},
	handleSave:null,handleSaveApply:null,handleReset:null
});
