# Cloud collaboration completion plan

Updated 2026-10-26. Owner: Codex plans and reviews; Anti implements and
revises against concrete review findings. Only one writer at a time.

## Current acceptance status (2026-10-26)

- Anti implemented the MV3 background storage queue and revised the cloud state
  machine from Codex review. Codex reviewed source and ran regression checks.
- Local checks: 24 Node tests pass; 37 Python tests discovered, 36 pass and the
  real Linux single-writer lock test is skipped on Windows. GitHub Actions is
  configured to run that test on Linux along with shell syntax and regressions.
- The account/quota handshake was checked against a live local Codex app-server.
  It reads account metadata around quota reads, using the documented non-refreshing
  account/read parameters. This verifies the local CLI, not the remote sign-in.
- Recovery uses bounded stage checkpoints, source/test hashes, persisted feedback,
  direct Anti edits and scope audits. No proposal-only fallback is used. Tests
  exercise completed/reviewed resume without duplicate model calls, partial-call
  blocking, quota read failure, permission denial and the five-call ceiling.
- The latest Codex completion grant permits finishing this concrete system task;
  it does not expand Anti budgets. Defaults remain 25 weekly points, stop at 23.
  Any runtime exception must be privately bound to account, period, run and goal;
  it must not erase the existing ledger. Account/reset drift requires inspection.
- Remote deployment and an end-to-end model run remain unverified: the browser
  tool reports local browser use disabled in Work settings, including after the
  user returned to the original account. Do not infer the cause from the account
  change, or claim the Codespace is running the new revision.
- Installed-browser extension verification is deferred at the user's request.
  Mock tests and static review must not be described as that verification.
- The earlier disconnection probe below remains limited evidence: it demonstrates
  a finite cloud command surviving browser disconnection, not indefinite uptime,
  a host stop/rebuild, or a physically powered-off-laptop test.

The following baseline records the earlier observations and original acceptance
criteria. Its quota figures and deployment revision are historical, not current.

## Verified baseline

- Existing tests: 14 Node extension tests and 10 Python runner tests pass.
- The Node tests use mocks; installed-browser behavior has not been verified.
- GitHub sign-in and Codespace terminal access work after the user trusted the
  folder. Remote revision is 013bd4a with a clean worktree. No tmux worker is
  running. Both CLIs have working sign-ins and quota reads.
- The latest local Anti implementation attempt was denied write_file access.
  It returned an empty response and made no source changes. Do not retry until
  the user has resolved permissions. Logs stay in ignored .anti-codex/.
- App and remote Codex report the same account identifier. Current weekly usage
  is 10% with reset 1793517578, but the retained cloud ledger has peak 28%,
  baseline 1%, reset 1793428548. The user explicitly confirmed an early provider
  reset on 2026-10-25. Preserve the old record, record this confirmation and
  transition only this identified account to reset 1793517578 with baseline 0
  and stop 23. The current 10% already used counts; the old +5 grant expires.
- This one-time transition was verified on 2026-10-26: new baseline 0, peak 16,
  reset 1793517578; old reset/base/peak retained in history and in the separate
  ignored cloud file .anti-codex/budget-before-reset-20261025.json. General
  account-switch and early-reset safeguards still need implementation/tests.
- Remote Anti reports Argon 95% remaining and Gemini shared weekly quota 100%
  remaining. These are observations, not baseline resets or new allowances.
- A finite nohup probe survived navigating the Codespace browser to about:blank
  and wrote its completion timestamp, 2026-10-25 10:17:09 UTC, before reconnection.
  Evidence is .anti-codex/disconnect-probe-20261025.txt on the cloud host. This
  verifies browser-disconnection survival only, not a host stop or a physical
  powered-off-laptop test.

## 1. Restore and verify cloud access

Inspect the existing Codespace, repository revision, uncommitted files, worker
status and budget ledger. Do not rebuild, delete or replace it to recover the
editor: that could discard runtime state or credentials. Read the remote Codex
account and quota through the supported account APIs; never inspect auth files.
Read Anti quota on that host independently of the local app.

Acceptance: terminal access works; repository and existing state are identified;
both remote sign-ins and quota readings are verified without a model call.

## 2. Resolve Anti execution permission

The user manages CLI permissions interactively in the environment where Anti
will run. A local permission does not establish the remote permission. Keep
access scoped to the assigned implementation paths; no bypass flags or broad
security changes. A permission error stops the worker immediately. Empty
responses, denied_actions and a SUCCESS label together are still a failure.

Acceptance: Anti actually writes the assigned source change using its normal
permissions; Codex verifies the diff. A text-only proposal is a separate mode
and must not silently replace the user's selected implementation workflow.

## 3. Preserve account and budget history

Files: cloud/quota_client.py, cloud/quota_parser.py, cloud/runner.py and tests.

- Read account metadata and quota within the same app-server session; recheck
  identity around a quota read and before launching work. Refuse missing or
  inconsistent identity. Keep identifiers private in ignored local state.
- Separate verified Codex identities while preserving every old account's
  records and peak usage. Never assign a legacy unbound ledger to a newly
  observed account without evidence, or delete it to unblock a run.
- A reset timestamp change before the previous period expires, or a usage
  decrease, must not automatically create another allowance. Stop for explicit
  reconciliation while retaining the historical peak and grant's exact period.
  The confirmed early reset above is a single account/old-period/new-period
  transition, not general permission to treat future drift as a reset.
- Use zero baseline conservatively outside the explicitly documented original
  window. Include usage from other sessions. Default budget is 25 percentage
  points with stop at 23; the old supplemental grant remains period-bound.
- Keep Anti pool history conservative across launches. Shared Gemini quota is
  only a bound for Flash, not a Flash-specific measurement. Do not claim account
  identity detection if Anti's supported output does not provide it.
- Check before, during and after each model call. Missing quota, identity
  mismatch, permission errors and STOP all cancel the owned worker and block
  further calls. Polling is not a provider-enforced hard cap.

Acceptance tests: switch away/back without budget refresh; unknown legacy
identity; early reset drift; decreasing usage; confirmed normal rollover;
supplement cannot leak to another account/window; restart preserves peaks;
unreadable quota and permission denial launch no subsequent model.

## 4. Codex plan -> Anti implement -> Codex review -> Anti revise

Files: cloud/runner.py, cloud/start.sh, extension source and regression tests.

Persist the goal, bounded allowed paths and Codex acceptance plan before Anti
starts. Persist each implementation result and test/review result. Supply
concrete failed checks to Anti for a bounded revision. Stop on completion,
budget/permission failure, lack of useful progress or the call/time limit.
Review must include source and actual tests, not just passing output.

First implementation: fix stale popup writes using a classic Manifest V3
background worker importing core.js. All list/create/update/delete operations
go through one serialized queue. Each operation reads and validates current
storage inside that queue. Popups never write whole cached arrays directly.
Update/delete compare an original-record snapshot, not timestamps alone;
reject concurrent changes/deletions while preserving the user's draft.
Keep the existing pocket_notes_data format and storage/activeTab permissions.

Acceptance: two stale popups can both create notes without loss; stale edits
and deletes cannot overwrite changes or resurrect notes; the 300-note limit
holds during simultaneous creates; storage/message errors preserve drafts;
a failed operation does not poison the queue; unknown senders/messages fail
safely; popup closure does not make it responsible for completing persistence.

## 5. Recovery and real-world verification

Persist a checkpoint for each completed stage and hashes of the source it
reviewed. After host interruption, mark an in-flight call as interrupted and
inspect partial changes before any restart. Resume only completed, validated
stages, rechecking identity, quota, STOP and the single-writer lock. Do not
replay an uncertain mutation or endlessly retry a denied call. No automatic
paid infrastructure provisioning or synthetic activity to defeat timeouts.

Acceptance: simulate host interruption at each stage and show no duplicate
writer or lost ledger. Launch a harmless finite remote probe, disconnect the
browser, reconnect and inspect its server-side completion evidence. This
establishes disconnection survival, not indefinite uptime or a physical
powered-off-laptop test. Codespace stop/rebuild is a separate failure case.

Install the extension in an actual supported browser with user-approved
installation, then verify storage persistence, two-window concurrency, failed
operations, save-current-tab, worker restart and literal rendering of script
text. Record actual observations and remaining limitations. Mock tests alone
must never produce an installed-browser PASS.

## Completion evidence

Record the final revision, changed files, test commands/results, bounded
plan/implementation/review/revision transcript, quota readings and ledger
preservation, remote disconnection probe and actual browser observations.
Keep credentials, private identifiers and raw logs out of Git. No claim that
all five parts are complete until their acceptance checks have succeeded.
