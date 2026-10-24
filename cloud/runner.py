"""Finite cloud collaboration: Anti proposes files, host tests, Codex reviews.

Uses existing account sign-ins. Never disables CLI security or purchases credits.
Quota polling is conservative best effort, not a provider-enforced spending cap.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import quota_client
from quota_parser import snapshot, save

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / '.anti-codex'
ALLOWED = ('extension/manifest.json', 'extension/popup.html', 'extension/popup.css',
           'extension/popup.js', 'extension/core.js')
POOLS = ('codex', 'anti:gemini models', 'anti:gemini 4 argon')


class Budget:
    def __init__(self, path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {'pools': {}, 'history': []}

    def update(self, week, short, now):
        if any(k not in week for k in POOLS):
            raise RuntimeError('Missing actual quota pool; no model call permitted')
        for key in POOLS:
            fresh = week[key]
            old = self.data['pools'].get(key)
            if old is None:
                # Include the current local Codex setup chat in the same budget.
                base = 1 if key == 'codex' and fresh['reset'] == 1793428548 else 0
                old = dict(reset=fresh['reset'], base=base, peak=fresh['used'])
                self.data['pools'][key] = old
            if now >= old['reset']:
                if fresh['reset'] < old['reset'] + 86400:
                    raise RuntimeError('Waiting for confirmed new weekly quota period')
                self.data['history'].append(dict(pool=key, **old))
                old = dict(reset=fresh['reset'], base=0, peak=fresh['used'])
                self.data['pools'][key] = old
            old['peak'] = max(old['peak'], fresh['used'])
            # Explicit one-time exception granted by user for Anti before 14:32.
            exempt = key.startswith('anti:') and now < 1792827120
            if not exempt and old['peak'] - old['base'] >= 23:
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
        week, short = snapshot(quota_client.quota(raw=True), quota_client.anti_quota())
        return self.update(week, short, time.time())


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


def call(target, prompt, run, index, budget, deadline):
    if time.time() >= deadline:
        raise RuntimeError('Job deadline reached before model call')
    budget.check()
    prefix = run / f'{index:02}-{target}'
    prefix.with_suffix('.prompt.txt').write_text(prompt, encoding='utf-8')
    if target == 'anti':
        model = 'gemini-3.8-flash-medium' if index == 1 else 'gemini-4-argon-medium'
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
                    if time.time() >= deadline or time.monotonic() - start > 600:
                        raise RuntimeError('Model deadline reached; inspect saved output before retry')
            if proc.returncode:
                raise RuntimeError(f'{target} failed; see {prefix}.err')
        finally:
            # Own process group only; stop descendants even if parent exited early.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
    budget.check()
    raw = prefix.with_suffix('.log').read_text(encoding='utf-8')
    if target == 'anti':
        result = decode(raw)
        result = result.get('result', result)
        if result.get('denied_actions') or result.get('status') not in ('SUCCESS', 'success'):
            raise RuntimeError('Anti permission denial or unsuccessful result; no retry')
        answer = result.get('response', '')
    else:
        events = [json.loads(line) for line in raw.splitlines() if line.strip()]
        answers = [e['item']['text'] for e in events if e.get('type') == 'item.completed'
                   and e.get('item', {}).get('type') == 'agent_message']
        if not answers or not any(e.get('type') == 'turn.completed' for e in events):
            raise RuntimeError('Codex did not complete a response')
        answer = answers[-1]
    prefix.with_suffix('.response.txt').write_text(answer, encoding='utf-8')
    return decode(answer)


def run_job():
    if not (STATE / 'ALLOW_ANTI').is_file():
        raise RuntimeError('Anti transfer is not authorized. Obtain renewed user authorization first.')
    if sys.platform != 'linux':
        raise RuntimeError('Run on Linux cloud host')
    import fcntl
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True)
    with (STATE / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run = STATE / ('cloud-' + time.strftime('%Y%m%d-%H%M%S', time.gmtime()))
        run.mkdir()
        (STATE / 'latest.txt').write_text(str(run), encoding='utf-8')
        status = {'status': 'running', 'started': time.time(), 'rounds': []}
        save(run / 'status.json', status)
        budget = Budget(STATE / 'budget.json')
        deadline = time.time() + 1800
        feedback = ('Inspect correctness, especially failed storage writes, duplicate submits, invalid stored records, '
                    'saving tab while editing, and empty search. Fix concrete issues only. Preserve Vietnamese UI.')
        try:
            for n in range(3):
                source = {p: (ROOT / p).read_text(encoding='utf-8') for p in ALLOWED}
                prompt = ('You are Anti, proposing source text for Pocket Notes MV3 extension. NO TOOLS, filesystem, '
                          'skills, commands, agents or peer calls. All source is below. Codex host applies and tests. '
                          'Return ONLY JSON mapping changed allowed paths to complete source strings; return {} if no fixes needed. '
                          'Do not change manifest permissions beyond storage and activeTab; no network/CDN/eval/inline scripts. '
                          'Fix only concrete issues; do not manufacture work. Feedback: ' + feedback + '\nSOURCE:\n' + json.dumps(source) +
                          '\nHOST REGRESSION TESTS (do not edit):\n' + (ROOT / 'tests/popup.test.cjs').read_text())
                patch = proposed_files(call('anti', prompt, run, n * 2 + 1, budget, deadline))
                backup = run / f'before-{n}'
                backup.mkdir()
                for name, body in patch.items():
                    path = ROOT / name
                    if path.is_symlink() or path.parent.is_symlink():
                        raise RuntimeError('Symlink in output scope')
                    (backup / path.name).write_bytes(path.read_bytes())
                    path.write_text(body, encoding='utf-8')
                tests = subprocess.run(['node', '--test', 'tests/core.test.cjs', 'tests/popup.test.cjs'],
                                       cwd=ROOT, capture_output=True, text=True, timeout=60)
                (run / f'tests-{n}.txt').write_text(tests.stdout + tests.stderr, encoding='utf-8')
                source = {p: (ROOT / p).read_text(encoding='utf-8') for p in ALLOWED}
                review = call('codex', 'Independently review this Pocket Notes extension source and host test output. '
                              'NO TOOLS, file access or agents; all evidence is supplied. '
                              'Return ONLY JSON {"verdict":"PASS|CHANGES","issues":["concrete bugs"],'
                              '"limitations":["unverified runtime behavior"]}. PASS means static code review only, '
                              'not installed browser verification. Look for data loss, storage errors, URL security, '
                              'limits, lifecycle and UI behavior. Do not invent bugs.\nSOURCE:\n' + json.dumps(source) +
                              '\nTEST EXIT: ' + str(tests.returncode) + '\n' + tests.stdout + tests.stderr,
                              run, n * 2 + 2, budget, deadline)
                if (not isinstance(review, dict) or review.get('verdict') not in ('PASS', 'CHANGES') or
                    not isinstance(review.get('issues'), list) or not isinstance(review.get('limitations'), list)):
                    raise RuntimeError('Invalid reviewer schema')
                status['rounds'].append({'files': list(patch), 'tests_exit': tests.returncode, 'review': review})
                save(run / 'status.json', status)
                if tests.returncode == 0 and review['verdict'] == 'PASS' and not review['issues']:
                    status['status'] = 'static_review_pass'
                    break
                feedback = json.dumps(review) + '\nTESTS:\n' + tests.stdout + tests.stderr
            else:
                status['status'] = 'needs_attention'
        except Exception as exc:
            status.update(status='blocked', reason=str(exc))
        status['finished'] = time.time()
        save(run / 'status.json', status)
        print(json.dumps(status, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['run', 'quota', 'status'])
    args = parser.parse_args()
    os.chdir(ROOT)
    STATE.mkdir(exist_ok=True)
    if args.action == 'run':
        run_job()
    elif args.action == 'quota':
        print(json.dumps(Budget(STATE / 'budget.json').check(), indent=2))
    else:
        path = Path((STATE / 'latest.txt').read_text().strip())
        print((path / 'status.json').read_text())
