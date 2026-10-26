import json
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
import subprocess
from pathlib import Path
from runner import (
    Budget, proposed_files, POOLS, call, ALLOWED, JOB_ID, JOB_GOAL,
    compute_source_hashes, compute_test_hashes,
    verify_hashes, verify_source_hashes, run_job, validate_run_path,
    parse_and_validate_completion_auth
)
from quota_parser import extract_account_id

LEGACY_CODEX_ACCOUNT = 'legacy-account-test-fixture'


def make_mock_fcntl():
    m = Mock()
    m.LOCK_EX = 2
    m.LOCK_NB = 4
    m.LOCK_SH = 1
    m.LOCK_UN = 8
    return m


class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (Path(__file__).resolve().parent.parent / '.anti-codex').mkdir(exist_ok=True)

    def test_unknown_pool_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            with self.assertRaises(RuntimeError):
                Budget(Path(d)/'b.json').update({}, {}, 1, account_id='acc_test')

    def test_existing_ledger_does_not_grant_another_budget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            Budget(path).update(week, short, 1900000000, account_id='acc_test')
            week['codex']['used'] = 23
            with self.assertRaises(RuntimeError):
                Budget(path).update(week, short, 1900000001, account_id='acc_test')

    def test_confirmed_reset_uses_zero_baseline(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 1900000010} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            Budget(path).update(week, short, 1900000000, account_id='acc_test')
            for v in week.values():
                v.update(used=2, reset=1900604810)
            record = Budget(path).update(week, short, 1900000020, account_id='acc_test')
            self.assertTrue(all(v['base'] == 0 for v in record['pools'].values()))

    def test_proposals_cannot_escape_scope(self):
        for proposal in ({'../escape': 'x'}, {'cloud/runner.py': 'x'}, {'extension/popup.js': 1}):
            with self.assertRaises(ValueError):
                proposed_files(proposal)

    def test_empty_proposal_is_allowed(self):
        self.assertEqual(proposed_files({}), {})

    def test_manifest_cannot_gain_host_permissions(self):
        manifest = {'manifest_version': 3, 'permissions': ['storage', 'activeTab'], 'host_permissions': ['<all_urls>']}
        with self.assertRaises(ValueError):
            proposed_files({'extension/manifest.json': json.dumps(manifest)})

    def test_proposals_allow_background_js(self):
        self.assertIn('extension/background.js', ALLOWED)
        res = proposed_files({'extension/background.js': '// background worker'})
        self.assertEqual(res, {'extension/background.js': '// background worker'})

    def test_supplement_preserves_baseline_and_stops_at_28(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'budget.json'
            (path.parent/'authorization.json').write_text(json.dumps(dict(
                reset=1793428548, cap_used=30, stop_used=28, extra_points=5,
                codex_account_id=LEGACY_CODEX_ACCOUNT)))
            week = {k: {'used': 0, 'reset': 1793428548} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            week['codex']['used'] = 27
            record = Budget(path).update(week, short, 1792850000, account_id=LEGACY_CODEX_ACCOUNT)
            self.assertEqual(record['pools']['codex']['base'], 1)
            week['codex']['used'] = 28
            with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop'):
                Budget(path).update(week, short, 1792850001, account_id=LEGACY_CODEX_ACCOUNT)

    def test_supplement_does_not_expand_anti_or_future_weeks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'budget.json'
            (path.parent/'authorization.json').write_text(json.dumps(dict(
                reset=1793428548, cap_used=30, stop_used=28, extra_points=5,
                codex_account_id=LEGACY_CODEX_ACCOUNT)))
            guard = Budget(path)
            self.assertEqual(guard.stop_at('anti:gemini models', 1793428548, 0, account_id=LEGACY_CODEX_ACCOUNT), 23)
            self.assertEqual(guard.stop_at('codex', 1794033348, 0, account_id=LEGACY_CODEX_ACCOUNT), 23)

    def test_legacy_supplement_rejects_unauthorized_account(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'budget.json'
            (path.parent/'authorization.json').write_text(json.dumps(dict(
                reset=1793428548, cap_used=30, stop_used=28, extra_points=5,
                codex_account_id=LEGACY_CODEX_ACCOUNT)))
            guard = Budget(path)
            # Different account must NOT receive 28 stop threshold
            self.assertEqual(guard.stop_at('codex', 1793428548, 0, account_id='wrong-account-id'), 23)

    def test_preflight_failure_never_launches_model(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            guard = Mock()
            guard.check.side_effect = RuntimeError('Unreadable quota')
            with patch('runner.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'Unreadable'):
                    call('anti', 'test', Path(d), 1, guard, 2000000000)
                launch.assert_not_called()

    def test_monitor_cancels_worker_on_unreadable_quota(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            guard = Mock()
            guard.check.side_effect = [None, RuntimeError('Unreadable quota')]
            guard.anti_model_policy = 'flash-then-argon'
            worker = Mock(pid=4321)
            worker.poll.side_effect = [None, None, 0]
            worker.wait.side_effect = [subprocess.TimeoutExpired('test', 15), 0]
            with patch('runner.subprocess.Popen', return_value=worker), \
                 patch('quota_client.os.killpg', create=True) as kill, \
                 patch('quota_client.signal.SIGKILL', 9, create=True):
                with self.assertRaisesRegex(RuntimeError, 'Unreadable'):
                    call('anti', 'test', Path(d), 1, guard, 2000000000)
                self.assertEqual(kill.call_count, 2)
                self.assertEqual(kill.call_args_list[0].args[0], 4321)
                self.assertEqual(kill.call_args_list[1].args, (4321, 9))

    # --- Invariant & Account Binding Tests ---

    def test_missing_account_id_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            b = Budget(path)
            with self.assertRaisesRegex(RuntimeError, 'Empty or missing Codex account identity'):
                b.update(week, short, 1900000000, account_id=None)
            with self.assertRaisesRegex(RuntimeError, 'Empty or missing Codex account identity'):
                b.update(week, short, 1900000000, account_id='')
            with self.assertRaisesRegex(RuntimeError, 'Empty or missing Codex account identity'):
                b.update(week, short, 1900000000, account_id='   ')

    def test_account_switch_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            b = Budget(path)
            b.update(week, short, 1900000000, account_id='acc_original')
            self.assertEqual(b.data['codex_account_id'], 'acc_original')

            with self.assertRaisesRegex(RuntimeError, 'Codex account switch detected'):
                Budget(path).update(week, short, 1900000001, account_id='acc_switched')

            week['codex']['used'] = 15
            b2 = Budget(path)
            res = b2.update(week, short, 1900000002, account_id='acc_original')
            self.assertEqual(res['pools']['codex']['peak'], 15)
            self.assertEqual(res['codex_account_id'], 'acc_original')

    def test_unbound_legacy_ledger_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'legacy.json'
            legacy_data = {
                'pools': {'codex': {'reset': 2000000000, 'base': 0, 'peak': 10}},
                'history': [{'pool': 'codex', 'reset': 1900000000, 'base': 0, 'peak': 20}]
            }
            path.write_text(json.dumps(legacy_data), encoding='utf-8')
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            with self.assertRaisesRegex(RuntimeError, 'Unbound legacy ledger'):
                Budget(path).update(week, short, 1900000000, account_id='new_acc')

    def test_early_reset_drift_fails_closed_and_preserves_peak(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            b = Budget(path)
            b.update(week, short, 1900000000, account_id='acc1')

            week['codex']['reset'] = 2000050000
            week['codex']['used'] = 14
            with self.assertRaisesRegex(RuntimeError, 'Reset timestamp drift detected'):
                Budget(path).update(week, short, 1900000005, account_id='acc1')

            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['pools']['codex']['peak'], 14)

    def test_decreasing_usage_fails_closed_and_preserves_peak(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 15, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            b = Budget(path)
            b.update(week, short, 1900000000, account_id='acc1')

            week['codex']['used'] = 8
            with self.assertRaisesRegex(RuntimeError, 'Usage decrease detected'):
                Budget(path).update(week, short, 1900000005, account_id='acc1')

            saved = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['pools']['codex']['peak'], 15)

    def test_per_job_completion_authorization_success_and_fallback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            auth_data = {
                'codex_account_id': 'acc_authorized',
                'reset': 2000000000,
                'run_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'stop_used': 28,
                'cap_used': 30
            }
            auth_file = Path(d)/'completion_auth.json'
            auth_file.write_text(json.dumps(auth_data), encoding='utf-8')

            b = Budget(path, completion_auth=auth_file, run_id='cloud-20261026-000000', goal=JOB_GOAL)
            b.data['codex_account_id'] = 'acc_authorized'
            self.assertEqual(b.stop_at('codex', 2000000000, 0, account_id='acc_authorized'), 28)

            # Never relaxes Anti pool
            self.assertEqual(b.stop_at('anti:gemini models', 2000000000, 0), 23)

            # Mismatched account falls back to 23
            self.assertEqual(b.stop_at('codex', 2000000000, 0, account_id='acc_wrong'), 23)

            # Mismatched reset falls back to 23
            self.assertEqual(b.stop_at('codex', 2000099999, 0, account_id='acc_authorized'), 23)

            # Mismatched run_id falls back to 23
            b_diff_run = Budget(path, completion_auth=auth_file, run_id='different-run-id', goal=JOB_GOAL)
            self.assertEqual(b_diff_run.stop_at('codex', 2000000000, 0, account_id='acc_authorized'), 23)

    def test_malformed_completion_auth_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'

            # 1. Non-existent file
            with self.assertRaisesRegex(RuntimeError, 'Completion auth file not found'):
                Budget(path, completion_auth=Path(d)/'missing.json')

            # 2. Invalid JSON syntax
            bad_json = Path(d)/'bad.json'
            bad_json.write_text('{not_json}', encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'Malformed completion auth'):
                Budget(path, completion_auth=bad_json)

            # 3. Missing required field
            missing_fields = Path(d)/'missing_fields.json'
            missing_fields.write_text(json.dumps({'codex_account_id': 'acc'}), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'missing required fields'):
                Budget(path, completion_auth=missing_fields)

            # 4. stop_used > cap_used
            invalid_relation = Path(d)/'invalid_relation.json'
            invalid_relation.write_text(json.dumps({
                'codex_account_id': 'acc',
                'reset': 2000000000,
                'run_id': 'run1',
                'goal': 'goal',
                'stop_used': 35,
                'cap_used': 30
            }), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'must be at least 2 points below'):
                Budget(path, completion_auth=invalid_relation)

            # 5. stop_used violates 2-point reserve (29 vs 30)
            tight_reserve = Path(d)/'tight_reserve.json'
            tight_reserve.write_text(json.dumps({
                'codex_account_id': 'acc',
                'reset': 2000000000,
                'run_id': 'run1',
                'goal': 'goal',
                'stop_used': 29,
                'cap_used': 30
            }), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'must be at least 2 points below'):
                Budget(path, completion_auth=tight_reserve)

    def test_extract_account_id_validation(self):
        self.assertEqual(extract_account_id({'accountId': 'usr_abc123'}), 'usr_abc123')
        self.assertEqual(extract_account_id({'account': {'id': 'usr_xyz456'}}), 'usr_xyz456')

        with self.assertRaises(ValueError):
            extract_account_id({})
        with self.assertRaises(ValueError):
            extract_account_id({'accountId': '   '})
        with self.assertRaises(ValueError):
            extract_account_id('not-a-dict')

    # --- Resume, Hash Integrity, and Stage Machine Tests ---

    def test_checkpoint_in_flight_mutation_marks_needs_attention(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'planned',
                'in_flight': True,
                'in_flight_target': 'anti_round_0',
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 1,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': []
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}):
                (Path(d)/'ALLOW_ANTI').write_text('1')
                with self.assertRaisesRegex(RuntimeError, 'Interrupted in-flight mutation; marked needs_attention'):
                    run_job(resume=True, run_dir=str(run_dir))
                mock_fcntl.flock.assert_called_once()

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['stage'], 'needs_attention')

    def test_resume_source_or_test_hash_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            bad_hashes = compute_source_hashes()
            bad_hashes['extension/manifest.json'] = '0' * 64
            checkpoint = {
                'stage': 'planned',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': bad_hashes,
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 1,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': []
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}):
                (Path(d)/'ALLOW_ANTI').write_text('1')
                with self.assertRaisesRegex(RuntimeError, 'Source hash mismatch'):
                    run_job(resume=True, run_dir=str(run_dir))

            # Test hash mismatch
            checkpoint['source_hashes'] = compute_source_hashes()
            checkpoint['test_hashes'] = {'tests/fake.cjs': '0000000000000000000000000000000000000000000000000000000000000000'}
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}):
                with self.assertRaisesRegex(RuntimeError, 'Regression test'):
                    run_job(resume=True, run_dir=str(run_dir))

    def test_resume_completed_run_validates_and_makes_no_model_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'static_review_pass',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 3,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': [{
                    'round': 0,
                    'files': ['extension/popup.js'],
                    'tests_exit': 0,
                    'review': {'verdict': 'PASS', 'issues': [], 'limitations': []}
                }]
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.call') as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertTrue(result)
                mock_call.assert_not_called()
                mock_fcntl.flock.assert_called_once()

    def test_resume_stage_implemented_does_not_replay_anti(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'implemented',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 2,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': []
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (run_dir/'changed_files_0.json').write_text(json.dumps(['extension/popup.js']), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            test_res = Mock(returncode=0, stdout='ok', stderr='')
            review_res = {'verdict': 'PASS', 'issues': [], 'limitations': []}

            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.run_node_tests', return_value=test_res), \
                 patch('runner.call', return_value=review_res) as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertTrue(result)

                # Ensure Anti was NEVER called; only Codex review was called
                called_targets = [call_args[0][0] for call_args in mock_call.call_args_list]
                self.assertNotIn('anti', called_targets)
                self.assertIn('codex', called_targets)

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['stage'], 'static_review_pass')

    def test_resume_stage_reviewed_advances_round_with_persisted_feedback(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            persisted_feedback = '{"verdict":"CHANGES","issues":["Fix storage leak"]}\nTESTS:\nfail'
            checkpoint = {
                'stage': 'reviewed',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 3,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': [{'round': 0, 'files': [], 'tests_exit': 1, 'review': {'verdict': 'CHANGES', 'issues': ['Fix storage leak']}}],
                'feedback': persisted_feedback
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (run_dir/'tests-0.txt').write_text('fail', encoding='utf-8')
            (run_dir/'codex_review_0.json').write_text(json.dumps({'verdict': 'CHANGES', 'issues': ['Fix storage leak'], 'limitations': []}), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            test_res = Mock(returncode=0, stdout='ok', stderr='')
            review_pass = {'verdict': 'PASS', 'issues': [], 'limitations': []}

            def mock_call_impl(target, prompt, *args, **kwargs):
                if target == 'anti':
                    self.assertIn('Fix storage leak', prompt)
                    return 'Anti updated code'
                return review_pass

            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.audit_workspace_scope', return_value=['extension/popup.js']), \
                 patch('runner.validate_manifest_permissions', return_value=None), \
                 patch('runner.run_node_tests', return_value=test_res), \
                 patch('runner.call', side_effect=mock_call_impl) as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertTrue(result)

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['round'], 1)
            self.assertEqual(saved['stage'], 'static_review_pass')

    def test_resume_reviewed_pass_without_model_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'reviewed',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 3,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': [{'round': 0, 'files': ['extension/popup.js'], 'tests_exit': 0, 'review': {'verdict': 'PASS', 'issues': [], 'limitations': []}}],
                'feedback': None
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.call') as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertTrue(result)
                mock_call.assert_not_called()

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['stage'], 'static_review_pass')

    def test_resume_malformed_checkpoint_no_calls(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            bad_checkpoint = {
                'stage': 'planned',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'deadline': 3000000000
                # Missing round, model_calls, started_at, plan
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(bad_checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.call') as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                with self.assertRaises(RuntimeError):
                    run_job(resume=True, run_dir=str(run_dir))
                mock_call.assert_not_called()

    def test_max_calls_across_resume_enforced(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'planned',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 5,  # Max 5 calls already reached
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': []
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.call') as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertFalse(result)
                mock_call.assert_not_called()

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['stage'], 'blocked')
            self.assertIn('Maximum model calls (5) reached', saved['reason'])

    def test_direct_anti_permission_denial_halts_without_subsequent_call(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            run_dir = Path(d)/'cloud-20261026-000000'
            run_dir.mkdir()
            checkpoint = {
                'stage': 'planned',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': 'cloud-20261026-000000',
                'job_id': 'cloud-20261026-000000',
                'goal': JOB_GOAL,
                'codex_account_id': 'test-acc',
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 1,
                'started_at': time.time() - 30,
                'deadline': time.time() + 600,
                'plan': {'plan': ['p1'], 'acceptance_criteria': ['c1']},
                'rounds': []
            }
            (run_dir/'checkpoint.json').write_text(json.dumps(checkpoint), encoding='utf-8')
            (Path(d)/'budget.json').write_text(json.dumps({'codex_account_id': 'test-acc', 'pools': {}, 'history': []}))

            mock_fcntl = make_mock_fcntl()
            with patch('runner.STATE', Path(d)), \
                 patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', mock_fcntl), \
                 patch('runner.Budget.check', return_value={}), \
                 patch('runner.call', side_effect=RuntimeError('Anti permission denial or unsuccessful result; no retry')) as mock_call:
                (Path(d)/'ALLOW_ANTI').write_text('1')
                result = run_job(resume=True, run_dir=str(run_dir))
                self.assertFalse(result)
                # Only 1 call attempted (Anti), failed, Codex was never called
                self.assertEqual(mock_call.call_count, 1)

            saved = json.loads((run_dir/'checkpoint.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['stage'], 'blocked')
            self.assertIn('Anti permission denial', saved['reason'])

    def test_run_path_rejects_symlink_and_traversal(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            state = Path(d)
            with patch('runner.STATE', state):
                # 1. Traversal outside STATE
                outside = state.parent / 'outside_run'
                with self.assertRaisesRegex(RuntimeError, 'must resolve directly under'):
                    validate_run_path(outside)

                # 2. Path containing ../
                traversal = state / '..' / 'escape'
                with self.assertRaisesRegex(RuntimeError, 'must resolve directly under'):
                    validate_run_path(traversal)

                # 3. Empty path
                with self.assertRaisesRegex(RuntimeError, 'Missing run path'):
                    validate_run_path('')

                # 4. New run must not exist
                existing = state / 'exists'
                existing.mkdir()
                with self.assertRaisesRegex(RuntimeError, 'already exists'):
                    validate_run_path(existing, must_not_exist=True)


if __name__ == '__main__':
    unittest.main()
