import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import runner


class FlashOnlyTests(unittest.TestCase):
    def setUp(self):
        runner.STATE.mkdir(exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=runner.STATE)
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'budget.json'
        self.week = {key: {'used': 2, 'reset': 2000000000} for key in runner.POOLS}
        self.week['anti:gemini 4 argon']['used'] = 30
        self.short = {key: {'used': 0} for key in runner.POOLS}

    def update(self, policy='flash-only'):
        return runner.Budget(self.path, anti_model_policy=policy).update(
            self.week, self.short, 1900000000, account_id='fixture-account')

    def test_argon_stays_blocked_without_blocking_unused_flash_budget(self):
        data = self.update()
        argon = data['pools']['anti:gemini 4 argon']
        self.assertEqual(argon['peak'], 30)
        self.assertEqual(argon['charged_used'], 30)
        self.assertEqual(argon['base'], 0)
        self.assertIn('anti:gemini 4 argon', data['blocked_pools'])
        with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop.*argon'):
            self.update(runner.DEFAULT_ANTI_POLICY)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved['pools']['anti:gemini 4 argon']['charged_used'], 30)

    def test_flash_or_codex_stop_is_never_bypassed(self):
        self.week['anti:gemini models']['used'] = 23
        with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop.*gemini models'):
            self.update()
        self.week['anti:gemini models']['used'] = 2
        self.week['codex']['used'] = 23
        with self.assertRaisesRegex(RuntimeError, 'Weekly budget stop.*codex'):
            self.update()

    def test_unknown_pool_still_fails_closed_in_flash_only(self):
        del self.week['anti:gemini 4 argon']
        with self.assertRaisesRegex(RuntimeError, 'Missing actual quota pool'):
            self.update()

    def test_revision_subprocess_cannot_select_argon_in_flash_only(self):
        guard = runner.Budget(self.path, anti_model_policy='flash-only')
        run = Path(self.folder.name)
        observed = []
        def fake_process(args, **kwargs):
            observed.append(args)
            kwargs['stdout'].write(json.dumps({'status':'SUCCESS','response':'done'}).encode())
            process = Mock(pid=12345, returncode=0)
            process.poll.return_value = 0
            return process
        with patch.object(guard, 'check'), \
             patch('runner.subprocess.Popen', side_effect=fake_process), \
             patch('runner.quota_client.stop_process'):
            self.assertEqual(runner.call('anti', 'fixture', run, 7, guard,
                                         2000000000, anti_round=1), 'done')
        args = observed[0]
        self.assertEqual(args[args.index('--model')+1], 'gemini-3.8-flash-medium')
        self.assertNotIn('gemini-4-argon-medium', args)


if __name__ == '__main__':
    unittest.main()
