"""PTY integration tests for the production state machine (CELL_TEST timings/paths).
No device is used; modem commands are answered by an emulator with URC/error cases.
Run under Linux: python3 tests/test_cellular_native.py /path/to/test-celld
"""
import unittest,os,threading,select,subprocess,tempfile,time,json,sys
try:import pty
except ImportError:pty=None
from pathlib import Path
BINARY=os.environ.get('U30_CELL_TEST_BINARY')
if __name__=='__main__' and len(sys.argv)>1 and not sys.argv[1].startswith('-'):BINARY=sys.argv.pop(1)
class Modem:
 heal_on_radio=False   # a CFUN=1 after CFUN=0 brings the registration back (a stuck modem)
 def __init__(self):
  self.master,self.slave=pty.openpty();self.port=os.ttyname(self.slave);self.commands=[];self.active=True;self.empty_context=False;self.reg=1;self.fail_pdp=False;self.silent=False;self.sim='READY';self.cesq_error=False;self.actual_apn='cmhk';self.running=True;self.overlap=False;self.serving=False
  # radio state; AT+CFUN=1 is answered 'ok' at once, 'slow' (OK after enable_delay s, as the CP early in a boot), 'silent' (never, a stuck CP) or 'refuse' (CME ERROR 3, after a CP reset)
  self.cfun=1;self.enable='ok';self.enable_delay=1.0;self.lock=threading.Lock()
  # AT+SFUN=4 (open the protocol stack, the radio-on request) behaves like AT+CFUN=1 above; sfun_supported=False answers it like a CP
  # without it; tsx_error refuses the TSX temperature report
  self.sfun_supported=True;self.tsx_error=False
  # additional contexts: cid -> {'apn','pdp','active'}; ims: a CP-owned CID 11 that must never be touched; fail_dial: CIDs whose CGDATA is refused
  self.ctx={};self.ims=False;self.fail_dial=set();self.silent_dial=set();self.dns_for={}
  self.thread=threading.Thread(target=self.loop,daemon=True);self.thread.start()
 def write(self,s):
  b=('\r\n'+s+'\r\n').encode()
  with self.lock:os.write(self.master,b[:4]);time.sleep(.003);os.write(self.master,b[4:])
 def radio_on(self):
  if not self.running:return
  self.cfun=1
  try:self.write('OK')
  except OSError:pass
 def loop(self):
  buf=b''
  while self.running:
   if not select.select([self.master],[],[],.05)[0]:continue
   try:b=os.read(self.master,4096)
   except OSError:break
   buf+=b
   while b'\r' in buf:
    cmd,buf=buf.split(b'\r',1);cmd=cmd.strip().decode(errors='replace')
    if not cmd:continue
    self.commands.append(cmd)
    if self.silent:continue
    if cmd=='AT+CPIN?':reply='+CPIN: '+self.sim
    elif cmd=='AT+CFUN?':reply='+CFUN: %d'%self.cfun
    elif cmd=='AT+CEREG?':reply='+CEREG: 2,%d,"9E99","01877901",13'%self.reg
    elif cmd=='AT+COPS?':reply='+COPS: 0,2,"45412",13'
    elif cmd=='AT+CSQ':reply='+CSQ: 18,99'
    elif cmd=='AT+CESQ':reply='ERROR' if self.cesq_error else '+CESQ: 99,99,255,255,12,25,255,255,255'
    elif cmd=='AT+CGDCONT?':reply='\r\n'.join(['+CGDCONT:1,IPV4V6,'+self.actual_apn+',0.0.0.0,0,0']+['+CGDCONT: %d,"%s","%s","0.0.0.0",0,0'%(c,x['pdp'],x['apn']) for c,x in sorted(self.ctx.items())]+(['+CGDCONT: 11,"IPV4V6","ims","0.0.0.0",0,0'] if self.ims else []))
    elif cmd=='AT+CGACT?':reply='\r\n'.join(['+CGACT:' if self.empty_context else '+CGACT: 1,%d'%self.active]+['+CGACT: %d,%d'%(c,x['active']) for c,x in sorted(self.ctx.items())]+(['+CGACT: 11,1'] if self.ims else []))
    elif cmd.startswith('AT+CGDCONT=') and not cmd.startswith('AT+CGDCONT=1,'):
     a=cmd[11:].split(',');c=int(a[0]);self.ctx.setdefault(c,{'active':False});self.ctx[c].update(pdp=a[1].strip('"'),apn=a[2].strip('"'));reply=''
    elif cmd.startswith('AT+CGDATA="M-ETHER",') and cmd!='AT+CGDATA="M-ETHER",1':
     c=int(cmd.split(',')[-1])
     if c in self.silent_dial:continue
     if c in self.fail_dial or c not in self.ctx:reply='+CME ERROR: 30'
     else:self.ctx[c]['active']=True;reply='CONNECT'
    elif cmd.startswith('AT+CGCONTRDP=') and cmd!='AT+CGCONTRDP=1':
     c=int(cmd[13:]);x=self.ctx.get(c)
     reply='+CGCONTRDP: %d,6,"%s.MNC012.MCC454.GPRS",10.9.0.%d.255.255.255.255,,%s'%(c,x['apn'].upper(),c,self.dns_for.get(c,',')) if x and x['active'] else 'ERROR'
    elif cmd.startswith('AT+CGACT=0,') and cmd!='AT+CGACT=0,1':
     c=int(cmd[11:])
     if c in self.ctx:self.ctx[c]['active']=False
     reply=''
    elif cmd=='AT+CGCONTRDP=1':reply='ERROR' if self.fail_pdp else '+CGCONTRDP: 1,5,"'+self.actual_apn.upper()+'.MNC012.MCC454.GPRS",10.1.2.3.255.255.255.255,,1.1.1.1,8.8.8.8'
    elif cmd=='AT+CGACT=0,1':self.active=False;reply=''
    elif cmd.startswith('AT+CGDCONT=1,'):self.empty_context=False;self.actual_apn=cmd.split(',')[-1].strip('"');reply=''
    elif cmd.startswith('AT+CGAUTH='):reply=''
    elif cmd=='AT+CGDATA="M-ETHER",1':self.active=True;reply='CONNECT'
    elif cmd=='AT+CFUN=0' or cmd=='AT+SFUN=5':self.cfun=0;reply=''
    elif cmd.startswith('AT+SPTSXTMP='):reply='+CME ERROR: 4' if self.tsx_error else ''
    elif cmd=='AT+SFUN=4' and not self.sfun_supported:reply='+CME ERROR: 65536014'
    elif cmd in ('AT+CFUN=1','AT+SFUN=4') and self.enable=='slow':threading.Timer(self.enable_delay,self.radio_on).start();continue
    elif cmd in ('AT+CFUN=1','AT+SFUN=4') and self.enable=='silent':continue
    elif cmd in ('AT+CFUN=1','AT+SFUN=4') and self.enable=='refuse':reply='+CME ERROR: 3'
    elif cmd in ('AT+CFUN=1','AT+SFUN=4'):self.cfun=1;reply='';self.reg=1 if self.heal_on_radio else self.reg
    else:reply='ERROR'
    # Unsolicited CSQ interleaved with registration response; not its terminal result.
    if cmd=='AT+CEREG?':self.write('+CSQ: 20,99')
    self.write(reply if reply in ['ERROR','CONNECT'] or reply.startswith('+CME ERROR') else reply+'\r\nOK')
 def close(self):
  self.running=False;self.thread.join(timeout=1);os.close(self.master);os.close(self.slave)
@unittest.skipUnless(BINARY and pty,'requires a Linux CELL_TEST binary argument or U30_CELL_TEST_BINARY')
class TestNativeCellular(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=Modem();self.env=dict(os.environ,U30_CELL_TEST_DIR=str(self.root/'run'),U30_CELL_TEST_NOTIFY=str(self.root/'notify'))
  hook=self.root/'notify';hook.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >>"'+str(self.root/'events')+'"\n');hook.chmod(0o755)
  if 'TSX' in self._testMethodName:self.env['U30_CELL_TEST_TSX']='36500'
  self.p=subprocess.Popen([BINARY,'--run','cellular',self.m.port,'cmhk'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 def tearDown(self):
  self.p.terminate()
  try:self.p.wait(timeout=5)
  except subprocess.TimeoutExpired:self.p.kill();self.p.wait()
  self.p.stderr.close();self.m.close();self.tmp.cleanup()
 def ctl(self,method='status'):
  q=subprocess.run([BINARY,'--ctl',method],env=self.env,capture_output=True,timeout=3)
  if q.returncode:raise RuntimeError((q.returncode,q.stderr,q.stdout))
  return json.loads(q.stdout)
 def wait(self,pred,seconds=6):
  end=time.monotonic()+seconds;last=None
  while time.monotonic()<end:
   try:last=self.ctl()
   except (RuntimeError,json.JSONDecodeError):time.sleep(.03);continue
   if pred(last):return last
   time.sleep(.03)
  self.fail(('timeout',last,self.m.commands))
 def connected(self):return self.wait(lambda s:s['connected'])
 def test_active_context_adopted_without_redial(self):
  s=self.connected();self.assertEqual(s['address'],'10.1.2.3');self.assertEqual(s['operator'],'CMHK');self.assertEqual(s['lte_rsrp_lower_dbm'],-116);self.assertEqual(s['lte_rsrq_lower_db'],-14);self.assertEqual(s['internet_status'],'not-tested');self.assertNotIn('AT+CGDATA="M-ETHER",1',self.m.commands);self.assertNotIn('AT+CGACT=0,1',self.m.commands)
 def test_cold_boot_empty_context_is_configured_and_dialed(self):
  self.m.active=False;self.m.empty_context=True;self.connected();self.assertIn('AT+CGDCONT=1,"IPV4V6","cmhk"',self.m.commands);self.assertIn('AT+CGDATA="M-ETHER",1',self.m.commands)
 def test_network_expanded_apn_does_not_redial(self):
  self.connected();time.sleep(1.5);self.assertTrue(self.ctl()['connected']);self.assertNotIn('AT+CGACT=0,1',self.m.commands);self.assertLess(len(self.m.commands),40)
 def test_changed_configured_apn_redials_once(self):
  self.connected();self.m.actual_apn='wrong';self.ctl('refresh');self.wait(lambda s:s['connected'] and s['generation']>=2);self.assertEqual(self.m.commands.count('AT+CGACT=0,1'),1)
 def test_parallel_readonly_status_does_not_send_AT(self):
  self.connected();before=len(self.m.commands);threads=[threading.Thread(target=self.ctl) for _ in range(12)]
  for t in threads:t.start()
  for t in threads:t.join()
  self.assertEqual(len(self.m.commands),before)
 def test_reconnect_changes_only_CID1(self):
  self.connected();self.ctl('reconnect');self.wait(lambda s:s['connected'] and s['generation']>=2);self.assertIn('AT+CGACT=0,1',self.m.commands);self.assertIn('AT+CGDATA="M-ETHER",1',self.m.commands);self.assertFalse(any('CFUN=0' in c or 'SFUN=5' in c or 'RESET' in c for c in self.m.commands))
 def test_registration_urc_removes_link(self):
  self.connected();self.m.reg=0;self.m.write('+CEREG: 0');s=self.wait(lambda s:not s['connected']);self.assertEqual(s['address'],'');self.m.reg=5;self.ctl('refresh');s=self.connected();self.assertEqual(s['registration'],5)
 def test_pdp_error_clears_stale_address_and_recovers(self):
  self.connected();self.m.fail_pdp=True;self.ctl('refresh');s=self.wait(lambda s:s['state']=='data-unavailable');self.assertFalse(s['connected']);self.assertEqual(s['address'],'');self.m.fail_pdp=False;self.ctl('refresh');self.connected()
 def test_optional_CESQ_error_does_not_block_data(self):
  self.m.cesq_error=True;self.connected()
 def test_disconnect_does_not_redial(self):
  self.connected();self.ctl('disconnect');self.wait(lambda s:s['state']=='disconnected');n=len(self.m.commands);time.sleep(.9);self.assertEqual(len(self.m.commands),n);self.assertFalse(self.ctl()['connected'])
 def test_second_owner_rejected(self):
  self.connected();q=subprocess.run([BINARY,'--run','cellular',self.m.port,'cmhk'],env=self.env,capture_output=True,timeout=2);self.assertNotEqual(q.returncode,0);self.assertIn(b'already running',q.stderr)
 def test_invalid_apn_rejected_before_open(self):
  q=subprocess.run([BINARY,'--run','cellular',self.m.port,'bad";AT+CFUN=0'],env=self.env,capture_output=True,timeout=2);self.assertEqual(q.returncode,2)
 def test_no_sim_does_not_activate_radio_or_pdp(self):
  self.m.sim='SIM PIN';s=self.wait(lambda s:s['state']=='waiting-sim');self.assertFalse(s['connected']);self.assertNotIn('AT+CGDATA="M-ETHER",1',self.m.commands)
 def test_stalled_registration_cycles_the_radio_and_recovers(self):
  self.m.reg=8;self.m.heal_on_radio=True;s=self.wait(lambda s:s['connected'],12);self.assertGreaterEqual(s['radio_resets'],1)
  i=self.m.commands.index('AT+SFUN=5');self.assertIn('AT+SFUN=4',self.m.commands[i:]);self.assertEqual(s['registration'],1)
 def test_short_registration_gap_does_not_cycle_the_radio(self):
  self.connected();self.m.reg=0;self.m.write('+CEREG: 0');self.wait(lambda s:not s['connected']);time.sleep(.5);self.m.reg=1;self.ctl('refresh');s=self.connected();self.assertEqual(s['radio_resets'],0);self.assertNotIn('AT+SFUN=5',self.m.commands)
 def test_radio_cycle_is_rate_limited(self):
  self.m.reg=8;self.wait(lambda s:s['radio_resets']>=1,12);time.sleep(2.5);s=self.ctl();self.assertEqual(s['radio_resets'],1,s)
 def test_timeout_clears_link(self):
  self.connected();self.m.silent=True;self.ctl('refresh');s=self.wait(lambda s:s['timeouts']>0);self.assertFalse(s['connected']);self.assertEqual(s['address'],'')
 def test_slow_radio_enable_is_waited_for_not_repeated(self):
  self.m.cfun=0;self.m.enable='slow';s=self.connected();self.assertEqual(self.m.commands.count('AT+SFUN=4'),1);self.assertEqual(s['timeouts'],0)
 def test_radio_reply_after_the_wait_is_picked_up_without_a_repeat(self):
  # the OK arrives after celld stopped waiting (the device's boot where the radio came on at 49 s): no second request
  self.m.cfun=0;self.m.enable='slow';self.m.enable_delay=2.0;s=self.wait(lambda s:s['connected'],12);self.assertEqual(self.m.commands.count('AT+SFUN=4'),1);self.assertGreaterEqual(s['timeouts'],1)
 def test_unanswered_radio_enable_is_not_repeated_within_the_gap(self):
  self.m.cfun=0;self.m.enable='silent';s=self.wait(lambda s:s['timeouts']>=1 and self.m.commands.count('AT+CFUN?')>=3,6)
  self.assertEqual(self.m.commands.count('AT+SFUN=4'),1);self.assertFalse(s['connected']);self.assertIn('AT+SFUN=4',s['last_error'])
 def test_refused_radio_enable_is_retried_once_per_gap(self):
  self.m.cfun=0;self.m.enable='refuse';s=self.wait(lambda s:self.m.commands.count('AT+CFUN?')>=3);self.assertEqual(self.m.commands.count('AT+SFUN=4'),1);self.assertEqual(s['state'],'data-unavailable');self.assertIn('AT+CFUN=1',s['last_error']);self.assertEqual(self.m.commands.count('AT+CFUN=1'),1)
  self.m.enable='ok';s=self.wait(lambda s:s['connected'],10);self.assertEqual(self.m.commands.count('AT+SFUN=4'),2)
 def test_radio_lost_after_it_was_on_gets_one_prompt_retry(self):
  self.m.cfun=0;self.connected();self.wait(lambda s:self.m.commands.count('AT+CFUN?')>=2);self.assertEqual(self.m.commands.count('AT+SFUN=4'),1)
  self.m.cfun=0;self.m.enable='refuse';self.m.write('+CEREG: 0');self.wait(lambda s:self.m.commands.count('AT+SFUN=4')==2,4);time.sleep(1.5);s=self.ctl()
  self.assertEqual(self.m.commands.count('AT+SFUN=4'),2);self.assertFalse(s['connected'])
 def test_radio_on_sends_the_TSX_temperature_before_the_stack(self):
  self.m.cfun=0;s=self.connected();c=self.m.commands;self.assertIn('AT+SPTSXTMP=36500',c);self.assertLess(c.index('AT+SPTSXTMP=36500'),c.index('AT+SFUN=4'));self.assertNotIn('AT+CFUN=1',c)
 def test_refused_TSX_temperature_still_opens_the_stack(self):
  self.m.cfun=0;self.m.tsx_error=True;s=self.connected();self.assertIn('AT+SFUN=4',self.m.commands);self.assertEqual(s['errors'],0)
 def test_no_SFUN_falls_back_to_CFUN(self):
  self.m.cfun=0;self.m.sfun_supported=False;s=self.connected();c=self.m.commands;self.assertEqual(c.count('AT+SFUN=4'),1);self.assertEqual(c.count('AT+CFUN=1'),1);self.assertLess(c.index('AT+SFUN=4'),c.index('AT+CFUN=1'))
@unittest.skipUnless(BINARY and pty,'requires a Linux CELL_TEST binary argument or U30_CELL_TEST_BINARY')
class TestDialProfile(unittest.TestCase):
 """auth / PDP type profile (<root>/profile) handling"""
 def start(self,profile=None):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=Modem();self.run_dir=self.root/'run';self.run_dir.mkdir(mode=0o700)
  if profile is not None:(self.run_dir/'profile').write_text(profile)
  self.env=dict(os.environ,U30_CELL_TEST_DIR=str(self.run_dir),U30_CELL_TEST_NOTIFY=str(self.root/'notify'))
  hook=self.root/'notify';hook.write_text('#!/bin/sh\nexit 0\n');hook.chmod(0o755)
  self.p=subprocess.Popen([BINARY,'--run','cellular',self.m.port,'cmhk'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 def tearDown(self):
  p=getattr(self,'p',None)
  if p:
   p.terminate()
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
   p.stderr.close()
  if hasattr(self,'m'):self.m.close()
  if hasattr(self,'tmp'):self.tmp.cleanup()
 def ctl(self,method='status'):
  q=subprocess.run([BINARY,'--ctl',method],env=self.env,capture_output=True,timeout=3)
  if q.returncode:raise RuntimeError((q.returncode,q.stderr,q.stdout))
  return json.loads(q.stdout)
 def wait(self,pred,seconds=6):
  end=time.monotonic()+seconds;last=None
  while time.monotonic()<end:
   try:last=self.ctl()
   except (RuntimeError,json.JSONDecodeError):time.sleep(.03);continue
   if pred(last):return last
   time.sleep(.03)
  self.fail(('timeout',last,self.m.commands))
 def cold(self):self.m.active=False;self.m.empty_context=True
 def test_pap_profile_is_programmed_before_the_data_call(self):
  self.start('auth=pap\nuser=card\npass=secret\npdp=ipv4v6\n');self.cold();s=self.wait(lambda s:s['connected'])
  c=self.m.commands;self.assertIn('AT+CGAUTH=1,1,"card","secret"',c);self.assertLess(c.index('AT+CGAUTH=1,1,"card","secret"'),c.index('AT+CGDATA="M-ETHER",1'))
  self.assertEqual(s['auth'],'pap');self.assertNotIn('secret',json.dumps(s));self.assertNotIn('card',json.dumps(s))
 def test_chap_uses_protocol_2(self):
  self.start('auth=chap\nuser=u\npass=p\n');self.cold();self.wait(lambda s:s['connected']);self.assertIn('AT+CGAUTH=1,2,"u","p"',self.m.commands)
 def test_ipv4_only_profile_selects_IP_pdp_type(self):
  self.start('pdp=ipv4\n');self.cold();s=self.wait(lambda s:s['connected']);self.assertIn('AT+CGDCONT=1,"IP","cmhk"',self.m.commands);self.assertEqual(s['pdp'],'ipv4')
 def test_auth_none_profile_clears_stored_credentials(self):
  self.start('auth=none\n');self.cold();self.wait(lambda s:s['connected']);self.assertIn('AT+CGAUTH=1,0',self.m.commands)
 def test_no_profile_keeps_the_old_command_sequence(self):
  self.start(None);self.cold();self.wait(lambda s:s['connected']);self.assertFalse(any(c.startswith('AT+CGAUTH') for c in self.m.commands));self.assertIn('AT+CGDCONT=1,"IPV4V6","cmhk"',self.m.commands)
 def test_invalid_profile_refuses_to_start(self):
  self.start('auth=pap\nuser=a"b\npass=x\n');self.assertEqual(self.p.wait(timeout=3),2)
 def test_missing_user_with_auth_is_invalid(self):
  self.start('auth=pap\npass=x\n');self.assertEqual(self.p.wait(timeout=3),2)
 def test_reload_applies_a_changed_profile_with_one_redial(self):
  self.start('pdp=ipv4v6\n');self.wait(lambda s:s['connected'])
  (self.run_dir/'profile').write_text('auth=pap\nuser=n\npass=m\npdp=ipv4\n');self.ctl('reload');s=self.wait(lambda s:s['connected'] and s['generation']>=2)
  self.assertIn('AT+CGDCONT=1,"IP","cmhk"',self.m.commands);self.assertIn('AT+CGAUTH=1,1,"n","m"',self.m.commands);self.assertEqual(self.m.commands.count('AT+CGACT=0,1'),1)
 def test_profile_apn_overrides_the_command_line_apn_on_reload(self):
  self.start('pdp=ipv4v6\n');self.wait(lambda s:s['connected'])
  (self.run_dir/'profile').write_text('apn=other.apn\n');self.ctl('reload');s=self.wait(lambda s:s['connected'] and s['generation']>=2)
  self.assertEqual(s['apn'],'other.apn');self.assertIn('AT+CGDCONT=1,"IPV4V6","other.apn"',self.m.commands)
 def test_profile_with_bad_apn_is_invalid(self):
  self.start('apn=bad apn;x\n');self.assertEqual(self.p.wait(timeout=3),2)
 def test_reload_with_bad_profile_keeps_running_settings(self):
  self.start('pdp=ipv4v6\n');self.wait(lambda s:s['connected']);(self.run_dir/'profile').write_text('auth=bogus\n')
  q=subprocess.run([BINARY,'--ctl','reload'],env=self.env,capture_output=True,timeout=3);self.assertIn(b'invalid-profile',q.stdout);s=self.ctl();self.assertEqual(s['auth'],'none');self.assertTrue(s['connected'])
@unittest.skipUnless(BINARY and pty,'requires a Linux CELL_TEST binary argument or U30_CELL_TEST_BINARY')
class TestExtraContexts(unittest.TestCase):
 """additional contexts (<root>/ctx/<name>): CIDs 2-8 next to the internet context"""
 def start(self,contexts,prepare=None):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.m=Modem();self.run_dir=self.root/'run';self.ctxdir=self.run_dir/'ctx';self.ctxdir.mkdir(parents=True,mode=0o700)
  for name,body in contexts.items():(self.ctxdir/name).write_text(body)
  if prepare:prepare(self.m)
  self.events=self.root/'events';self.env=dict(os.environ,U30_CELL_TEST_DIR=str(self.run_dir),U30_CELL_TEST_NOTIFY=str(self.root/'notify'))
  hook=self.root/'notify';hook.write_text('#!/bin/sh\nfor a in "$@"; do printf "%s|" "$a"; done >>"'+str(self.events)+'"\necho >>"'+str(self.events)+'"\n');hook.chmod(0o755)
  self.p=subprocess.Popen([BINARY,'--run','cellular',self.m.port,'cmhk'],env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 def tearDown(self):
  p=getattr(self,'p',None)
  if p:
   p.terminate()
   try:p.wait(timeout=5)
   except subprocess.TimeoutExpired:p.kill();p.wait()
   p.stderr.close()
  if hasattr(self,'m'):self.m.close()
  if hasattr(self,'tmp'):self.tmp.cleanup()
 def ctl(self,method='status'):
  q=subprocess.run([BINARY,'--ctl',method],env=self.env,capture_output=True,timeout=3)
  if q.returncode:raise RuntimeError((q.returncode,q.stderr,q.stdout))
  return json.loads(q.stdout)
 def wait(self,pred,seconds=6):
  end=time.monotonic()+seconds;last=None
  while time.monotonic()<end:
   try:last=self.ctl()
   except (RuntimeError,json.JSONDecodeError):time.sleep(.03);continue
   if pred(last):return last
   time.sleep(.03)
  self.fail(('timeout',last,self.m.commands))
 def hooks(self):
  try:return [l.rstrip('|').split('|') for l in self.events.read_text().splitlines() if l]
  except FileNotFoundError:return []
 def ctx(self,s,name):return next(x for x in s['contexts'] if x['name']==name)
 def up(self,name):return lambda s:s['connected'] and any(x['name']==name and x['connected'] for x in s['contexts'])
 def test_extra_context_is_dialled_after_the_internet_context(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\npdp=ipv4\n'});s=self.wait(self.up('mms'));c=self.m.commands
  seq=['AT+CGDCONT=2,"IP","mms"','AT+CGAUTH=2,0','AT+CGDATA="M-ETHER",2','AT+CGCONTRDP=2']
  idx=[c.index(x) for x in seq];self.assertEqual(idx,sorted(idx));self.assertLess(max(i for i,x in enumerate(c) if x=='AT+CGCONTRDP=1' and i<idx[0]),idx[0])
  x=self.ctx(s,'mms');self.assertEqual((x['cid'],x['device'],x['interface'],x['address'],x['dns']),(2,'sipa_eth1','cell_mms','10.9.0.2',[]))
  self.assertIn(['cell_mms','up','10.9.0.2','','','sipa_eth1'],self.hooks());self.assertIn(['cellular','up','10.1.2.3','1.1.1.1','8.8.8.8'],self.hooks())
 def test_dns_of_an_extra_context_is_reported(self):
  self.start({'corp':'cid=3\napn=corp.example\niface=cell_corp\nauth=chap\nuser=me\npass=pw\n'},lambda m:m.dns_for.update({3:'10.0.0.53,10.0.0.54'}))
  s=self.wait(self.up('corp'));x=self.ctx(s,'corp');self.assertEqual(x['dns'],['10.0.0.53','10.0.0.54']);self.assertIn('AT+CGAUTH=3,2,"me","pw"',self.m.commands)
  self.assertNotIn('"pw"',json.dumps(s));self.assertNotIn('"me"',json.dumps(s));self.assertIn(['cell_corp','up','10.9.0.3','10.0.0.53','10.0.0.54','sipa_eth2'],self.hooks())
 def test_active_extra_context_is_adopted_without_redial(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n'},lambda m:m.ctx.update({2:{'apn':'mms','pdp':'IP','active':True}}))
  self.wait(self.up('mms'));self.assertFalse(any(x.startswith('AT+CGDATA="M-ETHER",2') or x=='AT+CGACT=0,2' for x in self.m.commands))
 def test_failing_extra_context_backs_off_and_never_touches_the_internet_context(self):
  self.start({'bad':'cid=4\napn=nowhere\niface=cell_bad\n'},lambda m:m.fail_dial.add(4))
  s=self.wait(lambda s:s['connected'] and self.ctx(s,'bad')['last_error']);time.sleep(2.5);s=self.ctl()
  self.assertTrue(s['connected']);self.assertEqual(s['generation'],1);self.assertNotIn('AT+CGACT=0,1',self.m.commands);self.assertFalse(self.ctx(s,'bad')['connected'])
  self.assertLessEqual(self.m.commands.count('AT+CGDATA="M-ETHER",4'),3);self.assertIn('CGDATA',self.ctx(s,'bad')['last_error'])
 def test_unanswered_extra_dial_times_out_without_dropping_the_internet_context(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n','corp':'cid=3\napn=corp\niface=cell_corp\n'},lambda m:m.silent_dial.add(2))
  s=self.wait(lambda s:s['timeouts']>=1 and 'timeout' in self.ctx(s,'mms')['last_error'],8);self.assertTrue(s['connected']);self.assertEqual(s['generation'],1)
  s=self.wait(self.up('corp'),6);self.assertTrue(s['connected']);self.assertNotIn('AT+CGACT=0,1',self.m.commands)
 def test_deactivation_of_an_extra_cid_leaves_the_internet_context(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n'},lambda m:setattr(m,'ims',True));self.wait(self.up('mms'))
  self.m.ctx[2]['active']=False;self.m.write('+CGEV: NW PDN DEACT 2');s=self.wait(lambda s:not self.ctx(s,'mms')['connected'],3);self.assertTrue(s['connected'])
  s=self.wait(lambda s:self.ctx(s,'mms')['generation']>=2);self.assertEqual(s['generation'],1);self.assertIn(['cell_mms','down','','','','sipa_eth1'],self.hooks())
 def test_deactivation_of_another_owners_cid_is_ignored(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n'},lambda m:setattr(m,'ims',True));self.wait(self.up('mms'))
  self.m.write('+CGEV: NW PDN DEACT 11');self.m.write('+CGEV: NW DEACT 1,5,0');time.sleep(.8);s=self.ctl()
  self.assertTrue(s['connected']);self.assertTrue(self.ctx(s,'mms')['connected']);self.assertEqual(s['generation'],1);self.assertEqual(self.ctx(s,'mms')['generation'],1)
  self.assertFalse(any(x.endswith(',11') or x.endswith('=11') for x in self.m.commands))
 def test_deactivation_of_cid1_still_drops_the_internet_context(self):
  self.start({});self.wait(lambda s:s['connected']);self.m.active=False;self.m.write('+CGEV: NW PDN DEACT 1');self.wait(lambda s:s['connected'] and s['generation']>=2)
 def test_old_format_deactivation_without_cid_is_taken_as_cid1(self):
  self.start({});self.wait(lambda s:s['connected']);self.m.active=False;self.m.write('+CGEV: NW DEACT "IP","10.1.2.3"');self.wait(lambda s:s['connected'] and s['generation']>=2)
 def test_reload_removing_a_context_takes_only_it_down(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n','corp':'cid=3\napn=corp\niface=cell_corp\n'});self.wait(lambda s:self.up('mms')(s) and self.up('corp')(s))
  (self.ctxdir/'mms').unlink();self.ctl('reload');s=self.wait(lambda s:[x['name'] for x in s['contexts']]==['corp'] and 'AT+CGACT=0,2' in self.m.commands)
  self.assertIn(['cell_mms','down','','','','sipa_eth1'],self.hooks());self.assertNotIn('AT+CGACT=0,1',self.m.commands);self.assertNotIn('AT+CGACT=0,3',self.m.commands);self.assertEqual(s['generation'],1);self.assertTrue(self.ctx(s,'corp')['connected'])
 def test_reload_with_a_changed_apn_redials_only_that_context(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n'});self.wait(self.up('mms'))
  (self.ctxdir/'mms').write_text('cid=2\napn=mms.new\niface=cell_mms\n');self.ctl('reload');s=self.wait(lambda s:self.ctx(s,'mms')['connected'] and self.ctx(s,'mms')['apn']=='mms.new' and 'AT+CGDCONT=2,"IP","mms.new"' in self.m.commands)
  c=self.m.commands;self.assertLess(c.index('AT+CGACT=0,2'),c.index('AT+CGDCONT=2,"IP","mms.new"'));self.assertNotIn('AT+CGACT=0,1',c);self.assertEqual(s['generation'],1)
 def test_reload_adding_a_context_dials_it_promptly(self):
  self.start({});self.wait(lambda s:s['connected']);(self.ctxdir/'mms').write_text('cid=2\napn=mms\niface=cell_mms\n');self.ctl('reload');s=self.wait(self.up('mms'),3);self.assertEqual(s['generation'],1)
 def test_disconnect_takes_the_extra_contexts_down(self):
  self.start({'mms':'cid=2\napn=mms\niface=cell_mms\n'});self.wait(self.up('mms'));self.ctl('disconnect');s=self.wait(lambda s:s['state']=='disconnected' and not self.m.ctx[2]['active'])
  c=self.m.commands;self.assertLess(c.index('AT+CGACT=0,1'),c.index('AT+CGACT=0,2'));self.assertFalse(self.ctx(s,'mms')['connected']);self.assertIn(['cell_mms','down','','','','sipa_eth1'],self.hooks())
 def test_invalid_context_files_are_left_out(self):
  self.start({'one':'cid=1\napn=x\niface=cell_one\n','ims':'cid=11\napn=ims\niface=cell_ims\n','badapn':'cid=4\napn=a"b\niface=cell_b\n','noauthuser':'cid=5\napn=x\niface=cell_c\nauth=pap\n',
   'dupa':'cid=6\napn=x\niface=cell_d\n','dupb':'cid=6\napn=y\niface=cell_e\n','ok':'cid=7\napn=fine\niface=cell_ok\n'})
  s=self.wait(self.up('ok'));names=sorted(x['name'] for x in s['contexts']);self.assertIn('ok',names);self.assertEqual(len([n for n in names if n.startswith('dup')]),1)
  for n in ['one','ims','badapn','noauthuser']:self.assertNotIn(n,names)
  self.assertFalse(any(',11' in x or '=11' in x for x in self.m.commands))
 def test_no_contexts_adds_no_commands(self):
  # one CGACT? per check of the internet context, as before
  self.start({});self.wait(lambda s:s['connected']);time.sleep(1.5);self.assertEqual(self.ctl()['contexts'],[]);self.assertLessEqual(self.m.commands.count('AT+CGACT?'),self.m.commands.count('AT+CPIN?'))
if __name__=='__main__':unittest.main(verbosity=2)
