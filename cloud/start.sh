#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
mkdir -p .anti-codex
if [[ ! -f .anti-codex/ALLOW_ANTI ]]; then
  echo 'Anti source sharing is currently disabled by the user. Obtain renewed authorization first.'
  exit 1
fi
if tmux has-session -t pocket-notes 2>/dev/null; then
  echo 'Pocket Notes already running; use python3 cloud/runner.py status'
  exit 1
fi
if [[ -f .anti-codex/STOP ]]; then
  echo 'STOP is present. Inspect the previous run before explicitly removing this marker.'
  exit 1
fi
python3 -m unittest discover -s cloud -p test_runner.py
node --test tests/*.cjs
python3 cloud/runner.py quota
tmux new-session -d -s pocket-notes -c "$PWD" 'python3 cloud/runner.py run > .anti-codex/cloud.log 2>&1'
echo 'Cloud worker launched. Verify status and logs; tmux alone is not completion.'
