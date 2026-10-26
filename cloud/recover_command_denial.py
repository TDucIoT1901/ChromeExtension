"""Operator-invoked recovery of an inspected, command-denied verification.

No CLI permissions change. The old run is kept; its model-call count is carried
into a single new bounded attempt. No model is launched by this utility.
"""
import copy
import json
import time

import runner
from quota_parser import save


def recovery_checkpoint(previous, target_id, now):
    if (previous.get('stage') != 'blocked' or previous.get('in_flight') is not False or
        previous.get('round') != 0 or previous.get('model_calls') != 2 or
        previous.get('rounds') != [] or previous.get('recovery_of')):
        raise RuntimeError('Not the inspected first-round two-call failure; no recovery')
    if previous.get('reason') not in (
        'Anti permission denial or unsuccessful result; no retry',
        'Anti denied action(s): command; no retry'):
        raise RuntimeError('Failure reason is not the inspected command denial')
    result = copy.deepcopy(previous)
    result.update(stage='planned', in_flight=False, in_flight_target=None,
                  run_id=target_id, job_id=target_id, started_at=now,
                  deadline=now + 1200, recovery_of=previous['run_id'])
    result.pop('reason', None)
    result.pop('finished', None)
    return result


def prepare(source_path, target_path):
    if runner.sys.platform != 'linux' or runner.fcntl is None:
        raise RuntimeError('Run this on the Linux cloud host')
    if (runner.STATE / 'STOP').exists() or not (runner.STATE / 'ALLOW_ANTI').is_file():
        raise RuntimeError('STOP or missing Anti authorization; no recovery')
    source = runner.validate_run_path(source_path)
    target = runner.validate_run_path(target_path, must_not_exist=True)
    with (runner.STATE / 'runner.lock').open('a') as lock:
        runner.fcntl.flock(lock, runner.fcntl.LOCK_EX | runner.fcntl.LOCK_NB)
        old_bytes = (source / 'checkpoint.json').read_bytes()
        previous = json.loads(old_bytes)
        budget = runner.Budget(runner.STATE / 'budget.json')
        budget.check()
        runner.validate_checkpoint_evidence(previous, source, budget.data.get('codex_account_id'))
        runner.verify_hashes(previous.get('source_hashes'), previous.get('test_hashes'))
        result = recovery_checkpoint(previous, target.name, time.time())
        raw = json.loads((source / '02-anti.log').read_text(encoding='utf-8'))
        raw = raw.get('result', raw)
        denied = raw.get('denied_actions')
        if (raw.get('status') != 'SUCCESS' or raw.get('response') not in ('', None) or
            not isinstance(denied, list) or len(denied) != 1 or
            not isinstance(denied[0], dict) or denied[0].get('action') != 'command'):
            raise RuntimeError('Anti artifact does not match the inspected command denial')
        if json.loads((source / 'codex_plan.json').read_text(encoding='utf-8')) != previous['plan']:
            raise RuntimeError('Persisted Codex plan mismatch')
        if (source / 'checkpoint.json').read_bytes() != old_bytes:
            raise RuntimeError('Original run changed; no recovery')
        # A source run can authorize only one successor, even with a new target name.
        with (source / 'recovered_into.txt').open('x', encoding='utf-8') as marker:
            marker.write(str(target))
        target.mkdir()
        save(target / 'codex_plan.json', previous['plan'])
        save(target / 'checkpoint.json', result)
        save(target / 'status.json', result)
        (runner.STATE / 'latest.txt').write_text(str(target), encoding='utf-8')
        print('RECOVERY PREPARED: prior 2 calls retained; at most 3 more calls.')
        print('Old run and CLI permissions unchanged. No model called.')
        print('Resume new attempt at: ' + str(target))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-run', required=True)
    parser.add_argument('--to-run', required=True)
    args = parser.parse_args()
    runner.os.chdir(runner.ROOT)
    prepare(args.from_run, args.to_run)
