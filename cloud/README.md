# Anti + Codex on cloud

Current user authorization: complete cloud collaboration with extension source
and regression tests shared with Anti for proposals and Codex for review.
The launcher requires `.anti-codex/ALLOW_ANTI` for authorized sharing runs.
It is not an authentication credential or a CLI permission bypass.

Run from an existing Linux Codespace/VM with agy, codex, node, Python and tmux,
and both accounts already signed in. No API key or new paid service is configured.

```bash
# Start a fresh run
bash cloud/start.sh

# Or resume a previous interrupted completed stage
bash cloud/start.sh --resume

# Optional: pass explicit per-job completion authorization
bash cloud/start.sh --run .anti-codex/cloud-example-run --completion-auth .anti-codex/completion_auth.json

# Check worker and quota status
python3 cloud/runner.py status
python3 cloud/runner.py quota
cat .anti-codex/cloud.log
```

Stop: create `.anti-codex/STOP`; the quota monitor cancels its own worker at the
next check. Never remove a live lock or restart a failed job without inspecting
partial source changes, status and logs. A run has at most 2 Anti/Codex rounds,
5 total model calls by default, 8 minutes per call and 20 minutes total. It exits on completion or any blocker.

## Architecture and Workflow

The collaboration follows a strict multi-stage protocol:
1. **Codex Plan**: Codex receives current source and regression tests with no tools,
   producing a structured implementation plan and acceptance criteria.
2. **Anti Implementation**: Anti receives the Codex plan, current source, and tests.
   Anti directly modifies allowed extension files (`extension/manifest.json`,
   `extension/popup.html`, `extension/popup.css`, `extension/popup.js`,
   `extension/core.js`, `extension/background.js`) under ordinary sandbox permissions.
3. **Host Test & Scope Audit**: The host snapshots workspace files before Anti runs,
   audits tracked/untracked project scope after the run (failing if any file outside
   allowed scope was touched), validates manifest permissions, and runs Node regression
   tests (`tests/*.cjs`).
4. **Codex Review**: Codex independently reviews updated source, actual test source,
   acceptance criteria, and test runner exit code and stdout/stderr with no tools.
   If issues or test failures exist, actual feedback is persisted and passed to Anti
   for a bounded revision round.
5. **Static Review Pass**: Marked when all Node tests pass and Codex returns a
   verdict of `PASS` with zero issues.

Anti runs with ordinary CLI sandbox permissions (no bypass flags). Permission
denials fail immediately; they do not trigger a silent text-only fallback.
Anti receives current source and tests and is instructed to use file tools only;
the host runs commands, git and tests. A `command` denial does not establish that
`write_file` was denied. The result parser reports the denied action separately
from an unsuccessful status or empty response.

For the inspected case of a first-round empty SUCCESS response with exactly one
command denial after two model calls, `recover_command_denial.py` can prepare one
operator-invoked successor. It verifies unchanged source/test hashes, matching
account, normal quota guard and the retained Codex plan. The original run remains
unchanged and can produce only one successor. Both prior calls count toward the
same five-call ceiling, leaving at most three calls. The successor has a fresh
bounded twenty-minute execution window; nested recovery is rejected. No model is
launched until the operator explicitly starts it with `start.sh --resume --run`.
The separately inspected interrupted shared-quota attempt can use
`--quota-interruption` only with its three-call checkpoint, empty interrupted
ERROR result, no denied actions, and unchanged source/tests. Its successor keeps
all three calls and has only two calls left (implementation and review). This
mode can be used once and cannot be chained. Startup keeps full preflight logs
under `.anti-codex/` and prints summaries rather than simulated test checkpoints.

After the operator confirms scoped extension write permission, the inspected
four-call `write_file` denial can use `--write-permission-confirmed` once. Source,
tests, account, quota and denial artifacts must still validate. Its successor
retains all four calls and permits up to two implementation/review rounds, with
an explicit total ceiling of eight for this verification only. The default
five-call limit remains unchanged. No weekly allowance, CLI permission or past
record is reset. The mode cannot recover another permission-recovery attempt.
Initial implementation uses Flash; revision uses Argon, independently of the
inherited call count. Quota guards apply to both.

## Checkpoint, Crash Recovery and Resume

Every run maintains a persisted `checkpoint.json` recording:
- `stage`: `not_started`, `planned`, `implemented`, `reviewed`, `revised`,
  `reviewed_revision`, `static_review_pass`, `needs_attention`, or `blocked`.
- `in_flight`: boolean indicating an active mutation or model call.
- `source_hashes`: SHA-256 hashes of all allowed source files.
- `test_hashes`: SHA-256 hashes of all regression test files.
- Bounded counters: `round` (max 2), `model_calls` (counted persistently before spawning,
  normally max 5; the explicit operator-confirmed permission recovery above has max 8),
  and wall-clock `deadline`.

**Recovery and State Machine rules**:
- All resume and status run paths must resolve directly under `.anti-codex` and reject symlinks/traversal.
- If a run was interrupted during an in-flight mutation (`in_flight: true`),
  resuming marks the stage as `needs_attention` and fails closed. It will NEVER
  automatically replay uncertain mutations.
- `needs_attention` and `blocked` stages never replay automatically.
- Stage-driven resume:
  - `planned` -> Anti implementation.
  - `implemented` / `revised` -> Node tests then Codex review WITHOUT replaying Anti.
  - `reviewed` with `CHANGES` -> advances round with stored actual review feedback.
  - `static_review_pass` -> validates exact source and test hash sets, identity, goal,
    and run path before returning PASS with zero model calls. Never accepts missing/empty hashes or added files.
- Resuming completed stages requires:
  1. No `.anti-codex/STOP` marker.
  2. Single-writer lock acquisition (`runner.lock`).
  3. Verified quota check and account identity match.
  4. Exact SHA-256 hash match for all allowed files and regression tests.
  5. Unexpired deadline.

## Account and Budget Binding

`--flash-only` is an explicit model policy: both implementation and revision use
Flash, and Argon is never selected. Pass it to both preparation and `start.sh`.
The policy is stored in the checkpoint and cannot be changed during resume.
All three pool meters remain required and all ledger values are retained.
An exhausted unused Argon pool is recorded in `blocked_pools`; it blocks the
default mixed-model policy but does not spend or block Flash's separate budget.
Flash/shared-pool and Codex stops still block every call in this mode. No stop
threshold, baseline or quota history is reset, and there is no automatic fallback.

An explicitly confirmed early Codex reset can be reconciled with
`python3 cloud/prepare_verification.py --help` before a new verification run.
Supply the exact old/new reset timestamps, the highest observed previous Codex
usage across sessions, and unchanged-period Anti peak floors. The helper verifies
the signed-in identity against the retained ledger, holds the runner lock, backs
up the original ledger, archives the old Codex peak and preserves Anti baselines,
periods and peak usage. It preflights the normal 25-point/23-stop guard before
committing any change. It neither redeems a reset nor launches a model; CLI
permission policies are untouched. Reusing an existing run directory is rejected.

Budget: 25 weekly percentage points per account pool, early stop at 23 points.
- **Account Identity**: Non-empty `account_id` is strictly required for every budget update.
  The ledger binds to this account identity. Fabricated fallback identities are forbidden.
- **Account Switch**: Switching accounts fails closed (`RuntimeError`) to prevent
  unauthorized budget resets or cross-account allowance leakage.
- **Unbound Ledgers**: Historical ledgers missing account IDs will not be silently
  adopted; explicit manual reconciliation is required.
- **Legacy Supplement**: The one-time supplement for window 1793428548 (stop at 28)
  requires the verified original account in the ignored authorization file's
  `codex_account_id`, matching the retained ledger. An active old four-field
  authorization requires reconciliation; history must not be deleted.
- **Reset Drift**: A reset timestamp changing before the current period expires
  stops pending manual reconciliation. No automatic allowance is granted.
- **Codex Usage Decreases**: Preserve peak usage and stop for reconciliation.
- **Anti Display Fluctuations**: Preserve the raw peak and a persisted
  `charged_used` counter. A decrease never refunds charges; every subsequent
  increase is charged again, including increases below the old peak. The same
  23-point stop applies to this conservative counter. Repeated fluctuations can
  stop a job early, but cannot enlarge the allowance. Missing/invalid quota or
  a changed period still stops. This remains a shared-pool bound for Flash.
- **Rollover**: Normal period rollover occurs only after `now >= old_reset` and a
  genuine later reset (`fresh_reset >= old_reset + 86400`), resetting baseline to 0.

## Optional Per-Job Completion Authorization

To finish concrete system completion tasks when authorized by the user, an optional
per-job completion override may be configured:

File schema (`.anti-codex/completion_auth.json`):
```json
{
  "codex_account_id": "example-verified-account-id",
  "reset": 1793517578,
  "run_id": "cloud-example-run",
  "goal": "Fix Pocket Notes MV3 extension correctness issues and ensure all regression tests pass",
  "stop_used": 28,
  "cap_used": 30
}
```

- Requires `codex_account_id`, `reset`, `goal`, `stop_used`, `cap_used`, and `run_id` (or `job_id`).
- Must satisfy `stop_used <= cap_used - 2`, with `cap_used <= 100` and `stop_used <= 98`.
- Scoped strictly to the concrete run ID, goal, account, and reset timestamp; malformed explicit auth fails closed.
- Uses the explicitly authorized Codex stop threshold for that run only.
- `--completion-auth` requires `--run`; the run name must match the private file.
  A new run path must not exist. Resume uses the same explicit path and authorization.
  The example does not grant an allowance: record the user's actual authorization
  privately, including the verified account and current weekly period.
- Does NOT apply to Anti (Anti retains 25 budget, stop at 23).
- Does NOT bypass provider near-full limits (>=99%) or short-window limits (>=95%).

## Environment and Verification Notes

- Codespaces has idle timeouts and lifetime limits. Closing the browser disconnects
  the terminal, but remote tmux processes persist within Codespace limits. A host
  stop or rebuild interrupts the environment.
- `static_review_pass` validates Node mock regression tests and Codex static code
  review. It does NOT claim installed-browser UI/runtime verification.
