import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ArrowRight, CheckCircle2, CircleAlert, FileArchive, FolderOpen, GitCompareArrows, LoaderCircle, Moon, PackageCheck, ShieldCheck, Sun, Upload} from 'lucide-react';
import {Button} from './components/ui/button';
import {native, request, subscribe} from './protocol';
import './style.css';
import './ota.css';

interface Operation {path:string; before:unknown; after:unknown}
interface Manifest {family:string;profile:string;base_version:string;target_version:string;kernel_release:string;operations:Operation[];preserved:string[]}
interface Analysis {manifest:Manifest;blockers:string[];can_export:boolean;payload_bytes:number;files:number;added:number;removed:number;output?:string}
interface Review {token:string;check:{applicable:boolean;conflicts:string[];package_conflicts:string[];required_bytes:number;free_bytes:number}}
interface Status {identity:{version:string};transaction?:{started:number;total:number};job?:{status:string;result?:{error?:string}}}
const size = (n:number) => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MiB' : (n / 1024).toFixed(1) + ' KiB';

function Studio() {
  const generator = location.pathname.endsWith('diff.html');
  const [en,setEn] = useState(localStorage.getItem('language') === 'en');
  const [dark,setDark] = useState(matchMedia('(prefers-color-scheme: dark)').matches);
  const tr = (zh:string,english:string) => en ? english : zh;
  const explainBlocker = (message:string) => {
    if (en) return message;
    const reasons:[string,string][] = [
      ['First-time OTA installation: the old release has no OTA engine; bootstrap migration is not implemented','旧版没有 OTA 引擎；当前工具尚未实现首次安装 OTA 环境的迁移流程。'],
      ['Core runtime/module/updater changed; use the full installer: ','核心运行库、模块或更新器变化，当前工具尚不能更新：'],
      ['Installed package set changed; dependency migration is not implemented: ','基础软件包集合变化，当前工具尚未实现依赖迁移（新增／移除数量及软件包）：'],
      ['Package ABI/dependencies changed: ','软件包 ABI 或依赖变化：'],
      ['No hotfixable system files changed','没有可生成文件热补丁的系统变更。'],
    ];
    for (const [prefix,label] of reasons) if (message.startsWith(prefix)) return label+message.slice(prefix.length);
    return message;
  };
  const [old,setOld] = useState(''), [next,setNext] = useState('');
  const [analysis,setAnalysis] = useState<Analysis>();
  const [patch,setPatch] = useState(''), [manifest,setManifest] = useState<Manifest>();
  const [host,setHost] = useState('192.168.9.1'), [password,setPassword] = useState('');
  const [busy,setBusy] = useState(false), [progress,setProgress] = useState('');
  const [error,setError] = useState(''), [success,setSuccess] = useState('');
  const [review,setReview] = useState<Review>(), [running,setRunning] = useState(false);
  useEffect(() => { document.documentElement.classList.toggle('dark',dark); }, [dark]);
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
    const timer = setInterval(() => {
      void request<Status>('ota_status',{host,password}).then(s => {
        if (!alive) return;
        if(s.transaction)setProgress(`${s.transaction.started} / ${s.transaction.total}`);
        if (s.job?.status !== 'running') {
          setRunning(false);
          if (s.job?.status === 'complete') setSuccess(tr('更新完成，请在网页系统补丁页重启设备。','Update complete. Reboot from the device System patches page.'));
          else setError(s.job?.result?.error || tr('更新未完成，请检查设备事务状态。','Update incomplete. Review device transaction status.'));
        }
      }).catch(e => {if (alive) setProgress(tr('连接中断；设备继续处理，可稍后重新检查状态。','Connection interrupted. The device continues processing; check status again later.') + ' ' + String(e));});
    },2000);
    return () => {alive=false;clearInterval(timer);};
  },[running,host,password,en]);
  async function work<T>(fn:()=>Promise<T>):Promise<T|undefined> {
    setBusy(true);setError('');setSuccess('');setProgress('');
    try { return await fn(); } catch(e) {setError(String(e));return undefined;}
    finally {setBusy(false);}
  }
  async function pick(which:'old'|'new'|'patch') {
    const path = await native('pick_ota_input');
    if (!path) return;
    setReview(undefined);setAnalysis(undefined);setSuccess('');
    if (which === 'old') setOld(path);
    else if (which === 'new') setNext(path);
    else { setPatch(path); const r=await work(()=>request<{manifest:Manifest}>('ota_package',{path})); if(r){setManifest(r.manifest);if(host==='192.168.9.1'||host==='192.168.77.1')setHost(r.manifest.family==='mu300'?'192.168.77.1':'192.168.9.1');} }
  }
  const blocked = busy || running;
  return <div className="ota-shell">
    <header><div className="brand"><div className="brand-icon">{generator?<GitCompareArrows size={24}/>:<PackageCheck size={24}/>}</div><div><strong>{generator?'OpenWrt Diff Studio':'OpenWrt Patch Updater'}</strong><small>{tr('保留数据 · 只更新变更文件','Keep your data · Update changed files')}</small></div></div>
      <div className="preferences"><Button variant="ghost" disabled={blocked} onClick={()=>{setEn(!en);localStorage.setItem('language',en?'zh':'en');}}>{en?'中文':'English'}</Button><Button variant="ghost" onClick={()=>setDark(!dark)} aria-label="Theme">{dark?<Sun size={18}/>:<Moon size={18}/>}</Button></div></header>
    <main className="ota-main"><div className="ota-intro"><span className="ota-eyebrow">{generator?'RELEASE TO PATCH':'VERIFIED FILE UPDATE'}</span><h1>{generator?tr('把两版发行包，变成一次轻量更新','Two releases. One compact update.'):tr('更新系统，留下你的设置','Update the system. Keep your setup.')}</h1><p>{generator?tr('选择旧版和新版完整 ZIP。工具会解析系统文件，展示兼容性与差异；相同内容只保存一份。','Select the old and new full ZIPs. Review file changes and compatibility; identical content is stored once.'):tr('导入可信差异包，连接正在运行 OpenWrt 的设备，先检查再应用。','Import a trusted patch and connect to a running OpenWrt device. Review before applying.')}</p></div>
    <div className="ota-grid"><section className="ota-card"><h2><span className="ota-number">1</span>{generator?tr('选择发行版本','Select releases'):tr('选择补丁与设备','Select patch and device')}</h2>
    {generator?<><label className="ota-label">{tr('旧版完整包','Old full release')}</label><button className="ota-picker" disabled={blocked} onClick={()=>void pick('old')}><FileArchive/><span>{old || tr('选择 ZIP 文件','Select ZIP file')}</span><FolderOpen size={18}/></button><div className="ota-arrow"><ArrowRight size={18}/></div><label className="ota-label">{tr('新版完整包','New full release')}</label><button className="ota-picker" disabled={blocked} onClick={()=>void pick('new')}><FileArchive/><span>{next || tr('选择 ZIP 文件','Select ZIP file')}</span><FolderOpen size={18}/></button>
      <Button className="ota-wide" disabled={blocked || !old || !next} onClick={()=>void work(async()=>{const r=await request<Analysis>('ota_analyze',{old,new:next});setAnalysis(r);})}>{busy?<LoaderCircle className="animate-spin" size={18}/>:<GitCompareArrows size={18}/>} {tr('分析差异','Analyze changes')}</Button></>
      :<><button className="ota-picker" disabled={blocked} onClick={()=>void pick('patch')}><FileArchive/><span>{patch || tr('选择 .ota 差异包','Select .ota patch')}</span><FolderOpen size={18}/></button><label className="ota-label">{tr('设备管理地址','Device management address')}</label><input value={host} disabled={blocked} onChange={e=>{setHost(e.target.value);setReview(undefined);}}/><label className="ota-label">SSH root {tr('密码','password')}</label><input type="password" value={password} disabled={blocked} onChange={e=>{setPassword(e.target.value);setReview(undefined);}} autoComplete="off"/>
      <Button className="ota-wide" disabled={blocked || !patch || !host} onClick={()=>void work(async()=>{setReview(await request<Review>('ota_probe',{host,password,path:patch}));})}><ShieldCheck size={18}/>{tr('连接并检查','Connect and review')}</Button><Button variant="ghost" className="ota-wide" disabled={busy || !host} onClick={()=>void work(async()=>{const s=await request<Status>('ota_status',{host,password});setRunning(s.job?.status==='running');setSuccess(JSON.stringify(s));})}>{tr('重新检查设备状态','Check device status')}</Button></>}
    </section><section className="ota-card ota-result"><h2><span className="ota-number">2</span>{tr('检查并继续','Review and continue')}</h2><div className="ota-result-body" tabIndex={0} aria-label={tr('检查结果','Review results')}>
      {analysis || manifest?<><div className="ota-release"><small>{tr('目标设备','Target profile')}</small><strong>{(analysis?.manifest || manifest)!.profile}</strong><small>{(analysis?.manifest || manifest)!.kernel_release}</small></div><p className="ota-versions">{(analysis?.manifest || manifest)!.base_version}<ArrowRight size={16}/>{(analysis?.manifest || manifest)!.target_version}</p></>:<div className="ota-empty"><ShieldCheck size={42}/><p>{tr('选择文件后，在这里查看版本与兼容检查结果。','Select files to review versions and compatibility here.')}</p></div>}
      {analysis && <><div className="ota-stats"><div><strong>{analysis.files}</strong><small>{tr('文件变更','Changed files')}</small></div><div><strong>{size(analysis.payload_bytes)}</strong><small>{tr('未压缩载荷','Uncompressed payload')}</small></div><div><strong>{analysis.added} / {analysis.removed}</strong><small>{tr('新增 / 删除','Added / removed')}</small></div></div>
        {!!analysis.blockers.length && <div className="ota-error ota-blockers" tabIndex={0} role="region" aria-label={tr('无法生成的原因','Reasons export is unavailable')}>{analysis.blockers.map((b,i)=><p key={i}>{explainBlocker(b)}</p>)}</div>}<details className="ota-files"><summary>{tr('查看文件清单','View file list')}</summary>{analysis.manifest.operations.map(o=><div key={o.path}>{o.before?(o.after?'~':'−'):'+'} {o.path}</div>)}</details>
        <Button className="ota-wide" disabled={blocked || !analysis.can_export} onClick={()=>void work(async()=>{const output=await native('save_ota');if(!output)return;const r=await request<Analysis>('ota_export',{output});setSuccess(tr('已生成差异包：','Patch saved: ')+r.output);})}><Upload size={18}/>{tr('导出差异包','Export patch')}</Button></>}
      {review && <><p>{tr('所需空间 / 可用空间：','Required / available: ')}{size(review.check.required_bytes)} / {size(review.check.free_bytes)}</p>{!![...review.check.conflicts,...review.check.package_conflicts].length && <div className="ota-error">{tr('本地文件或依赖冲突，未写入：','Local file or dependency conflicts; nothing written: ')}{[...review.check.conflicts,...review.check.package_conflicts].join(', ')}</div>}<Button className="ota-wide" disabled={blocked || !review.check.applicable} onClick={()=>void work(async()=>{await request('ota_apply',{host,password,token:review.token});setRunning(true);setReview(undefined);})}><PackageCheck size={18}/>{tr('确认应用此补丁','Apply reviewed patch')}</Button></>}
    </div></section></div>
    {(busy || running) && <div className="ota-notice"><LoaderCircle className="animate-spin" size={18}/><span>{running?tr('设备正在写入，关闭电脑更新器不会终止设备事务。','Device is writing. Closing this app does not cancel its transaction.'):tr('正在处理，请稍候…','Processing…')}<small>{progress}</small></span></div>}
    {error && <div className="ota-error" role="alert"><CircleAlert size={18}/>{error}</div>}{success && <div className="ota-notice" role="status"><CheckCircle2 size={18}/><span>{success}</span></div>}
    <footer className="ota-footer"><ShieldCheck size={16}/>{tr('保留配置、额外插件和用户数据。检测到本地冲突时停止，写入前保留恢复副本。仅导入可信来源的补丁。','Settings, extra packages and user data are retained. Local conflicts stop the update. Recovery copies precede writes. Import trusted patches only.')}</footer>
    </main></div>;
}
createRoot(document.getElementById('root')!).render(<Studio/>);
