"""Protocol and Linux process checks; never launch a model or read credentials."""
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import quota_client
import runner


class CapturedInput(io.StringIO):
    def close(self):
        pass


class ProtocolTests(unittest.TestCase):
    def test_invalid_checkpoint_cannot_expand_call_or_time_limits(self):
        checkpoint = {'stage': 'planned', 'codex_account_id': 'account-a',
                      'goal': runner.JOB_GOAL, 'run_id': 'cloud-test',
                      'started_at': time.time() - 10, 'deadline': time.time() + 600,
                      'model_calls': 1, 'round': 0,
                      'plan': {'plan': ['check'], 'acceptance_criteria': ['pass']}}
        for field, value in [('model_calls', True), ('model_calls', 6),
                             ('round', 2), ('deadline', float('nan')),
                             ('deadline', time.time() + 3600), ('started_at', True)]:
            with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                runner.validate_checkpoint_evidence({**checkpoint, field: value},
                                                    Path('cloud-test'), 'account-a')

    def read_protocol(self, responses):
        input_stream = CapturedInput()
        output = io.StringIO(''.join(json.dumps({'id': i, 'result': value}) + '\n'
                                   for i, value in responses))
        proc = Mock(stdin=input_stream, stdout=output, pid=12345)
        proc.poll.return_value = 0
        with patch('quota_client.subprocess.Popen', return_value=proc), \
             patch('quota_client.stop_process') as cleanup:
            result = quota_client.quota(raw=True)
        cleanup.assert_called_once_with(proc)
        return result, [json.loads(line) for line in input_stream.getvalue().splitlines()]

    def quota_packet(self, account='account-a'):
        return {'accountId': account, 'rateLimits': {
            'secondary': {'windowDurationMins': 10080, 'usedPercent': 12, 'resetsAt': 2000000000}}}

    def test_documented_account_read_parameters_and_fallback_identity(self):
        account = {'account': {'type': 'chatgpt', 'planType': 'plus'}}
        result, sent = self.read_protocol([
            (1, {}), (2, account), (3, self.quota_packet()), (4, account),
            (5, self.quota_packet())])
        reads = [message for message in sent if message.get('method') == 'account/read']
        self.assertEqual(len(reads), 2)
        self.assertTrue(all(message['params'] == {'refreshToken': False} for message in reads))
        self.assertEqual(result['accountId'], 'account-a')
        self.assertEqual(quota_client.weekly_used(result), 12)

    def test_account_switch_during_read_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'switched'):
            self.read_protocol([
                (1, {}), (2, {'account': {'id': 'account-a'}}),
                (3, self.quota_packet()), (4, {'account': {'id': 'account-b'}})])

    def test_fallback_requires_second_matching_quota_identity(self):
        account = {'account': {'type': 'chatgpt'}}
        with self.assertRaisesRegex(RuntimeError, 'switched or missing'):
            self.read_protocol([
                (1, {}), (2, account), (3, self.quota_packet()),
                (4, account), (5, self.quota_packet('account-b'))])

    def test_no_identity_does_not_produce_a_budget(self):
        with self.assertRaisesRegex(RuntimeError, 'Missing Codex account identity'):
            self.read_protocol([(1, {}), (2, {}), (3, {'rateLimits': {}}), (4, {})])

    def test_cleanup_kills_group_even_after_parent_exit(self):
        proc = Mock(pid=12345)
        proc.poll.return_value = 0
        with patch('quota_client.os.killpg', create=True) as kill, \
             patch('quota_client.signal.SIGKILL', 9, create=True):
            quota_client.stop_process(proc)
        self.assertEqual([entry.args for entry in kill.call_args_list],
                         [(12345, signal.SIGTERM), (12345, 9)])


@unittest.skipUnless(sys.platform == 'linux', 'Real fcntl lock requires Linux')
class LinuxLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runner.STATE.mkdir(exist_ok=True)

    def test_another_process_cannot_enter_runner_while_locked(self):
        import fcntl
        with tempfile.TemporaryDirectory(dir=runner.STATE) as folder:
            state = Path(folder)
            (state / 'ALLOW_ANTI').write_text('authorized test fixture', encoding='utf-8')
            with (state / 'runner.lock').open('a') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                script = """import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import runner
with patch('runner.STATE', Path(sys.argv[2])), patch('runner.Budget.check', side_effect=AssertionError('lock failed')):
    try:
        runner.run_job()
    except BlockingIOError:
        print('single-writer-lock-confirmed')
    else:
        raise AssertionError('second writer entered')
"""
                result = subprocess.run(
                    [sys.executable, '-c', script, str(runner.ROOT / 'cloud'), folder],
                    cwd=runner.ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('single-writer-lock-confirmed', result.stdout)


if __name__ == '__main__':
    unittest.main()
