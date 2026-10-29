import React, {lazy, Suspense, useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {invoke} from '@tauri-apps/api/core';
import {AnimatePresence, domAnimation, LazyMotion, m, MotionConfig, useReducedMotion} from 'motion/react';
import {toast} from 'sonner';
import {Toaster} from './components/ui/sonner';
import {ArrowRight, BookOpen, Check, CheckCircle2, ChevronDown, CircleAlert, CircleHelp, Copy, Download, FolderOpen, HardDrive, LoaderCircle, Monitor, Moon, Pause, RefreshCw, RotateCcw, Settings2, ShieldCheck, Smartphone, Sun, Upload, Usb, Wifi, WifiOff} from 'lucide-react';
import {Button} from './components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from './components/ui/dialog';
import {SupportDialog, SupportInfo, type RepositoryId} from './components/support';
import {helpStrings} from './help-i18n';
import {strings, type Language} from './i18n';
import {gib, native, request, stateGroup, subscribe, type AdbConnection, type Device, type Package, type ResetPrompt, type Transaction, type Transfer} from './protocol';
import './style.css';

const HelpCenter = lazy(() => import('./components/help-center').then(module => ({default:module.HelpCenter})));
const GettingStarted = lazy(() => import('./components/help-center').then(module => ({default:module.GettingStarted})));

type View = 'home' | 'install' | 'update' | 'resume' | 'boot' | 'advanced' | 'plan' | 'progress';
function App() {
  const [lang, setLang] = useState<Language>(localStorage.getItem('language') === 'en' ? 'en' : 'zh');
  const [theme, setTheme] = useState<'light' | 'dark' | 'system'>(() => {
    const saved = localStorage.getItem('theme');
    return saved === 'light' || saved === 'dark' ? saved : 'system';
  });
  const t = strings[lang];
  const help = helpStrings[lang];
  const [helpOpen, setHelpOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [supportOpen, setSupportOpen] = useState(false);
  const [pendingGuide, setPendingGuide] = useState(false);
  const [helpTopic, setHelpTopic] = useState('start');
  const [view, setView] = useState<View>('home');
  const [pkg, setPkg] = useState<Package>();
  const [path, setPath] = useState('');
  const [devices, setDevices] = useState<Device[]>([]);
  const [devicesLoaded, setDevicesLoaded] = useState(false);
  const [serial, setSerial] = useState('');
  const [reconnectSerial, setReconnectSerial] = useState('');
  const [wirelessAddress, setWirelessAddress] = useState('192.168.0.1');
  const [wirelessBusy, setWirelessBusy] = useState<'connect' | 'disconnect' | null>(null);
  const [device, setDevice] = useState<Device>();
  const [records, setRecords] = useState<Transaction[]>([]);
  const [tx, setTx] = useState<Transaction>();
  const [transfer, setTransfer] = useState<Transfer>();
  const [method, setMethod] = useState('part');
  const [planOrigin, setPlanOrigin] = useState<View>('install');
  const [size, setSize] = useState('4');
  const [grow, setGrow] = useState(true);
  const [shrinkMode, setShrinkMode] = useState('prefer-online');
  const [host, setHost] = useState('192.168.9.1');
  const [password, setPassword] = useState('');
  const [accepted, setAccepted] = useState(false);
  const [onlineInput, setOnlineInput] = useState('');
  const [onlineDismissed, setOnlineDismissed] = useState(false);
  useEffect(() => {setOnlineInput('');setOnlineDismissed(false);}, [tx?.online_fallback?.nonce]);
  const [resetPrompt, setResetPrompt] = useState<ResetPrompt>();
  const [busy, setBusy] = useState(false);
  const [restartPending, setRestartPending] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [logOpen, setLogOpen] = useState(false);
  const [clock, setClock] = useState(Date.now());
  const [since, setSince] = useState(Date.now());
  const pending = useRef(0);
  const lastTransaction = useRef('');
  const verificationTransaction = useRef('');
  const revisions = useRef(new Map<string, number>());
  const pageRef = useRef<HTMLDivElement>(null);
  const stepperRef = useRef<HTMLOListElement>(null);
  const reduceMotion = useReducedMotion();
  const append = useCallback((text: string) => setLogs(old => [...old.slice(-499), `${new Date().toLocaleTimeString()}  ${text}`]), []);
  const reportError = useCallback((message: string) => {
    toast.error(t.error, {id: 'operation-error', description: message, duration: Infinity});
    append(message);
  }, [append, t.error]);
  const acceptTransaction = useCallback((next: Transaction, announce = true) => {
    // Native events can arrive before the response to start/resume. Never let
    // that older response replace a newer persisted checkpoint on the screen.
    if (next.revision !== undefined) {
      if (next.revision < (revisions.current.get(next.id) ?? -1)) return;
      revisions.current.set(next.id, next.revision);
    }
    if (next.details?.reboot_manually === false || next.details?.verification_error || ['completed','failed','cancelled'].includes(next.status)) setRestartPending(false);
    const key = `${next.id}:${next.stage}:${next.status}:${next.details?.tool || ''}:${next.details?.connection_state || ''}:${next.details?.verification_error || ''}:${next.error?.message || ''}`;
    if (key !== lastTransaction.current) {
      lastTransaction.current = key;
      if (!['completed','failed','cancelled'].includes(next.status)) setSince(Date.now());
      setTransfer(undefined);
      const label = next.choices.method === 'uninstall' && next.stage === 'plan' ? t.uninstallPlanReady : next.stage === 'openwrt_boot' && next.choices.method === 'update' ? next.details?.connection_state === 'reconnecting' ? t.updateReconnecting : t.updateReboot : t.stages[next.stage as keyof typeof t.stages] || next.stage;
      append(label);
      if (announce) {
        const options = {id: 'installation-status', description: next.error?.message ||
          (next.stage === 'openwrt_boot' && next.status.startsWith('waiting') ? (next.details?.verification_error || (next.choices.method === 'update' ? t.updateRebootDesc : next.details?.boot_confirmed ? t.bootConfirmed : next.details?.reboot_manually !== false ? t.rebootManual : t.rebootAutomatic)) : '') ||
          (next.status === 'ready' ? t.readyStatus : (t as unknown as Record<string, string>)[next.status]), duration: next.status === 'failed' ? Infinity : 5000};
        if (next.status === 'failed') toast.error(label, options);
        else if (next.status === 'completed') toast.success(next.choices.method === 'uninstall' ? t.successUninstall : next.choices.method === 'boot' ? t.successBoot : next.choices.method === 'update' ? t.successUpdate : t.success, options);
        else if (next.details?.verification_error) toast.warning(label, options);
        else if (next.status.startsWith('waiting')) toast.info(label, options);
        else toast.message(label, options);
      }
    }
    if (next.stage === 'openwrt_boot' && verificationTransaction.current !== next.id) {
      verificationTransaction.current = next.id;
      setHost(next.details?.host || next.choices.host || (next.package.adapter === 'e5' ? '192.168.9.1' : '192.168.77.1'));
    }
    setTx(next);
  }, [append, t]);
  const task = async <T,>(fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
    pending.current++; setBusy(true);
    toast.dismiss('operation-error');
    const notice = success ? toast.loading(t.running) : undefined;
    try {
      const value = await fn();
      if (notice !== undefined) {
        if (value === false) toast.dismiss(notice);
        else toast.success(success, {id:notice});
      }
      return value;
    } catch (e) {if (notice !== undefined) toast.dismiss(notice); reportError(String(e)); return undefined;}
    finally {pending.current--; setBusy(pending.current > 0);}
  };
  const rebootDevice = async () => {
    if (!tx || restartPending) return;
    setRestartPending(true);
    const accepted = await task(async () => (await request<{accepted:boolean}>('reboot_device', {transaction:tx.id})).accepted, t.rebootQueued);
    if (!accepted) setRestartPending(false);
  };
  useEffect(() => {setRestartPending(false);}, [tx?.id]);
  useEffect(() => {
    const query = matchMedia('(prefers-color-scheme: dark)');
    const update = () => document.documentElement.classList.toggle('dark', theme === 'dark' || theme === 'system' && query.matches);
    update(); query.addEventListener('change', update); localStorage.setItem('theme', theme);
    return () => query.removeEventListener('change', update);
  }, [theme]);
  useEffect(() => {localStorage.setItem('language', lang); document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';}, [lang]);
  useEffect(() => {pageRef.current?.querySelector<HTMLElement>('h1')?.focus({preventScroll: true});}, [view]);
  useEffect(() => {if (tx?.device.serial.includes(':')) setWirelessAddress(tx.device.serial);}, [tx?.id]);
  const timingActive = tx && ['running','waiting_user','waiting_device'].includes(tx.status);
  const elapsed = tx ? Math.max(0,Math.floor(((tx.stage_finished_at ? tx.stage_finished_at*1000 : timingActive ? clock : tx.updated*1000) - (tx.stage_started_at ? tx.stage_started_at*1000 : since))/1000)) : 0;
  useEffect(() => {
    if (!timingActive) return;
    setClock(Date.now());
    const timer = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [timingActive, since]);
  useEffect(() => {
    let dispose: (() => void) | undefined;
    let cancelled = false;
    subscribe(event => {
      if (event.type === 'transaction') {
        const next = event.data as Transaction;
        acceptTransaction(next);
      } else if (event.type === 'transfer') setTransfer(event.data as Transfer);
      else if (event.type === 'activity') setTransfer(undefined);
      else if (event.type === 'notice') {
        const notice = event.data as {code:string; label?:string};
        if (notice.code === 'mtp_disabled') {
          toast.info(t.mtpDisabled, {id:'usb-mtp', description:t.mtpDisabledDesc, duration:10000});
          append(t.mtpDisabled);
        } else if (notice.code === 'mtp_disable_failed') {
          toast.warning(t.mtpDisableFailed, {id:'usb-mtp', description:t.mtpDisableFailedDesc, duration:12000});
          append(t.mtpDisableFailed);
        }
      }
      else if (event.type === 'log') {
        const entry = event.data as {message:string; level?:string; scope?:string};
        append(`[${(entry.level || 'debug').toUpperCase()}]${entry.scope ? ' ['+entry.scope+']' : ''} ${entry.message}`);
      }
      else if (event.type === 'close_blocked') reportError(t.closeBlocked);
      else if (event.type === 'backend_exit') reportError(t.noBackend);
    }).then(stop => {if (cancelled) stop(); else dispose = stop;}).catch(e => {if (!cancelled) reportError(String(e));});
    return () => {cancelled = true; dispose?.();};
  }, [append, acceptTransaction, reportError, t]);
  useEffect(() => {
    task(async () => {
      const hello = await request<{package_path: string; transactions: Transaction[]}>('hello');
      setPath(hello.package_path); setRecords(hello.transactions);
      const guide = !hello.transactions.length && !localStorage.getItem('onboarding-v1');
      setPendingGuide(guide);setSupportOpen(true);
    });
  }, []);
  const verify = async (folder = path) => {
    setPkg(undefined); setDevice(undefined);
    const p = await task(() => request<Package>('package', {path: folder}), t.verified);
    if (p) {setPkg(p); setPath(p.path); setHost(p.adapter === 'e5' ? '192.168.9.1' : '192.168.77.1'); append(t.verified);}
  };
  const refresh = async () => {
    setDevice(undefined);
    setDevicesLoaded(false);
    const values = await task(() => request<Device[]>('devices'));
    if (values) {
      setDevices(values); setDevicesLoaded(true);
      if (values.length === 1) {setSerial(values[0].serial); if (values[0].serial !== serial) setDevice(undefined);}
      else if (!values.some(d => d.serial === serial)) {setSerial(''); setDevice(undefined);}
      if (!values.length) toast.info(t.noDevices, {id:'device-selection'});
      else if (values.length > 1) toast.info(t.multipleDevices, {id:'device-selection'});
      else toast.success(t.refreshed, {id:'device-selection'});
    } else {setDevices([]); setSerial('');}
  };
  const connectWireless = async () => {
    setWirelessBusy('connect'); setDevice(undefined);
    try {
      const result = await task(() => request<AdbConnection>('adb_connect', {address:wirelessAddress}));
      if (!result) return;
      setDevices(result.devices); setDevicesLoaded(true); setWirelessAddress(result.endpoint);
      setSerial(old => result.devices.length === 1 ? result.devices[0].serial : result.devices.some(d => d.serial === old) ? old : '');
      if (result.state === 'unauthorized') toast.info(t.wirelessAuthorize, {id:'wireless-adb'});
      else if (result.state === 'offline') toast.info(t.wirelessOffline, {id:'wireless-adb'});
      else toast.success(t.wirelessConnected, {id:'wireless-adb'});
      if (result.devices.length > 1) toast.info(t.multipleDevices, {id:'device-selection'});
    } finally {setWirelessBusy(null);}
  };
  const disconnectWireless = async () => {
    const selected = serial;
    setWirelessBusy('disconnect'); setDevice(undefined);
    try {
      const result = await task(() => request<AdbConnection>('adb_disconnect', {serial:selected}), t.wirelessDisconnected);
      if (result) {setDevices(result.devices); setDevicesLoaded(true); setSerial(old => old === selected ? '' : old);}
    } finally {setWirelessBusy(null);}
  };
  const inspect = async () => {
    setDevice(undefined);
    const value = await task(() => request<Device>('probe', {serial, method: view === 'boot' ? 'boot' : method}), t.inspected);
    if (value) setDevice(value);
  };
  const navigate = async (next: View) => {
    setView(next); setAccepted(false); setTransfer(undefined); setDevice(undefined);
    if (next === 'resume') {const values = await task(() => request<Transaction[]>('list')); if (values) setRecords(values);}
    if (['install','advanced','boot'].includes(next)) {setMethod(next === 'boot' ? 'boot' : next === 'advanced' ? 'reinstall' : 'part'); await refresh();}
    if (next === 'update') setMethod('update');
  };
  const plan = async (resetToken?: string) => {
    const value = await task(() => request<Transaction | ResetPrompt>('plan', {serial, choices: {method, size_gib: Number(size), grow, host, shrink_strategy:shrinkMode, wipe:method === 'part' && shrinkMode === 'format'}, password, reset_token:resetToken}));
    if (value && 'confirmation_required' in value) {
      setResetPrompt(value); reportError(value.reason); return;
    }
    if (value) {setResetPrompt(undefined); setPlanOrigin(view); acceptTransaction(value); setView('plan'); setAccepted(false);}
  };
  const start = async (command: string, record = tx) => {
    if (!record) return;
    const value = await task(() => request<Transaction>(command, {transaction: record.id, confirmed: command === 'start', password, serial: command !== 'start' && record.choices.method === 'uninstall' ? reconnectSerial || undefined : undefined, path: pkg?.path || path}));
    if (value) {acceptTransaction(value); setView('progress'); setLogOpen(true); setSince(Date.now()); setTransfer(undefined);}
  };
  const continueRecord = async (record: Transaction) => {
    if (record.online_fallback?.state === 'awaiting') {acceptTransaction(record,false);setView('progress');setOnlineDismissed(false);return;}
    if (record.status === 'ready') {
      acceptTransaction(record, false); setPlanOrigin('resume'); setAccepted(false); setView('plan');
    } else await start('resume', record);
  };
  const exportBackup = async () => {
    if (!tx) return;
    await task(async () => {const target = await native('save_backup');
      if (!target) return false;
      return request('export', {transaction: tx.id, path: target});
    }, t.exportedBackup);
  };
  const diagnosticText = async () => tx ? (await request<{text:string}>('diagnostics', {transaction:tx.id})).text : logs.join('\n');
  const copyDiagnostics = () => task(async () => navigator.clipboard.writeText(await diagnosticText()), t.copied);
  const exportLogs = async () => {
    await task(async () => {const target = await native('save_log');
      if (!target) return false;
      return invoke('write_log', {path: target, text: await diagnosticText()});
    }, t.exportedLog);
  };
  const choosePackage = async () => {
    const folder = await task(() => native('pick_package'));
    if (folder) {setPath(folder); await verify(folder);}
  };
  const openData = () => task(() => native('open_data'), t.openedData);
  const statusLabel = (status: string) => status === 'ready' ? t.readyStatus : (t as unknown as Record<string, string>)[status] || status;
  const stageLabel = (stage: string) => t.stages[stage as keyof typeof t.stages] || stage;
  const current = stateGroup(['plan','progress'].includes(view) ? tx?.stage || 'plan' : 'plan');
  useEffect(() => {
    const list = stepperRef.current;
    if (!list) return;
    const revealCurrent = () => {
      const item = list.querySelector<HTMLElement>('.current');
      if (!item) return;
      const a = list.getBoundingClientRect(), b = item.getBoundingClientRect();
      if (b.bottom > a.bottom) list.scrollTop += b.bottom-a.bottom;
      else if (b.top < a.top) list.scrollTop -= a.top-b.top;
    };
    revealCurrent();
    const observer = new ResizeObserver(revealCurrent);observer.observe(list);
    return () => observer.disconnect();
  }, [current, lang]);
  const waitingOnline = tx?.online_fallback?.state === 'awaiting';
  const active = tx && ['running', 'waiting_device', 'waiting_user'].includes(tx.status);
  const openHelp = useCallback(() => {
    setHelpTopic(view === 'advanced' && method === 'uninstall' || ['plan','progress'].includes(view) && tx?.choices.method === 'uninstall' ? 'uninstall' : view === 'resume' || view === 'progress' && (['failed','completed'].includes(tx?.status || '') || tx?.stage === 'openwrt_boot') ? 'recovery' : view === 'progress' && ['android_boot','android_setup'].includes(tx?.stage || '') ? 'reset' : view === 'progress' && tx?.status.startsWith('waiting') ? 'faq' : view === 'plan' ? 'storage' : view === 'home' ? 'start' : 'purpose');
    setGuideOpen(false); setHelpOpen(true);
  }, [view, method, tx?.choices.method, tx?.status, tx?.stage]);
  const showGuide = () => {setHelpOpen(false); setGuideOpen(true);};
  const changeGuide = (open:boolean) => {
    setGuideOpen(open);
    if (!open) localStorage.setItem('onboarding-v1','seen');
  };
  const changeSupport = (open:boolean) => {
    setSupportOpen(open);
    if (!open) {
      if (pendingGuide) {setPendingGuide(false);setGuideOpen(true);}
    }
  };
  const showSupport = () => {setHelpOpen(false);setGuideOpen(false);setPendingGuide(false);setSupportOpen(true);};
  const openRepository = (repository:RepositoryId) => task(() => invoke('open_repository',{repository}),t.repositoryOpened);
  useEffect(() => {
    const onKey = (event:KeyboardEvent) => {
      if (event.key === 'F1') {event.preventDefault(); if (!guideOpen && !helpOpen && !supportOpen) openHelp();}
    };
    window.addEventListener('keydown',onKey);
    return () => window.removeEventListener('keydown',onKey);
  }, [openHelp, guideOpen, helpOpen, supportOpen]);
  const purposes = [
    {id:'install', icon:Download, title:t.install, description:t.installDesc},
    {id:'update', icon:RefreshCw, title:t.update, description:t.updateDesc},
    {id:'resume', icon:RotateCcw, title:t.resume, description:t.resumeDesc},
    {id:'boot', icon:Usb, title:t.boot, description:t.bootDesc},
    {id:'advanced', icon:Settings2, title:t.advanced, description:t.advancedDesc},
  ] as const;
  const impact = tx?.choices.method === 'uninstall' ? (tx.plan.wipe ? t.uninstallWipe : tx.plan.data_action?.startsWith('grow-') ? t.uninstallPreserve : t.uninstallKeep) : tx?.plan.wipe ? t.wipe : tx?.choices.method === 'tf' ? t.tfErase : tx?.choices.method === 'reinstall' ? t.reinstallErase : tx?.choices.method === 'image' ? t.imageImpact : tx?.choices.method === 'update' ? t.updateImpact : tx?.choices.method === 'boot' ? t.bootImpact : t.preserve;
  const impactNotice = tx && <div className={'alert ' + (tx.plan.wipe || tx.plan.experimental || tx.choices.method === 'uninstall' ? 'danger' : '')}><CircleAlert size={20}/><div><strong>{t.impact}</strong><p>{impact}</p>{tx.plan.experimental && <p>{t.experimental}</p>}</div></div>;
  const waitingForOpenwrt = tx?.stage === 'openwrt_boot' && tx.status.startsWith('waiting') && !tx.unsafe;
  const manualRestart = waitingForOpenwrt && tx?.details?.reboot_manually !== false;
  const waitingForAndroid = tx?.status.startsWith('waiting') && !tx.unsafe && ['uninstall_offline','android_boot','android_setup','usb_debugging','debug_authorization','magisk','root_permission'].includes(tx.stage);
  const wirelessControls = (reconnectOnly = false) => <details className="wireless-adb" open={reconnectOnly ? true : undefined}>
    <summary><Wifi size={15}/>{t.wirelessTitle}</summary>
    <div className="wireless-controls"><label>{t.wirelessAddress}<input disabled={busy} value={wirelessAddress} placeholder="192.168.0.1:5555" onChange={e => setWirelessAddress(e.target.value)} onKeyDown={e => {if (e.key === 'Enter' && !busy && wirelessAddress.trim()) {e.preventDefault();void connectWireless();}}} autoComplete="off" spellCheck={false}/></label>
      <Button variant="outline" disabled={busy || !wirelessAddress.trim()} onClick={connectWireless}>{wirelessBusy === 'connect' ? <LoaderCircle size={15} className="spin"/> : <Wifi size={15}/>} {t.connectWireless}</Button>
      {!reconnectOnly && <Button variant="ghost" disabled={busy || !!active || !devices.some(d => d.serial === serial && d.wireless)} onClick={disconnectWireless}>{wirelessBusy === 'disconnect' ? <LoaderCircle size={15} className="spin"/> : <WifiOff size={15}/>} {t.disconnectWireless}</Button>}
    </div><p className="device-hint">{t.wirelessHelp}</p>
    {!reconnectOnly && devices.some(d => d.serial === serial && d.wireless) && <p className="device-hint wireless-target">{t.wirelessTarget}: <code>{serial}</code></p>}
  </details>;
  return <div className="app-shell">
    <header><div className="brand"><div className="brand-icon"><HardDrive size={20}/></div><div><strong>{t.title}</strong><small>{t.subtitle}</small></div></div>
      <div className="preferences"><label className="sr-only" htmlFor="language">{t.language}</label><select id="language" value={lang} onChange={e => setLang(e.target.value as Language)}><option value="zh">中文</option><option value="en">English</option></select>
        {theme === 'dark' ? <Moon size={16}/> : theme === 'light' ? <Sun size={16}/> : <Monitor size={16}/>}
        <label className="sr-only" htmlFor="theme">{t.theme}</label><select id="theme" value={theme} onChange={e => setTheme(e.target.value as typeof theme)}><option value="system">{t.system}</option><option value="light">{t.light}</option><option value="dark">{t.dark}</option></select><Button variant="ghost" className="help-trigger" onClick={openHelp} aria-label={help.help} title={`${help.help} (F1)`}><CircleHelp size={18}/><span>{help.help}</span></Button></div>
    </header>
    <div className="workspace"><aside><button className="nav-home" disabled={busy || !!active} onClick={() => navigate('home')}><HardDrive size={17}/>{t.home}</button>
      <ol className="stepper" ref={stepperRef}>{(tx?.choices.method === 'uninstall' && ['plan','progress'].includes(view) || view === 'advanced' && method === 'uninstall' ? t.uninstallSteps : t.steps).map((name, i) => <li key={name} className={current === i ? 'current' : current > i ? 'done' : ''}><span>{current > i ? <Check size={14}/> : i+1}</span><div>{name}</div></li>)}</ol>
      <div className="sidebar-bottom"><SupportInfo language={lang} onCopy={number => task(() => navigator.clipboard.writeText(number), `${t.groupCopied}: ${number}`)} onRepository={openRepository} onSupport={showSupport}/><div className="sidebar-note"><ShieldCheck size={19}/><p>{t.packageHint}</p></div>
      <Button variant="ghost" className="data-folder" disabled={busy} onClick={openData}><FolderOpen size={16}/>{t.openData}</Button></div>
    </aside><main aria-busy={busy} className={waitingForOpenwrt ? 'boot-wait' : undefined}>
      <div className="busy-track" aria-hidden="true">{busy && <m.div animate={{opacity:reduceMotion ? 1 : [.4,1,.4]}} transition={{duration:1.3,repeat:reduceMotion ? 0 : Infinity}}/>}</div>
      <AnimatePresence initial={false} mode="wait" onExitComplete={() => requestAnimationFrame(() => pageRef.current?.querySelector<HTMLElement>('h1')?.focus({preventScroll:true}))}>
      <m.div key={view} ref={pageRef} className="page" initial={{opacity:0, y:reduceMotion ? 0 : 8}} animate={{opacity:1,y:0}} exit={{opacity:0}} transition={{duration:reduceMotion ? 0 : 0.16, ease:'easeOut'}}>
      {view === 'home' && <><div className="eyebrow">OPENWRT · DESKTOP INSTALLER</div><h1 tabIndex={-1}>{lang === 'zh' ? '你想如何使用 OpenWrt？' : 'What would you like to do?'}</h1><p className="intro">{t.connect}</p>
        <div className="guide-callout"><BookOpen size={20}/><div><strong>{help.firstTime}</strong><p>{help.firstTimeDesc}</p></div><Button variant="outline" onClick={showGuide}>{help.guide}<ArrowRight size={15}/></Button></div>
        <div className="purpose-list scroll-area">{purposes.map(({id, icon:Icon, title, description}, index) => <m.button initial={{opacity:0,y:reduceMotion ? 0 : 6}} animate={{opacity:1,y:0}} transition={{delay:reduceMotion ? 0 : index*0.025,duration:0.18}} whileHover={reduceMotion ? undefined : {x:3}} whileTap={reduceMotion ? undefined : {scale:0.992}} key={id} disabled={busy} onClick={() => navigate(id)} className="purpose"><div className="purpose-icon"><Icon size={21}/></div><div><h2>{title}</h2><p>{description}</p></div><ArrowRight size={18}/></m.button>)}</div>
      </>}
      {['install','update','boot','advanced'].includes(view) && <><h1 tabIndex={-1}>{purposes.find(p => p.id === view)?.title}</h1><p className="intro">{purposes.find(p => p.id === view)?.description}</p>
        {view === 'update' && <div className="alert" role="note"><CircleAlert size={20}/><div><strong>{t.updateCompatibilityTitle}</strong><p>{t.updateCompatibilityWarning}</p></div></div>}
        <div className="page-scroll scroll-area"><section><div className="section-title"><h2>{t.package}</h2>{pkg && <span className="badge"><Check size={13}/>{t.verified}</span>}</div>
          <div className="package-row"><div className="file-path" title={path}>{pkg?.release_version || pkg?.name || path || t.choosePackage}</div><Button variant="outline" disabled={busy} onClick={choosePackage}><FolderOpen size={16}/>{t.choosePackage}</Button><Button disabled={busy || !path} onClick={() => verify()}>{t.verify}</Button></div></section>
        {view === 'update' ? <section><h2>{t.device}</h2><div className="form-grid"><label>{t.host}<input disabled={busy} value={host} onChange={e => setHost(e.target.value)} autoComplete="off"/></label><label>{t.password}<input disabled={busy} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off"/></label></div><p className="device-hint">{t.existingLogin}</p><Button variant="outline" disabled={busy || !pkg} onClick={async () => {setDevice(undefined); const d = await task(() => request<Device>('probe_update', {host, password}), t.inspected); if (d) setDevice(d);}}>{t.checkUpdate}</Button></section> : <section><div className="section-title"><h2>{t.device}</h2><Button variant="ghost" disabled={busy} onClick={refresh}><RefreshCw size={15}/>{t.refresh}</Button></div><div className="device-select"><Smartphone size={18}/><select disabled={busy} aria-label={t.select} value={serial} onChange={e => {setSerial(e.target.value); setDevice(undefined);}}><option value="">{devicesLoaded && !devices.length ? t.noDevicesShort : t.select}</option>{devices.map(d => <option key={d.serial} value={d.serial}>{d.label} · {d.wireless ? d.serial : '••••'+d.serial.slice(-4)} · {t.deviceStates[d.state as keyof typeof t.deviceStates] || d.state}</option>)}</select><Button variant="outline" disabled={!serial || !pkg || busy} onClick={inspect}>{t.inspect}</Button></div>{devicesLoaded && !devices.length && <p className="device-hint" role="status">{t.noDevices}</p>}{!pkg && <p className="device-hint">{t.inspectNeedsPackage}</p>}{wirelessControls()}</section>}
        {view === 'boot' && <section><h2>{t.visit}</h2><div className="form-grid"><label>{t.host}<input disabled={busy} value={host} onChange={e => setHost(e.target.value)} autoComplete="off"/></label><label>{t.password}<input disabled={busy} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off"/></label></div><p className="device-hint">{t.existingLogin}</p></section>}
        {device && <div className="device-card"><div><small>{t.model}</small><strong>{device.model || t.unknown}</strong></div><div><small>{t.os}</small><strong>{device.system || t.unknown}</strong></div><div><small>{t.slot}</small><strong>{device.slot || t.unknown}</strong></div><div><small>{t.root}</small><strong className={device.root ? 'good-text' : ''}>{device.root ? t.ready : t.unavailable}</strong></div></div>}
        {device?.readiness && device.readiness !== 'ready' && <div className="alert"><CircleAlert size={19}/><div><strong>{stageLabel(device.readiness)}</strong><p>{t.waitingAndroid}</p></div></div>}
        {view !== 'update' && view !== 'boot' && <section><h2>{view === 'advanced' ? t.advanced : t.install}</h2><div className="method-options" role="group" aria-label={view === 'advanced' ? t.advanced : t.install}>{(view === 'advanced' ? ['reinstall','image','uninstall'] : ['tf','part','reinstall']).map(m => <button disabled={busy} key={m} aria-pressed={method === m} className={method === m ? 'selected' : ''} onClick={() => setMethod(m)}>{m === 'tf' ? <Usb size={17}/> : <HardDrive size={17}/>}{(t as unknown as Record<string,string>)[m]}{(m === 'tf' && device?.tf || m === 'reinstall' && device?.existing) && <span>{t.recommended}</span>}</button>)}</div>
          {method === 'uninstall' && <div className="alert danger"><CircleAlert size={18}/><p>{t.uninstallHelp}</p></div>}
          {method === 'image' && <div className="alert danger"><CircleAlert size={18}/><strong>{t.experimental}</strong></div>}
          {method === 'part' && <label className="size-field">{t.shrinkMethod}<select disabled={busy} value={shrinkMode} onChange={e => setShrinkMode(e.target.value)}><option value="prefer-online">{t.shrinkPrefer}</option><option value="format">{t.shrinkFormat}</option></select></label>}
          {['part','image'].includes(method) && <label className="size-field">{t.size}<input disabled={busy} type="number" min="2" step="0.25" value={size} onChange={e => setSize(e.target.value)}/></label>}
          {['part','reinstall'].includes(method) && <label className="checkbox-label"><input disabled={busy} type="checkbox" checked={grow} onChange={e => setGrow(e.target.checked)}/><div>{t.grow}<small>{t.growHelp}</small></div></label>}
        </section>}
        </div><div className="actions"><Button disabled={busy} variant="ghost" onClick={() => navigate('home')}>{t.back}</Button><Button disabled={busy || !pkg || !device?.root} onClick={() => plan()}>{method === 'uninstall' ? t.planUninstall : t.plan}<ArrowRight size={16}/></Button></div>
      </>}
      {view === 'plan' && tx && <><div className="eyebrow">{tx.choices.method === 'uninstall' ? t.reviewUninstall : t.review}</div><h1 tabIndex={-1}>{tx.device.model}</h1><p className="intro">{tx.package.name} · ••••{tx.device.serial.slice(-4)}</p>{tx.choices.method === 'uninstall' && impactNotice}<div className="page-scroll scroll-area"><section>{tx.choices.method === 'uninstall' && tx.plan.layout_source === 'device' && <p className="device-hint">{t.uninstallDeviceBounds}</p>}<dl className="plan-list"><div><dt>{tx.choices.method === 'uninstall' ? t.operationMethod : t.installMethod}</dt><dd>{tx.choices.method === 'boot' ? t.boot : tx.choices.method === 'update' ? t.update : (t as unknown as Record<string,string>)[tx.choices.method]}</dd></div><div><dt>{tx.choices.method === 'uninstall' ? t.uninstallTarget : t.target}</dt><dd>{tx.plan.target}</dd></div><div><dt>{tx.choices.method === 'uninstall' ? t.androidAllocation : t.android}</dt><dd>{tx.choices.method === 'uninstall' && <>{gib(tx.plan.previous_android_bytes)} → </>}{gib(tx.plan.android_bytes)}</dd></div><div><dt>{tx.choices.method === 'uninstall' ? t.release : t.allocation}</dt><dd>{tx.choices.method === 'boot' ? '—' : gib(tx.plan.openwrt_bytes)}</dd></div>{tx.choices.method === 'uninstall' ? <><div><dt>{t.reclaim}</dt><dd>{gib(tx.plan.reclaimed_bytes)}</dd></div>{!!tx.plan.released_unallocated_bytes && <div><dt>{t.unallocatedRelease}</dt><dd>{gib(tx.plan.released_unallocated_bytes)}</dd></div>}</> : <div><dt>{t.grow}</dt><dd>{tx.plan.grow ? <Check size={17}/> : '—'}</dd></div>}</dl></section>
        {tx.choices.method !== 'uninstall' && impactNotice}
        {tx.plan.shrink_strategy === 'online-f2fs' && <div className="alert"><ShieldCheck size={20}/><div><strong>{t.onlineTitle}</strong><p>{t.onlineDesc}</p></div></div>}
        {tx.plan.online_unavailable && <div className="alert danger"><CircleAlert size={20}/><p>{t.onlineUnavailable} {tx.plan.online_unavailable}</p></div>}
        {tx.plan.shrink_strategy !== 'online-f2fs' && (tx.plan.offline || tx.plan.two_stage) && <div className="alert"><RefreshCw size={20}/><p>{tx.choices.method === 'uninstall' ? t.uninstallFirstBoot : t.firstBoot}</p></div>}
        </div><label className="checkbox-label confirmation"><input disabled={busy} type="checkbox" checked={accepted} onChange={e => setAccepted(e.target.checked)}/>{tx.choices.method === 'uninstall' ? t.uninstallAccept : t.accept}</label>
        <div className="actions"><Button disabled={busy} variant="outline" onClick={() => {setView(planOrigin); setAccepted(false);}}>{t.back}</Button><Button variant={tx.plan.wipe || tx.choices.method === 'uninstall' ? 'destructive' : 'default'} disabled={!accepted || busy} onClick={() => start('start')}>{tx.choices.method === 'uninstall' ? t.startUninstall : t.start}<ArrowRight size={16}/></Button></div>
      </>}
      {view === 'resume' && <><h1 tabIndex={-1}>{t.resume}</h1><p className="intro">{t.resumeDesc}</p><div className="resume-tools"><Button variant="outline" disabled={busy} onClick={async () => {const folder = await task(() => native('pick_recovery')); if (folder) {const value = await task(() => request<Transaction>('import', {path: folder}), t.imported); if (value) setRecords(old => [value, ...old.filter(r => r.id !== value.id)]);}}}><Upload size={16}/>{t.import}</Button><Button variant="outline" disabled={busy} onClick={choosePackage}><FolderOpen size={16}/>{t.choosePackage}</Button></div>
        {records.some(record => record.choices.method === 'uninstall') && <details className="wireless-adb"><summary>{t.reconnectDevice}</summary><p className="device-hint">{t.reconnectHelp}</p><Button variant="outline" disabled={busy} onClick={refresh}><RefreshCw size={15}/>{t.refresh}</Button><label>{t.reconnectDevice}<select disabled={busy} value={reconnectSerial} onChange={event => setReconnectSerial(event.target.value)}><option value="">{t.keepRecordedDevice}</option>{devices.map(entry => <option key={entry.serial} value={entry.serial}>{entry.model || entry.label || t.device} · {entry.serial} · {entry.state}</option>)}</select></label>{wirelessControls()}</details>}
        <label className="resume-password"><span>{t.password}</span><input disabled={busy} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off"/></label>
        <div className="records-list scroll-area">{!records.length && <p className="empty-state">{t.noRecords}</p>}{records.map(r => <section className="record" key={r.id}><div><h2>{r.device.model}</h2><p>{r.choices.method === 'boot' ? t.boot : r.choices.method === 'update' ? t.update : (t as unknown as Record<string,string>)[r.choices.method]} · ••••{r.device.serial.slice(-4)}</p><p>{stageLabel(r.stage)} · {statusLabel(r.status)}</p><small>{new Date(r.updated*1000).toLocaleString()} · {r.id.slice(0,8)}</small></div><Button disabled={busy} onClick={() => continueRecord(r)}>{r.status === 'ready' ? r.choices.method === 'uninstall' ? t.reviewUninstall : t.review : r.choices.method === 'uninstall' ? t.continueUninstall : t.continue}<ArrowRight size={16}/></Button></section>)}</div>
      </>}
      {view === 'progress' && tx && <><div className="eyebrow">{t.progress} · {tx.device.model}</div><h1 tabIndex={-1}>{tx.status === 'completed' ? tx.choices.method === 'uninstall' ? t.successUninstall : tx.choices.method === 'boot' ? t.successBoot : tx.choices.method === 'update' ? t.successUpdate : t.success : manualRestart ? t.rebootNow : waitingForOpenwrt && tx.choices.method === 'update' ? tx.details?.connection_state === 'reconnecting' ? t.updateReconnecting : t.updateReboot : stageLabel(tx.stage)}</h1><div className="status-line"><span className={'status-dot '+tx.status}/><span>{statusLabel(tx.status)}</span><span className="elapsed">{t.elapsed} {elapsed}s</span></div>
        {waitingForOpenwrt && <div className="boot-guidance"><Smartphone size={21}/><p>{tx.choices.method === 'update' ? t.updateRebootDesc : tx.details?.boot_confirmed ? t.bootConfirmed : manualRestart ? t.rebootManual : t.rebootAutomatic}</p></div>}
        <div className="page-scroll scroll-area">{tx.status === 'completed' ? <section className="success-panel"><m.div className="success-icon" initial={{opacity:0,scale:reduceMotion ? 1 : .9}} animate={{opacity:1,scale:1}} transition={{duration:reduceMotion ? 0 : .24}}><CheckCircle2 size={42} className="good-text"/></m.div><dl className="plan-list"><div><dt>{tx.choices.method === 'uninstall' ? t.androidActual : t.actual}</dt><dd>{gib(tx.result?.filesystem_bytes)}</dd></div>{tx.choices.method !== 'uninstall' && <div><dt>{t.visit}</dt><dd>{tx.result?.url}</dd></div>}</dl><h2>{t.next}</h2><p>{tx.choices.method === 'uninstall' ? t.uninstallNext : tx.choices.method === 'update' ? t.updateNext : t.nextDesc}</p>{tx.choices.method !== 'uninstall' && <p className="device-hint login-hint">{['update','boot'].includes(tx.choices.method) ? t.existingLogin : t.defaultLogin}</p>}</section> : <>
          {waitingForOpenwrt ? <div className="network-guidance"><p>{t.waitingOpenwrt}</p><Button variant="ghost" className="context-help" onClick={openHelp}><CircleHelp size={16}/>{help.currentHelp}</Button></div> : <section className="activity"><m.div key={tx.stage+tx.status} className="activity-heading" initial={{opacity:reduceMotion ? 1 : 0,y:reduceMotion ? 0 : 4}} animate={{opacity:1,y:0}} transition={{duration:reduceMotion ? 0 : .18}}>{tx.status === 'failed' ? <CircleAlert size={28}/> : tx.status === 'running' ? <LoaderCircle size={28} className="spin"/> : tx.status.startsWith('waiting') ? <Smartphone size={28}/> : <Pause size={28}/>}<div><h2>{stageLabel(tx.stage)}</h2><p>{tx.package.name}{tx.details?.tool ? " · "+tx.details.tool : ""}</p></div></m.div>
            <div className="stage-progress"><small>{t.phase} {Math.min(5,stateGroup(tx.stage)+1)} / 5 · {statusLabel(tx.status)}</small>{(!transfer || tx.status !== 'running') && <><div className={'indeterminate-progress '+(tx.status === 'running' ? 'is-running' : '')} role="progressbar" aria-label={t.stageProgress} aria-valuetext={`${stageLabel(tx.stage)} · ${statusLabel(tx.status)}`}><div/></div><small>{tx.status === 'running' ? t.noPercentage : statusLabel(tx.status)}</small></>}</div>
            {transfer && tx.status === 'running' && <div className="transfer"><div><span>{transfer.name}</span><span>{Math.floor(transfer.bytes/transfer.total*100)}%</span></div><progress value={transfer.bytes} max={transfer.total}/><small>{t.bytes}: {transfer.bytes.toLocaleString()} / {transfer.total.toLocaleString()} B · {t.speed}: {(transfer.bytes_per_second/1048576).toFixed(1)} MiB/s</small></div>}
            {tx.status.startsWith('waiting') && <><p className="waiting-guidance">{tx.choices.method === 'uninstall' && tx.stage === 'uninstall_offline' ? t.uninstallWaiting : tx.stage === 'openwrt_boot' ? t.waitingOpenwrt : t.waitingAndroid}</p><Button variant="ghost" className="context-help" onClick={openHelp}><CircleHelp size={16}/>{help.currentHelp}</Button></>}
            {waitingForAndroid && wirelessControls(true)}
            {tx.choices.method === 'uninstall' && (tx.uninstall_armed || tx.unsafe) && <div className="alert danger"><CircleAlert size={19}/><p>{tx.uninstall_armed ? t.uninstallArmedWarning : t.uninstallUncertain}</p></div>}
            {tx.unsafe && <div className="alert danger"><ShieldCheck size={19}/><p>{t.unsafe}</p></div>}
            {tx.error && <div className="alert danger"><CircleAlert size={20}/><div><p className="error-message">{tx.error.message}</p><Button className="copy-diagnostics" variant="outline" disabled={busy} onClick={copyDiagnostics}><Copy size={15}/>{t.copyDiagnostics}</Button></div></div>}
          </section>}</>} </div>
        {waitingForOpenwrt && <div className="verification-controls"><div className="form-grid"><label>{t.host}<input disabled={busy} value={host} onChange={e => setHost(e.target.value)} autoComplete="off"/></label><label>{t.password}<input disabled={busy} type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off"/></label></div>{tx.choices.method !== 'uninstall' && <p className="device-hint login-hint">{['update','boot'].includes(tx.choices.method) ? t.existingLogin : t.defaultLogin}</p>}{tx.details?.verification_error && <p className="verification-error" role="status">{tx.details.verification_error}</p>}</div>}
        {waitingOnline && <Button variant="outline" onClick={() => {setOnlineDismissed(false);setOnlineInput('');}}>{t.onlineFallbackTitle}</Button>}
        {tx.status !== 'completed' && !waitingOnline && <div className="actions">{manualRestart && <Button disabled={busy || restartPending} onClick={rebootDevice}>{restartPending ? <LoaderCircle size={16} className="spin"/> : <RefreshCw size={16}/>} {restartPending ? t.rebootSending : t.rebootDevice}</Button>}{waitingForOpenwrt && <Button variant={manualRestart ? "outline" : "default"} disabled={busy || restartPending || !host.trim()} onClick={() => task(async () => (await request<{accepted:boolean}>('verify_openwrt', {transaction:tx.id, host:host.trim(), password})).accepted, t.verificationRequested)}><CheckCircle2 size={16}/>{t.confirmOpenwrt}</Button>}{active ? <Button variant="outline" disabled={busy} onClick={() => task(async () => (await request<{accepted:boolean}>('cancel')).accepted, t.pauseRequested)}><Pause size={16}/>{t.pause}</Button> : <Button disabled={busy || tx.error?.retryable === false} onClick={() => start('retry')}>{t.retry}<RotateCcw size={16}/></Button>}</div>
        }
        <div className="backup-actions"><Button disabled={busy} variant="outline" onClick={exportBackup}><Download size={16}/>{t.exportBackup}</Button><Button disabled={busy} variant="ghost" onClick={openData}><FolderOpen size={16}/>{t.openData}</Button></div>
      </>}
      </m.div></AnimatePresence>
      <div className="log-panel"><button className="log-toggle" aria-expanded={logOpen} aria-controls="installation-log" onClick={() => setLogOpen(!logOpen)}><ChevronDown size={14}/>{t.logs} · DEBUG<span>{logs.length}</span></button>
        <AnimatePresence initial={false}>{logOpen && <m.div id="installation-log" className="log-content" initial={{height:0,opacity:0}} animate={{height:'auto',opacity:1}} exit={{height:0,opacity:0}} transition={{duration:reduceMotion ? 0 : .18}}><div className="log-inner"><div className="log-actions"><small>{t.newestFirst}</small><Button variant="ghost" disabled={busy || !logs.length && !tx} onClick={copyDiagnostics}><Copy size={14}/>{t.copy}</Button><Button variant="ghost" disabled={busy || !logs.length && !tx} onClick={exportLogs}><Download size={14}/>{t.exportLog}</Button></div><pre tabIndex={0} aria-label={t.logs}>{[...logs].reverse().join('\n') || '—'}</pre></div></m.div>}</AnimatePresence>
      </div>
    </main></div><footer><span><ShieldCheck size={13}/>OpenWrt Installer · v1.0</span><span>USB / SSH · {pkg?.profile || '—'}</span></footer>
    <Suspense fallback={<div className="help-loading" role="status"><LoaderCircle size={17} className="spin"/>{t.running}</div>}>{helpOpen && <HelpCenter language={lang} open={helpOpen} onOpenChange={setHelpOpen} initialTopic={helpTopic} onGuide={showGuide}/>}
    {guideOpen && <GettingStarted language={lang} open={guideOpen} onOpenChange={changeGuide}/>}</Suspense>
    {waitingOnline && !onlineDismissed && <Dialog open onOpenChange={open => {if (!open) {setOnlineDismissed(true);setOnlineInput('');}}}>
      <DialogContent closeLabel={t.onlineCancel}>
        <div className="dialog-heading"><DialogTitle>{t.onlineFallbackTitle}</DialogTitle><DialogDescription>{t.onlineFallbackDesc}</DialogDescription></div>
        <div className="scroll-area"><p className="error-message">{tx?.online_fallback?.reason}</p><label>yes<input value={onlineInput} onChange={e => setOnlineInput(e.target.value)} autoComplete="off" spellCheck={false} placeholder="yes"/></label></div>
        <div className="dialog-footer"><Button variant="outline" onClick={() => {setOnlineDismissed(true);setOnlineInput('');}}>{t.onlineCancel}</Button><Button variant="destructive" disabled={busy || onlineInput !== 'yes'} onClick={() => void task(async () => {
          const next = await request<Transaction>('online_fallback',{transaction:tx!.id,nonce:tx!.online_fallback!.nonce,confirmation:onlineInput});
          acceptTransaction(next);setAccepted(false);setView('plan');setOnlineInput('');
        })}>{t.onlineFallbackAction}</Button></div>
      </DialogContent>
    </Dialog>}
    {resetPrompt && <Dialog open onOpenChange={open => {if (!open && !busy) setResetPrompt(undefined);}}>
      <DialogContent closeLabel={help.close} className="removal-reset-dialog">
        <div className="dialog-heading"><DialogTitle>{t.uninstallResetQuestion}</DialogTitle><DialogDescription>{resetPrompt.model} · ••••{resetPrompt.serial_hint}</DialogDescription></div>
        <div className="reset-choice-body scroll-area"><div className="alert danger"><CircleAlert size={20}/><div><p className="error-message">{resetPrompt.reason}</p><p>{t.uninstallResetExplanation}</p></div></div></div>
        <div className="dialog-footer"><Button disabled={busy} variant="outline" onClick={() => setResetPrompt(undefined)}>{t.uninstallCancel}</Button><Button disabled={busy} variant="destructive" onClick={() => plan(resetPrompt.reset_token)}>{t.uninstallChooseReset}</Button></div>
      </DialogContent>
    </Dialog>}
    {supportOpen && <SupportDialog language={lang} onOpenChange={changeSupport} onRepository={openRepository}/>}
    <Toaster theme={theme} containerAriaLabel={t.notifications} toastOptions={{closeButtonAriaLabel:t.dismiss, classNames:{toast:"app-toast",description:"toast-description"}}}/>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><MotionConfig reducedMotion="user"><LazyMotion features={domAnimation} strict><App/></LazyMotion></MotionConfig></React.StrictMode>);
