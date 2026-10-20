import hashlib
import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location('checkpoint', Path(__file__).with_name('hourly-checkpoint.py'))
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


class ManualLeaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime.now(timezone.utc)
        self.rid = 'facebook:manual:2026-10-20:one-exception'
        self.token = 'fixture-token-not-live'
        self.put('hourly-state.json', {'active': False, 'run_id': 'completed22', 'status': 'partial'})
        self.put('social-collector-state.json', {'active': False, 'run_id': 'completed22'})
        self.put('work-cycle-state.json', {'enabled': True, 'phase': 'sophie_ready',
                 'next_owner': h._manual.THREAD, 'not_before': (self.now + timedelta(hours=8)).isoformat(),
                 'dispatch': None})
        self.put('authorization.json', {'human_request': 'Fixture only, no real authority'})
        self.grant = {'thread_id': h._manual.THREAD, 'scope': h._manual.SCOPE,
            'token_sha256': hashlib.sha256(self.token.encode()).hexdigest(),
            'authorization_evidence': 'authorization.json', 'human_authorization_source': 'fixture',
            'not_before': (self.now - timedelta(seconds=5)).isoformat(),
            'expires_at': (self.now + timedelta(minutes=20)).isoformat(), 'status': 'unused',
            'cycle_sha256': h._manual.digest(self.root / 'work-cycle-state.json'),
            'hourly_sha256': h._manual.digest(self.root / 'hourly-state.json')}
        self.sync()
        self.data = {'run_id': self.rid, 'thread_id': h._manual.THREAD, 'platform': 'facebook',
                     'manual_authorization_token': self.token,
                     'deadline': (self.now + timedelta(minutes=15)).isoformat()}

    def put(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def sync(self):
        self.put(h._manual.FILE, {'grants': {self.rid: self.grant}})

    def execute(self, command='lease-begin', **updates):
        return h.execute(self.root, command, {**self.data, **updates})

    def test_round_trip_preserves_cycle_and_hourly_bytes_and_never_stores_token(self):
        protected = {n: (self.root / n).read_bytes() for n in ['work-cycle-state.json', 'hourly-state.json']}
        self.assertTrue(self.execute()['ok'])
        lease = json.loads((self.root / 'social-collector-state.json').read_text())
        self.assertTrue(lease['manual_exception'])
        self.assertNotIn('manual_authorization_token', lease)
        self.assertFalse(self.execute()['ok'])
        self.put('terminal.json', {'run_id': self.rid, 'thread_id': h._manual.THREAD,
                                  'inflight_send': False, 'receipts_reconciled': True})
        self.assertTrue(self.execute('lease-finish', checkpoint='terminal.json')['ok'])
        self.assertFalse(self.execute()['ok'])
        for name, content in protected.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.assertFalse(json.loads((self.root / 'social-collector-state.json').read_text())['active'])

    def test_wrong_owner_token_or_run_denied(self):
        for update in [{'thread_id': 'other'}, {'manual_authorization_token': 'wrong'},
                       {'run_id': 'facebook:2026-10-20:2200'}, {'platform': 'upwork'}]:
            with self.subTest(update=update):
                self.assertFalse(self.execute(**update)['ok'])

    def test_no_hourly_begin_or_checkpoint_overwrite(self):
        before = (self.root / 'hourly-state.json').read_bytes()
        self.assertFalse(self.execute('begin')['ok'])
        self.assertEqual((self.root / 'hourly-state.json').read_bytes(), before)

    def test_no_theft_unknown_guard_or_replay(self):
        for active in [True, None]:
            self.put('social-collector-state.json', {'active': active})
            self.assertFalse(self.execute()['ok'])
        self.put('social-collector-state.json', {'active': False})
        self.grant['status'] = 'consumed'; self.sync()
        self.assertFalse(self.execute()['ok'])

    def test_expired_overlong_or_future_deadline_denied(self):
        self.assertFalse(self.execute(deadline=(self.now - timedelta(seconds=1)).isoformat())['ok'])
        self.assertFalse(self.execute(deadline=(self.now + timedelta(minutes=25)).isoformat())['ok'])
        self.grant['expires_at'] = (self.now + timedelta(minutes=31)).isoformat(); self.sync()
        self.assertFalse(self.execute()['ok'])

    def test_missing_human_evidence_or_changed_reservation_denied(self):
        self.grant['authorization_evidence'] = 'missing.json'; self.sync()
        self.assertFalse(self.execute()['ok'])
        self.grant['authorization_evidence'] = 'authorization.json'; self.sync()
        (self.root / 'work-cycle-state.json').write_text('{}')
        self.assertFalse(self.execute()['ok'])

    def test_release_requires_exact_owner_and_reconciled_checkpoint(self):
        self.assertTrue(self.execute()['ok'])
        self.assertFalse(self.execute('lease-finish', checkpoint='missing.json')['ok'])
        self.put('terminal.json', {'run_id': self.rid, 'thread_id': h._manual.THREAD,
                                  'inflight_send': True, 'receipts_reconciled': False})
        self.assertFalse(self.execute('lease-finish', checkpoint='terminal.json')['ok'])
        self.assertFalse(self.execute('lease-finish', thread_id='other', checkpoint='terminal.json')['ok'])
        self.assertFalse(h.execute(self.root, 'lease-finish', {'run_id': self.rid})['ok'])


if __name__ == '__main__':
    unittest.main()
