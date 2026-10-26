#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.local/bin:$PATH"
cd "$(dirname "$0")/.."
mkdir -p .anti-codex

ACTION="run"
EXTRA_ARGS=()
RUN_ARG=""
AUTH_ARG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --resume|resume)
      ACTION="resume"
      shift
      ;;
    --flash-only)
      EXTRA_ARGS+=(--flash-only)
      shift
      ;;
    --run)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --run requires a path argument"
        exit 1
      fi
      RUN_ARG="$2"
      shift 2
      ;;
    --completion-auth)
      if [[ -z "${2:-}" ]]; then
        echo "Error: --completion-auth requires a file path argument"
        exit 1
      fi
      AUTH_ARG="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: bash cloud/start.sh [--resume|resume] [--flash-only] [--run <path>] [--completion-auth <path>]"
      exit 1
      ;;
  esac
done

if [[ -n "$AUTH_ARG" && -z "$RUN_ARG" ]]; then
  echo "Error: --run is required when --completion-auth is set"
  exit 1
fi

if [[ -n "$RUN_ARG" ]]; then
  EXTRA_ARGS+=(--run "$RUN_ARG")
fi
if [[ -n "$AUTH_ARG" ]]; then
  EXTRA_ARGS+=(--completion-auth "$AUTH_ARG")
fi

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

if ! python3 -m unittest discover -s cloud -p 'test*.py' > .anti-codex/preflight-python.log 2>&1; then
  tail -n 50 .anti-codex/preflight-python.log
  exit 1
fi
tail -n 4 .anti-codex/preflight-python.log
if ! node --test tests/*.cjs > .anti-codex/preflight-node.log 2>&1; then
  tail -n 50 .anti-codex/preflight-node.log
  exit 1
fi
tail -n 8 .anti-codex/preflight-node.log
if ! python3 cloud/runner.py quota "${EXTRA_ARGS[@]}" > .anti-codex/preflight-quota.log 2>&1; then
  tail -n 20 .anti-codex/preflight-quota.log
  exit 1
fi
echo 'Quota preflight passed. Full preflight logs are in .anti-codex/.'

CMD_ARGS=("python3" "cloud/runner.py" "$ACTION" "${EXTRA_ARGS[@]}")
CMD=""
for arg in "${CMD_ARGS[@]}"; do
  CMD+="$(printf '%q ' "$arg")"
done
CMD+="> .anti-codex/cloud.log 2>&1"
tmux new-session -d -s pocket-notes -c "$PWD" "$CMD"
echo "Cloud worker launched in $ACTION mode. Verify status and logs; tmux alone is not completion."
