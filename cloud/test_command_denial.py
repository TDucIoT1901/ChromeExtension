import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import runner
from recover_command_denial import prepare, recovery_checkpoint, verify_failure_artifact


class CommandDenialTests(unittest.TestCase):
    def checkpoint(self):
        now = time.time()
        return dict(stage='blocked', in_flight=False, in_flight_target=None,
                    run_id='cloud-old', goal=runner.JOB_GOAL,
                    codex_account_id='fixture-account', started_at=now-30,
                    deadline=now+600, model_calls=2, round=0, rounds=[],
                    plan={'plan':['check'], 'acceptance_criteria':['pass']},
                    source_hashes=runner.compute_source_hashes(),
                    test_hashes=runner.compute_test_hashes(),
                    reason='Anti permission denial or unsuccessful result; no retry')

    def test_prompt_supplies_source_and_forbids_commands(self):
        packet = runner.anti_evidence()
        self.assertIn('Do not run commands', packet)
        self.assertIn('host runs all commands and tests', packet)
        for path, content in runner.read_source_files().items():
            self.assertIn(json.dumps(path), packet)
            self.assertIn(json.dumps(content), packet)

    def test_parser_never_treats_success_with_denial_as_success(self):
        raw = json.dumps(dict(status='SUCCESS', response='', denied_actions=[{'action':'command'}]))
        with self.assertRaisesRegex(RuntimeError, 'denied action.*command'):
            runner.anti_answer(raw)
        with self.assertRaisesRegex(RuntimeError, 'empty response'):
            runner.anti_answer(json.dumps(dict(status='SUCCESS', response='', denied_actions=[])))
        self.assertEqual(runner.anti_answer(json.dumps(dict(status='SUCCESS', response='done'))), 'done')

    def test_call_count_is_carried_and_nested_recovery_is_rejected(self):
        previous = self.checkpoint()
        result = recovery_checkpoint(previous, 'cloud-next', time.time())
        self.assertEqual(result['model_calls'], 2)
        self.assertEqual(result['stage'], 'planned')
        self.assertEqual(previous['stage'], 'blocked')
        self.assertEqual(result['deadline']-result['started_at'], 1200)
        with self.assertRaises(RuntimeError):
            recovery_checkpoint({**previous, 'recovery_of':'older'}, 'another', time.time())

    def test_unchanged_original_and_single_successor_no_model_call(self):
        runner.STATE.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runner.STATE) as folder:
            state = Path(folder)
            source = state/'cloud-old'
            source.mkdir()
            previous = self.checkpoint()
            original = json.dumps(previous).encode()
            (source/'checkpoint.json').write_bytes(original)
            (source/'codex_plan.json').write_text(json.dumps(previous['plan']))
            (source/'02-anti.log').write_text(json.dumps(dict(
                status='SUCCESS', response='', denied_actions=[{'action':'command'}])))
            (state/'budget.json').write_text(json.dumps(dict(
                codex_account_id='fixture-account', pools={}, history=[])))
            (state/'ALLOW_ANTI').write_text('test fixture')
            fake_lock = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
            with patch('runner.STATE', state), patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', fake_lock), patch('runner.Budget.check'), \
                 patch('runner.call') as model:
                prepare(source, state/'cloud-next')
                self.assertEqual((source/'checkpoint.json').read_bytes(), original)
                self.assertEqual(json.loads((state/'cloud-next'/'checkpoint.json').read_text())['model_calls'], 2)
                with self.assertRaises(FileExistsError):
                    prepare(source, state/'cloud-another')
                model.assert_not_called()

    def test_quota_interruption_preserves_three_calls_and_cannot_repeat(self):
        old = {**self.checkpoint(), 'model_calls': 3, 'recovery_of': 'cloud-root',
               'reason': 'Usage decrease detected for anti:gemini models (2.0% < 3.0%); stop pending reconciliation'}
        result = recovery_checkpoint(old, 'cloud-final', time.time(), quota_interruption=True)
        self.assertEqual(result['model_calls'], 3)
        self.assertEqual(result['recovery_of'], 'cloud-root')
        self.assertEqual(result['quota_recovery_of'], 'cloud-old')
        with self.assertRaises(RuntimeError):
            recovery_checkpoint({**old, 'quota_recovery_of': 'older'},
                                'another', time.time(), quota_interruption=True)
        with self.assertRaises(RuntimeError):
            recovery_checkpoint({**old, 'model_calls': 4},
                                'another', time.time(), quota_interruption=True)

    def test_interruption_artifact_cannot_hide_a_permission_denial(self):
        raw = {'status':'ERROR', 'error':'interrupted', 'response':''}
        verify_failure_artifact(raw, quota_interruption=True)
        for changed in ({'error':'other'}, {'response':'partial'},
                        {'denied_actions':[{'action':'write_file'}]}):
            with self.assertRaises(RuntimeError):
                verify_failure_artifact({**raw, **changed}, quota_interruption=True)

    def permission_failure(self):
        return {**self.checkpoint(), 'model_calls': 4,
                'reason': 'Anti denied action(s): write_file; no retry'}

    def test_write_permission_recovery_is_scoped_and_keeps_four_calls(self):
        previous = self.permission_failure()
        with self.assertRaises(RuntimeError):
            recovery_checkpoint(previous, 'next', time.time())
        result = recovery_checkpoint(previous, 'next', time.time(),
                                     write_permission_confirmed=True)
        self.assertEqual(result['model_calls'], 4)
        self.assertEqual(runner.checkpoint_call_limit(result), 8)
        self.assertEqual(runner.checkpoint_call_limit(previous), 5)
        with self.assertRaises(RuntimeError):
            runner.checkpoint_call_limit({'model_call_limit': 8})
        with self.assertRaises(RuntimeError):
            runner.checkpoint_call_limit({**result, 'write_permission_confirmed': False})
        with self.assertRaises(RuntimeError):
            recovery_checkpoint({**previous, 'permission_recovery_of': 'older'},
                                'another', time.time(), write_permission_confirmed=True)
        verify_failure_artifact({'denied_actions': [{'action': 'write_file'}]},
                                write_permission_confirmed=True)
        with self.assertRaises(RuntimeError):
            verify_failure_artifact({'denied_actions': [{'action': 'command'}]},
                                    write_permission_confirmed=True)

    def test_permission_attempt_can_finish_two_rounds_at_eight_total_calls(self):
        runner.STATE.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runner.STATE) as folder:
            state = Path(folder)
            run = state/'cloud-permission-recovery'
            run.mkdir()
            checkpoint = recovery_checkpoint(self.permission_failure(), run.name,
                                              time.time(), write_permission_confirmed=True)
            (run/'checkpoint.json').write_text(json.dumps(checkpoint))
            (state/'budget.json').write_text(json.dumps(dict(
                codex_account_id='fixture-account', pools={}, history=[])))
            (state/'ALLOW_ANTI').write_text('test fixture')
            fake_lock = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
            results = ['implementation',
                       {'verdict':'CHANGES', 'issues':['specific fix'], 'limitations':[]},
                       'revision', {'verdict':'PASS', 'issues':[], 'limitations':[]}]
            with patch('runner.STATE', state), patch('runner.sys.platform', 'linux'), \
                 patch('runner.fcntl', fake_lock), patch('runner.Budget.check'), \
                 patch('runner.run_node_tests', return_value=Mock(returncode=0, stdout='ok', stderr='')), \
                 patch('runner.audit_workspace_scope', return_value=[]), \
                 patch('runner.call', side_effect=results) as model:
                self.assertTrue(runner.run_job(resume=True, run_dir=str(run)))
            saved = json.loads((run/'checkpoint.json').read_text())
            self.assertEqual(saved['model_calls'], 8)
            self.assertEqual(saved['stage'], 'static_review_pass')
            self.assertEqual([item.args[0] for item in model.call_args_list],
                             ['anti', 'codex', 'anti', 'codex'])
            anti_calls = [item for item in model.call_args_list if item.args[0] == 'anti']
            self.assertEqual([item.kwargs['anti_round'] for item in anti_calls], [0, 1])


if __name__ == '__main__':
    unittest.main()
