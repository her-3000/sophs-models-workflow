"""One-use, owner-evidenced manual lease checks; called under hourly-state.lock.

No grant creation, UI, network, hourly checkpoint or cycle mutation. A coordinator
must record the exact human authorization before supplying a one-time token.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone

FILE = 'manual-collector-authorizations.json'
THREAD = 'fixture-casting-thread'
SCOPE = 'readonly_sophie_pass_and_reviewed_native_summary'


def stamp(value):
    value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        raise ValueError('manual_aware_timestamp_required')
    return value


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(root, data, lease, state, releasing=False, now=None):
    """Validate without writes. Caller saves consumed status before acquisition."""
    now = now or datetime.now(timezone.utc)
    rid = data.get('run_id', '')
    if (data.get('thread_id') != THREAD or not rid.startswith('facebook:manual:')
            or data.get('platform') != 'facebook'):
        raise ValueError('manual_exact_owner_run_platform_required')
    grants = json.loads((root / FILE).read_text())
    grant = grants['grants'].get(rid, {})
    token = data.get('manual_authorization_token', '')
    if (not token or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(),
                                             grant.get('token_sha256', ''))
            or grant.get('thread_id') != THREAD or grant.get('scope') != SCOPE):
        raise ValueError('manual_authorization_mismatch')
    evidence = grant.get('authorization_evidence', '')
    if (not evidence or not (root / evidence).is_file()
            or not grant.get('human_authorization_source')):
        raise ValueError('manual_human_authorization_evidence_required')
    # Both protected records must remain byte-identical throughout the exception.
    for name, field in [('work-cycle-state.json', 'cycle_sha256'),
                        ('hourly-state.json', 'hourly_sha256')]:
        if digest(root / name) != grant.get(field):
            raise ValueError('manual_protected_state_changed')
    cycle = json.loads((root / 'work-cycle-state.json').read_text())
    if (cycle.get('phase') != 'sophie_ready' or cycle.get('next_owner') != THREAD
            or cycle.get('dispatch') is not None):
        raise ValueError('manual_future_reservation_required')
    start, expiry = stamp(grant['not_before']), stamp(grant['expires_at'])
    if not 0 < (expiry - start).total_seconds() <= 1800:
        raise ValueError('manual_maximum_30_minutes')
    if releasing:
        if (not lease.get('active') or not lease.get('manual_exception')
                or lease.get('run_id') != rid or lease.get('thread_id') != THREAD
                or grant.get('status') != 'consumed'):
            raise ValueError('manual_own_active_lease_required')
        checkpoint = json.loads((root / data['checkpoint']).read_text())
        if (checkpoint.get('run_id') != rid or checkpoint.get('thread_id') != THREAD
                or checkpoint.get('inflight_send') is not False
                or checkpoint.get('receipts_reconciled') is not True):
            raise ValueError('manual_terminal_checkpoint_required')
    else:
        if state.get('active') is not False or lease.get('active') is not False:
            raise ValueError('manual_guards_not_idle')
        if grant.get('status') != 'unused':
            raise ValueError('manual_token_already_used')
        deadline = stamp(data['deadline'])
        if not start <= now < deadline <= expiry or deadline > stamp(cycle['not_before']):
            raise ValueError('manual_invalid_window')
    return grants, grant
