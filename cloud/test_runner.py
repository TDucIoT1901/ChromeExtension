import json
import tempfile
import unittest
from pathlib import Path
from runner import Budget, proposed_files, POOLS

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

if __name__ == '__main__': unittest.main()
