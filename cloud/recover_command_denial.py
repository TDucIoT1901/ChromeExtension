"""Operator-invoked recovery of an inspected, command-denied verification.

No CLI permissions change. The old run is kept; its model-call count is carried
into a single new bounded attempt. No model is launched by this utility.
"""
import copy
import json
import time

import runner
from quota_parser import save


def recovery_checkpoint(previous, target_id, now, quota_interruption=False, write_permission_confirmed=False):
    if (previous.get('stage') != 'blocked' or previous.get('in_flight') is not False or
        previous.get('round') != 0 or previous.get('rounds') != []):
        raise RuntimeError('Not an inspected first-round failure; no recovery')
    if quota_interruption and write_permission_confirmed:
        raise RuntimeError('Select exactly one inspected recovery mode')
    if write_permission_confirmed:
        if (previous.get('model_calls') != 4 or previous.get('permission_recovery_of') or
            previous.get('reason') != 'Anti denied action(s): write_file; no retry'):
            raise RuntimeError('Not the inspected four-call write_file denial')
    elif quota_interruption:
        if (previous.get('model_calls') != 3 or not previous.get('recovery_of') or
            previous.get('quota_recovery_of') or not isinstance(previous.get('reason'), str) or
            not previous['reason'].startswith('Usage decrease detected for anti:gemini models ')):
            raise RuntimeError('Not the inspected three-call shared-quota interruption')
    elif (previous.get('model_calls') != 2 or previous.get('recovery_of') or previous.get('reason') not in (
        'Anti permission denial or unsuccessful result; no retry',
        'Anti denied action(s): command; no retry')):
        raise RuntimeError('Failure reason is not the inspected command denial')
    result = copy.deepcopy(previous)
    result.update(stage='planned', in_flight=False, in_flight_target=None,
                  run_id=target_id, job_id=target_id, started_at=now,
                  deadline=now + 1200,
                  recovery_of=previous.get('recovery_of') or previous['run_id'])
    if quota_interruption:
        result['quota_recovery_of'] = previous['run_id']
    if write_permission_confirmed:
        result.update(permission_recovery_of=previous['run_id'],
                      write_permission_confirmed=True,
                      inherited_model_calls=4, model_call_limit=8)
    result.pop('reason', None)
    result.pop('finished', None)
    return result


def verify_failure_artifact(raw, quota_interruption=False, write_permission_confirmed=False):
    if not isinstance(raw, dict):
        raise RuntimeError('Invalid Anti failure artifact')
    raw = raw.get('result', raw)
    if not isinstance(raw, dict):
        raise RuntimeError('Invalid Anti failure result')
    denied = raw.get('denied_actions')
    if write_permission_confirmed:
        valid = (isinstance(denied, list) and bool(denied) and
                 all(isinstance(item, dict) and item.get('action') == 'write_file' for item in denied))
    elif quota_interruption:
        valid = (raw.get('status') == 'ERROR' and raw.get('error') == 'interrupted' and
                 raw.get('response') == '' and not denied)
    else:
        valid = (raw.get('status') == 'SUCCESS' and raw.get('response') in ('', None) and
                 isinstance(denied, list) and len(denied) == 1 and
                 isinstance(denied[0], dict) and denied[0].get('action') == 'command')
    if not valid:
        raise RuntimeError('Anti artifact does not match the inspected failure; no recovery')


def prepare(source_path, target_path, quota_interruption=False, write_permission_confirmed=False):
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
        result = recovery_checkpoint(previous, target.name, time.time(),
                                     quota_interruption, write_permission_confirmed)
        filename = ('04-anti.log' if write_permission_confirmed else
                    '03-anti.log' if quota_interruption else '02-anti.log')
        raw = json.loads((source / filename).read_text(encoding='utf-8'))
        verify_failure_artifact(raw, quota_interruption, write_permission_confirmed)
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
        count = result['model_calls']
        limit = runner.checkpoint_call_limit(result)
        print(f'RECOVERY PREPARED: prior {count} calls retained; at most {limit-count} more calls (total cap {limit}).')
        print('Old run and CLI permissions unchanged. No model called.')
        print('Resume new attempt at: ' + str(target))


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--from-run', required=True)
    parser.add_argument('--to-run', required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--quota-interruption', action='store_true',
                        help='Only the inspected interrupted shared-quota attempt; preserves 3 calls')
    modes.add_argument('--write-permission-confirmed', action='store_true',
                       help='Operator confirms scoped extension write permission; preserves 4 calls, total cap 8')
    args = parser.parse_args()
    runner.os.chdir(runner.ROOT)
    prepare(args.from_run, args.to_run, args.quota_interruption, args.write_permission_confirmed)
