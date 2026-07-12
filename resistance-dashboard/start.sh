#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

# Load persistent secrets (created for you at ~/.config/resistance-dashboard/env)
if [ -f "$HOME/.config/resistance-dashboard/env" ]; then
  # shellcheck disable=SC1090
  source "$HOME/.config/resistance-dashboard/env"
fi

export LOCAL_WORKSPACE_DIR="${LOCAL_WORKSPACE_DIR:-$(cd .. && pwd)}"
export PORT="${PORT:-8787}"

# Free port if a previous instance is still up
if command -v lsof >/dev/null 2>&1; then
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
fi

echo "Resistance dashboard → http://127.0.0.1:${PORT}/"
exec python3 server.py "$PORT"
