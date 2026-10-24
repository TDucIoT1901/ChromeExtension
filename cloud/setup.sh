#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
sudo apt-get update
sudo apt-get install -y tmux
if ! command -v codex >/dev/null; then npm install -g @openai/codex; fi
if ! command -v agy >/dev/null; then
  installer=$(mktemp)
  trap 'rm -f "$installer"' EXIT
  curl --fail --show-error --silent --location https://antigravity.google/cli/install.sh -o "$installer"
  bash "$installer"
fi
mkdir -p .anti-codex
node --test tests/*.cjs
python3 -m unittest discover -s cloud -p test_runner.py
printf '%s\n' 'Tools prepared. Sign in interactively to Codex and Anti on this cloud host.'
printf '%s\n' 'No model worker has started. After sign-in, run: bash cloud/start.sh'
