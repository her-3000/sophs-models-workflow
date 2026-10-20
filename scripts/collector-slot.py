#!/usr/bin/env python3
"""Atomic slot admission and one-attempt digest journal. No network, UI or sleep."""
import argparse, fcntl, hashlib, json, os, tempfile, tomllib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo('Asia/Bangkok')
DESTINATION = 'fixture-internal-group'

def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def write(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name+'.')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)

def next_slot(config_path, after):
    """Only interpret the actually deployed daily BYHOUR schedule; fail closed otherwise."""
    cfg = tomllib.loads(Path(config_path).read_text())
    rule = dict(part.split('=', 1) for part in cfg['rrule'].removeprefix('RRULE:').split(';'))
    if cfg.get('status') != 'ACTIVE': return {'ok': False, 'reason': 'inactive'}
    if set(rule) != {'FREQ','BYHOUR','BYMINUTE','BYSECOND'} or rule['FREQ'] != 'DAILY':
        return {'ok': False, 'reason': 'unsupported_schedule'}
    if after.tzinfo is None: raise ValueError('aware time required')
    hours = [int(v) for v in rule['BYHOUR'].split(',')]
    minutes = [int(v) for v in rule['BYMINUTE'].split(',')]
    seconds = [int(v) for v in rule['BYSECOND'].split(',')]
    local = after.astimezone(TZ)
    candidates = [ (local + timedelta(days=d)).replace(hour=h, minute=m, second=s, microsecond=0)
        for d in (0,1) for h in hours for m in minutes for s in seconds ]
    return {'ok': True, 'next_start': min(t for t in candidates if t > local).isoformat(),
            'basis': 'persisted_config_not_scheduler_next_run', 'rrule': cfg['rrule']}

def execute(root, command, data, now=None):
    now = now or datetime.now(TZ)
    if now.tzinfo is None: raise ValueError('aware time required')
    slot = datetime.fromisoformat(data['slot_at']).astimezone(TZ)
    if datetime.fromisoformat(data['slot_at']).tzinfo is None: raise ValueError('aware slot required')
    if slot.minute or slot.second or slot.microsecond or not 7 <= slot.hour <= 22:
        return {'ok': False, 'reason': 'invalid_slot'}
    if data.get('platform') != 'instagram' or not data.get('dispatch_id'):
        return {'ok': False, 'reason': 'invalid_owner'}
    key = 'instagram:' + slot.strftime('%Y-%m-%d:%H00')
    root.mkdir(parents=True, exist_ok=True)
    with (root/'hourly-state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        journal_path = root/'collector-slots.json'
        journal = read(journal_path, {})
        entry = journal.get(key)
        report = root/'reviews'/f'{slot:%Y-%m-%d-%H00}-instagram.md'
        # Legacy reports conservatively close a slot; never reopen an already reported run.
        if (entry is None or command == 'acquire') and report.exists():
            return {'ok': False, 'reason': 'existing_slot_report'}
        if entry and entry['dispatch_id'] != data['dispatch_id']:
            return {'ok': False, 'reason': 'duplicate_dispatch'}
        if entry and entry.get('terminal'):
            return {'ok': False, 'reason': 'terminal_slot'}
        wait_until = slot + timedelta(minutes=35)
        deadline = slot + timedelta(minutes=55)
        if command == 'acquire':
            if now < slot - timedelta(seconds=90):
                return {'ok': False, 'reason': 'future_slot'}
            if entry and entry['state'] != 'waiting':
                return {'ok': False, 'reason': 'already_admitted_or_finished'}
            entry = entry or {'dispatch_id': data['dispatch_id'], 'run_id': key, 'state': 'waiting',
                'slot_at': slot.isoformat(), 'deadline': deadline.isoformat(), 'report_state': 'not_sent'}
            if now >= wait_until:
                entry['state'] = 'deferred_ready'; result = {'ok': True, 'action': 'terminal_defer'}
            else:
                lease_path = root/'social-collector-state.json'
                lease = read(lease_path, None)
                state = read(root/'hourly-state.json', {})
                if lease is None or lease.get('active') or state.get('active'):
                    result = {'ok': True, 'action': 'wait', 'retry_after_seconds': 30,
                              'reason': 'lease_missing' if lease is None else 'collector_busy'}
                else:
                    write(lease_path, {'active': True, 'run_id': key, 'platform': 'instagram',
                        'dispatch_id': data['dispatch_id'], 'thread_id': data.get('thread_id'),
                        'started_at_bangkok': now.isoformat(), 'deadline': deadline.isoformat()})
                    entry['state'] = 'acquired'
                    result = {'ok': True, 'action': 'collect', 'run_id': key, 'deadline': deadline.isoformat()}
        else:
            if not entry: return {'ok': False, 'reason': 'unknown_slot'}
            if command == 'report-intent':
                if entry['state'] not in ('acquired','deferred_ready') or entry['report_state'] != 'not_sent':
                    return {'ok': False, 'reason': 'duplicate_or_early_report'}
                if data.get('recipient') != DESTINATION or not data.get('digest'):
                    return {'ok': False, 'reason': 'invalid_digest'}
                entry['report_state'] = 'intent'
                entry['digest_sha256'] = hashlib.sha256(data['digest'].encode()).hexdigest()
                result = {'ok': True, 'action': 'one_existing_authorized_send'}
            elif command == 'finish':
                if entry['report_state'] != 'intent' or data.get('report_status') not in ('accepted','uncertain','failed'):
                    return {'ok': False, 'reason': 'invalid_report_result'}
                if data['report_status'] == 'accepted' and not data.get('message_id'):
                    return {'ok': False, 'reason': 'message_id_required'}
                lease_path = root/'social-collector-state.json'; lease = read(lease_path, {})
                if entry['state'] == 'acquired':
                    if lease.get('run_id') != key or lease.get('dispatch_id') != data['dispatch_id']:
                        return {'ok': False, 'reason': 'lease_ownership_changed'}
                    lease.update(active=False, stopped_at_bangkok=now.isoformat()); write(lease_path, lease)
                entry.update(terminal=True, state='finished', report_state=data['report_status'],
                             message_id=data.get('message_id'), finished_at=now.isoformat())
                result = {'ok': True, 'action': 'finished'}
            else: raise ValueError(command)
        journal[key] = entry; write(journal_path, journal)
        return {**result, 'wait_until': wait_until.isoformat(), 'deadline': deadline.isoformat()}

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('command', choices=['acquire','report-intent','finish','next-slot'])
    p.add_argument('--data', default='{}'); p.add_argument('--root', type=Path, default=ROOT); p.add_argument('--config',type=Path)
    a=p.parse_args()
    result = next_slot(a.config, datetime.now(TZ)) if a.command == 'next-slot' else execute(a.root,a.command,json.loads(a.data))
    print(json.dumps(result)); raise SystemExit(0 if result['ok'] else 2)
