"""One confirmed quota reconciliation before a finite Codespace verification.

This does not redeem a reset, alter CLI permissions, or grant Anti more budget.
All evidence and backups stay in the ignored runtime directory.
"""
import argparse
import copy
import json
from pathlib import Path
import tempfile
import time

import quota_client
import runner
from quota_parser import extract_account_id, percent, save, snapshot, stamp


def reconcile(ledger, week, account_id, old_reset, new_reset, prior_peak, anti_floors):
    if not isinstance(account_id, str) or ledger.get('codex_account_id') != account_id:
        raise RuntimeError('Account mismatch; no ledger change')
    if set(runner.POOLS) - set(week):
        raise RuntimeError('Missing quota pool; no ledger change')
    old_reset, new_reset = stamp(old_reset), stamp(new_reset)
    prior_peak = percent(prior_peak)
    if new_reset <= old_reset or stamp(week['codex']['reset']) != new_reset:
        raise RuntimeError('Live reset does not match the explicitly confirmed transition')
    data = copy.deepcopy(ledger)
    pools = data.get('pools', {})
    codex = pools.get('codex')
    if not isinstance(codex, dict):
        raise RuntimeError('Missing retained Codex ledger')
    transition = {'account_id': account_id, 'old_reset': old_reset, 'new_reset': new_reset}
    records = data.setdefault('confirmed_resets', [])
    existing = next((r for r in records if all(r.get(k) == v for k, v in transition.items())), None)
    current_reset = stamp(codex['reset'])
    if current_reset == old_reset:
        if existing:
            raise RuntimeError('Inconsistent retained reset evidence')
        archived = copy.deepcopy(codex)
        archived['peak'] = max(percent(archived['peak']), prior_peak)
        data.setdefault('history', []).append({
            **archived, 'pool': 'codex', 'account_id': account_id,
            'reason': 'User explicitly confirmed this early provider reset',
            'external_session_peak': prior_peak})
        pools['codex'] = {
            'reset': new_reset, 'base': 0,
            'peak': percent(week['codex']['used']),
            'last_used': percent(week['codex']['used'])}
        records.append({**transition, 'confirmed': True, 'recorded_at': time.time(),
                        'retained_old_peak': archived['peak']})
    elif current_reset == new_reset and existing and existing.get('confirmed') is True:
        if percent(existing['retained_old_peak']) < prior_peak:
            raise RuntimeError('New prior-period evidence needs explicit reconciliation')
    else:
        raise RuntimeError('Ledger period does not match this confirmed transition')

    # Retain every Anti pool and baseline. Floors include observed other sessions.
    # Reconciling stale display values never lowers the recorded peak or raises a cap.
    for key in runner.POOLS:
        if key == 'codex':
            continue
        old = pools.get(key)
        if not isinstance(old, dict) or percent(old['base']) != 0:
            raise RuntimeError('Unknown Anti baseline; manual inspection required')
        if stamp(old['reset']) != stamp(week[key]['reset']):
            raise RuntimeError('Anti period changed; no automatic reset is authorized')
        old['peak'] = max(percent(old['peak']), percent(week[key]['used']),
                          percent(anti_floors[key]))
        if max(old['peak'], percent(old.get('charged_used', old['peak']))) >= 23:
            raise RuntimeError('Anti budget stop; no model launch allowed')
        if 'charged_used' not in old:
            old['last_used'] = percent(week[key]['used'])
    return data


def prepare(old_reset, new_reset, prior_peak, anti_floors, run_path):
    if runner.sys.platform != 'linux' or runner.fcntl is None:
        raise RuntimeError('Run this preparation on the Linux Codespace')
    state = runner.STATE
    if (state / 'STOP').exists() or not (state / 'ALLOW_ANTI').is_file():
        raise RuntimeError('STOP or missing Anti authorization; inspect before proceeding')
    run = runner.validate_run_path(run_path, must_not_exist=True)
    with (state / 'runner.lock').open('a') as lock:
        runner.fcntl.flock(lock, runner.fcntl.LOCK_EX | runner.fcntl.LOCK_NB)
        path = state / 'budget.json'
        original_bytes = path.read_bytes()
        ledger = json.loads(original_bytes)
        raw = quota_client.quota(raw=True)
        account_id = extract_account_id(raw)
        week, short = snapshot(raw, quota_client.anti_quota())
        planned = reconcile(ledger, week, account_id, old_reset, new_reset, prior_peak, anti_floors)
        # Exercise the unchanged normal guard in temporary runtime state first.
        # Failure cannot partially update the real ledger.
        with tempfile.TemporaryDirectory(prefix='quota-preflight-', dir=state) as folder:
            guard = runner.Budget(Path(folder) / 'budget.json')
            guard.data = planned
            validated = guard.update(week, short, time.time(), account_id=account_id)
        # No lost update or repeated transition after an interrupted preparation.
        if path.read_bytes() != original_bytes:
            raise RuntimeError('Ledger changed during preparation; no overwrite')
        backup = state / ('budget-before-confirmed-reset-%d-%d.json' %
                          (stamp(old_reset), stamp(new_reset)))
        already_prepared = stamp(ledger['pools']['codex']['reset']) == stamp(new_reset)
        if not already_prepared:
            with backup.open('xb') as output:
                output.write(original_bytes)
        elif not backup.is_file():
            raise RuntimeError('Missing original backup for previously prepared transition')
        save(path, validated)
        print('PREPARED: confirmed Codex reset; old peak/history retained.')
        print('Normal budget: 25 weekly points, stop at 23; Anti caps unchanged.')
        print('No model called. Start one run at: ' + str(run))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--confirmed-codex-reset', nargs=2, type=float, required=True,
                        metavar=('OLD_RESET', 'NEW_RESET'))
    parser.add_argument('--prior-codex-peak', type=float, required=True)
    parser.add_argument('--anti-shared-peak', type=float, required=True)
    parser.add_argument('--anti-argon-peak', type=float, required=True)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    runner.os.chdir(runner.ROOT)
    prepare(*args.confirmed_codex_reset, args.prior_codex_peak,
            {'anti:gemini models': args.anti_shared_peak,
             'anti:gemini 4 argon': args.anti_argon_peak}, args.run)
