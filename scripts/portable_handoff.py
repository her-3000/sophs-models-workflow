#!/usr/bin/env python3
"""Private, file-only preflight and outcome journal. Never operates UI or sends."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote, urlsplit
import uuid

REPO = Path(__file__).resolve().parents[1]
SCHEMA = REPO / 'schemas/private-handoff-v1.json'
COMMUNICATION_SKILL = REPO / '.agents/skills/sophs-casting-communication/SKILL.md'
STATE = 'handoff-state.json'
SOURCES = {'campaigns', 'steps', 'holds', 'attempts', 'sop', 'profile',
           'asset', 'contact', 'template', 'preferences', 'checkpoint'}
STATUSES = {'known_unsent', 'completed', 'held', 'uncertain', 'intent'}
PROJECTION_LISTS = ['campaigns', 'campaign_steps', 'work_queue_items', 'pending_candidates',
                    'legacy_pending_leads', 'legacy_hourly_held_candidates', 'triage_dispositions',
                    'post_dispositions', 'organiser_policy_holds', 'protected_uncertain_campaign_keys',
                    'reply_watches', 'notification_protections', 'source_queue',
                    'reply_relay_protections']


class Hold(ValueError):
    """Stable, non-sensitive error code suitable for harness logs."""


def require(condition, code):
    if not condition:
        raise Hold(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 'aware_timestamp_required')
        return result
    except (AttributeError, TypeError, ValueError):
        raise Hold('aware_timestamp_required') from None


def fresh(value, now, seconds):
    require(0 <= (now - stamp(value)).total_seconds() <= seconds, 'stale_or_future_evidence')


def read_bytes(path):
    require(path.is_file() and path.stat().st_size <= 10_000_000, 'missing_or_oversize_file')
    return path.read_bytes()


def read_json(path):
    try:
        return json.loads(read_bytes(path))
    except (UnicodeError, json.JSONDecodeError):
        raise Hold('invalid_json') from None


def local_file(root, name):
    require(isinstance(name, str) and name and not Path(name).is_absolute(), 'relative_private_path_required')
    parts = Path(name).parts
    require('..' not in parts and not any((root / Path(*parts[:i])).is_symlink()
                for i in range(1, len(parts) + 1)), 'unsafe_private_path')
    path = (root / name).resolve()
    require(path.is_relative_to(root), 'private_path_escape')
    return path


def private_root(path, expected_host):
    # Host verification precedes every private read, including lock access.
    require(expected_host and expected_host == os.uname().nodename, 'host_not_verified')
    p = Path(path)
    require(p.is_absolute() and p.is_dir() and not p.is_symlink(), 'private_root_not_verified')
    root = p.resolve()
    require(root == p, 'private_root_symlink_or_noncanonical')
    require(not root.is_relative_to(REPO) and not REPO.is_relative_to(root), 'private_root_overlaps_code')
    require(root != Path('/'), 'private_root_not_verified')
    return root


def shape(value, rule, schema):
    """Validate the bounded subset used by the checked-in v1 JSON Schema."""
    if '$ref' in rule:
        target = schema
        for part in rule['$ref'].removeprefix('#/').split('/'):
            target = target[part]
        return shape(value, target, schema)
    if 'const' in rule:
        require(type(value) is type(rule['const']) and value == rule['const'], 'schema_constant_mismatch')
    if 'enum' in rule:
        require(value in rule['enum'], 'unknown_status')
    if 'type' in rule:
        kinds = rule['type'] if isinstance(rule['type'], list) else [rule['type']]
        types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'boolean': bool, 'null': type(None)}
        require(any(type(value) is types[k] for k in kinds), 'schema_type_mismatch')
    if isinstance(value, dict):
        require(set(rule.get('required', [])) <= value.keys(), 'schema_missing_field')
        for key, child in value.items():
            properties = rule.get('properties', {})
            if key in properties:
                shape(child, properties[key], schema)
            elif isinstance(rule.get('additionalProperties'), dict):
                shape(child, rule['additionalProperties'], schema)
    if isinstance(value, list):
        require(len(value) >= rule.get('minItems', 0), 'schema_missing_items')
        if 'items' in rule:
            for child in value:
                shape(child, rule['items'], schema)
    if isinstance(value, str):
        require(len(value.strip()) >= rule.get('minLength', 0), 'schema_empty_field')
        if 'pattern' in rule:
            require(re.fullmatch(rule['pattern'], value) is not None, 'invalid_digest')
        if rule.get('format') == 'date-time':
            stamp(value)
    if type(value) is int:
        require(value >= rule.get('minimum', value), 'schema_number_out_of_range')


def identity(url):
    try:
        p = urlsplit(url)
        require(p.scheme == 'https' and p.hostname and not p.username and not p.password,
                'invalid_source_url')
        require(not p.port or p.port == 443, 'invalid_source_url')
        host = p.hostname.lower().removeprefix('www.').removeprefix('m.')
        return host + p.path.replace('/permalink/', '/posts/').rstrip('/')
    except (TypeError, ValueError):
        raise Hold('invalid_source_url') from None


def keys(row):
    return {row['campaign_key'], identity(row['source_url']), *row['aliases']}


def communication_language(brief, recipient, contextual=None, evidence=None):
    """Local SOP rule; ambiguous contexts need a recorded decision, never a default."""
    clear = {'th', 'en'}
    if brief in clear and recipient in {None, '', brief}:
        return brief
    if recipient in clear and brief in {None, '', recipient}:
        return recipient
    require(contextual in clear and bool(evidence), 'language_context_review_required')
    return contextual


def rows_valid(rows):
    seen = set()
    for row in rows:
        require(row['key'] not in seen, 'duplicate_record_key')
        seen.add(row['key'])
        require(row['status'] in STATUSES, 'unknown_status')
        require(row['reason'].strip() and row['evidence'].strip(), 'missing_receipt_context')
        require(row['status'] != 'completed' or bool(row['receipt']), 'completed_receipt_missing')
        require(row['status'] not in {'uncertain', 'intent'} or row['receipt'] is None,
                'ambiguous_receipt_status')
        keys(row)


def pinned_input(root, manifest_sha256, name):
    data = read_bytes(local_file(root, name))
    require(re.fullmatch('[a-f0-9]{64}', manifest_sha256 or '') is not None
            and sha(data) == manifest_sha256, 'manifest_readback_mismatch')
    try:
        doc = json.loads(data)
    except (UnicodeError, json.JSONDecodeError):
        raise Hold('invalid_json') from None
    require(isinstance(doc, dict), 'invalid_export_envelope')
    return doc


def projection_report(doc):
    """Versioned read-only adapter; preserves the projection and never promotes it."""
    require(doc.get('schema_name') == 'sophs-private-operational-projection'
            and type(doc.get('schema_version')) is int and doc['schema_version'] == 1,
            'unsupported_projection_schema')
    required = {*PROJECTION_LISTS, 'authority', 'model', 'public_contact_preference',
                'approved_card', 'source_membership_gaps', 'coverage', 'source_manifest',
                'paused_schedule_receipt', 'missing_fields', 'counts', 'pending_nan_relay',
                'field_projection_allowlist', 'schema_definition', 'schema_sha256', 'privacy_review'}
    require(required <= doc.keys(), 'projection_missing_protection_section')
    encoded = json.dumps(doc['schema_definition'], sort_keys=True, separators=(',', ':')).encode()
    require(sha(encoded) == doc['schema_sha256'], 'projection_schema_hash_mismatch')
    require(set(doc['schema_definition']['required_top_level']) <= doc.keys(),
            'projection_missing_schema_field')
    require(doc['owner_only_private'] is True and doc['authority']['all_schedules_paused'] is True
            and doc['authority']['local_authority_unchanged'] is True
            and doc['authority']['resume_or_send_authorization'] is False, 'projection_authority_conflict')
    for name in PROJECTION_LISTS:
        require(isinstance(doc[name], list), 'projection_list_missing')
    mappings = {'canonical_records': 'campaigns', 'perpost_records': 'campaign_steps',
                'pending_candidates_includingverified': 'pending_candidates',
                'work_queue_items': 'work_queue_items', 'legacy_pending_leads': 'legacy_pending_leads',
                'legacy_hourly_holds': 'legacy_hourly_held_candidates',
                'triage_dispositions': 'triage_dispositions', 'post_dispositions': 'post_dispositions',
                'recorded_source_groups': 'source_queue', 'uncertain_campaigns': 'protected_uncertain_campaign_keys'}
    for field, name in mappings.items():
        require(type(doc['counts'].get(field)) is int and doc['counts'][field] == len(doc[name]),
                'projection_count_mismatch')
    required_record = doc['schema_definition']['record_required']
    for name in ['campaigns', 'campaign_steps', 'work_queue_items', 'pending_candidates',
                 'legacy_pending_leads', 'legacy_hourly_held_candidates', 'triage_dispositions',
                 'post_dispositions', 'reply_relay_protections']:
        require(all(isinstance(row, dict) and set(required_record) <= row.keys()
                    for row in doc[name]), 'projection_record_incomplete')
    require(isinstance(doc['source_manifest'], list) and bool(doc['source_manifest'])
            and all(re.fullmatch('[a-f0-9]{64}', row.get('sha256', ''))
                    for row in doc['source_manifest']), 'projection_source_manifest_invalid')
    blockers = ['partial_projection_not_runtime_import', 'local_os_remains_authoritative']
    if doc['coverage'].get('whole_operational_history_complete') is not True:
        blockers.append('partial_operational_history')
    contact = doc['public_contact_preference']
    if not contact.get('personal_share_url') or contact.get('personal_share_url_verified') is not True:
        blockers.append('verified_personal_line_url_missing')
    if doc['coverage'].get('source_pointers_not_materialized_in_cloud') is not False:
        blockers.append('source_pointers_not_materialized')
    if doc['approved_card'].get('bytes_included') is not True:
        blockers.append('approved_asset_requires_separate_local_byte_verification')
    return {'ok': False, 'status': 'hold', 'projection_validated_read_only': True,
            'schema_version': 1, 'blockers': blockers,
            'counts': {name: len(doc[name]) for name in PROJECTION_LISTS},
            'source_hash_count': len(doc['source_manifest']),
            'execution_permitted': False, 'migration_complete': False,
            'normalization': 'none; all original records preserved; absence never clearance'}


def load_export(root, manifest_sha256, export_id, now, input_name='handoff.json'):
    doc = pinned_input(root, manifest_sha256, input_name)
    contact_check = doc.get('contact', {})
    require(isinstance(contact_check, dict) and contact_check.get('verified') is True
            and contact_check.get('kind') == 'personal_line' and bool(contact_check.get('url')),
            'verified_personal_line_url_missing')
    schema = read_json(SCHEMA)
    shape(doc, schema, schema)
    require(doc['export_id'] == export_id and bool(export_id), 'export_identity_mismatch')
    fresh(doc['generated_at'], now, 3600)
    require(bool(doc['campaigns']), 'empty_ledger_reset_refused')
    source_names = [s['name'] for s in doc['sources']]
    require(len(source_names) == len(set(source_names)) and SOURCES <= set(source_names),
            'incomplete_source_inventory')
    for name in ['campaigns', 'steps', 'holds', 'attempts']:
        require(doc['coverage']['counts'].get(name) == len(doc[name]), 'partial_export_count_mismatch')
        rows_valid(doc[name])
    require(all(r['status'] == 'held' for r in doc['holds']), 'hold_projection_invalid')
    # Importer validates byte-complete documents; it never generates substitutes.
    docs = {}
    for name in ['sop', 'profile', 'asset', 'template']:
        ref = doc[name]
        require(bool(ref['revision'].strip()), 'missing_approved_revision')
        raw = read_bytes(local_file(root, ref['path']))
        require(sha(raw) == ref['sha256'], 'approved_document_hash_mismatch')
        docs[name] = raw
    try:
        profile = json.loads(docs['profile'])
        require(profile.get('verified') is True and isinstance(profile.get('facts'), dict)
                and bool(profile['facts']) and all(v not in (None, '') for v in profile['facts'].values()),
                'verified_profile_missing')
        caption = docs['template'].decode('utf-8')
    except (UnicodeError, json.JSONDecodeError):
        raise Hold('invalid_approved_document') from None
    contact = doc['contact']
    url = contact['url']
    parsed = urlsplit(url)
    require(contact['revision'].strip() and parsed.scheme == 'https' and parsed.hostname == 'line.me'
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and parsed.port in (None, 443)
            and re.fullmatch(r'/(?:R/)?ti/p/[^/]+', parsed.path) is not None
            and '@' not in unquote(parsed.path), 'verified_personal_line_url_missing')
    require(doc['template'].get('contact_revision') == contact['revision'], 'stale_contact_template')
    communication = doc['template'].get('communication', {})
    require(COMMUNICATION_SKILL.is_file() and communication.get('skill_revision') == 1
            and communication.get('language') in {'th', 'en'}
            and communication.get('message_kind') in {'initial_interest', 'follow_up', 'missing_info'}
            and bool(communication.get('language_evidence'))
            and communication.get('sop_sha256') == doc['sop']['sha256']
            and communication.get('agency_identity_verified') is True
            and communication.get('claims_reviewed') is True, 'communication_source_review_required')
    require(communication['language'] == communication_language(
            communication.get('brief_language'), communication.get('recipient_language'),
            communication.get('contextual_language'), communication.get('language_evidence')),
            'caption_language_mismatch')
    require(caption.strip() and caption.count(url) == 1, 'caption_contact_missing_or_duplicated')
    line_links = re.findall(r'https://(?:lin\.ee|line\.me)/[^\s<>"\)]+', caption)
    require(line_links == [url], 'caption_contains_other_line_contact')
    outbound = re.findall(r'(?:https?://|www\.)[^\s<>"\)]+', caption)
    require(outbound == [url], 'caption_outbound_link_policy')
    remaining = caption.replace(url, '')
    require(not re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', remaining)
            and not re.search(r'\b[\w-]+(?:\.[\w-]+)*\.(?:com|net|org|io|me|co|th)(?:/|\b)', remaining),
            'caption_other_contact_not_allowed')
    require(not re.search(r'\{\{.*?\}\}|\$\{.*?\}|\[PRIVATE_[^\]]+\]', caption),
            'unresolved_private_placeholder')
    return doc, caption


def load_state(root):
    path = local_file(root, STATE)
    if not path.exists():
        return None
    state = read_json(path)
    require(isinstance(state, dict) and state.get('schema_version') == 1
            and isinstance(state.get('attempts'), list)
            and state.get('phase') in {'prepared', 'intent', 'completed', 'known_unsent', 'uncertain'},
            'invalid_resume_state')
    rows_valid(state['attempts'])
    return state


def save_state(root, state):
    fd, name = tempfile.mkstemp(dir=root, prefix='.handoff-state-')
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(state, out, ensure_ascii=False, indent=2)
            out.write('\n'); out.flush(); os.fsync(out.fileno())
        os.replace(name, local_file(root, STATE))
        require(read_json(local_file(root, STATE)) == state, 'outcome_readback_failed')
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def locked(root):
    path = local_file(root, 'handoff.lock')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def candidate_check(doc, candidate, state, now):
    require(isinstance(candidate, dict) and all(candidate.get(k) for k in
            ['key', 'campaign_key', 'source_url', 'source_evidence']), 'candidate_identity_missing')
    require(isinstance(candidate.get('aliases'), list)
            and all(isinstance(x, str) and x for x in candidate['aliases']), 'candidate_aliases_missing')
    fresh(candidate.get('checked_at'), now, 300)
    require(all(candidate.get(k) is True for k in ['open', 'eligible', 'route_verified', 'paid'])
            and isinstance(candidate.get('category'), str) and bool(candidate['category'].strip()),
            'eligibility_or_paid_mv_hold')
    target = keys(candidate)
    history = [r for n in ['campaigns', 'steps', 'holds', 'attempts'] for r in doc[n]]
    history += state['attempts'] if state else []
    # Any non-known-unsent match wins, even if another row says unsent.
    require(not any(target & keys(r) and r['status'] != 'known_unsent' for r in history),
            'duplicate_held_or_uncertain')
    # A previous known-unsent attempt still needs explicit new reconciliation evidence.
    require(not any(target & keys(r) for r in (state or {}).get('attempts', []))
            or bool(candidate.get('retry_review_evidence')), 'retry_review_required')


def run(command, root_path, expected_host, digest, export_id, candidate_name=None,
        contact_revision=None, outcome_name=None, now=None, input_name='handoff.json'):
    now = now or datetime.now(timezone.utc)
    root = private_root(root_path, expected_host)
    if command == 'record-result':
        # Reconciliation must remain possible after an export expires or an asset
        # changes. It never authorizes another action or clears imported holds.
        require(local_file(root, STATE).is_file(), 'pending_intent_required')
        with locked(root):
            state = load_state(root)
            require(state and state.get('phase') in {'intent', 'uncertain'}, 'pending_intent_required')
            require(state.get('manifest_sha256') == digest and state.get('export_id') == export_id,
                    'attempt_export_identity_mismatch')
            result = read_json(local_file(root, outcome_name))
            require(result.get('attempt_id') == state['attempt_id'], 'attempt_identity_mismatch')
            require(result.get('status') in {'completed', 'known_unsent', 'uncertain'}
                    and bool(result.get('evidence')), 'outcome_evidence_required')
            require(state['phase'] != 'uncertain' or bool(result.get('reconciliation_evidence')),
                    'uncertain_reconciliation_evidence_required')
            require(result['status'] != 'completed' or bool(result.get('receipt')), 'completed_receipt_missing')
            require(result['status'] != 'known_unsent' or result.get('transmission_started') is False,
                    'unknown_write_must_remain_uncertain')
            row = state['attempts'][-1]
            row.setdefault('history', []).append(dict(row, history=[]))
            row.update(status=result['status'], evidence=result['evidence'],
                       receipt=result.get('receipt') if result['status'] == 'completed' else None,
                       reason='recorded_outcome', recorded_at=now.isoformat(),
                       reconciliation_evidence=result.get('reconciliation_evidence'))
            state.update(phase=result['status'], checkpoint_at=now.isoformat())
            save_state(root, state)
            return {'ok': True, 'outcome_recorded': True, 'execution_permitted': False}
    # Missing or partial exports never cause creation of lock/state/default ledgers.
    supplied = pinned_input(root, digest, input_name)
    if supplied.get('schema_name') == 'sophs-private-operational-projection':
        report = projection_report(supplied)
        require(command == 'validate-import', 'projection_read_only_no_candidate_clearance')
        return report
    doc, caption = load_export(root, digest, export_id, now, input_name)
    if command == 'validate-import':
        return {'ok': True, 'import_validated': True, 'authority': 'local_os',
                'execution_permitted': False, 'migration_complete': False}
    with locked(root):
        doc, caption = load_export(root, digest, export_id, now, input_name)
        state = load_state(root)
        require(command in {'preflight', 'final-check'}, 'unknown_command')
        require(contact_revision and contact_revision == doc['contact']['revision'], 'contact_revision_not_verified')
        require(not state or state.get('phase') != 'intent', 'reconcile_pending_write_first')
        candidate = read_json(local_file(root, candidate_name))
        candidate_check(doc, candidate, state, now)
        fingerprint = sha(json.dumps(candidate, sort_keys=True).encode())
        if command == 'preflight':
            require(not state or state.get('phase') != 'uncertain', 'reconcile_uncertain_write_first')
            attempts = state['attempts'] if state else []
            state = {'schema_version': 1, 'phase': 'prepared', 'export_id': export_id,
                     'manifest_sha256': digest, 'contact_revision': contact_revision,
                     'candidate_sha256': fingerprint, 'caption': caption,
                     'caption_sha256': sha(caption.encode()), 'asset_sha256': doc['asset']['sha256'],
                     'prepared_at': now.isoformat(), 'attempts': attempts,
                     'execution_permitted': False}
        else:
            require(state and state.get('phase') == 'prepared', 'prepare_caption_before_attachment')
            fresh(state['prepared_at'], now, 300)
            require(state['export_id'] == export_id and state['manifest_sha256'] == digest
                    and state['contact_revision'] == contact_revision
                    and state['candidate_sha256'] == fingerprint
                    and state['caption_sha256'] == sha(caption.encode())
                    and state['asset_sha256'] == doc['asset']['sha256'], 'prepared_packet_changed')
            attempt_id = str(uuid.uuid4())
            state['attempts'].append({'key': attempt_id, 'campaign_key': candidate['campaign_key'],
                'source_url': candidate['source_url'], 'aliases': candidate['aliases'],
                'status': 'intent', 'reason': 'final_check_reserved_no_send_executed',
                'evidence': 'prepared_caption_and_fresh_export_checks', 'receipt': None})
            state.update(phase='intent', attempt_id=attempt_id, checkpoint_at=now.isoformat())
        save_state(root, state)
        return {'ok': True, 'phase': state['phase'], 'attempt_id': state.get('attempt_id'),
                'caption_sha256': state['caption_sha256'], 'execution_permitted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['validate-import', 'preflight', 'final-check', 'record-result'])
    p.add_argument('--private-root', required=True)
    p.add_argument('--expected-host', required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--export-id', required=True)
    p.add_argument('--manifest-file', default='handoff.json')
    p.add_argument('--candidate')
    p.add_argument('--contact-revision')
    p.add_argument('--outcome')
    a = p.parse_args()
    try:
        result = run(a.command, a.private_root, a.expected_host, a.manifest_sha256,
                     a.export_id, a.candidate, a.contact_revision, a.outcome,
                     input_name=a.manifest_file)
    except Hold as error:
        print(json.dumps({'ok': False, 'status': 'hold', 'hold_code': str(error),
                          'execution_permitted': False}))
        return 2
    except (OSError, KeyError, TypeError, ValueError):
        # Never print raw private values, URLs, documents or exception filenames.
        print(json.dumps({'ok': False, 'status': 'hold', 'execution_permitted': False}))
        return 2
    print(json.dumps(result))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
