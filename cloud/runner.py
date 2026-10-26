"""Finite cloud collaboration: Codex plans, Anti implements, host tests, Codex reviews.

Uses existing account sign-ins. Never disables CLI security or purchases credits.
Quota polling is conservative best effort, not a provider-enforced spending cap.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

try:
    import fcntl
except ImportError:
    fcntl = None

import quota_client
from quota_parser import snapshot, save, percent, stamp, extract_account_id

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.anti-codex'
JOB_ID = 'pocket-notes-cloud'
JOB_GOAL = 'Fix Pocket Notes MV3 extension correctness issues and ensure all regression tests pass'
ALLOWED = (
    'extension/manifest.json',
    'extension/popup.html',
    'extension/popup.css',
    'extension/popup.js',
    'extension/core.js',
    'extension/background.js',
)
POOLS = ('codex', 'anti:gemini models', 'anti:gemini 4 argon')


def validate_run_path(run_path, must_not_exist=False):
    if not run_path:
        raise RuntimeError('Missing run path')
    path = Path(run_path)
    state_resolved = STATE.resolve()
    if path.is_symlink():
        raise RuntimeError(f'Symlink run path rejected: {path}')
    resolved = path.resolve()
    if resolved.is_symlink():
        raise RuntimeError(f'Resolved symlink run path rejected: {resolved}')
    if resolved.parent != state_resolved:
        raise RuntimeError(f'Run path must resolve directly under {STATE}, got {resolved}')
    cur = path
    while cur != cur.parent:
        if cur.is_symlink():
            raise RuntimeError(f'Symlink traversal in run path rejected: {path}')
        cur = cur.parent
    if must_not_exist and resolved.exists():
        raise RuntimeError(f'New run path already exists: {resolved}')
    return resolved


def parse_and_validate_completion_auth(auth_input, run_id=None, job_id=None, goal=None):
    if not auth_input:
        return None
    if isinstance(auth_input, (str, Path)):
        auth_path = Path(auth_input)
        if not auth_path.exists():
            raise RuntimeError(f'Completion auth file not found: {auth_path}')
        try:
            data = json.loads(auth_path.read_text(encoding='utf-8'))
        except Exception as exc:
            raise RuntimeError(f'Malformed completion auth JSON: {exc}') from exc
    elif isinstance(auth_input, dict):
        data = auth_input
    else:
        raise RuntimeError('Completion auth must be a file path or dict')

    if not isinstance(data, dict):
        raise RuntimeError('Malformed completion auth: expected JSON object')

    req_fields = {'codex_account_id', 'reset', 'goal', 'stop_used', 'cap_used'}
    if not req_fields.issubset(data.keys()):
        missing = req_fields - set(data.keys())
        raise RuntimeError(f'Malformed completion auth: missing required fields {missing}')

    acc = data.get('codex_account_id')
    if not isinstance(acc, str) or not acc.strip():
        raise RuntimeError('Malformed completion auth: codex_account_id must be non-empty string')
    acc = acc.strip()

    auth_goal = data.get('goal')
    if not isinstance(auth_goal, str) or not auth_goal.strip():
        raise RuntimeError('Malformed completion auth: goal must be non-empty string')
    auth_goal = auth_goal.strip()

    raw_run_id = data.get('run_id') or data.get('job_id')
    if not isinstance(raw_run_id, str) or not raw_run_id.strip():
        raise RuntimeError('Malformed completion auth: run_id or job_id must be non-empty string')
    auth_run_id = raw_run_id.strip()

    if 'run_id' in data and (not isinstance(data['run_id'], str) or not data['run_id'].strip()):
        raise RuntimeError('Malformed completion auth: run_id must be non-empty string')
    if 'job_id' in data and (not isinstance(data['job_id'], str) or not data['job_id'].strip()):
        raise RuntimeError('Malformed completion auth: job_id must be non-empty string')

    try:
        reset_val = stamp(data['reset'])
    except Exception as exc:
        raise RuntimeError(f'Malformed completion auth reset: {exc}') from exc

    try:
        stop_u = percent(data['stop_used'])
        cap_u = percent(data['cap_used'])
    except Exception as exc:
        raise RuntimeError(f'Malformed completion auth percentages: {exc}') from exc

    if stop_u > cap_u - 2.0:
        raise RuntimeError(f'Malformed completion auth: stop_used ({stop_u}) must be at least 2 points below cap_used ({cap_u})')
    if cap_u > 100.0 or stop_u > 98.0:
        raise RuntimeError('Malformed completion auth: stop_used or cap_used exceeds maximum limits')

    return {
        'codex_account_id': acc,
        'reset': reset_val,
        'run_id': auth_run_id,
        'job_id': auth_run_id,
        'goal': auth_goal,
        'stop_used': stop_u,
        'cap_used': cap_u,
    }


class Budget:
    def __init__(self, path, completion_auth=None, job_id=JOB_ID, goal=JOB_GOAL, run_id=None):
        self.path = Path(path)
        self.completion_auth = completion_auth
        self.job_id = job_id
        self.goal = goal
        self.run_id = run_id
        self.completion_auth_data = parse_and_validate_completion_auth(
            completion_auth, run_id=run_id, job_id=job_id, goal=goal
        )
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'pools': {}, 'history': []}

    def legacy_grant(self, reset, account_id):
        grant_path = self.path.with_name('authorization.json')
        if not grant_path.exists():
            return None
        grant = json.loads(grant_path.read_text(encoding='utf-8'))
        if not isinstance(grant, dict):
            raise RuntimeError('Invalid budget authorization')
        if stamp(grant.get('reset')) != reset:
            return None
        required = {'reset', 'cap_used', 'stop_used', 'extra_points', 'codex_account_id'}
        if set(grant) != required:
            raise RuntimeError('Legacy authorization requires verified codex_account_id; reconcile without deleting history')
        grant_account = grant['codex_account_id']
        if (not isinstance(grant_account, str) or not grant_account.strip() or
            stamp(grant['reset']) != 1793428548 or grant['extra_points'] != 5 or
            percent(grant['cap_used']) != 30 or percent(grant['stop_used']) != 28):
            raise RuntimeError('Unrecognized budget authorization')
        if account_id != grant_account or self.data.get('codex_account_id') != account_id:
            return None
        return grant

    def stop_at(self, key, reset, baseline, account_id=None):
        """One explicitly approved, period-bound grant; never reset usage history."""
        normal = baseline + 23
        if key != 'codex':
            return normal

        current_acc = account_id or self.data.get('codex_account_id')

        # Check explicit per-job completion authorization if configured
        if self.completion_auth_data:
            auth = self.completion_auth_data
            matches_acc = (isinstance(current_acc, str) and bool(current_acc.strip()) and auth['codex_account_id'] == current_acc.strip())
            matches_reset = (auth['reset'] == reset)
            matches_goal = (isinstance(self.goal, str) and bool(self.goal.strip()) and auth['goal'] == self.goal.strip())
            matches_run = False
            allowed_ids = {i.strip() for i in (self.run_id, self.job_id) if isinstance(i, str) and i.strip()}
            if allowed_ids:
                matches_run = (auth['run_id'] in allowed_ids or auth['job_id'] in allowed_ids)

            if matches_acc and matches_reset and matches_goal and matches_run:
                return min(auth['stop_used'], 98.0)

        grant = self.legacy_grant(reset, current_acc)
        if grant:
            return grant['stop_used']

        return normal

    def update(self, week, short, now, account_id=None):
        if any(k not in week for k in POOLS):
            raise RuntimeError('Missing actual quota pool; no model call permitted')

        # Account binding & identity verification: nonempty account_id always required
        if account_id is None or not isinstance(account_id, str) or not account_id.strip():
            raise RuntimeError('Empty or missing Codex account identity; account_id is required')
        account_id = account_id.strip()

        bound_account = self.data.get('codex_account_id')
        if bound_account is not None:
            if account_id != bound_account:
                raise RuntimeError(
                    f'Codex account switch detected: ledger bound to {bound_account}, got {account_id}; '
                    'fails closed pending manual reconciliation'
                )
        else:
            has_history = bool(self.data.get('pools') or self.data.get('history'))
            if has_history:
                raise RuntimeError('Unbound legacy ledger without codex_account_id; manual reconciliation required')
            self.data['codex_account_id'] = account_id

        for key in POOLS:
            fresh = week[key]
            percent(fresh['used'])
            fresh_reset = stamp(fresh['reset'])
            old = self.data['pools'].get(key)
            if old is None:
                base = 1 if key == 'codex' and self.legacy_grant(fresh_reset, account_id) else 0
                old = dict(reset=fresh_reset, base=base, peak=fresh['used'], last_used=fresh['used'])
                self.data['pools'][key] = old
            else:
                old_reset = stamp(old['reset'])
                if now < old_reset:
                    if fresh_reset != old_reset:
                        old['peak'] = max(old['peak'], fresh['used'])
                        save(self.path, self.data)
                        raise RuntimeError(
                            f'Reset timestamp drift detected for {key} before period ended '
                            f'(expected {old_reset}, got {fresh_reset}); stop pending reconciliation'
                        )
                    last_used = old.get('last_used', old['peak'])
                    if key == 'codex' and fresh['used'] < last_used:
                        old['peak'] = max(old['peak'], fresh['used'])
                        save(self.path, self.data)
                        raise RuntimeError(
                            f'Usage decrease detected for {key} ({fresh["used"]}% < {last_used}%); '
                            'stop pending reconciliation'
                        )
                else:
                    if fresh_reset < old_reset + 86400:
                        raise RuntimeError('Waiting for confirmed new weekly quota period')
                    self.data['history'].append(dict(pool=key, **old))
                    old = dict(reset=fresh_reset, base=0, peak=fresh['used'], last_used=fresh['used'])
                    self.data['pools'][key] = old

            if key.startswith('anti:'):
                previous_display = percent(old.get('last_used', old['peak']))
                charged = percent(old.get('charged_used', old['peak']))
                # Never refund a display decrease. Every subsequent rise is
                # charged again, even below a previously observed peak. This
                # may stop early on jitter, but cannot enlarge the allowance.
                old['charged_used'] = max(old['peak'], fresh['used'],
                                          charged + max(0, fresh['used'] - previous_display))
                if fresh['used'] < previous_display:
                    old['display_decreases'] = old.get('display_decreases', 0) + 1
            old['peak'] = max(old['peak'], fresh['used'])
            old['last_used'] = fresh['used']

            exempt = key.startswith('anti:') and now < 1792827120
            stop_threshold = self.stop_at(key, old['reset'], old['base'], account_id=self.data.get('codex_account_id'))
            budget_used = old.get('charged_used', old['peak']) if key.startswith('anti:') else old['peak']
            if not exempt and budget_used >= stop_threshold:
                save(self.path, self.data)
                raise RuntimeError('Weekly budget stop: ' + key + ' (2 point reserve)')
            if old['peak'] >= 99:
                raise RuntimeError('Account quota nearly exhausted: ' + key)

        for key in ('codex', 'anti:gemini models'):
            if key not in short or short[key]['used'] >= 95:
                raise RuntimeError('Short-window quota unavailable or nearly full: ' + key)

        self.data['checked_at'] = now
        save(self.path, self.data)
        return self.data

    def check(self):
        if (STATE / 'STOP').exists():
            raise RuntimeError('User requested stop')
        raw_codex = quota_client.quota(raw=True)
        raw_anti = quota_client.anti_quota()
        account_id = extract_account_id(raw_codex)
        week, short = snapshot(raw_codex, raw_anti)
        return self.update(week, short, time.time(), account_id=account_id)


def decode(text):
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    return json.loads(text)


def proposed_files(value):
    if not isinstance(value, dict) or not set(value).issubset(ALLOWED):
        raise ValueError('Proposal outside assigned extension files')
    if any(not isinstance(v, str) or len(v) > 60000 for v in value.values()):
        raise ValueError('Invalid or excessive proposal')
    if len(json.dumps(value)) > 180000:
        raise ValueError('Proposal too large')
    if 'extension/manifest.json' in value:
        manifest = json.loads(value['extension/manifest.json'])
        if (manifest.get('manifest_version') != 3 or
            set(manifest.get('permissions', [])) != {'storage', 'activeTab'} or
            any(manifest.get(k) for k in ('host_permissions', 'optional_permissions',
                'optional_host_permissions', 'content_scripts', 'externally_connectable'))):
            raise ValueError('Manifest exceeds authorized permissions')
    return value


def verify_source_hashes(expected_hashes):
    if not isinstance(expected_hashes, dict):
        raise RuntimeError('Invalid or missing source hashes in checkpoint')
    current = compute_source_hashes()
    for p, h in expected_hashes.items():
        if current.get(p) != h:
            raise RuntimeError(f'Source hash mismatch for {p}: expected {h}, got {current.get(p)}')


def compute_source_hashes():
    ext_dir = ROOT / 'extension'
    if not ext_dir.is_dir():
        raise RuntimeError('Missing extension directory')
    hashes = {}
    for p in sorted(ext_dir.rglob('*')):
        if p.is_symlink():
            raise RuntimeError(f'Symlink detected in extension files: {p}')
        if p.is_file():
            rel = str(p.relative_to(ROOT)).replace('\\', '/')
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    if 'extension/manifest.json' not in hashes:
        raise RuntimeError('Mandatory extension/manifest.json is missing')
    return hashes


def compute_test_hashes():
    tests_dir = ROOT / 'tests'
    if not tests_dir.is_dir():
        raise RuntimeError('Missing tests directory')
    hashes = {}
    for p in sorted(tests_dir.rglob('*')):
        if p.is_symlink():
            raise RuntimeError(f'Symlink detected in test files: {p}')
        if p.is_file():
            rel = str(p.relative_to(ROOT)).replace('\\', '/')
            hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    if not hashes:
        raise RuntimeError('No test files found under tests directory')
    return hashes


def verify_hashes(expected_source_hashes, expected_test_hashes):
    if not isinstance(expected_source_hashes, dict) or not expected_source_hashes:
        raise RuntimeError('Missing or empty source hashes in checkpoint')
    if any(not isinstance(h, str) or not h.strip() for h in expected_source_hashes.values()):
        raise RuntimeError('Empty hash entry detected in source hashes')

    current_source = compute_source_hashes()
    if set(current_source.keys()) != set(expected_source_hashes.keys()):
        raise RuntimeError('Source file set mismatch (added or removed source files)')
    for p, h in expected_source_hashes.items():
        if current_source.get(p) != h:
            raise RuntimeError(f'Source hash mismatch for {p}: expected {h}, got {current_source.get(p)}')

    if not isinstance(expected_test_hashes, dict) or not expected_test_hashes:
        raise RuntimeError('Missing or empty test hashes in checkpoint')
    if any(not isinstance(h, str) or not h.strip() for h in expected_test_hashes.values()):
        raise RuntimeError('Empty hash entry detected in test hashes')

    current_tests = compute_test_hashes()
    if set(current_tests.keys()) != set(expected_test_hashes.keys()):
        raise RuntimeError('Regression test file set mismatch (added or removed test files)')
    for p, h in expected_test_hashes.items():
        if current_tests.get(p) != h:
            raise RuntimeError(f'Regression test hash mismatch for {p}: expected {h}, got {current_tests.get(p)}')


def snapshot_workspace_files():
    """Hash all files in workspace excluding .git, .anti-codex, and python caches."""
    files = {}
    for p in ROOT.rglob('*'):
        if p.is_file():
            rel = str(p.relative_to(ROOT)).replace('\\', '/')
            parts = rel.split('/')
            if parts[0] in ('.git', '.anti-codex'):
                continue
            if any(part in ('__pycache__', '.pytest_cache') or part.endswith('.pyc') for part in parts):
                continue
            files[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return files


def audit_workspace_scope(before_snapshot):
    after_snapshot = snapshot_workspace_files()
    all_keys = set(before_snapshot.keys()) | set(after_snapshot.keys())
    changed_allowed = []
    unauthorized = []
    for rel in all_keys:
        if before_snapshot.get(rel) != after_snapshot.get(rel):
            if rel in ALLOWED:
                changed_allowed.append(rel)
            else:
                unauthorized.append(rel)
    if unauthorized:
        raise RuntimeError(f'Unauthorized path changes outside allowed scope: {unauthorized}')
    return changed_allowed


def backup_allowed_files(backup_dir):
    backup_dir.mkdir(parents=True, exist_ok=True)
    for rel in ALLOWED:
        path = ROOT / rel
        if path.is_file():
            target = backup_dir / Path(rel).name
            target.write_bytes(path.read_bytes())


def validate_manifest_permissions():
    manifest_path = ROOT / 'extension/manifest.json'
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if (manifest.get('manifest_version') != 3 or
            set(manifest.get('permissions', [])) != {'storage', 'activeTab'} or
            any(manifest.get(k) for k in ('host_permissions', 'optional_permissions',
                'optional_host_permissions', 'content_scripts', 'externally_connectable'))):
            raise RuntimeError('Manifest exceeds authorized permissions')


def get_test_files():
    tests_dir = ROOT / 'tests'
    if not tests_dir.is_dir():
        return []
    return sorted(str(p.relative_to(ROOT)).replace('\\', '/') for p in tests_dir.glob('*.cjs'))


def run_node_tests():
    test_files = get_test_files()
    if not test_files:
        raise RuntimeError('No test files found under tests/*.cjs')
    return subprocess.run(['node', '--test', *test_files],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)


def read_source_files():
    return {p: (ROOT / p).read_text(encoding='utf-8') for p in ALLOWED if (ROOT / p).is_file()}


def read_test_files_summary():
    tests_dir = ROOT / 'tests'
    items = []
    if tests_dir.is_dir():
        for p in sorted(tests_dir.glob('*.cjs')):
            rel = str(p.relative_to(ROOT)).replace('\\', '/')
            items.append(f'// FILE: {rel}\n' + p.read_text(encoding='utf-8'))
    return '\n\n'.join(items)


def anti_answer(raw):
    result = decode(raw)
    if not isinstance(result, dict):
        raise RuntimeError('Invalid Anti result; no retry')
    result = result.get('result', result)
    if not isinstance(result, dict):
        raise RuntimeError('Invalid Anti result envelope; no retry')
    denied = result.get('denied_actions')
    if denied:
        actions = set()
        for item in denied if isinstance(denied, list) else [denied]:
            name = item.get('action') if isinstance(item, dict) else None
            actions.add(name if name in ('command', 'write_file', 'read_file') else 'unknown')
        raise RuntimeError('Anti denied action(s): ' + ', '.join(sorted(actions)) + '; no retry')
    if result.get('status') not in ('SUCCESS', 'success'):
        raise RuntimeError('Anti unsuccessful result; no retry')
    answer = result.get('response')
    if not isinstance(answer, str) or not answer.strip():
        raise RuntimeError('Anti returned empty response')
    return answer


def anti_evidence():
    return (
        '\nUse read_file/write_file tools only within the allowed extension paths. '
        'Do not run commands, shells, terminal tools, subprocesses, tests, git, network requests or peer agents. '
        'The host runs all commands and tests. All source and test evidence is supplied below. '
        'If a file tool is denied, stop and report it; do not switch to a command tool. '
        'Return a nonempty summary of actual edits or remaining blockers.\n'
        'CURRENT SOURCE:\n' + json.dumps(read_source_files(), indent=2)
    )


def call(target, prompt, run, index, budget, deadline, anti_round=0):
    if time.time() >= deadline:
        raise RuntimeError('Job deadline reached before model call')
    budget.check()
    prefix = run / f'{index:02}-{target}'
    prefix.with_suffix('.prompt.txt').write_text(prompt, encoding='utf-8')
    if target == 'anti':
        model = 'gemini-3.8-flash-medium' if anti_round == 0 else 'gemini-4-argon-medium'
        # Direct Anti implementation: remove --mode plan and allow ordinary sandbox edits
        args = ['agy', '--sandbox', '--model', model, '--output-format', 'json', '-p', prompt]
        payload = None
    else:
        args = ['codex', 'exec', '--json', '--sandbox', 'read-only', '--skip-git-repo-check', '-']
        payload = prompt.encode()
    with prefix.with_suffix('.log').open('wb') as out, prefix.with_suffix('.err').open('wb') as err:
        proc = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.PIPE if payload else subprocess.DEVNULL,
                                stdout=out, stderr=err, start_new_session=True)
        if payload:
            proc.stdin.write(payload)
            proc.stdin.close()
        start = time.monotonic()
        try:
            while proc.poll() is None:
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    budget.check()
                    if time.time() >= deadline or time.monotonic() - start > 480:
                        raise RuntimeError('Model deadline reached; inspect saved output before retry')
            if proc.returncode:
                raise RuntimeError(f'{target} failed; see {prefix}.err')
        finally:
            quota_client.stop_process(proc)
    budget.check()
    raw = prefix.with_suffix('.log').read_text(encoding='utf-8')
    if target == 'anti':
        answer = anti_answer(raw)
    else:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        answers = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                   and e.get('item', {}).get('type') == 'agent_message']
        if not answers or not any(e.get('type') == 'turn.completed' for e in events):
            raise RuntimeError('Codex did not complete a response')
        answer = answers[-1]
    prefix.with_suffix('.response.txt').write_text(answer, encoding='utf-8')
    return decode(answer) if target == 'codex' else answer


VALID_STAGES = {
    'not_started', 'planned', 'implemented', 'revised',
    'reviewed', 'reviewed_revision', 'static_review_pass',
    'needs_attention', 'blocked'
}


def checkpoint_call_limit(checkpoint):
    limit = checkpoint.get('model_call_limit', 5)
    if type(limit) is int and limit == 5:
        return 5
    if (type(limit) is int and limit == 8 and
        checkpoint.get('write_permission_confirmed') is True and
        isinstance(checkpoint.get('permission_recovery_of'), str) and
        checkpoint['permission_recovery_of'].strip() and
        type(checkpoint.get('inherited_model_calls')) is int and
        checkpoint['inherited_model_calls'] == 4):
        return 8
    raise RuntimeError('Unrecognized model-call limit; no model call allowed')


def validate_checkpoint_evidence(checkpoint, run, account_id):
    if not isinstance(checkpoint, dict):
        raise RuntimeError('Checkpoint must be a JSON object')

    stage = checkpoint.get('stage')
    if stage not in VALID_STAGES:
        raise RuntimeError(f'Unknown or invalid checkpoint stage: {stage}')

    chk_acc = checkpoint.get('codex_account_id')
    if not isinstance(chk_acc, str) or not chk_acc.strip() or chk_acc != account_id:
        raise RuntimeError(f'Account mismatch or missing in checkpoint: expected {account_id}, got {chk_acc}')

    chk_goal = checkpoint.get('goal')
    if not isinstance(chk_goal, str) or chk_goal != JOB_GOAL:
        raise RuntimeError(f'Goal mismatch or missing in checkpoint: expected {JOB_GOAL}, got {chk_goal}')

    run_id = run.name
    chk_run_id = checkpoint.get('run_id')
    if not isinstance(chk_run_id, str) or chk_run_id != run_id:
        raise RuntimeError(f'Run ID mismatch with folder: folder is {run_id}, checkpoint has {chk_run_id}')

    started_at = checkpoint.get('started_at')
    if type(started_at) not in (int, float) or not math.isfinite(started_at):
        raise RuntimeError('Missing or invalid started_at timestamp in checkpoint')

    deadline = checkpoint.get('deadline')
    if (type(deadline) not in (int, float) or not math.isfinite(deadline) or
        not 0 < deadline - started_at <= 1200 or deadline > time.time() + 1200):
        raise RuntimeError('Missing or invalid deadline timestamp in checkpoint')

    model_calls = checkpoint.get('model_calls')
    limit = checkpoint_call_limit(checkpoint)
    minimum = checkpoint.get('inherited_model_calls', 0) if limit == 8 else 0
    if type(model_calls) is not int or not minimum <= model_calls <= limit:
        raise RuntimeError(f'Missing or invalid model_calls counter in checkpoint: {model_calls}')

    rnd = checkpoint.get('round')
    if type(rnd) is not int or rnd not in (0, 1):
        raise RuntimeError(f'Missing or invalid round counter in checkpoint: {rnd}')

    if stage in ('planned', 'implemented', 'revised', 'reviewed', 'reviewed_revision', 'static_review_pass'):
        plan = checkpoint.get('plan')
        if not isinstance(plan, dict) or not isinstance(plan.get('plan'), list):
            raise RuntimeError(f'Missing or invalid plan in checkpoint for stage {stage}')

    if stage == 'static_review_pass':
        rounds = checkpoint.get('rounds')
        if not isinstance(rounds, list) or not rounds:
            raise RuntimeError('Completed stage static_review_pass requires saved rounds evidence')
        last_round = rounds[-1]
        if not isinstance(last_round, dict):
            raise RuntimeError('Invalid round record in completed checkpoint')
        if last_round.get('tests_exit') != 0:
            raise RuntimeError('Completed stage requires tests_exit == 0 in final round')
        rev = last_round.get('review')
        if not isinstance(rev, dict) or rev.get('verdict') != 'PASS' or rev.get('issues'):
            raise RuntimeError('Completed stage requires successful PASS review with no issues in final round')

    if stage in ('implemented', 'revised'):
        changed_rec = run / f'changed_files_{rnd}.json'
        if not changed_rec.is_file():
            raise RuntimeError(f'Missing changed_files record for stage {stage}: {changed_rec}')


def run_job(resume=False, run_dir=None, completion_auth=None):
    if not (STATE / 'ALLOW_ANTI').is_file():
        raise RuntimeError('Anti transfer is not authorized. Obtain renewed user authorization first.')
    if sys.platform != 'linux':
        raise RuntimeError('Run on Linux cloud host')
    if fcntl is None:
        raise RuntimeError('fcntl required for lock management')
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True)
    with (STATE / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

        if resume:
            if run_dir:
                run = validate_run_path(run_dir)
            else:
                latest = STATE / 'latest.txt'
                if not latest.exists():
                    raise RuntimeError('No previous run found to resume')
                run = validate_run_path(latest.read_text(encoding='utf-8').strip())

            if not run.is_dir() or not (run / 'checkpoint.json').is_file():
                raise RuntimeError(f'Invalid run directory for resume: {run}')

            run_id = run.name
            budget = Budget(STATE / 'budget.json', completion_auth=completion_auth, job_id=run_id, goal=JOB_GOAL, run_id=run_id)
            budget.check()
            account_id = budget.data.get('codex_account_id')
            if not account_id:
                raise RuntimeError('Missing Codex account identity in budget ledger')

            checkpoint = json.loads((run / 'checkpoint.json').read_text(encoding='utf-8'))

            # Check in-flight status: interrupted mutations must mark needs_attention and not replay
            if checkpoint.get('in_flight'):
                checkpoint['stage'] = 'needs_attention'
                checkpoint['reason'] = 'Interrupted in-flight mutation; cannot auto replay. Inspect partial changes and logs manually.'
                checkpoint['finished'] = time.time()
                save(run / 'checkpoint.json', checkpoint)
                save(run / 'status.json', checkpoint)
                print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
                raise RuntimeError('Interrupted in-flight mutation; marked needs_attention. Inspect manually before resuming.')

            validate_checkpoint_evidence(checkpoint, run, account_id)

            stage = checkpoint.get('stage')
            if stage in ('needs_attention', 'blocked'):
                raise RuntimeError(f"Cannot resume run in terminal stage '{stage}'; inspect manually.")

            # Verify source and test hashes before proceeding
            verify_hashes(checkpoint.get('source_hashes'), checkpoint.get('test_hashes'))

            deadline = checkpoint.get('deadline')
            if time.time() >= deadline:
                checkpoint['stage'] = 'needs_attention'
                checkpoint['reason'] = 'Job deadline expired'
                checkpoint['finished'] = time.time()
                save(run / 'checkpoint.json', checkpoint)
                save(run / 'status.json', checkpoint)
                raise RuntimeError('Job deadline expired; cannot resume')

            # Validate completed run without replaying
            if stage == 'static_review_pass':
                print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
                return True

            # Resume from reviewed / reviewed_revision
            if stage in ('reviewed', 'reviewed_revision'):
                rounds = checkpoint.get('rounds') or []
                if not rounds:
                    raise RuntimeError('Reviewed stage missing rounds evidence')
                last_round_record = rounds[-1]
                last_review = last_round_record.get('review') or {}
                tests_exit = last_round_record.get('tests_exit')
                issues = last_review.get('issues') or []

                if tests_exit == 0 and last_review.get('verdict') == 'PASS' and not issues:
                    checkpoint['stage'] = 'static_review_pass'
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                    print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
                    return True

                if checkpoint.get('round', 0) == 0:
                    tests_file = run / 'tests-0.txt'
                    review_file = run / 'codex_review_0.json'
                    if not tests_file.is_file() or not review_file.is_file():
                        raise RuntimeError('Cannot derive feedback on resume: missing stored review or tests artifact')
                    tests_out = tests_file.read_text(encoding='utf-8')
                    stored_review = json.loads(review_file.read_text(encoding='utf-8'))
                    derived_feedback = json.dumps(stored_review) + '\nTESTS:\n' + tests_out

                    checkpoint['round'] = 1
                    checkpoint['stage'] = 'planned'
                    checkpoint['feedback'] = derived_feedback
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                else:
                    checkpoint['stage'] = 'needs_attention'
                    checkpoint['reason'] = 'Maximum rounds (2) completed without static review pass'
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                    raise RuntimeError('Maximum rounds (2) completed without static review pass; inspect manually.')
        else:
            if run_dir:
                run = validate_run_path(run_dir, must_not_exist=True)
            else:
                run = STATE / ('cloud-' + time.strftime('%Y%m%d-%H%M%S', time.gmtime()))
                validate_run_path(run, must_not_exist=True)
            run.mkdir()
            run_id = run.name
            (STATE / 'latest.txt').write_text(str(run), encoding='utf-8')

            budget = Budget(STATE / 'budget.json', completion_auth=completion_auth, job_id=run_id, goal=JOB_GOAL, run_id=run_id)
            budget.check()
            account_id = budget.data.get('codex_account_id')
            if not account_id:
                raise RuntimeError('Missing Codex account identity in budget ledger')

            deadline = time.time() + 1200
            checkpoint = {
                'stage': 'not_started',
                'in_flight': False,
                'in_flight_target': None,
                'run_id': run_id,
                'job_id': run_id,
                'goal': JOB_GOAL,
                'codex_account_id': account_id,
                'source_hashes': compute_source_hashes(),
                'test_hashes': compute_test_hashes(),
                'round': 0,
                'model_calls': 0,
                'started_at': time.time(),
                'deadline': deadline,
                'plan': None,
                'rounds': [],
                'feedback': None,
            }
            save(run / 'checkpoint.json', checkpoint)
            save(run / 'status.json', checkpoint)

        def record_call_start(target_name):
            calls = checkpoint.get('model_calls', 0)
            limit = checkpoint_call_limit(checkpoint)
            if calls >= limit:
                raise RuntimeError(f'Maximum model calls ({limit}) reached; refusing to call {target_name}')
            checkpoint['model_calls'] = calls + 1
            checkpoint['in_flight'] = True
            checkpoint['in_flight_target'] = target_name
            save(run / 'checkpoint.json', checkpoint)
            save(run / 'status.json', checkpoint)
            return checkpoint['model_calls']

        try:
            # Stage 1: Codex Plan
            if checkpoint.get('stage') == 'not_started' or checkpoint.get('plan') is None:
                call_index = record_call_start('codex_plan')
                source = read_source_files()
                tests_summary = read_test_files_summary()
                plan_prompt = (
                    'You are Codex, planning the implementation for Pocket Notes MV3 extension. '
                    'NO TOOLS, file access or agents; all evidence is supplied. '
                    f'Goal: {JOB_GOAL}\n'
                    f'Allowed files to modify: {list(ALLOWED)}\n'
                    'Manifest permissions must remain strictly storage and activeTab only; no external network. '
                    'Return ONLY JSON {"plan":["step 1", "step 2", ...], "acceptance_criteria":["criterion 1", ...]}.\n'
                    'SOURCE:\n' + json.dumps(source, indent=2) +
                    '\nREGRESSION TESTS (do not edit):\n' + tests_summary
                )
                source_before = compute_source_hashes()
                test_before = compute_test_hashes()
                plan = call('codex', plan_prompt, run, call_index, budget, deadline)
                verify_hashes(source_before, test_before)
                if not isinstance(plan, dict) or not isinstance(plan.get('plan'), list):
                    raise RuntimeError('Invalid plan schema from Codex')

                save(run / 'codex_plan.json', plan)
                checkpoint['plan'] = plan
                checkpoint['stage'] = 'planned'
                checkpoint['in_flight'] = False
                checkpoint['in_flight_target'] = None
                checkpoint['source_hashes'] = compute_source_hashes()
                checkpoint['test_hashes'] = compute_test_hashes()
                save(run / 'checkpoint.json', checkpoint)
                save(run / 'status.json', checkpoint)

            plan = checkpoint['plan']
            acceptance_criteria = plan.get('acceptance_criteria', [])

            # Stage-driven loop for rounds 0 and 1
            start_round = checkpoint.get('round', 0)
            for r in range(start_round, 2):
                checkpoint['round'] = r

                # Determine if Anti should run or skip because already implemented/revised
                curr_stage = checkpoint.get('stage')
                skip_anti = (curr_stage in ('implemented', 'revised'))

                if not skip_anti:
                    # Anti Implementation (r=0) or Revision (r=1)
                    backup = run / f'before-{r}'
                    backup_allowed_files(backup)
                    before_workspace = snapshot_workspace_files()

                    call_index = record_call_start(f'anti_round_{r}')
                    tests_summary = read_test_files_summary()

                    if r == 0:
                        anti_prompt = (
                            'You are Anti, implementing fixes directly for Pocket Notes MV3 extension based on the Codex plan. '
                            'Edit the allowed extension files directly in the workspace. '
                            f'Goal: {JOB_GOAL}\n'
                            f'Allowed files to modify: {list(ALLOWED)}\n'
                            'Manifest permissions must remain strictly storage and activeTab only; no external network/CDN/eval/inline scripts. '
                            'Do not modify any files outside the allowed extension files.\n'
                            'Codex Plan:\n' + json.dumps(plan, indent=2) + '\n'
                            'REGRESSION TESTS (do not edit tests):\n' + tests_summary
                        )
                    else:
                        feedback = checkpoint.get('feedback')
                        if not feedback or not isinstance(feedback, str) or not feedback.strip():
                            raise RuntimeError('Missing feedback for revision round; no generic fallback allowed')
                        anti_prompt = (
                            'You are Anti, revising source files for Pocket Notes MV3 extension to resolve review issues. '
                            'Edit the allowed extension files directly in the workspace. '
                            f'Goal: {JOB_GOAL}\n'
                            f'Allowed files to modify: {list(ALLOWED)}\n'
                            'Manifest permissions must remain strictly storage and activeTab only; no external network/CDN/eval/inline scripts. '
                            'Do not modify any files outside the allowed extension files.\n'
                            'Review Feedback:\n' + feedback + '\n'
                            'REGRESSION TESTS (do not edit tests):\n' + tests_summary
                        )

                    anti_prompt += anti_evidence()
                    try:
                        call('anti', anti_prompt, run, call_index, budget, deadline, anti_round=r)
                    except Exception:
                        audit_workspace_scope(before_workspace)
                        raise

                    # Audit scope: fail if unauthorized paths changed; preserve backup
                    changed_files = audit_workspace_scope(before_workspace)
                    validate_manifest_permissions()

                    tests = run_node_tests()
                    (run / f'tests-{r}.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')

                    checkpoint['stage'] = 'implemented' if r == 0 else 'revised'
                    checkpoint['in_flight'] = False
                    checkpoint['in_flight_target'] = None
                    checkpoint['source_hashes'] = compute_source_hashes()
                    checkpoint['test_hashes'] = compute_test_hashes()
                    save(run / f'changed_files_{r}.json', changed_files)
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                else:
                    # Resuming from implemented/revised: execute tests then review WITHOUT replaying Anti
                    tests = run_node_tests()
                    (run / f'tests-{r}.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
                    changed_file_record = run / f'changed_files_{r}.json'
                    if not changed_file_record.is_file():
                        raise RuntimeError(f'Missing changed_files record for resume: {changed_file_record}')
                    changed_files = json.loads(changed_file_record.read_text(encoding='utf-8'))

                # Codex Review
                call_index = record_call_start(f'codex_review_{r}')
                source = read_source_files()
                tests_summary = read_test_files_summary()
                review_prompt = (
                    'Independently review this Pocket Notes extension source, test code, test output, and plan acceptance criteria. '
                    'NO TOOLS, file access or agents; all evidence is supplied. '
                    'Return ONLY JSON {"verdict":"PASS|CHANGES","issues":["concrete bugs"],'
                    '"limitations":["unverified runtime behavior"]}. PASS means static code review only, '
                    'not installed browser verification. Look for data loss, storage errors, URL security, '
                    'limits, lifecycle and UI behavior. Do not invent bugs.\n'
                    f'ACCEPTANCE CRITERIA:\n{json.dumps(acceptance_criteria, indent=2)}\n'
                    f'SOURCE:\n{json.dumps(source, indent=2)}\n'
                    f'TEST FILES SOURCE:\n{tests_summary}\n'
                    f'TEST EXIT CODE: {tests.returncode}\n'
                    f'TEST RUNNER OUTPUT:\n{tests.stdout}{tests.stderr}'
                )
                source_before = compute_source_hashes()
                test_before = compute_test_hashes()
                review = call('codex', review_prompt, run, call_index, budget, deadline)
                verify_hashes(source_before, test_before)
                if (not isinstance(review, dict) or review.get('verdict') not in ('PASS', 'CHANGES') or
                    not isinstance(review.get('issues'), list) or not isinstance(review.get('limitations'), list)):
                    raise RuntimeError('Invalid reviewer schema from Codex')

                save(run / f'codex_review_{r}.json', review)
                round_record = {'round': r, 'files': changed_files, 'tests_exit': tests.returncode, 'review': review}
                checkpoint['rounds'].append(round_record)
                checkpoint['source_hashes'] = compute_source_hashes()
                checkpoint['test_hashes'] = compute_test_hashes()

                actual_feedback = json.dumps(review) + '\nTESTS:\n' + tests.stdout + tests.stderr
                is_pass = (tests.returncode == 0 and review['verdict'] == 'PASS' and not review['issues'])

                if is_pass:
                    checkpoint['stage'] = 'static_review_pass'
                    checkpoint['in_flight'] = False
                    checkpoint['in_flight_target'] = None
                    checkpoint['feedback'] = None
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                    break

                if r == 0:
                    checkpoint['round'] = 1
                    checkpoint['stage'] = 'planned'
                    checkpoint['feedback'] = actual_feedback
                    checkpoint['in_flight'] = False
                    checkpoint['in_flight_target'] = None
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                else:
                    checkpoint['stage'] = 'needs_attention'
                    checkpoint['reason'] = 'Maximum rounds (2) completed without static review pass'
                    checkpoint['in_flight'] = False
                    checkpoint['in_flight_target'] = None
                    save(run / 'checkpoint.json', checkpoint)
                    save(run / 'status.json', checkpoint)
                    break
            else:
                if checkpoint.get('stage') != 'static_review_pass':
                    checkpoint['stage'] = 'needs_attention'

        except Exception as exc:
            checkpoint.update(stage='blocked', reason=str(exc), in_flight=False, in_flight_target=None)

        checkpoint['finished'] = time.time()
        save(run / 'checkpoint.json', checkpoint)
        save(run / 'status.json', checkpoint)
        print(json.dumps(checkpoint, ensure_ascii=False), flush=True)
        return checkpoint['stage'] == 'static_review_pass'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['run', 'resume', 'quota', 'status'])
    parser.add_argument('--run', dest='run_dir', help='Specific run directory for new run, resume or status')
    parser.add_argument('--completion-auth', dest='completion_auth', help='Path to completion authorization JSON')
    args = parser.parse_args()
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True)
    if args.completion_auth and not args.run_dir:
        raise RuntimeError('completion auth requires explicit --run')
    if args.action == 'run':
        sys.exit(0 if run_job(resume=False, run_dir=args.run_dir, completion_auth=args.completion_auth) else 1)
    elif args.action == 'resume':
        sys.exit(0 if run_job(resume=True, run_dir=args.run_dir, completion_auth=args.completion_auth) else 1)
    elif args.action == 'quota':
        if args.run_dir:
            run_id = validate_run_path(args.run_dir).name
        else:
            latest = STATE / 'latest.txt'
            if latest.exists():
                try:
                    run_id = validate_run_path(latest.read_text(encoding='utf-8').strip()).name
                except Exception:
                    run_id = JOB_ID
            else:
                run_id = JOB_ID
        budget = Budget(STATE / 'budget.json', completion_auth=args.completion_auth, job_id=run_id, goal=JOB_GOAL, run_id=run_id)
        print(json.dumps(budget.check(), indent=2))
    else:
        if args.run_dir:
            path = validate_run_path(args.run_dir)
        else:
            latest = STATE / 'latest.txt'
            if latest.exists():
                path = validate_run_path(latest.read_text(encoding='utf-8').strip())
            else:
                path = None
        if path and path.is_dir() and (path / 'checkpoint.json').is_file():
            print((path / 'checkpoint.json').read_text(encoding='utf-8'))
        elif path and path.is_dir() and (path / 'status.json').is_file():
            print((path / 'status.json').read_text(encoding='utf-8'))
        else:
            print('{"status":"not_started","stage":"not_started"}')
