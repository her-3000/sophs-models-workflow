import unittest,tempfile
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from notification_guard import apply,GROUP
class GuardTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
  self.packet={'key':'2026-10-20:final','text':'Submitted: 3. Needs Sophie: current photos. Next: verify originals.\n- Mia','destination':GROUP,'api_status':'failed_429','c_account_verified':True,'group_context_verified':True,'current_rules_allowed':True,'human_messages_reconciled':True,'own_lease_verified':True,'review_evidence':'specific-review','context_evidence':'profile+members+history','consolidates':['19','20','21','22']}
 def tearDown(self): self.tmp.cleanup()
 def test_one_intent_and_no_replay(self):
  self.assertTrue(apply(self.root,'prepare',self.packet)['send'])
  self.assertFalse(apply(self.root,'prepare',self.packet)['send'])
 def test_uncertain_api_no_fallback(self):
  self.packet['api_status']='uncertain'
  with self.assertRaises(ValueError):apply(self.root,'prepare',self.packet)
 def test_checks(self):
  for key in ['c_account_verified','group_context_verified','current_rules_allowed','human_messages_reconciled','own_lease_verified']:
   d=dict(self.packet);d[key]=False
   with self.assertRaises(ValueError):apply(self.root,'prepare',d)
 def test_format(self):
  for text in ['Hi','Upwork\n- Mia','x'*4500+'\n- Mia']:
   d=dict(self.packet,text=text)
   with self.assertRaises(ValueError):apply(self.root,'prepare',d)
 def test_outgoing_requires_full_readback(self):
  apply(self.root,'prepare',self.packet)
  with self.assertRaises(ValueError):apply(self.root,'result',{'key':self.packet['key'],'status':'visible_outgoing'})
  result=apply(self.root,'result',{'key':self.packet['key'],'status':'visible_outgoing','full_text_readback':True,'evidence':'screenshot'})
  self.assertEqual(result['consolidates'],['19','20','21','22'])
  self.assertFalse(apply(self.root,'prepare',self.packet)['send'])
 def test_failure_and_uncertainty_never_blind_retry(self):
  for status in ['failed_known_unsent','uncertain']:
   d=dict(self.packet,key=status);apply(self.root,'prepare',d);apply(self.root,'result',{'key':status,'status':status})
   self.assertFalse(apply(self.root,'prepare',d)['send'])
 def test_quota_once_bangkok_day_even_failure(self):
  now=datetime(2026,10,20,23,tzinfo=ZoneInfo('Asia/Bangkok'))
  self.assertTrue(apply(self.root,'quota-claim',{},now)['check'])
  apply(self.root,'quota-result',{'error':'unavailable'},now)
  self.assertFalse(apply(self.root,'quota-claim',{},now)['check'])
  self.assertTrue(apply(self.root,'quota-claim',{},now.replace(day=21))['check'])
 def test_quota_evidence_preserved_no_invented_reset(self):
  apply(self.root,'quota-claim',{});apply(self.root,'quota-result',{'limited':300,'totalUsage':299,'reset':'unknown'})
  self.assertEqual(next(iter(apply(self.root,'view',{})['quota'].values()))['evidence']['reset'],'unknown')
if __name__=='__main__':unittest.main()
