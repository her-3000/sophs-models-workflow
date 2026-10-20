#!/usr/bin/env python3
"""File-only Sophie/Upwork handoff. Uses the existing desktop lock; never sends."""
import argparse
import fcntl
import json
import os
import tempfile
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
FILE = 'work-cycle-state.json'
SOPHIE = 'fixture-casting-thread'
UPWORK = 'fixture-secondary-thread'
TZ = ZoneInfo('Asia/Bangkok')
CONFIG = Path('config/automation.example.toml')

def read(root, name, default=None):
    p = root / name
    return json.loads(p.read_text()) if p.exists() else default

def save(root, value):
    fd, name = tempfile.mkstemp(dir=root, prefix=FILE + '.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, root / FILE)
    finally:
        if os.path.exists(name): os.unlink(name)

def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None: raise ValueError('aware_timestamp_required')
    return result

def idle(root):
    return all(read(root, n, {}).get('active') is False
               for n in ['hourly-state.json', 'social-collector-state.json'])

def in_hours(now, owner='upwork'):
    local = now.astimezone(TZ)
    return 7 <= local.hour <= 22 if owner == 'sophie' else 7 <= local.hour < 22

def upwork_window(now):
    config = tomllib.loads(CONFIG.read_text())
    rule = dict(part.split('=', 1) for part in config['rrule'].removeprefix('RRULE:').split(';'))
    if config.get('status') != 'ACTIVE' or set(rule) != {'FREQ','BYHOUR','BYMINUTE','BYSECOND'} or rule['FREQ'] != 'DAILY':
        raise ValueError('unsupported_or_inactive_sophie_schedule')
    local = now.astimezone(TZ)
    starts = [(local + timedelta(days=day)).replace(hour=int(hour), minute=int(minute), second=int(second), microsecond=0)
              for day in [0,1] for hour in rule['BYHOUR'].split(',')
              for minute in rule['BYMINUTE'].split(',') for second in rule['BYSECOND'].split(',')]
    start = min(value for value in starts if value > local)
    return {'next_sophie_start': start.isoformat(), 'upwork_cutoff': (start - timedelta(minutes=10)).isoformat()}

def admission(root, command, data, now=None):
    """Called inside hourly-checkpoint's existing lock, before any acquisition."""
    c = read(root, FILE)
    if not c or not c.get('enabled'): return None
    now = now or datetime.now(timezone.utc)
    # A casting begin followed by its matching lease-begin is one acquisition.
    if (c['phase'] == 'sophie_running' and data.get('run_id') == c.get('sophie_run_id')
            and data.get('thread_id') == SOPHIE and command == 'lease-begin'):
        return None
    owner = 'sophie' if data.get('thread_id') == SOPHIE else 'upwork' if data.get('thread_id') == UPWORK else None
    if not owner or c['phase'] != owner + '_ready':
        return {'ok': False, 'reason': 'cycle_reserved', 'phase': c['phase'], 'next_owner': c['next_owner']}
    if not in_hours(now, owner): return {'ok': False, 'reason': 'cycle_outside_hours'}
    if c.get('not_before') and now < stamp(c['not_before']):
        return {'ok': False, 'reason': 'cycle_not_due', 'not_before': c['not_before']}
    if owner == 'upwork' and (command != 'lease-begin' or data.get('cycle_id') != c['cycle_id']):
        return {'ok': False, 'reason': 'cycle_id_required'}
    if owner == 'upwork' and now >= stamp(c['upwork_cutoff']):
        return {'ok': False, 'reason': 'upwork_window_closed', 'next_sophie_start': c['next_sophie_start']}
    if owner == 'sophie' and (command != 'begin' or data.get('run_id') in c.get('completed_sophie_runs', [])):
        return {'ok': False, 'reason': 'cycle_duplicate_or_wrong_entrypoint'}
    return None

def admitted(root, command, data, now=None):
    c = read(root, FILE)
    if not c or not c.get('enabled'): return
    now = now or datetime.now(timezone.utc)
    if c['phase'] == 'sophie_ready' and command == 'begin':
        c.update(phase='sophie_running', cycle_id=data['run_id'], sophie_run_id=data['run_id'],
                 next_owner=SOPHIE, phase_started_at=now.isoformat(),
                 target_end=(now + timedelta(minutes=15)).isoformat(), dispatch=None)
    elif c['phase'] == 'upwork_ready' and command == 'lease-begin':
        c.update(phase='upwork_running', upwork_run_id=data['run_id'], next_owner=UPWORK,
                 phase_started_at=now.isoformat(), target_end=c['upwork_cutoff'], dispatch=None)
    else: return
    save(root, c)

def casting_complete(root, c, now, adopt_upwork=False):
    """Terminal report + digest outcome + postflight + explicit release, not LINE alone."""
    lease = read(root, 'social-collector-state.json', {})
    adopting = (adopt_upwork and lease.get('active') is True and lease.get('thread_id') == UPWORK
                and read(root, 'hourly-state.json', {}).get('active') is False)
    if not idle(root) and not adopting: raise ValueError('guards_active')
    state = read(root, 'hourly-state.json', {})
    if state.get('run_id') != c['sophie_run_id']: raise ValueError('casting_run_changed')
    report_path = (root / state.get('report', '')).resolve()
    if not report_path.is_relative_to(root.resolve()) or report_path.suffix != '.json':
        raise ValueError('owner_local_report_required')
    report = json.loads(report_path.read_text())
    if report.get('run_id') != c['sophie_run_id'] or not state.get('stopped_at_bangkok'):
        raise ValueError('terminal_checkpoint_required')
    if state.get('inflight_send') is not False or report.get('inflight_send') is not False:
        raise ValueError('inflight_or_unknown_send')
    events = read(root, 'collector-health-checkpoints.json', {}).get(c['sophie_run_id'], [])
    if not any(e.get('phase') == 'postflight' for e in events): raise ValueError('postflight_required')
    delivery = state.get('line_digest_status')
    if delivery not in ['api_accepted', 'accepted', 'failed', 'uncertain']:
        raise ValueError('digest_outcome_required')
    if delivery in ['api_accepted', 'accepted'] and not state.get('line_message_id'):
        raise ValueError('digest_receipt_required')
    if report.get('line_digest_status') != delivery: raise ValueError('digest_report_mismatch')
    c.setdefault('completed_sophie_runs', []).append(c['sophie_run_id'])
    c['completed_sophie_runs'] = c['completed_sophie_runs'][-100:]
    c.update(phase='upwork_ready', next_owner=UPWORK, not_before=now.isoformat(), dispatch=None,
             casting_completion={'run_id': c['sophie_run_id'], 'terminal_at': state['stopped_at_bangkok'],
                 'report': state['report'], 'digest_status': delivery, 'message_id': state.get('line_message_id'),
                 'delivery_issue_requires_notice': delivery in ['failed', 'uncertain'],
                 'full_client_readback': state.get('line_readback_verified') is True},
             phase_updated_at=now.isoformat())
    c.update(upwork_window(now))
    if not in_hours(now, 'upwork') or now >= stamp(c['upwork_cutoff']):
        c.update(phase='sophie_ready', next_owner=SOPHIE, not_before=c['next_sophie_start'],
                 skipped_upwork_window=True)
    return c

def released(root, data, now=None):
    """Casting release hook. Missing completion evidence leaves phase reserved, safely."""
    c = read(root, FILE)
    if not c or c.get('phase') != 'sophie_running' or data.get('run_id') != c.get('sophie_run_id'): return
    try: c = casting_complete(root, c, now or datetime.now(timezone.utc))
    except (ValueError, OSError) as error:
        c['handoff_blocker'] = str(error)
    save(root, c)

def apply(root, command, data, now=None):
    now = now or datetime.now(timezone.utc)
    with (root / 'hourly-state.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        c = read(root, FILE)
        if command == 'view': return {'ok': True, 'state': c, 'guards_idle': idle(root)}
        if command == 'init':
            if c: return {'ok': True, 'state': c, 'existing': True}
            state = read(root, 'hourly-state.json', {})
            if state.get('run_id') != data['sophie_run_id']: raise ValueError('bootstrap_run_changed')
            c = {'schema_version': 1, 'enabled': True, 'cycle_id': data['sophie_run_id'],
                 'sophie_run_id': data['sophie_run_id'], 'phase': 'sophie_running', 'next_owner': SOPHIE,
                 'phase_started_at': state.get('started_at_bangkok'), 'grandfather_current_run': True,
                 'budgets_minutes': {'sophie': 15, 'upwork': 'available_until_next_sophie_minus_10'}, 'completed_sophie_runs': []}
            lease = read(root, 'social-collector-state.json', {})
            if lease.get('active') is True and lease.get('thread_id') == UPWORK and state.get('active') is False:
                c = casting_complete(root, c, now, adopt_upwork=True)
                started = stamp(lease['started_at_bangkok'])
                c.update(phase='upwork_running', upwork_run_id=lease['run_id'],
                         phase_started_at=started.isoformat(), target_end=c['upwork_cutoff'],
                         adopted_existing_upwork_lease=True)
            elif idle(root): c = casting_complete(root, c, now)
        else:
            if not c: raise ValueError('cycle_not_initialized')
            if data.get('cycle_id') != c['cycle_id']: raise ValueError('cycle_identity_changed')
            if command == 'complete-sophie':
                if c['phase'] != 'sophie_running': return {'ok': True, 'state': c, 'existing': True}
                c = casting_complete(root, c, now)
            elif command == 'complete-upwork':
                if (c['phase'] == 'sophie_ready' and data.get('run_id') == c.get('upwork_run_id')
                        and c.get('upwork_checkpoint')):
                    return {'ok': True, 'state': c, 'existing': True}
                if c['phase'] != 'upwork_running' or data.get('run_id') != c.get('upwork_run_id'):
                    raise ValueError('upwork_owner_changed')
                if not idle(root): raise ValueError('guards_active')
                lease = read(root, 'social-collector-state.json', {})
                if lease.get('run_id') != c['upwork_run_id'] or lease.get('thread_id') != UPWORK:
                    raise ValueError('upwork_release_not_verified')
                if data.get('inflight_send') is not False or data.get('receipts_reconciled') is not True:
                    raise ValueError('receipt_reconciliation_required')
                proof = Path(data.get('checkpoint', '')).resolve()
                if not proof.is_file() or proof.suffix != '.json': raise ValueError('checkpoint_required')
                facts = json.loads(proof.read_text())
                if (facts.get('run_id') != c['upwork_run_id'] or facts.get('inflight_send') is not False
                        or facts.get('receipts_reconciled') is not True): raise ValueError('checkpoint_mismatch')
                c.update(phase='sophie_ready', next_owner=SOPHIE, dispatch=None,
                         not_before=c['next_sophie_start'], upwork_checkpoint=str(proof), phase_updated_at=now.isoformat())
            elif command == 'refresh-window':
                if c['phase'] not in ['upwork_ready','upwork_running']: raise ValueError('not_upwork_phase')
                c.update(upwork_window(now))
                c['budgets_minutes']['upwork'] = 'available_until_next_sophie_minus_10'
                if c['phase'] == 'upwork_running': c['target_end'] = c['upwork_cutoff']
                c['phase_updated_at'] = now.isoformat()
            elif command == 'claim-dispatch':
                if not c['phase'].endswith('_ready') or not idle(root): raise ValueError('not_ready_or_busy')
                # Reserve the next scheduled Sophie phase even outside dispatch hours.
                # This performs no message or lease acquisition, including on restart.
                if c['phase'] == 'upwork_ready' and (not in_hours(now, 'upwork') or now >= stamp(c['upwork_cutoff'])):
                    c.update(phase='sophie_ready', next_owner=SOPHIE, not_before=c['next_sophie_start'], dispatch=None, skipped_upwork_window=True)
                    save(root,c)
                    return {'ok': True, 'send_message': False, 'state': c, 'reason': 'upwork_window_closed'}
                if not in_hours(now, 'sophie' if c['next_owner'] == SOPHIE else 'upwork'): raise ValueError('outside_hours')
                if c.get('not_before') and now < stamp(c['not_before']): raise ValueError('not_due')
                if c.get('dispatch'): return {'ok': True, 'send_message': False, 'state': c}
                c['dispatch'] = {'id': c['cycle_id'] + ':' + c['phase'], 'status': 'intent',
                                 'target': c['next_owner'], 'claimed_at': now.isoformat()}
                save(root, c)
                return {'ok': True, 'send_message': True, 'target': c['next_owner'],
                        'dispatch_id': c['dispatch']['id'], 'state': c}
            elif command == 'dispatch-result':
                if not c.get('dispatch') or data.get('dispatch_id') != c['dispatch']['id']:
                    raise ValueError('dispatch_claim_required')
                if c['dispatch']['status'] != 'intent': return {'ok': True, 'state': c, 'existing': True}
                if data.get('status') not in ['accepted', 'failed', 'uncertain']: raise ValueError('invalid_dispatch_result')
                c['dispatch'].update(status=data['status'], result_at=now.isoformat())
            else: raise ValueError('unknown_command')
        save(root, c)
        return {'ok': True, 'state': c, 'send_executed': False}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'view', 'complete-sophie', 'complete-upwork', 'refresh-window', 'claim-dispatch', 'dispatch-result'])
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--data', default='{}')
    args = parser.parse_args()
    try: result = apply(args.root, args.command, json.loads(args.data))
    except (ValueError, OSError, KeyError, TypeError) as error: result = {'ok': False, 'reason': str(error), 'send_executed': False}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['ok'] else 2)
