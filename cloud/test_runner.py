import json
import tempfile
import unittest
from unittest.mock import patch, Mock
import subprocess
from pathlib import Path
from runner import Budget, proposed_files, POOLS, call

class BudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (Path(__file__).resolve().parent.parent / '.anti-codex').mkdir(exist_ok=True)
    def test_unknown_pool_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            with self.assertRaises(RuntimeError):
                Budget(Path(d)/'b.json').update({}, {}, 1)

    def test_existing_ledger_does_not_grant_another_budget(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 2000000000} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            Budget(path).update(week, short, 1900000000)
            week['codex']['used'] = 23
            with self.assertRaises(RuntimeError):
                Budget(path).update(week, short, 1900000001)

    def test_confirmed_reset_uses_zero_baseline(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'b.json'
            week = {k: {'used': 10, 'reset': 1900000010} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            Budget(path).update(week, short, 1900000000)
            for v in week.values(): v.update(used=2, reset=1900604810)
            record = Budget(path).update(week, short, 1900000020)
            self.assertTrue(all(v['base'] == 0 for v in record['pools'].values()))

    def test_proposals_cannot_escape_scope(self):
        for proposal in ({'../escape': 'x'}, {'cloud/runner.py': 'x'}, {'extension/popup.js': 1}):
            with self.assertRaises(ValueError): proposed_files(proposal)

    def test_empty_proposal_is_allowed(self):
        self.assertEqual(proposed_files({}), {})

    def test_manifest_cannot_gain_host_permissions(self):
        manifest = {'manifest_version': 3, 'permissions': ['storage', 'activeTab'], 'host_permissions': ['<all_urls>']}
        with self.assertRaises(ValueError):
            proposed_files({'extension/manifest.json': json.dumps(manifest)})

    def test_supplement_preserves_baseline_and_stops_at_28(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'budget.json'
            (path.parent/'authorization.json').write_text(json.dumps(dict(
                reset=1793428548, cap_used=30, stop_used=28, extra_points=5)))
            week = {k: {'used': 0, 'reset': 1793428548} for k in POOLS}
            short = {k: {'used': 0} for k in POOLS}
            week['codex']['used'] = 27
            record = Budget(path).update(week, short, 1792850000)
            self.assertEqual(record['pools']['codex']['base'], 1)
            week['codex']['used'] = 28
            with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop'):
                Budget(path).update(week, short, 1792850001)

    def test_supplement_does_not_expand_anti_or_future_weeks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            path = Path(d)/'budget.json'
            (path.parent/'authorization.json').write_text(json.dumps(dict(
                reset=1793428548, cap_used=30, stop_used=28, extra_points=5)))
            guard = Budget(path)
            self.assertEqual(guard.stop_at('anti:gemini models',1793428548,0),23)
            self.assertEqual(guard.stop_at('codex',1794033348,0),23)

    def test_preflight_failure_never_launches_model(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            guard = Mock()
            guard.check.side_effect = RuntimeError('Unreadable quota')
            with patch('runner.subprocess.Popen') as launch:
                with self.assertRaisesRegex(RuntimeError, 'Unreadable'):
                    call('anti','test',Path(d),1,guard,2000000000)
                launch.assert_not_called()

    def test_monitor_cancels_worker_on_unreadable_quota(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent.parent / '.anti-codex') as d:
            guard = Mock()
            guard.check.side_effect = [None,RuntimeError('Unreadable quota')]
            worker = Mock(pid=4321)
            worker.poll.side_effect = [None,None]
            worker.wait.side_effect = [subprocess.TimeoutExpired('test',15),0]
            with patch('runner.subprocess.Popen',return_value=worker), patch('runner.os.killpg',create=True) as kill:
                with self.assertRaisesRegex(RuntimeError,'Unreadable'):
                    call('anti','test',Path(d),1,guard,2000000000)
                kill.assert_called_once()
                self.assertEqual(kill.call_args.args[0],4321)

if __name__ == '__main__': unittest.main()
