#!/bin/bash
# Start Grok with /goal preloaded from backlog: Build one small but real automation that multiplies output o
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$ROOT"
PROMPT_FILE="$ROOT/ops/backlog/seeds/build-one-small-but-real-automation-that-multipl-0b839cb9.prompt.txt"
SEED="$ROOT/ops/backlog/seeds/build-one-small-but-real-automation-that-multipl-0b839cb9.md"
OBJ="$ROOT/ops/backlog/seeds/build-one-small-but-real-automation-that-multipl-0b839cb9.goal.txt"

if [ ! -f "$PROMPT_FILE" ]; then
  echo "Missing prompt file: $PROMPT_FILE" >&2
  exit 1
fi

# Resolve grok binary
if command -v grok >/dev/null 2>&1; then
  GROK_BIN="$(command -v grok)"
elif [ -x "$HOME/.grok/bin/grok" ]; then
  GROK_BIN="$HOME/.grok/bin/grok"
else
  echo "grok CLI not found. Install Grok Build or add it to PATH." >&2
  exit 1
fi

echo "=== Workflow Management: initiate backlog goal ==="
echo "Title: Build one small but real automation that multiplies output o"
echo "Seed:  $SEED"
echo "Prompt: $PROMPT_FILE"
echo ""
echo "Starting Grok with /goal + backlog details as the initial session prompt…"
echo ""

# Interactive session: first argument is the initial user prompt (NOT headless -p).
# Multi-line objective is read from the prompt file so quoting stays reliable.
exec "$GROK_BIN" --cwd "$ROOT" --fullscreen "$(cat "$PROMPT_FILE")"
