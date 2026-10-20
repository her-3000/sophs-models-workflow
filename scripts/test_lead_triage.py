import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
spec=importlib.util.spec_from_file_location('triage',Path(__file__).with_name('lead-triage.py'))
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)

class TriageTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=datetime(2026,10,20,tzinfo=timezone.utc)
  self.d={'run_id':'r','thread_id':'t','lead_key':'sophie|job|role','source_url':'https://www.facebook.com/groups/1/posts/2/?tracking=omit','priority':'fresh_paid'}
  for n in ['hourly-state.json','social-collector-state.json']:self.put(n,{'active':True,'run_id':'r','thread_id':'t'})
 def tearDown(self):self.tmp.cleanup()
 def put(self,n,d):(self.root/n).write_text(json.dumps(d))
 def call(self,c,sec=0,**kwargs):return h.apply(self.root,c,{**self.d,**kwargs},self.now+timedelta(seconds=sec))
 def test_busy_owner_denied(self):
  self.put('social-collector-state.json',{'active':True,'run_id':'other','thread_id':'x'})
  with self.assertRaises(ValueError):self.call('start')
 def test_twenty_second_target_and_idempotent_start(self):
  a=self.call('start');b=self.call('start',10);self.assertEqual(a['lead']['deadline'],b['lead']['deadline'])
  self.assertFalse(self.call('check',19)['move_on']);self.assertTrue(self.call('check',20)['move_on'])
 def test_brief_two_minutes_no_clock_reset(self):
  self.call('start');self.call('classify',5,classification='promising')
  self.assertFalse(self.call('check',124)['move_on']);self.assertTrue(self.call('check',125)['move_on'])
  with self.assertRaises(ValueError):self.call('classify',126,classification='promising')
 def test_login_and_failed_reload_move_on(self):
  for flag in ['login_wall','failed_reload']:
   with self.subTest(flag=flag):
    self.call('start');self.assertTrue(self.call('check',1,**{flag:True})['move_on'])
    self.call('dispose',2,disposition='recovery_evidence_queued',reason=flag,evidence='actual.json',transmission_started=False)
    self.d['lead_key']+='x'
 def test_repeated_action_without_progress(self):
  self.call('start');self.assertFalse(self.call('check',1,action_key='same',progress=False)['move_on'])
  self.assertTrue(self.call('check',2,action_key='same',progress=False)['move_on'])
 def test_new_evidence_is_progress_not_more_time(self):
  self.call('start');self.call('check',1,action_key='same',progress=True,evidence='fact1.json',progress_key='date')
  self.assertFalse(self.call('check',2,action_key='same',progress=True,evidence='fact2.json',progress_key='pay')['move_on'])
  self.assertTrue(self.call('check',21,progress=True,evidence='fact3.json',progress_key='route')['move_on'])
 def test_must_dispose_then_continue(self):
  self.call('start')
  with self.assertRaises(ValueError):self.call('start',lead_key='next')
  self.call('dispose',2,disposition='skipped',reason='male-only original',evidence='brief.json',transmission_started=False)
  self.assertTrue(self.call('start',3,lead_key='next')['ok'])
 def test_complex_brief_queues_not_rejects(self):
  self.call('start');self.call('classify',10,classification='needs_more_evidence')
  x=self.call('dispose',11,disposition='recovery_evidence_queued',reason='image incomplete',evidence='image.png',transmission_started=False)
  self.assertEqual(x['lead']['disposition'],'recovery_evidence_queued')
 def test_uncertain_never_becomes_skipped_or_missing(self):
  self.call('start')
  with self.assertRaises(ValueError):self.call('dispose',disposition='required_materials_missing',reason='x',evidence='x',transmission_started=True)
  x=self.call('dispose',disposition='recovery_evidence_queued',reason='uncertain',evidence='x',transmission_started=True)
  self.assertTrue(x['lead']['receipt_reconciliation_required'])
 def test_send_requires_current_run_real_receipt(self):
  self.call('start')
  with self.assertRaises(ValueError):self.call('dispose',disposition='enquiry_verified_sent',reason='assertion',evidence='x')
  row={'run_id':'r','source_url':self.d['source_url'],'status':'verified_enquiry','receipt':'comment123','evidence':'sent.png'}
  self.put('campaign-receipts.json',{'key':row});self.assertEqual(self.call('dispose',disposition='enquiry_verified_sent',reason='published',evidence='sent.png')['next_action'],'next_lead')
 def test_same_evidence_cannot_fake_progress(self):
  self.call('start');self.call('check',1,action_key='same',progress=True,evidence='fact.json',progress_key='date')
  self.assertTrue(self.call('check',2,action_key='same',progress=True,evidence='fact.json',progress_key='date')['move_on'])
 def test_post_only_receipt_is_recognized(self):
  self.call('start');self.put('campaign-step-receipts.json',{'k':{'run_id':'r','post_url':self.d['source_url'],'status':'verified','receipt':'c123','evidence':'sent.png'}})
  self.assertEqual(self.call('dispose',disposition='enquiry_verified_sent',reason='published',evidence='sent.png')['next_action'],'next_lead')
 def test_inflight_not_aborted_by_budget(self):
  self.call('start');self.assertEqual(self.call('check',30,inflight_send=True)['next_action'],'finish_inflight_receipt_then_dispose')
 def reviewed_lead(self):
  self.call('start');self.call('classify',5,classification='promising')
  self.call('review-wait',30,review_evidence='exact-packet.json')
  self.call('review-result',330,decision='approved',review_evidence='fresh-specific-review.json')
  for n in ['hourly-state.json','social-collector-state.json']:
   self.put(n,{'active':True,'run_id':'r','thread_id':'t','deadline':(self.now+timedelta(seconds=900)).isoformat()})
 def action(self,sec=331,**kw):
  return self.call('action-start',sec,live_checks={k:True for k in ['open_status','requested_route','eligibility','assets','dedupe']},live_evidence='live-original.json',**kw)
 def test_external_approval_wait_does_not_exhaust_screening(self):
  self.call('start');self.call('classify',5,classification='promising');self.call('review-wait',30,review_evidence='packet.json')
  waiting=self.call('check',329);self.assertFalse(waiting['move_on']);self.assertEqual(waiting['next_action'],'await_review_or_queue_known_unsent')
  approved=self.call('review-result',330,decision='approved',review_evidence='current-review.json');self.assertEqual(approved['lead']['review_wait_seconds'],300)
  self.assertFalse(self.call('check',424)['move_on']);self.assertTrue(self.call('check',425)['move_on'])
 def test_approved_action_has_separate_single_bounded_attempt(self):
  self.reviewed_lead();a=self.action();self.assertEqual(a['lead']['phase'],'action');self.assertFalse(a['permission_granted'])
  b=self.action(390);self.assertEqual(a['lead']['deadline'],b['lead']['deadline'])
  self.assertEqual(self.call('check',452,inflight_send=True)['next_action'],'finish_inflight_receipt_then_dispose')
 def test_approval_cannot_extend_overall_cutoff_or_bypass_no_progress(self):
  self.reviewed_lead()
  with self.assertRaisesRegex(ValueError,'insufficient_run_time'):self.action(800)
  self.call('check',331,login_wall=True)
  with self.assertRaisesRegex(ValueError,'no_progress'):self.action(332)
 def test_disposed_known_unsent_is_not_reopened_by_approval(self):
  self.reviewed_lead();self.call('dispose',331,disposition='recovery_evidence_queued',reason='queued known-unsent',evidence='packet.json',transmission_started=False)
  with self.assertRaisesRegex(ValueError,'active_undisposed'):self.action(332)

if __name__=='__main__':unittest.main(verbosity=2)
