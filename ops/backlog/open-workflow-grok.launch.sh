#!/bin/bash
# Open named Grok Build session: Workflow Management
set -euo pipefail
ROOT='/Users/cvolkernick/personal-workspace'
RUN_CWD='/Users/cvolkernick'
SESSION_ID='019f6e82-4398-72d0-b180-58f68680ae23'
SESSION_NAME='Workflow Management'
cd "$RUN_CWD"

if command -v grok >/dev/null 2>&1; then
  GROK_BIN="$(command -v grok)"
elif [ -x "$HOME/.grok/bin/grok" ]; then
  GROK_BIN="$HOME/.grok/bin/grok"
else
  echo "grok CLI not found. Install Grok Build or add it to PATH." >&2
  exit 1
fi

echo "=== Workflow Management → Grok Build ==="
echo "Session: $SESSION_NAME"
echo "ID:      $SESSION_ID"
echo "Cwd:     $RUN_CWD"
echo "Workspace files: $ROOT"
echo ""
echo "Resuming named session (not most-recent)…"
exec "$GROK_BIN" --cwd "$RUN_CWD" --fullscreen --resume "$SESSION_ID"
