"""Synthetic private roots only. No browser, network or external actions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import portable_handoff as h


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
        self.host = os.uname().nodename
        self.contact = 'https://line.me/ti/p/~synthetic-fixture'
        self.doc = {
            'schema_version': 1, 'kind': 'COMPLETE_PRIVATE_HANDOFF',
            'export_id': 'synthetic-export-v1', 'generated_at': self.now.isoformat(),
            'authority': 'local_os', 'paused': True,
            'coverage': {'complete': True, 'reconciled': True,
                         'counts': {'campaigns': 1, 'steps': 0, 'holds': 0, 'attempts': 0}},
            'sources': [{'name': name, 'sha256': '0' * 64} for name in sorted(h.SOURCES)],
            'contact': {'kind': 'personal_line', 'url': self.contact,
                        'revision': 'contact-fixture-1', 'verified': True},
            'preferences': {'paid_mv_required': True},
            'campaigns': [self.row('other', 'known_unsent')], 'steps': [], 'holds': [], 'attempts': [],
            'checkpoint': {'revision': 'fixture-1', 'reconciled': True, 'inflight': False}}
        for name, raw in {
            'sop': b'Synthetic instructions only.',
            'profile': json.dumps({'verified': True, 'facts': {'name': 'Synthetic actor'}}).encode(),
            'asset': b'Synthetic card bytes; not an image or actual card.',
            'template': ('Synthetic enquiry. Contact: ' + self.contact).encode()
        }.items():
            (self.root / name).write_bytes(raw)
            self.doc[name] = {'path': name, 'revision': 'fixture-1', 'approved': True,
                              'sha256': h.sha(raw)}
        self.doc['template']['contact_revision'] = 'contact-fixture-1'
        self.doc['template']['communication'] = {'skill_revision': 1, 'language': 'en',
            'brief_language': 'en', 'recipient_language': 'en', 'message_kind': 'initial_interest',
            'language_evidence': 'synthetic approved SOP rule',
            'sop_sha256': self.doc['sop']['sha256'], 'agency_identity_verified': True,
            'claims_reviewed': True}
        self.candidate = {'key': 'new', 'campaign_key': 'new',
            'source_url': 'https://example.invalid/posts/new', 'aliases': [],
            'source_evidence': 'synthetic-original', 'checked_at': self.now.isoformat(),
            'open': True, 'eligible': True, 'route_verified': True, 'paid': True,
            'category': 'music_video'}
        self.put('candidate.json', self.candidate)
        self.pin()

    def row(self, key, status):
        return {'key': key, 'campaign_key': key,
                'source_url': 'https://example.invalid/posts/' + key, 'aliases': [],
                'status': status, 'reason': 'synthetic reason', 'evidence': 'synthetic evidence',
                'receipt': 'synthetic receipt' if status == 'completed' else None}

    def put(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def pin(self):
        self.put('handoff.json', self.doc)
        self.digest = h.sha((self.root / 'handoff.json').read_bytes())

    def run_command(self, command='validate-import', **kwargs):
        args = dict(command=command, root_path=str(self.root), expected_host=self.host,
                    digest=self.digest, export_id='synthetic-export-v1',
                    candidate_name='candidate.json', contact_revision='contact-fixture-1', now=self.now)
        args.update(kwargs)
        return h.run(**args)

    def test_validate_import_is_read_only_and_never_authorizes(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.assertFalse(self.run_command()['execution_permitted'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_missing_export_does_not_initialize_lock_or_state(self):
        (self.root / 'handoff.json').unlink()
        with self.assertRaises(h.Hold): self.run_command()
        self.assertFalse((self.root / 'handoff.lock').exists())
        self.assertFalse((self.root / h.STATE).exists())

    def test_host_and_root_verified_before_private_read(self):
        with patch.object(h, 'read_bytes', side_effect=AssertionError('private read')):
            with self.assertRaises(h.Hold): self.run_command(expected_host='wrong-host')
            with self.assertRaises(h.Hold): self.run_command(root_path=str(h.REPO))

    def test_partial_projection_unknown_and_empty_ledgers_fail_closed(self):
        original = deepcopy(self.doc)
        for change in ['partial', 'projection', 'counts', 'unknown', 'empty', 'source', 'inflight']:
            self.doc = deepcopy(original)
            if change == 'partial': self.doc['coverage']['complete'] = False
            if change == 'projection': self.doc['kind'] = 'NON_RUNTIME_MIGRATION_EVIDENCE'
            if change == 'counts': self.doc['coverage']['counts']['holds'] = 8
            if change == 'unknown': self.doc['campaigns'][0]['status'] = 'unknown'
            if change == 'empty': self.doc['campaigns'] = []; self.doc['coverage']['counts']['campaigns'] = 0
            if change == 'source': self.doc['sources'].pop()
            if change == 'inflight': self.doc['checkpoint']['inflight'] = True
            self.pin()
            with self.subTest(change=change), self.assertRaises(h.Hold): self.run_command()

    def test_missing_receipt_or_context_is_not_unsent(self):
        for field in ['receipt', 'reason', 'evidence']:
            self.doc['campaigns'][0] = self.row('other', 'completed')
            self.doc['campaigns'][0][field] = None if field == 'receipt' else ''
            self.pin()
            with self.subTest(field=field), self.assertRaises(h.Hold): self.run_command()

    def test_all_ledger_locations_block_duplicate_held_and_uncertain(self):
        for ledger in ['campaigns', 'steps', 'holds', 'attempts']:
            for status in ['completed', 'held', 'uncertain', 'intent']:
                if ledger == 'holds' and status != 'held': continue
                self.doc[ledger] = [self.row('new', status)]
                self.doc['coverage']['counts'][ledger] = 1
                self.pin()
                with self.subTest(ledger=ledger, status=status), self.assertRaises(h.Hold):
                    self.run_command('preflight')
                self.doc[ledger] = [self.row('other', 'known_unsent')] if ledger == 'campaigns' else []
                self.doc['coverage']['counts'][ledger] = len(self.doc[ledger])

    def test_alias_and_canonical_url_crosspost_protection(self):
        for mode in ['alias', 'url']:
            row = self.row('other', 'completed')
            if mode == 'alias': row['aliases'] = ['new']
            else: row['source_url'] = self.candidate['source_url'] + '?tracking=fixture'
            self.doc['steps'] = [row]; self.doc['coverage']['counts']['steps'] = 1; self.pin()
            with self.subTest(mode=mode), self.assertRaises(h.Hold): self.run_command('preflight')

    def test_missing_or_unapproved_asset_profile_sop_template_blocks(self):
        original = deepcopy(self.doc)
        for name in ['asset', 'profile', 'sop', 'template']:
            self.doc = deepcopy(original); self.doc[name]['approved'] = False; self.pin()
            with self.subTest(name=name), self.assertRaises(h.Hold): self.run_command('preflight')
        self.doc = deepcopy(original); self.pin(); (self.root / 'asset').unlink()
        with self.assertRaises(h.Hold): self.run_command('preflight')

    def test_asset_hash_and_symlink_escape_block(self):
        (self.root / 'asset').write_bytes(b'changed')
        with self.assertRaises(h.Hold): self.run_command('preflight')
        (self.root / 'asset').unlink(); (self.root / 'asset').symlink_to(self.root / 'sop')
        with self.assertRaises(h.Hold): self.run_command('preflight')

    def test_personal_line_missing_unverified_business_or_invented_url_blocks(self):
        for url, verified in [('', True), (self.contact, False),
                              ('https://line.me/ti/p/@business-fixture', True),
                              ('https://example.invalid/contact', True)]:
            self.doc['contact'].update(url=url, verified=verified); self.pin()
            with self.subTest(url=url), self.assertRaises(h.Hold): self.run_command('preflight')

    def test_contact_revision_rechecked_before_intent(self):
        self.run_command('preflight')
        self.doc['contact']['revision'] = 'contact-fixture-2'; self.pin()
        with self.assertRaises(h.Hold): self.run_command('final-check')
        with self.assertRaises(h.Hold): self.run_command('final-check', contact_revision='contact-fixture-2')
        self.assertEqual(json.loads((self.root / h.STATE).read_text())['phase'], 'prepared')

    def test_caption_prepared_before_attachment_and_intent(self):
        with self.assertRaises(h.Hold): self.run_command('final-check')
        self.run_command('preflight')
        state = json.loads((self.root / h.STATE).read_text())
        self.assertIn(self.contact, state['caption'])
        result = self.run_command('final-check')
        self.assertFalse(result['execution_permitted'])
        with self.assertRaises(h.Hold): self.run_command('preflight')

    def test_stale_evidence_and_paid_mv_default(self):
        for field, value in [('paid', False), ('category', ''), ('eligible', False), ('open', False)]:
            candidate = dict(self.candidate); candidate[field] = value; self.put('candidate.json', candidate)
            with self.subTest(field=field), self.assertRaises(h.Hold): self.run_command('preflight')
        self.put('candidate.json', self.candidate)
        with self.assertRaises(h.Hold): self.run_command('preflight', now=self.now + timedelta(minutes=6))
        with self.assertRaises(h.Hold): self.run_command(now=self.now + timedelta(hours=2))

    def intent(self):
        self.run_command('preflight')
        result = self.run_command('final-check')
        self.put('outcome.json', {'attempt_id': result['attempt_id'], 'status': 'uncertain',
                                 'evidence': 'synthetic ambiguous UI result'})

    def test_uncertain_outcome_persisted_and_never_blindly_replayed(self):
        self.intent(); self.run_command('record-result', outcome_name='outcome.json')
        with self.assertRaises(h.Hold): self.run_command('preflight')
        with self.assertRaises(h.Hold): self.run_command('record-result', outcome_name='outcome.json')
        state = json.loads((self.root / h.STATE).read_text())
        self.assertEqual(state['attempts'][-1]['status'], 'uncertain')
        self.assertEqual(state['attempts'][-1]['history'][0]['status'], 'intent')

    def test_receipt_recording_survives_expired_export_and_missing_asset(self):
        self.intent(); (self.root / 'asset').unlink()
        self.assertTrue(self.run_command('record-result', outcome_name='outcome.json',
                                       now=self.now + timedelta(days=1))['outcome_recorded'])

    def test_completed_receipt_immediately_blocks_duplicate(self):
        self.intent(); result = json.loads((self.root / 'outcome.json').read_text())
        result.update(status='completed', receipt='synthetic outgoing readback')
        self.put('outcome.json', result); self.run_command('record-result', outcome_name='outcome.json')
        with self.assertRaises(h.Hold): self.run_command('preflight')

    def test_unknown_transmission_cannot_be_known_unsent(self):
        self.intent(); result = json.loads((self.root / 'outcome.json').read_text())
        result.update(status='known_unsent', transmission_started=True)
        self.put('outcome.json', result)
        with self.assertRaises(h.Hold): self.run_command('record-result', outcome_name='outcome.json')
        self.assertEqual(json.loads((self.root / h.STATE).read_text())['phase'], 'intent')

    def test_manifest_pin_and_export_identity(self):
        with self.assertRaises(h.Hold): self.run_command(digest='0' * 64)
        with self.assertRaises(h.Hold): self.run_command(export_id='different-export')

    def projection(self):
        d = {key: [] for key in h.PROJECTION_LISTS}
        d.update(schema_name='sophs-private-operational-projection', schema_version=1,
            exported_at=self.now.isoformat(), owner_only_private=True,
            authority={'all_schedules_paused': True, 'local_authority_unchanged': True,
                       'resume_or_send_authorization': False}, model={},
            public_contact_preference={'personal_share_url': None, 'personal_share_url_verified': False},
            approved_card={'bytes_included': False}, source_membership_gaps={},
            coverage={'whole_operational_history_complete': False,
                      'source_pointers_not_materialized_in_cloud': True},
            source_manifest=[{'sha256': '0' * 64}], paused_schedule_receipt=[], missing_fields=[],
            pending_nan_relay={}, field_projection_allowlist=[], privacy_review={})
        d['counts'] = {key: 0 for key in ['canonical_records','perpost_records',
            'pending_candidates_includingverified','work_queue_items','legacy_pending_leads',
            'legacy_hourly_holds','triage_dispositions','post_dispositions','recorded_source_groups',
            'uncertain_campaigns']}
        d['schema_definition'] = {'version': 1, 'required_top_level': ['authority','coverage'],
                                  'record_required': ['record_key','source_pointer','action_gate']}
        d['schema_sha256'] = h.sha(json.dumps(d['schema_definition'],sort_keys=True,separators=(',',':')).encode())
        return d

    def projection_pin(self, doc):
        self.put('projection.json', doc)
        return h.sha((self.root / 'projection.json').read_bytes())

    def test_actual_projection_adapter_preserves_all_holds_and_never_clears(self):
        doc = self.projection()
        doc['protected_uncertain_campaign_keys'] = ['synthetic-hold-' + str(i) for i in range(8)]
        doc['counts']['uncertain_campaigns'] = 8
        digest = self.projection_pin(doc)
        before = (self.root / 'projection.json').read_bytes()
        result = self.run_command(digest=digest, input_name='projection.json')
        self.assertFalse(result['ok']); self.assertFalse(result['migration_complete'])
        self.assertEqual(result['counts']['protected_uncertain_campaign_keys'], 8)
        self.assertIn('verified_personal_line_url_missing', result['blockers'])
        self.assertEqual(before, (self.root / 'projection.json').read_bytes())
        self.assertFalse((self.root / h.STATE).exists())
        with self.assertRaises(h.Hold):
            self.run_command('preflight', digest=digest, input_name='projection.json')

    def test_projection_claiming_complete_still_cannot_authorize(self):
        doc = self.projection(); doc['coverage']['whole_operational_history_complete'] = True
        result = self.run_command(digest=self.projection_pin(doc), input_name='projection.json')
        self.assertFalse(result['ok']); self.assertFalse(result['execution_permitted'])

    def test_projection_schema_hash_counts_and_sections_fail_closed(self):
        for kind in ['schema', 'counts', 'section', 'paused', 'record']:
            doc = self.projection()
            if kind == 'schema': doc['schema_sha256'] = '1' * 64
            if kind == 'counts': doc['counts']['canonical_records'] = 1
            if kind == 'section': del doc['reply_relay_protections']
            if kind == 'paused': doc['authority']['all_schedules_paused'] = False
            if kind == 'record':
                doc['campaigns'] = [{}]; doc['counts']['canonical_records'] = 1
            with self.subTest(kind=kind), self.assertRaises(h.Hold):
                self.run_command(digest=self.projection_pin(doc), input_name='projection.json')

    def test_known_unsent_retry_needs_new_review(self):
        self.intent(); result = json.loads((self.root / 'outcome.json').read_text())
        result.update(status='known_unsent', transmission_started=False)
        self.put('outcome.json', result); self.run_command('record-result', outcome_name='outcome.json')
        with self.assertRaises(h.Hold): self.run_command('preflight')
        candidate = dict(self.candidate, retry_review_evidence='synthetic fresh review')
        self.put('candidate.json', candidate)
        self.assertEqual(self.run_command('preflight')['phase'], 'prepared')

    def test_reconciled_uncertainty_keeps_history_without_clearing_imported_hold(self):
        self.intent(); self.run_command('record-result', outcome_name='outcome.json')
        result = json.loads((self.root / 'outcome.json').read_text())
        result.update(status='completed', receipt='synthetic verified readback',
                      reconciliation_evidence='synthetic fresh exact outgoing evidence')
        self.put('outcome.json', result); self.run_command('record-result', outcome_name='outcome.json')
        state = json.loads((self.root / h.STATE).read_text())
        self.assertEqual([r['status'] for r in state['attempts'][-1]['history']], ['intent','uncertain'])
        with self.assertRaises(h.Hold): self.run_command('preflight')

    def test_caption_only_verified_personal_link_no_other_outbound_contacts(self):
        original = (self.root / 'template').read_text()
        for suffix in [' https://example.invalid/portfolio', ' writer@example.invalid',
                       ' www.example.invalid/card', ' instagram.com/synthetic-fixture',
                       ' https://line.me/ti/p/@business-fixture']:
            raw = (original + suffix).encode(); (self.root / 'template').write_bytes(raw)
            self.doc['template']['sha256'] = h.sha(raw); self.pin()
            with self.subTest(suffix=suffix), self.assertRaises(h.Hold): self.run_command('preflight')

    def test_private_placeholders_stay_in_examples_and_never_become_send_ready(self):
        original = (self.root / 'template').read_text()
        for placeholder in ['{{PRIVATE_ACTOR_NAME}}', '${PRIVATE_AGE}', '[PRIVATE_AVAILABILITY]']:
            raw = (placeholder + ' ' + original).encode(); (self.root / 'template').write_bytes(raw)
            self.doc['template']['sha256'] = h.sha(raw); self.pin()
            with self.subTest(placeholder=placeholder), self.assertRaises(h.Hold): self.run_command('preflight')

    def test_communication_language_purpose_and_source_review_required(self):
        original = deepcopy(self.doc['template']['communication'])
        for key,value in [('language', 'auto'), ('message_kind', 'booking_confirmation'),
                          ('language_evidence',''), ('sop_sha256','0'*64),
                          ('agency_identity_verified',False), ('claims_reviewed',False),
                          ('language','th')]:
            self.doc['template']['communication'] = {**original,key:value}; self.pin()
            with self.subTest(key=key), self.assertRaises(h.Hold): self.run_command('preflight')

    def test_language_choice_uses_source_context_and_no_universal_default(self):
        self.assertEqual(h.communication_language('th','th'),'th')
        self.assertEqual(h.communication_language('en',None),'en')
        self.assertEqual(h.communication_language(None,'th'),'th')
        for brief,recipient in [('en','th'),('mixed','th'),(None,None),('unknown','unknown')]:
            with self.subTest(brief=brief), self.assertRaises(h.Hold):
                h.communication_language(brief,recipient)
            self.assertEqual(h.communication_language(brief,recipient,'en','synthetic contextual review'),'en')

    def test_reusable_templates_keep_private_placeholders_and_no_attached_claim(self):
        path = h.COMMUNICATION_SKILL.parent / 'references/templates.json'
        templates=json.loads(path.read_text())
        for language in ['th','en']:
            self.assertEqual(set(templates[language]),{'initial_interest','follow_up','missing_info'})
            for text in templates[language].values():
                self.assertIn('{{PRIVATE_PERSONAL_LINE_URL}}',text)
                self.assertNotIn('https://',text)
                self.assertNotIn('attached',text.lower())
        self.assertIn('{{PRIVATE_AGE}}',templates['en']['initial_interest'])
        self.assertIn('{{PRIVATE_AGE}}',templates['th']['initial_interest'])


if __name__ == '__main__':
    unittest.main()
