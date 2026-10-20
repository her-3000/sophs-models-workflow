import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
import work_cycle as h

class CycleTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=datetime(2026,10,20,9,tzinfo=timezone.utc);self.rid='facebook:2026-10-20:1600';self.previous_config=h.CONFIG;h.CONFIG=self.root/'automation.toml';h.CONFIG.write_text('status="ACTIVE"\nrrule="FREQ=DAILY;BYHOUR=7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22;BYMINUTE=0;BYSECOND=0"\n')
  (self.root/'reviews').mkdir();self.put('hourly-state.json',{'active':False,'run_id':self.rid,'thread_id':h.SOPHIE,'report':'reviews/run.json','started_at_bangkok':self.now.isoformat(),'stopped_at_bangkok':self.now.isoformat(),'inflight_send':False,'line_digest_status':'api_accepted','line_message_id':'receipt'})
  self.put('social-collector-state.json',{'active':False,'run_id':self.rid,'thread_id':h.SOPHIE})
  self.put('reviews/run.json',{'run_id':self.rid,'inflight_send':False,'line_digest_status':'api_accepted'})
  self.put('collector-health-checkpoints.json',{self.rid:[{'phase':'postflight'}]})
 def tearDown(self):h.CONFIG=self.previous_config;self.tmp.cleanup()
 def put(self,n,d):(self.root/n).write_text(json.dumps(d))
 def read(self,n):return json.loads((self.root/n).read_text())
 def init(self):return h.apply(self.root,'init',{'sophie_run_id':self.rid},self.now)['state']
 def test_terminal_receipt_release_required_and_idempotent(self):
  c=self.init();self.assertEqual(c['phase'],'upwork_ready')
  self.assertTrue(h.apply(self.root,'init',{'sophie_run_id':'wrong'},self.now)['existing'])
  self.assertTrue(h.apply(self.root,'complete-sophie',{'cycle_id':self.rid},self.now)['existing'])
 def test_api_receipt_alone_cannot_handoff(self):
  self.put('social-collector-state.json',{'active':True,'run_id':self.rid,'thread_id':h.SOPHIE});c=self.init();self.assertEqual(c['phase'],'sophie_running')
  with self.assertRaisesRegex(ValueError,'guards_active'):h.apply(self.root,'complete-sophie',{'cycle_id':self.rid},self.now)
 def test_missing_postflight_or_unknown_send_blocks(self):
  self.put('collector-health-checkpoints.json',{})
  with self.assertRaisesRegex(ValueError,'postflight'):self.init()
  self.put('collector-health-checkpoints.json',{self.rid:[{'phase':'postflight'}]});d=self.read('reviews/run.json');d['inflight_send']=True;self.put('reviews/run.json',d)
  with self.assertRaisesRegex(ValueError,'inflight'):self.init()
 def test_failed_or_uncertain_digest_does_not_deadlock_or_resend(self):
  for status in ['failed','uncertain']:
   for n in ['hourly-state.json','reviews/run.json']:
    d=self.read(n);d['line_digest_status']=status;d.pop('line_message_id',None);self.put(n,d)
   (self.root/h.FILE).unlink(missing_ok=True);c=self.init();self.assertEqual(c['phase'],'upwork_ready');self.assertTrue(c['casting_completion']['delivery_issue_requires_notice'])
 def test_wrong_owner_missing_cycle_and_busy_claim(self):
  self.init();self.assertEqual(h.admission(self.root,'begin',{'thread_id':h.SOPHIE},self.now)['reason'],'cycle_reserved')
  self.assertEqual(h.admission(self.root,'lease-begin',{'thread_id':h.UPWORK},self.now)['reason'],'cycle_id_required')
  self.put('social-collector-state.json',{'active':True})
  with self.assertRaisesRegex(ValueError,'busy'):h.apply(self.root,'claim-dispatch',{'cycle_id':self.rid},self.now)
 def test_dispatch_at_most_once_even_uncertain(self):
  self.init();d={'cycle_id':self.rid};a=h.apply(self.root,'claim-dispatch',d,self.now);self.assertTrue(a['send_message']);self.assertFalse(h.apply(self.root,'claim-dispatch',d,self.now)['send_message'])
  h.apply(self.root,'dispatch-result',{**d,'dispatch_id':a['dispatch_id'],'status':'uncertain'},self.now)
  self.assertFalse(h.apply(self.root,'claim-dispatch',d,self.now)['send_message'])
 def test_upwork_cutoff_and_next_hour_safe_return(self):
  self.init();d={'thread_id':h.UPWORK,'run_id':'upwork:cycle:1','cycle_id':self.rid};self.assertIsNone(h.admission(self.root,'lease-begin',d,self.now));h.admitted(self.root,'lease-begin',d,self.now)
  self.put('social-collector-state.json',{'active':False,**d});self.put('upwork.json',{'run_id':d['run_id'],'inflight_send':False,'receipts_reconciled':True})
  finish={**d,'checkpoint':str(self.root/'upwork.json'),'inflight_send':False,'receipts_reconciled':True}
  c=h.apply(self.root,'complete-upwork',finish,self.now+timedelta(minutes=10))['state'];self.assertEqual(c['phase'],'sophie_ready')
  self.assertEqual(h.admission(self.root,'begin',{'thread_id':h.SOPHIE,'run_id':'new'},self.now+timedelta(minutes=59))['reason'],'cycle_not_due')
  self.assertIsNone(h.admission(self.root,'begin',{'thread_id':h.SOPHIE,'run_id':'new'},self.now+timedelta(minutes=60)))
  h.admitted(self.root,'begin',{'thread_id':h.SOPHIE,'run_id':'new'},self.now+timedelta(minutes=60));self.assertEqual(self.read(h.FILE)['cycle_id'],'new')
 def test_uncertain_upwork_must_reconcile_before_return(self):
  self.init();d={'thread_id':h.UPWORK,'run_id':'u','cycle_id':self.rid};h.admitted(self.root,'lease-begin',d,self.now);self.put('social-collector-state.json',{'active':False,**d})
  with self.assertRaisesRegex(ValueError,'reconciliation'):h.apply(self.root,'complete-upwork',{**d,'inflight_send':True},self.now)
 def test_no_lease_theft_or_hours_bypass(self):
  self.init();d={'thread_id':h.UPWORK,'cycle_id':self.rid}
  self.assertEqual(h.admission(self.root,'lease-begin',d,self.now.replace(hour=16))['reason'],'cycle_outside_hours')
  self.assertEqual(h.admission(self.root,'lease-begin',{'thread_id':'unrelated'},self.now)['reason'],'cycle_reserved')
 def test_adopt_existing_authorized_upwork_without_lease_mutation(self):
  lease={'active':True,'run_id':'approved-upwork','thread_id':h.UPWORK,'started_at_bangkok':self.now.isoformat()}
  self.put('social-collector-state.json',lease);c=self.init();self.assertEqual(c['phase'],'upwork_running');self.assertEqual(c['upwork_run_id'],'approved-upwork');self.assertEqual(self.read('social-collector-state.json'),lease)
  self.assertEqual(h.stamp(c['target_end']).minute,50)
 def test_late_window_is_skipped_not_extended(self):
  c=h.apply(self.root,'init',{'sophie_run_id':self.rid},self.now+timedelta(minutes=51))['state']
  self.assertEqual(c['phase'],'sophie_ready');self.assertEqual(h.stamp(c['not_before']).hour,17)
 def test_remaining_window_shortens_and_admission_closes_at_50(self):
  c=self.init();self.assertEqual(h.stamp(c['next_sophie_start']).hour,17)
  d={'thread_id':h.UPWORK,'cycle_id':self.rid,'run_id':'u'}
  self.assertIsNone(h.admission(self.root,'lease-begin',d,self.now+timedelta(minutes=49)))
  self.assertEqual(h.admission(self.root,'lease-begin',d,self.now+timedelta(minutes=50))['reason'],'upwork_window_closed')
 def test_unsupported_schedule_fails_closed(self):
  h.CONFIG.write_text('status="PAUSED"\nrrule="FREQ=HOURLY"\n')
  with self.assertRaisesRegex(ValueError,'unsupported'):self.init()
 def test_final_sophie_release_reserves_morning_without_upwork(self):
  night=self.now.replace(hour=15,minute=14)
  c=h.apply(self.root,'init',{'sophie_run_id':self.rid},night)['state']
  self.assertEqual(c['phase'],'sophie_ready');self.assertEqual(c['next_owner'],h.SOPHIE)
  self.assertEqual(c['not_before'],'2026-10-21T07:00:00+07:00')
  with self.assertRaisesRegex(ValueError,'not_due'):h.apply(self.root,'claim-dispatch',{'cycle_id':self.rid},night)
  morning=night.replace(hour=0,minute=0)+timedelta(days=1)
  self.assertIsNone(h.admission(self.root,'begin',{'thread_id':h.SOPHIE,'run_id':'next-morning'},morning))
 def test_existing_overnight_upwork_ready_reconciles_without_dispatch(self):
  self.init();c=self.read(h.FILE);c.update(next_sophie_start='2026-10-21T07:00:00+07:00',upwork_cutoff='2026-10-21T06:50:00+07:00');self.put(h.FILE,c)
  lease=self.read('social-collector-state.json');a=h.apply(self.root,'claim-dispatch',{'cycle_id':self.rid},self.now.replace(hour=15,minute=14))
  self.assertFalse(a['send_message']);self.assertEqual(a['state']['phase'],'sophie_ready');self.assertEqual(a['state']['not_before'],'2026-10-21T07:00:00+07:00');self.assertEqual(self.read('social-collector-state.json'),lease)
 def test_release_hook_requires_actual_terminal_and_digest(self):
  self.put('social-collector-state.json',{'active':True,'run_id':self.rid,'thread_id':h.SOPHIE});self.init()
  h.released(self.root,{'run_id':self.rid},self.now);self.assertEqual(self.read(h.FILE)['phase'],'sophie_running')
  self.put('social-collector-state.json',{'active':False,'run_id':self.rid,'thread_id':h.SOPHIE})
  h.released(self.root,{'run_id':self.rid},self.now);self.assertEqual(self.read(h.FILE)['phase'],'upwork_ready')
 def test_admission_integration_and_release_hook(self):
  spec=importlib.util.spec_from_file_location('checkpoint',Path(__file__).with_name('hourly-checkpoint.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
  m._cycle.CONFIG=h.CONFIG; original_admission=m._cycle.admission;m._cycle.admission=lambda root,command,data:original_admission(root,command,data,self.now)
  self.init();self.assertFalse(m.execute(self.root,'begin',{'thread_id':h.SOPHIE,'run_id':'x'})['ok'])
  d={'thread_id':h.UPWORK,'cycle_id':self.rid,'run_id':'u'};self.assertTrue(m.execute(self.root,'lease-begin',d)['ok'])
  self.assertFalse(m.execute(self.root,'lease-begin',d)['ok']);self.assertTrue(m.execute(self.root,'lease-finish',d)['ok'])

if __name__=='__main__':unittest.main(verbosity=2)
