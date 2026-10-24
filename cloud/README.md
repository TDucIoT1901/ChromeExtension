# Anti + Codex on cloud

Current user authorization: complete cloud collaboration with extension source
and regression tests shared with Anti for proposals and Codex for review.
The launcher requires `.anti-codex/ALLOW_ANTI` for authorized sharing runs.
It is not an authentication credential or a CLI permission bypass.

Run from an existing Linux Codespace/VM with agy, codex, node, Python and tmux,
and both accounts already signed in. No API key or new paid service is configured.

    bash cloud/start.sh
    python3 cloud/runner.py status
    cat .anti-codex/cloud.log

Stop: create `.anti-codex/STOP`; the quota monitor cancels its own worker at the
next check. Never remove a live lock or restart a failed job without inspecting
partial source changes, status and logs. A run has at most 2 Anti/Codex rounds,
8 minutes per call and 20 minutes total. It exits on completion or any blocker.

Anti generates proposed extension source from supplied files. The controller
applies only a fixed file list, runs Node regression tests, and supplies actual
results plus source to Codex for independent review. Codex feedback is returned
to Anti if changes are needed. No security/approval bypass flags are used.
Snapshots before changes and complete responses remain under `.anti-codex`.
This avoids requiring command permission in Anti headless mode.

Budget: 25 weekly percentage points per account pool, early stop at 23 points.
Codex starts at the original 1% reading for the present weekly window, so local
setup/chat use counts too. Other sessions' usage counts conservatively. Flash
has no separate quota meter in the observed `/usage` output: the shared Gemini
pool caps it conservatively; Argon additionally has its own pool. New ledgers
use baseline zero outside the original Codex window, conservatively counting
all usage already consumed in that period. Unknown quota
stops the run. Existing ledger is reused. Confirmed new periods use baseline 0.
Anti has a one-time user-approved exception before 24 Oct 2026 14:32 Asia/Saigon.
Polling and provider rounding mean this is NOT a guaranteed hard 25% cap.

The user approved 5 additional Codex account points when total usage was 25%.
Only for the window ending at Unix 1793428548, ignored file
`.anti-codex/authorization.json` may contain
`{"reset":1793428548,"cap_used":30,"stop_used":28,"extra_points":5}`.
This stops at 28% total, reserving 2 points below 30%, without resetting the
original ledger, changing any Anti budget or expanding any future week.

Closing the laptop does not stop remote Linux processes. Stopping/rebuilding
Codespaces does. Codespaces also has idle and maximum lifetime limits; this is
not an always-on service. A 20-minute job fits the default 30-minute idle window
when launched from a connected session. Earlier host stops still interrupt it.
No auto-resume is promised. Do not fabricate activity to defeat host timeouts
or silently provision paid infrastructure.
https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle

`static_review_pass` means Node tests plus a completed Codex source review.
It does not claim browser installation/UI verification. The default models are
Gemini 3.8 Flash Medium first, Gemini 4 Argon Medium for follow-up corrections;
Codex keeps its configured model. No credits, resets, purchases or deployment.
