import React, {useEffect, useRef, useState} from 'react';
import {getCurrentWebview, type DragDropEvent} from '@tauri-apps/api/webview';
import {createRoot} from 'react-dom/client';
import {ArrowRight, CheckCircle2, CircleAlert, FileArchive, FolderOpen, GitCompareArrows, LoaderCircle, Moon, PackageCheck, Power, ShieldCheck, Sun, Upload} from 'lucide-react';
import {Button} from './components/ui/button';
import {Dialog, DialogContent, DialogTitle, DialogDescription} from './components/ui/dialog';
import {native, request, subscribe} from './protocol';
import './style.css';
import './ota.css';
import {explainError} from '../../../openwrt/luci-app-unisoc-ota/htdocs/luci-static/resources/unisoc-ota/errors.js';

interface Operation {path:string; before:unknown; after:unknown}
interface Manifest {family:string;profile:string;base_version:string;target_version:string;kernel_release:string;operations:Operation[];preserved:string[];boot?:{size:number};runtime?:unknown}
interface Analysis {manifest:Manifest;blockers:string[];can_export:boolean;payload_bytes:number;files:number;added:number;removed:number;output?:string;suggested_name?:string}
interface Review {token:string;check:{applicable:boolean;conflicts:string[];package_conflicts:string[];required_bytes:number;free_bytes:number}}
interface Status {identity:{version:string};transaction?:{started:number;total:number;status?:string};job?:{status:string;result?:{error?:string}};reboot?:{status:string;error?:string};activation?:{status:string;error?:string}}
const size = (n:number) => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MiB' : (n / 1024).toFixed(1) + ' KiB';

function Studio() {
  const generator = location.pathname.endsWith('diff.html');
  const [en,setEn] = useState(localStorage.getItem('language') === 'en');
  const [dark,setDark] = useState(matchMedia('(prefers-color-scheme: dark)').matches);
  const tr = (zh:string,english:string) => en ? english : zh;
  const explainBlocker = (message:string) => explainError(message,en).message;
  const [old,setOld] = useState(''), [next,setNext] = useState('');
  const [analysis,setAnalysis] = useState<Analysis>();
  const [patch,setPatch] = useState(''), [manifest,setManifest] = useState<Manifest>();
  const [host,setHost] = useState('192.168.9.1'), [password,setPassword] = useState('');
  const [busy,setBusy] = useState(false), [progress,setProgress] = useState('');
  const [error,setError] = useState(''), [success,setSuccess] = useState('');
  const [review,setReview] = useState<Review>(), [running,setRunning] = useState(false);
  const [autoReboot,setAutoReboot] = useState(true), [rebootReady,setRebootReady] = useState(false), [rebootDialog,setRebootDialog] = useState(false);
  const [dropTarget,setDropTarget] = useState<'old'|'new'|'patch'>();
  const dropHandler = useRef<(event:DragDropEvent)=>void>(()=>{});
  function showStatus(s:Status) {
    const done=s.job?.status==='complete' && ['complete','rolled_back'].includes(s.transaction?.status||'');
    setSuccess('');setError('');
    setRebootReady(done);
    if(s.job?.status==='failed') {
      setError(s.job.result?.error || tr('更新未完成，请检查设备事务状态。','Update incomplete. Review device transaction status.'));return;
    }
    if(s.reboot?.status==='rebooting') {
      setRebootReady(false);setSuccess(tr('更新完成，设备正在重启。稍后可重新检查设备状态。','Update complete. The device is rebooting. Check its status again shortly.'));
    } else if(s.reboot?.error) {
      setError(tr('更新已完成，重启失败，可点击下方按钮重试：','Update completed, but reboot failed. Retry below: ')+s.reboot.error);
    } else if(done) setSuccess(s.transaction?.status==='rolled_back'?tr('回滚完成，可在此重启设备。','Rollback complete. Reboot the device here.'):tr('更新完成，可在此直接重启设备使更新生效。','Update complete. Reboot the device here to activate the update.'));
    else if(s.job?.status!=='running') setSuccess(tr('设备状态已读取。当前版本：','Device status loaded. Current version: ')+s.identity.version);
    if(s.activation?.status==='warning') setError(tr('系统文件已更新，网页插件重载失败：','System files are updated, but web plugin reload failed: ')+s.activation.error);
  }
  useEffect(() => { document.documentElement.classList.toggle('dark',dark); }, [dark]);
  useEffect(() => {
    let alive=true;
    const stop=getCurrentWebview().onDragDropEvent(event=>{if(alive)dropHandler.current(event.payload);});
    void stop.catch(e=>{if(alive)setError(String(e));});
    const prevent=(event:DragEvent)=>event.preventDefault();
    window.addEventListener('dragover',prevent);window.addEventListener('drop',prevent);
    return ()=>{alive=false;void stop.then(unlisten=>unlisten()).catch(()=>{});window.removeEventListener('dragover',prevent);window.removeEventListener('drop',prevent);};
  },[]);
  useEffect(() => {
    if (!generator) void request<{path:string;manifest:Manifest}>('ota_package').then(p=>{
      setPatch(p.path);setManifest(p.manifest);if(p.manifest.family==='mu300')setHost('192.168.77.1');
    }).catch(()=>{});
  },[]);
  useEffect(() => {
    const stop = subscribe(e => {
      if (e.type === 'ota_progress') {
        const d = e.data as {stage:string;path?:string;done?:number;total?:number};
        setProgress(d.stage === 'upload' ? `↑ ${size(d.done || 0)} / ${size(d.total || 0)}` : d.path || d.stage);
      }
    });
    return () => { void stop.then(f => f()); };
  }, []);
  useEffect(() => {
    if (!running) return;
    let alive = true;
    let timer:ReturnType<typeof setTimeout>;
    async function tick() {
      let again=true;
      try {
        const s=await request<Status>('ota_status',{host,password,path:patch});
        if (!alive) return;
        if(s.transaction)setProgress(`${s.transaction.started} / ${s.transaction.total}`);
        if (s.job?.status !== 'running') {
          again=false;
          setRunning(false);
          showStatus(s);
        }
      } catch(e) {
        if (alive) setProgress(tr('连接中断；设备继续处理，可稍后重新检查状态。','Connection interrupted. The device continues processing; check status again later.'));
      } finally {
        if(alive && again)timer=setTimeout(()=>void tick(),2000);
      }
    }
    timer=setTimeout(()=>void tick(),2000);
    return () => {alive=false;clearTimeout(timer);};
  },[running,host,password,en,patch]);
  async function work<T>(fn:()=>Promise<T>):Promise<T|undefined> {
    setBusy(true);setError('');setSuccess('');setProgress('');
    try { return await fn(); } catch(e) {setError(String(e));return undefined;}
    finally {setBusy(false);}
  }
  async function pick(which:'old'|'new'|'patch') {
    const path = await work(()=>native('pick_ota_input'));
    if (!path) return;
    await selectInput(which,path);
  }
  async function selectInput(which:'old'|'new'|'patch',path:string) {
    if(!(which==='patch'?/\.ota$/i:/\.zip$/i).test(path)) {
      setError(which==='patch'?tr('请选择 .ota 差异包。','Select an .ota patch.'):tr('请选择完整 ZIP 发行包。','Select a full release ZIP.'));return;
    }
    setError('');
    setReview(undefined);setAnalysis(undefined);setSuccess('');setRebootReady(false);
    if (which === 'old') setOld(path);
    else if (which === 'new') setNext(path);
    else { setPatch(path);setManifest(undefined); const r=await work(()=>request<{manifest:Manifest}>('ota_package',{path})); if(r){setManifest(r.manifest);if(host==='192.168.9.1'||host==='192.168.77.1')setHost(r.manifest.family==='mu300'?'192.168.77.1':'192.168.9.1');} }
  }
  const blocked = busy || running;
  dropHandler.current = event=>{
    if(blocked || event.type==='leave') {setDropTarget(undefined);return;}
    // Tauri reports physical pixels; DOM hit testing uses CSS pixels.
    const scale=window.devicePixelRatio||1;
    const element=document.elementFromPoint(event.position.x/scale,event.position.y/scale)?.closest<HTMLButtonElement>('[data-ota-drop]');
    const target=element?.dataset.otaDrop as 'old'|'new'|'patch'|undefined;
    if(event.type!=='drop') {setDropTarget(target);return;}
    setDropTarget(undefined);
    if(!target) {setError(tr('请将文件拖入对应的选择框。','Drop the file into its selection box.'));return;}
    if(event.paths.length!==1) {setError(tr('每个选择框一次只接收一个文件。','Drop one file into each selection box.'));return;}
    void selectInput(target,event.paths[0]);
  };
  return <div className="ota-shell">
    <header><div className="brand"><div className="brand-icon">{generator?<GitCompareArrows size={24}/>:<PackageCheck size={24}/>}</div><div><strong>{generator?'OpenWrt Diff Studio':'OpenWrt Patch Updater'}</strong><small>{tr('保留数据 · 只更新变更文件','Keep your data · Update changed files')}</small></div></div>
      <div className="preferences"><Button variant="ghost" disabled={blocked} onClick={()=>{setEn(!en);localStorage.setItem('language',en?'zh':'en');}}>{en?'中文':'English'}</Button><Button variant="ghost" onClick={()=>setDark(!dark)} aria-label="Theme">{dark?<Sun size={18}/>:<Moon size={18}/>}</Button></div></header>
    <main className="ota-main"><div className="ota-intro"><span className="ota-eyebrow">{generator?'RELEASE TO PATCH':'DIFFERENTIAL SYSTEM UPDATE'}</span><h1>{generator?tr('把两版发行包，变成一次轻量更新','Two releases. One compact update.'):tr('更新系统，留下你的设置','Update the system. Keep your setup.')}</h1><p>{generator?tr('点击选择，或将旧版和新版完整 ZIP 分别拖入对应框。相同内容只保存一份。','Select or drop the old and new full ZIPs into their respective boxes. Identical content is stored once.'):tr('导入可信差异包，连接正在运行 OpenWrt 的设备，先检查再应用。','Import a trusted patch and connect to a running OpenWrt device. Review before applying.')}</p></div>
    <div className="ota-grid"><section className="ota-card"><h2><span className="ota-number">1</span>{generator?tr('选择发行版本','Select releases'):tr('选择补丁与设备','Select patch and device')}</h2>
    {generator?<><label className="ota-label">{tr('旧版完整包','Old full release')}</label><button className={'ota-picker'+(dropTarget==='old'?' is-dragover':'')} data-ota-drop="old" disabled={blocked} onClick={()=>void pick('old')}><FileArchive/><span>{old || tr('选择 ZIP 文件','Select ZIP file')}</span><FolderOpen size={18}/></button><div className="ota-arrow"><ArrowRight size={18}/></div><label className="ota-label">{tr('新版完整包','New full release')}</label><button className={'ota-picker'+(dropTarget==='new'?' is-dragover':'')} data-ota-drop="new" disabled={blocked} onClick={()=>void pick('new')}><FileArchive/><span>{next || tr('选择 ZIP 文件','Select ZIP file')}</span><FolderOpen size={18}/></button>
      <Button className="ota-wide" disabled={blocked || !old || !next} onClick={()=>void work(async()=>{const r=await request<Analysis>('ota_analyze',{old,new:next});setAnalysis(r);})}>{busy?<LoaderCircle className="animate-spin" size={18}/>:<GitCompareArrows size={18}/>} {tr('分析差异','Analyze changes')}</Button></>
      :<><button className={'ota-picker'+(dropTarget==='patch'?' is-dragover':'')} data-ota-drop="patch" disabled={blocked} onClick={()=>void pick('patch')}><FileArchive/><span>{patch || tr('选择 .ota 差异包','Select .ota patch')}</span><FolderOpen size={18}/></button><label className="ota-label">{tr('设备管理地址','Device management address')}</label><input value={host} disabled={blocked} onChange={e=>{setHost(e.target.value);setReview(undefined);setRebootReady(false);}}/><label className="ota-label">SSH root {tr('密码','password')}</label><input type="password" value={password} disabled={blocked} onChange={e=>{setPassword(e.target.value);setReview(undefined);setRebootReady(false);}} autoComplete="off"/>
      <Button className="ota-wide" disabled={blocked || !patch || !host} onClick={()=>void work(async()=>{setReview(await request<Review>('ota_probe',{host,password,path:patch}));})}><ShieldCheck size={18}/>{tr('连接并检查','Connect and review')}</Button><Button variant="ghost" className="ota-wide" disabled={busy || !host} onClick={()=>void work(async()=>{const s=await request<Status>('ota_status',{host,password,path:patch});setRunning(s.job?.status==='running');showStatus(s);})}>{tr('重新检查设备状态','Check device status')}</Button></>}
    </section><section className="ota-card ota-result"><h2><span className="ota-number">2</span>{tr('检查并继续','Review and continue')}</h2><div className="ota-result-body" tabIndex={0} aria-label={tr('检查结果','Review results')}>
      {analysis || manifest?<><div className="ota-release"><small>{tr('目标设备','Target profile')}</small><strong>{(analysis?.manifest || manifest)!.profile}</strong><small>{(analysis?.manifest || manifest)!.kernel_release}</small></div><p className="ota-versions">{(analysis?.manifest || manifest)!.base_version}<ArrowRight size={16}/>{(analysis?.manifest || manifest)!.target_version}</p></>:<div className="ota-empty"><ShieldCheck size={42}/><p>{tr('选择文件后，在这里查看版本与兼容检查结果。','Select files to review versions and compatibility here.')}</p></div>}
      {(analysis?.manifest || manifest)?.boot && <p className="ota-notice">{tr('包含内核与启动镜像更新，配套模块随系统差异一起更新；用户插件、配置和数据保留。','Includes kernel/boot update and matching module changes. User packages, settings and data are retained.')}</p>}
      {analysis && <><div className="ota-stats"><div><strong>{analysis.files}</strong><small>{tr('文件变更','Changed files')}</small></div><div><strong>{size(analysis.payload_bytes)}</strong><small>{tr('未压缩载荷','Uncompressed payload')}</small></div><div><strong>{analysis.added} / {analysis.removed}</strong><small>{tr('新增 / 删除','Added / removed')}</small></div></div>
        {!!analysis.blockers.length && <div className="ota-error ota-blockers" tabIndex={0} role="region" aria-label={tr('无法生成的原因','Reasons export is unavailable')}>{analysis.blockers.map((b,i)=><p key={i}>{explainBlocker(b)}</p>)}<details><summary>{tr('技术详情','Technical details')}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{analysis.blockers.join('\n')}</pre></details></div>}<details className="ota-files"><summary>{tr('查看文件清单','View file list')}</summary>{analysis.manifest.operations.map(o=><div key={o.path}>{o.before?(o.after?'~':'−'):'+'} {o.path}</div>)}</details>
        <Button className="ota-wide" disabled={blocked || !analysis.can_export} onClick={()=>void work(async()=>{const output=await native('save_ota',{suggestedName:analysis.suggested_name,releasePath:next});if(!output)return;const r=await request<Analysis>('ota_export',{output});setSuccess(tr('已生成差异包：','Patch saved: ')+r.output);})}><Upload size={18}/>{tr('导出差异包','Export patch')}</Button></>}
      {review && <><p>{tr('所需空间 / 可用空间：','Required / available: ')}{size(review.check.required_bytes)} / {size(review.check.free_bytes)}</p>{!![...review.check.conflicts,...review.check.package_conflicts].length && <div className="ota-error">{tr('本地文件或依赖冲突，未写入：','Local file or dependency conflicts; nothing written: ')}{[...review.check.conflicts,...review.check.package_conflicts].join(', ')}</div>}<label style={{display:'flex',gap:10,alignItems:'center',margin:'16px 0',fontSize:14}}><input type="checkbox" style={{width:'auto',height:'auto',margin:0}} checked={autoReboot} disabled={blocked} onChange={e=>setAutoReboot(e.target.checked)}/>{tr('更新完成后自动重启','Automatically reboot after updating')}</label><Button className="ota-wide" disabled={blocked || !review.check.applicable} onClick={()=>void work(async()=>{await request('ota_apply',{host,password,token:review.token,auto_reboot:autoReboot});setRebootReady(false);setRunning(true);setReview(undefined);})}><PackageCheck size={18}/>{tr('确认应用此补丁','Apply reviewed patch')}</Button></>}
    </div></section></div>
    {(busy || running) && <div className="ota-notice"><LoaderCircle className="animate-spin" size={18}/><span>{running?tr('设备正在写入，关闭电脑更新器不会终止设备事务。','Device is writing. Closing this app does not cancel its transaction.'):tr('正在处理，请稍候…','Processing…')}<small>{progress}</small></span></div>}
    {error && <div className="ota-error" role="alert"><CircleAlert size={18}/><div><p>{explainError(error,en).message}</p><details><summary>{tr('技术详情','Technical details')}</summary><pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:180,overflow:'auto'}}>{explainError(error,en).details}</pre></details></div></div>}{success && <div className="ota-notice" role="status"><CheckCircle2 size={18}/><span>{success}</span></div>}
    {!generator && rebootReady && <Button disabled={blocked} onClick={()=>setRebootDialog(true)}><Power size={18}/>{tr('重启设备','Reboot device')}</Button>}
    <Dialog open={rebootDialog} onOpenChange={setRebootDialog}><DialogContent closeLabel={tr('关闭','Close')}><DialogTitle>{tr('重启设备？','Reboot device?')}</DialogTitle><DialogDescription>{tr('更新已完成。重启会短暂断开网络，配置和数据会保留。','The update is complete. Rebooting briefly disconnects networking; settings and data are retained.')}</DialogDescription><div style={{display:'flex',gap:12,justifyContent:'flex-end',marginTop:24}}><Button variant="ghost" onClick={()=>setRebootDialog(false)}>{tr('取消','Cancel')}</Button><Button onClick={()=>{setRebootDialog(false);void work(async()=>{await request('ota_reboot',{host,password});setRebootReady(false);setSuccess(tr('重启命令已发送，设备正在重新启动。','Reboot requested. The device is restarting.'));});}}>{tr('确认重启','Confirm reboot')}</Button></div></DialogContent></Dialog>
    <footer className="ota-footer"><ShieldCheck size={16}/>{tr('保留配置、额外插件和用户数据。系统变更写入前保留恢复副本，内核和模块可一同更新。仅导入可信来源的补丁。','Settings, extra packages and user data are retained. Recovery copies precede writes. Kernel and modules can update together. Import trusted patches only.')}</footer>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<Studio/>);
