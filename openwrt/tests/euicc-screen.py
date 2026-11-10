"""Shared screen job adapter: UI compatibility must not bypass card identity."""
import importlib.machinery,importlib.util,runpy,unittest
from pathlib import Path
from unittest.mock import Mock
TOP=Path(__file__).resolve().parents[2]
APP=next(TOP/'openwrt'/n for n in ('luci-app-t551m','luci-app-mu300') if (TOP/'openwrt'/n).exists())
ROOT=APP/'root/usr/libexec/unisoc-modem'
loader=importlib.machinery.SourceFileLoader('euicc',str(ROOT/'euicc'))
spec=importlib.util.spec_from_loader(loader.name,loader);e=importlib.util.module_from_spec(spec);loader.exec_module(e)
f=runpy.run_path(str(ROOT/'euicc-screen'))
EID='89049032000100123450001234567890';ICCID='8912345678901234567';JOB='b'*32
class Screen(unittest.TestCase):
 def setUp(self):
  self.start=Mock(side_effect=lambda req:(e.validate(req),{'ok':1,'job':JOB})[1])
  self.state={'ok':1,'job':JOB,'state':'running','slot':'1','action':'download','stage':'preview','preview':True,'metadata':{'profileName':'Example'}}
  self.api=dict(Failure=e.Failure,LPAC='/missing-fixture',start=self.start,current=Mock(return_value=self.state),status=Mock(return_value=self.state),control=Mock(return_value={'ok':1}))
 def invoke(self,method,**params):return f['invoke']({'method':method,'params':params},self.api)
 def test_scan_requires_exact_slot_and_uses_shared_nonce(self):
  with self.assertRaisesRegex(e.Failure,'invalid_request'):self.invoke('snapshot',slot=True,job=JOB)
  self.assertEqual(self.invoke('snapshot',slot=1,job=JOB)['wait'],'snapshot')
  self.assertEqual(self.start.call_args.args[0]['slot'],'1')
 def test_mutation_cannot_bypass_initial_eid_scan(self):
  with self.assertRaisesRegex(e.Failure,'scan_required'):self.invoke('operation_start',slot=1,job=JOB,request={'action':'delete','iccid':ICCID})
 def test_mutation_targets_exact_scanned_card(self):
  self.invoke('operation_start',slot=1,job=JOB,eid=EID,request={'action':'switch','iccid':ICCID,'enabled':False})
  req=self.start.call_args.args[0];self.assertEqual((req['action'],req['eid'],req['iccid']),('disable',EID,ICCID))
 def test_arbitrary_requests_and_invalid_profile_state_are_refused(self):
  for action in ({'action':'raw_at'},{'action':'switch','enabled':'false','iccid':ICCID}):
   with self.assertRaisesRegex(e.Failure,'invalid_request'):self.invoke('operation_start',slot=1,job=JOB,eid=EID,request=action)
 def test_notification_batch_preserves_explicit_sequences(self):
  self.invoke('operation_start',slot=1,job=JOB,eid=EID,request={'action':'notifications','remove':True,'notifications':[{'seqNumber':14}]})
  req=self.start.call_args.args[0];self.assertEqual(req['sequences'],[14]);self.assertTrue(req['remove'])
 def test_status_preserves_preview_and_existing_job(self):
  out=self.invoke('job_status',job=JOB);self.assertTrue(out['preview']);self.assertEqual(out['job'],JOB);self.assertEqual(out['metadata']['profileName'],'Example');self.start.assert_not_called()
 def test_confirm_and_cancel_target_saved_job_without_replay(self):
  self.invoke('job_confirm',job=JOB,accept=False);self.api['control'].assert_called_with({'job':JOB,'accept':False},'answer')
  self.invoke('job_cancel',job=JOB);self.api['control'].assert_called_with({'job':JOB,'accept':None},'cancel');self.start.assert_not_called()
 def test_async_read_formats_only_successful_results(self):
  self.state.update(state='done',action='scan',result={'chip':{'eidValue':EID},'profiles':[],'notifications':[],'ses':3})
  out=self.invoke('job_status',job=JOB,format='snapshot');self.assertEqual(out['chip']['data']['eidValue'],EID);self.assertEqual(out['ses'],3)
  self.state.update(state='failed',error='card_changed');self.assertFalse(self.invoke('job_status',job=JOB,format='snapshot')['ok'])
if __name__=='__main__':unittest.main(verbosity=2)
