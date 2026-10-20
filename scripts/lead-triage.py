#!/usr/bin/env python3
"""File-only lead budgets/dispositions. No UI, network, sending or guard mutation."""
import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
FILE = 'lead-triage-state.json'
OUTCOMES = {'enquiry_verified_sent', 'required_materials_missing', 'skipped', 'recovery_evidence_queued'}


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def stamp(value):
    at = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not at.tzinfo:
        raise ValueError('aware_time_required')
    return at


def source(value):
    p = urlsplit(value)
    if p.scheme != 'https' or p.hostname not in {'facebook.com', 'www.facebook.com', 'instagram.com', 'www.instagram.com'}:
        raise ValueError('public_original_source_required')
    return 'https://' + p.hostname.removeprefix('www.') + p.path.rstrip('/')


def save(path, data):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name+'.')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def apply(root, command, data, now):
    if command == 'view':
        return read(root / FILE, {'runs': {}})
    rid, thread, key = (data.get(k) for k in ('run_id', 'thread_id', 'lead_key'))
    if not all((rid, thread, key)):
        raise ValueError('run_thread_lead_required')
    for name in ('hourly-state.json', 'social-collector-state.json'):
        g = read(root / name, {})
        if g.get('active') is not True or g.get('run_id') != rid or g.get('thread_id') != thread:
            raise ValueError('active_matching_owner_required')
    journal = read(root / FILE, {'schema_version': 1, 'runs': {}})
    run = journal['runs'].setdefault(rid, {'leads': {}, 'active_lead': None})
    lead = run['leads'].get(key)
    if command == 'start':
        if run['active_lead'] not in (None, key):
            raise ValueError('dispose_current_lead_before_next')
        if lead:
            return {'ok': True, 'lead': lead, 'idempotent': True, 'send_executed': False}
        if data.get('priority') not in {'fresh_paid', 'fresh_unknown', 'historical'}:
            raise ValueError('explicit_priority_required')
        lead = {'source_url': source(data['source_url']), 'priority': data['priority'],
                'started_at': now.isoformat(), 'phase_started_at': now.isoformat(), 'phase': 'triage',
                'deadline': (now+timedelta(seconds=20)).isoformat(), 'classification': None,
                'disposition': None, 'last_action': None, 'last_progress': None, 'move_on_reason': None}
        run['leads'][key] = lead; run['active_lead'] = key
    else:
        if not lead or run['active_lead'] != key or lead['disposition']:
            raise ValueError('active_undisposed_lead_required')
        if now < stamp(lead['phase_started_at']):
            raise ValueError('time_before_phase')
        if command == 'classify':
            choice = data.get('classification')
            if choice not in {'promising', 'skip', 'needs_more_evidence'}:
                raise ValueError('explicit_triage_classification_required')
            if lead['classification'] is not None or lead['move_on_reason']:
                raise ValueError('classification_already_recorded_no_clock_reset')
            lead['classification'] = choice
            if choice == 'promising':
                lead.update(phase='brief', phase_started_at=now.isoformat(), deadline=(now+timedelta(minutes=2)).isoformat())
            else:
                lead['move_on_reason'] = 'triage_'+choice
        elif command == 'check':
            reason = lead['move_on_reason']
            if not lead.get('review_wait_started_at') and now >= stamp(lead['deadline']):
                reason = lead['phase']+'_budget_elapsed'
            if data.get('login_wall'): reason = 'login_wall_no_auth_pursuit'
            if data.get('failed_reload'): reason = 'one_failed_reload'
            action = hashlib.sha256(data['action_key'].encode()).hexdigest() if data.get('action_key') else None
            new_progress = False
            if data.get('progress') is True:
                if not data.get('evidence') or not data.get('progress_key'): raise ValueError('distinct_progress_key_and_evidence_required')
                identity = hashlib.sha256((data['evidence']+'|'+data['progress_key']).encode()).hexdigest()
                new_progress = identity != (lead['last_progress'] or {}).get('identity')
                if new_progress:
                    lead['last_progress'] = {'at': now.isoformat(), 'evidence': data['evidence'], 'identity': identity}
            if action and action == lead['last_action'] and not new_progress:
                reason = 'repeated_action_without_new_progress'
            if action: lead['last_action'] = action
            lead['move_on_reason'] = reason
        elif command == 'review-wait':
            if lead.get('review_wait_started_at'):
                return {'ok': True, 'lead': lead, 'idempotent': True, 'send_executed': False, 'permission_granted': False}
            if (lead['phase'] != 'brief' or lead['move_on_reason'] or lead.get('review_decision')
                    or now >= stamp(lead['deadline']) or not data.get('review_evidence')):
                raise ValueError('completed_screening_and_review_request_required')
            lead.update(screening_completed_at=now.isoformat(), review_wait_started_at=now.isoformat(),
                        review_request_evidence=data['review_evidence'])
        elif command == 'review-result':
            if lead.get('review_decision'):
                raise ValueError('review_already_recorded_no_clock_reset')
            if not lead.get('review_wait_started_at') or data.get('decision') not in {'approved','denied'} or not data.get('review_evidence'):
                raise ValueError('specific_review_result_required')
            waited = (now-stamp(lead['review_wait_started_at'])).total_seconds()
            if waited < 0: raise ValueError('time_before_review_wait')
            lead.update(review_wait_seconds=waited, review_completed_at=now.isoformat(),
                        review_decision=data['decision'], review_evidence=data['review_evidence'],
                        deadline=(stamp(lead['deadline'])+timedelta(seconds=waited)).isoformat())
            lead.pop('review_wait_started_at')
            if data['decision'] == 'denied': lead['move_on_reason'] = 'review_denied_queue_only'
        elif command == 'action-start':
            if lead.get('action_attempt_started_at'):
                return {'ok': True, 'lead': lead, 'idempotent': True, 'send_executed': False, 'permission_granted': False}
            checks = data.get('live_checks', {})
            if (lead.get('review_decision') != 'approved' or not data.get('live_evidence')
                    or not all(checks.get(k) is True for k in ('open_status','requested_route','eligibility','assets','dedupe'))):
                raise ValueError('fresh_specific_review_and_live_checks_required')
            if lead['move_on_reason'] not in (None,'brief_budget_elapsed'):
                raise ValueError('no_progress_blocker_requires_queue')
            seconds = data.get('attempt_seconds', 120)
            if type(seconds) is not int or not 1 <= seconds <= 300:
                raise ValueError('bounded_attempt_seconds_required')
            deadlines = [stamp(read(root/name, {})['deadline']) for name in ('hourly-state.json','social-collector-state.json')]
            if now+timedelta(seconds=seconds+60) > min(deadlines):
                raise ValueError('insufficient_run_time_queue_known_unsent')
            lead.update(screening_deadline=lead['deadline'], phase='action', phase_started_at=now.isoformat(),
                        deadline=(now+timedelta(seconds=seconds)).isoformat(), action_attempt_started_at=now.isoformat(),
                        live_checks_evidence=data['live_evidence'], move_on_reason=None, last_action=None)
        elif command == 'dispose':
            outcome = data.get('disposition')
            if outcome not in OUTCOMES or not data.get('reason') or not data.get('evidence'):
                raise ValueError('explicit_disposition_reason_evidence_required')
            if outcome == 'enquiry_verified_sent':
                verified = []
                for file in ('campaign-receipts.json', 'campaign-step-receipts.json'):
                    for row in read(root / file, {}).values():
                        same = False
                        for field in ('source_url', 'post_url'):
                            try: same = same or source(row.get(field, '')) == lead['source_url']
                            except (ValueError, TypeError): pass
                        if same and row.get('run_id') == rid and row.get('status') in {'verified', 'verified_enquiry'} and row.get('receipt') and row.get('evidence'):
                            verified.append(row['receipt'])
                if not verified: raise ValueError('matching_current_run_verified_receipt_required')
                lead['receipts'] = sorted(set(verified))
            elif data.get('transmission_started') is not False:
                if outcome != 'recovery_evidence_queued':
                    raise ValueError('uncertain_transmission_must_queue_reconciliation')
                lead['receipt_reconciliation_required'] = True
            lead.update(disposition=outcome, reason=data['reason'], evidence=data['evidence'], disposed_at=now.isoformat())
            run['active_lead'] = None
        else:
            raise ValueError('unsupported_command')
    save(root / FILE, journal)
    move = bool(lead['move_on_reason'] or lead['disposition'])
    return {'ok': True, 'lead': lead, 'move_on': move,
            'next_action': 'finish_inflight_receipt_then_dispose' if move and data.get('inflight_send') else 'dispose_and_next_lead' if move and not lead['disposition'] else 'next_lead' if lead['disposition'] else 'await_review_or_queue_known_unsent' if lead.get('review_wait_started_at') else 'continue_within_budget',
            'send_executed': False, 'permission_granted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['start', 'classify', 'check', 'review-wait', 'review-result', 'action-start', 'dispose', 'view'])
    p.add_argument('--root', type=Path, default=ROOT); p.add_argument('--data', default='{}'); p.add_argument('--now')
    a = p.parse_args()
    try:
        now = stamp(a.now) if a.now else datetime.now(timezone.utc)
        with (a.root/'hourly-state.lock').open('r') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            print(json.dumps(apply(a.root, a.command, json.loads(a.data), now), ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({'ok': False, 'reason': str(e), 'send_executed': False})); raise SystemExit(2)


if __name__ == '__main__': main()
