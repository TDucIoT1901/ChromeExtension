import ast, json, math, os, queue, signal, subprocess, sys, threading, time
from pathlib import Path
ROOT = Path.cwd().resolve()
BASE = Path(__file__).resolve().parent


def validate_bridge(path):
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    banned = ('--dangerously-bypass-approvals-and-sandbox', '--dangerously-skip-permissions', 'danger-full-access')
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and any(flag in node.value for flag in banned):
            raise RuntimeError('Bridge con co bypass. Can review bridge, khong tu bo sandbox de chay Anti.')

def response_artifact(logfile, task):
    """Use bridge's canonical response artifact, not terminal prose or exit code."""
    event = None
    for line in logfile.read_text(encoding='utf-8').splitlines():
        try:
            candidate = json.loads(line)
        except ValueError:
            continue
        if isinstance(candidate, dict) and candidate.get('task_id') == task and candidate.get('response_file'):
            event = candidate
    if event is None:
        raise RuntimeError('Thieu response artifact; khong suy ra review PASS tu exit 0.')
    if event.get('call', {}).get('status') != 'completed':
        raise RuntimeError('Bridge chua xac nhan completed; dung.')
    folder = (ROOT / '.anti-codex' / task).resolve()
    path = Path(event['response_file']).resolve()
    if not path.is_relative_to(folder) or path.name != 'response.md':
        raise RuntimeError('Response path ngoai task; dung.')
    if not path.is_file():
        raise RuntimeError('Thieu response.md thuc te.')
    return path

def write_request(run, filename, request):
    if set(request) != {'goal', 'scope', 'current', 'expected', 'constraints', 'checked', 'deliverable', 'uncertainties'}:
        raise RuntimeError('Request contract sai.')
    text = json.dumps(request, ensure_ascii=False, indent=2)
    if len(text) > 12000:
        raise RuntimeError('Request qua dai; khong cat review Codex.')
    path = run / filename
    path.write_text(text, encoding='utf-8')
    return path

def ask_command(bridge, task, request, target, mode):
    cmd = bridge + ['ask', '--workspace', '.', '--task', task, '--request', str(request),
                    '--mode', mode, '--timeout', '900']
    if target == 'codex':
        cmd += ['--effort', 'medium']
    if mode == 'implement':
        cmd.append('--allow-edits')
    return cmd

def weekly_used(result):
    buckets = result.get('rateLimitsByLimitId')
    if buckets:
        bucket = buckets.get('codex')
    else:
        bucket = result.get('rateLimits')
    if not isinstance(bucket, dict):
        raise RuntimeError('Khong doc duoc bucket Codex; dung an toan.')
    values = []
    for key in ('primary', 'secondary'):
        window = bucket.get(key)
        if isinstance(window, dict) and window.get('windowDurationMins') == 10080:
            value = window.get('usedPercent')
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                raise RuntimeError('Quota tuan khong hop le.')
            values.append(value)
    if not values:
        raise RuntimeError('Khong co so lieu quota tuan; khong goi worker.')
    return max(values)

def _extract_identity(payload):
    if not isinstance(payload, dict):
        return None
    candidates = []
    for key in ('accountId', 'id', 'userId'):
        if key in payload:
            candidates.append(payload.get(key))
    acc_obj = payload.get('account')
    if isinstance(acc_obj, dict):
        for key in ('accountId', 'id', 'userId'):
            if key in acc_obj:
                candidates.append(acc_obj.get(key))
    found = None
    for val in candidates:
        if val is None:
            continue
        if not isinstance(val, str) or not val.strip():
            raise RuntimeError('Malformed or empty Codex account identity field')
        cleaned = val.strip()
        if found is not None and cleaned != found:
            raise RuntimeError('Conflicting Codex account identities within single response')
        found = cleaned
    return found


def quota(raw=False):
    # Uses existing CLI sign-in, never reads credentials or starts a model turn.
    proc = subprocess.Popen(['codex', 'app-server'], cwd=ROOT,
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
                            start_new_session=True)
    messages = queue.Queue()

    def reader():
        try:
            for line in proc.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def send(message):
        proc.stdin.write(json.dumps(message) + '\n')
        proc.stdin.flush()

    def receive(identity):
        deadline = time.monotonic() + 20
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError('Qua han doc quota; dung an toan.')
            try:
                line = messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise RuntimeError('Qua han doc quota; dung an toan.') from exc
            if line is None:
                raise RuntimeError('App-server dung truoc khi tra quota.')
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get('id') == identity:
                if 'error' in message:
                    raise RuntimeError('App-server tu choi doc quota; khong goi worker.')
                res = message.get('result')
                if not isinstance(res, dict):
                    raise RuntimeError('App-server tra ket qua khong hop le.')
                return res

    try:
        send({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'personal_design_guard', 'version': '1.0.0'}}})
        receive(1)
        send({'method': 'initialized', 'params': {}})

        send({'id': 2, 'method': 'account/read', 'params': {'refreshToken': False}})
        acc_before = receive(2)

        send({'id': 3, 'method': 'account/rateLimits/read'})
        result = receive(3)

        send({'id': 4, 'method': 'account/read', 'params': {'refreshToken': False}})
        acc_after = receive(4)

        id_before = _extract_identity(acc_before)
        id_rate = _extract_identity(result)
        id_after = _extract_identity(acc_after)

        if (id_before is None) != (id_after is None):
            raise RuntimeError('Codex account/read returned inconsistent identity presence')

        if id_before is not None and id_after is not None:
            if id_before != id_after:
                raise RuntimeError('Codex account switched between account/read checks')
            if id_rate is not None and id_rate != id_before:
                raise RuntimeError('Codex account mismatch between account/read and rateLimits/read')
            verified_id = id_before
        else:
            if id_rate is None:
                raise RuntimeError('Missing Codex account identity in account/read and rateLimits/read')
            send({'id': 5, 'method': 'account/rateLimits/read'})
            result_after = receive(5)
            id_rate_after = _extract_identity(result_after)
            if not id_rate_after or id_rate_after != id_rate:
                raise RuntimeError('Codex account switched or missing across rateLimits/read checks')
            verified_id = id_rate
            result = result_after

        result['accountId'] = verified_id
        return result if raw else weekly_used(result)
    finally:
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        stop_process(proc)
        thread.join(timeout=2)
        try:
            if proc.stdout and not proc.stdout.closed:
                proc.stdout.close()
        except Exception:
            pass

def anti_quota():
    proc = subprocess.Popen(['agy', '-p', '/usage'], cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding='utf-8', start_new_session=True)
    try:
        output, error = proc.communicate(timeout=20)
        if proc.returncode:
            raise RuntimeError('Anti /usage that bai; kiem tra dang nhap thu cong, khong goi worker.')
        # Some CLI versions print human-readable quota tables on stderr.
        # Keep both in memory only; parser extracts quota rows, never logs auth text.
        return output + '\n' + (error or '')
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('Anti /usage qua han (co the can dang nhap); dung.') from exc
    finally:
        stop_process(proc)

def stop_process(proc):
    if proc is None:
        return
    pgid = getattr(proc, 'pid', None)
    killpg = getattr(os, 'killpg', None)
    if callable(killpg) and pgid is not None:
        try:
            killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            if proc.poll() is None:
                proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            if proc.poll() is None:
                proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
    else:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
