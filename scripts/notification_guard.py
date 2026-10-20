#!/usr/bin/env python3
"""File-only notification admission and receipts. Never contacts LINE."""
import argparse, fcntl, hashlib, json, os, tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

GROUP='fixture-internal-group'
TZ=ZoneInfo('Asia/Bangkok')

def apply(root, command, data, now=None):
    root=Path(root); now=now or datetime.now(TZ); day=now.astimezone(TZ).date().isoformat()
    path=root/'notification-state.json'
    with (root/'notification-state.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=json.loads(path.read_text()) if path.exists() else {'quota':{},'digests':{}}
        key=data.get('key')
        if command=='view': return state
        if command=='quota-claim':
            if day in state['quota']: return {'check':False,'record':state['quota'][day]}
            state['quota'][day]={'status':'intent','at':now.isoformat()}
            result={'check':True}
        elif command=='quota-result':
            q=state['quota'].get(day)
            if not q or q['status']!='intent': raise ValueError('daily_quota_claim_required')
            q.update(status='observed' if 'limited' in data and 'totalUsage' in data else 'failed', evidence=data, at=now.isoformat())
            result=q
        elif command=='prepare':
            if key in state['digests']: return {'send':False,'record':state['digests'][key]}
            text=data['text']
            if len(text)>4500 or not text.rstrip().endswith('- Mia') or 'upwork' in text.lower(): raise ValueError('invalid_summary_format')
            if data.get('destination')!=GROUP: raise ValueError('wrong_destination')
            if data.get('api_status')=='uncertain': raise ValueError('reconcile_uncertain_api_first')
            if data.get('api_status') not in ['failed_429','quota_insufficient']: raise ValueError('fallback_reason_required')
            checks=['c_account_verified','group_context_verified','current_rules_allowed','human_messages_reconciled','own_lease_verified']
            if not all(data.get(k) is True for k in checks): raise ValueError('live_checks_required')
            if not data.get('review_evidence') or not data.get('context_evidence'): raise ValueError('evidence_required')
            record={'status':'intent','at':now.isoformat(),'sha256':hashlib.sha256(text.encode()).hexdigest(),'text':text,'destination':GROUP,'route':'native_C','scope':data,'consolidates':data.get('consolidates',[])}
            state['digests'][key]=record; result={'send':True,'record':record}
        elif command=='result':
            record=state['digests'].get(key)
            if not record or record['status']!='intent': raise ValueError('one_pending_intent_required')
            status=data['status']
            if status not in ['visible_outgoing','failed_known_unsent','uncertain']: raise ValueError('invalid_result')
            if status=='visible_outgoing' and (not data.get('full_text_readback') or not data.get('evidence')): raise ValueError('outgoing_readback_required')
            record.update(status=status,result=data,result_at=now.isoformat()); result=record
        else: raise ValueError('unknown_command')
        fd,tmp=tempfile.mkstemp(dir=root,prefix='notification-state.')
        try:
            with os.fdopen(fd,'w') as stream: json.dump(state,stream,ensure_ascii=False,indent=2); stream.write('\n');stream.flush();os.fsync(stream.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
        return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['view','quota-claim','quota-result','prepare','result']);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--data',type=Path)
    a=p.parse_args()
    try: print(json.dumps(apply(a.root,a.command,json.loads(a.data.read_text()) if a.data else {}),ensure_ascii=False,indent=2))
    except (ValueError,KeyError) as e: p.exit(1,str(e)+'\n')
