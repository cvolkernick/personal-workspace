#!/bin/bash
# Open Grok Build for Workflow Management (personal-workspace)
set -euo pipefail
ROOT='/Users/cvolkernick/personal-workspace'
cd "$ROOT"
PROMPT_FILE='/Users/cvolkernick/personal-workspace/ops/backlog/open-workflow-grok.prompt.txt'
MODE='continue'

if command -v grok >/dev/null 2>&1; then
  GROK_BIN="$(command -v grok)"
elif [ -x "$HOME/.grok/bin/grok" ]; then
  GROK_BIN="$HOME/.grok/bin/grok"
else
  echo "grok CLI not found. Install Grok Build or add it to PATH." >&2
  exit 1
fi

echo "=== Workflow Management → Grok Build ==="
echo "Workspace: $ROOT"
echo "Mode: $MODE (continue = resume last session for this cwd)"
echo ""

if [ "$MODE" = "new" ]; then
  echo "Starting a new Workflow Management Grok session…"
  exec "$GROK_BIN" --cwd "$ROOT" --fullscreen "$(cat "$PROMPT_FILE")"
fi

echo "Continuing most recent Grok session for personal-workspace…"
exec "$GROK_BIN" --cwd "$ROOT" --fullscreen --continue
