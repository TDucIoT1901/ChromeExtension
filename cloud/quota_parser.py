"""25 percentage points per weekly pool, shared across launches; no automatic grants."""
import copy
import json
import math
import re
import time
from datetime import datetime, timezone


class BudgetStop(RuntimeError):
    pass


class ShortWindowFull(RuntimeError):
    pass


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError('Invalid quota percentage; no worker started')
    return float(value)


def stamp(value):
    if isinstance(value, str):
        value = value.strip()
        if value.upper().endswith(' UTC'):
            value = datetime.strptime(value.upper(), '%Y-%m-%d %H:%M UTC').replace(tzinfo=timezone.utc).timestamp()
        else:
            value = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Invalid quota reset timestamp')
    return value


def extract_account_id(codex):
    if not isinstance(codex, dict):
        raise ValueError('Invalid Codex quota response; expected dict')
    account_id = codex.get('accountId')
    if not account_id and isinstance(codex.get('account'), dict):
        account_id = codex['account'].get('id')
    if not account_id or not isinstance(account_id, str) or not account_id.strip():
        raise ValueError('Missing or empty Codex accountId in quota response')
    return account_id.strip()


def snapshot(codex, anti):
    buckets = codex.get('rateLimitsByLimitId')
    bucket = buckets.get('codex') if buckets else codex.get('rateLimits')
    if not isinstance(bucket, dict):
        raise ValueError('Missing Codex account quota')
    week, short = {}, {}
    for value in (bucket.get('primary'), bucket.get('secondary')):
        if not isinstance(value, dict):
            continue
        mins = value.get('windowDurationMins')
        if mins in (300, 10080):
            dest = week if mins == 10080 else short
            if 'codex' in dest:
                raise ValueError('Duplicate Codex window')
            dest['codex'] = {'used': percent(value.get('usedPercent')), 'reset': stamp(value.get('resetsAt'))}
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', anti)
    # Also retain model-specific weekly pools, e.g. Gemini 4 Argon Weekly Limit.
    pattern = r'^\s*(.+?)\s+(Weekly Limit(?: Remaining)?|Five Hour Limit Remaining|Gemini .+? Weekly Limit(?: Remaining)?)\s+(\d+(?:\.\d+)?)%\s+(.+?)\s*$'
    for line in text.splitlines():
        match = re.match(pattern, line, re.I)
        if not match:
            continue
        name, kind, left, reset = match.groups()
        name = ' '.join(name.casefold().split())
        key = 'anti:' + name
        dest = short if kind.casefold().startswith('five hour') else week
        if key in dest:
            raise ValueError('Duplicate Anti quota: ' + key)
        dest[key] = {'used': 100 - percent(float(left)), 'reset': stamp(reset)}
    required = {'codex', 'anti:gemini models', 'anti:claude and gpt models'}
    if not required.issubset(week) or not required.issubset(short):
        raise ValueError('Missing weekly/5h quota for Codex or Anti; no worker started')
    return week, short


def save(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def migrate(record):
    record = copy.deepcopy(record)
    if record.get('policy') == 'weekly-delta-25-v2':
        return record
    if record.get('policy') != 'weekly-total-60-v1' or record.get('ceiling') != 60 or record.get('stop_at') != 58:
        raise ValueError('Unknown previous policy; cannot migrate')
    if not record.get('baseline') or set(record['baseline']) != set(record.get('peak_used', {})):
        raise ValueError('Invalid previous ledger')
    for key, base in record['baseline'].items():
        if percent(record['peak_used'][key]) < percent(base['used']):
            raise ValueError('Previous peak below baseline')
        stamp(base['reset'])
    record['previous_policy'] = record['policy']
    record.update(policy='weekly-delta-25-v2', budget_points=25, stop_delta=23)
    record.pop('ceiling')
    record.pop('stop_at')
    return record


class WeeklyBudget:
    def __init__(self, read_codex, read_anti, path, clock=time.time):
        self.read_codex, self.read_anti = read_codex, read_anti
        self.path, self.clock = path, clock
        self.record = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def check(self):
        week, short = snapshot(self.read_codex(), self.read_anti())
        now = self.clock()
        if self.record is None:
            self.record = {'policy': 'weekly-delta-25-v2', 'budget_points': 25, 'stop_delta': 23,
                           'baseline': week, 'peak_used': {k: v['used'] for k, v in week.items()},
                           'started_at': now}
        record = self.record
        if record.get('policy') != 'weekly-delta-25-v2' or record.get('budget_points') != 25 or record.get('stop_delta') != 23:
            raise ValueError('Unknown saved budget policy; refusing to replace history')
        if set(week) != set(record['baseline']) or set(week) != set(record['peak_used']):
            raise ValueError('Weekly quota pools changed; inspect before continuing')
        for key, value in week.items():
            if percent(record['peak_used'][key]) < percent(record['baseline'][key]['used']):
                raise ValueError('Peak below original baseline')
            record['peak_used'][key] = max(percent(record['peak_used'][key]), value['used'])
        record['spent_points'] = {k: record['peak_used'][k] - percent(v['used']) for k, v in record['baseline'].items()}
        record.update(latest_week=week, latest_5h=short, checked_at=now)
        save(self.path, record)
        print('WEEKLY BUDGET: ' + ' | '.join(f'{k}=+{record["spent_points"][k]:g}/25 points (total {v["used"]:g}%)' for k, v in week.items()), flush=True)
        # Reset estimates may drift. Keep a fixed original end and peak: no reset
        # grants, no refunds, no dependence on a five-hour baseline.
        if any(now >= stamp(v['reset']) for v in record['baseline'].values()):
            raise BudgetStop('WEEK_ENDED: authorization for this weekly window has ended')
        if any(v >= record['stop_delta'] for v in record['spent_points'].values()):
            raise BudgetStop('BUDGET_STOP: used +23 weekly points; reserve 2 below +25')
        if any(v >= 98 for v in record['peak_used'].values()):
            raise BudgetStop('ACCOUNT_NEAR_FULL: total weekly usage >=98%')
        if any(v['used'] >= 95 for v in short.values()):
            raise ShortWindowFull('5h usage >=95%; wait without launching another worker')
        return record
