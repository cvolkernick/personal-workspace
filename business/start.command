#!/bin/bash
# Panamerica Auto - start local static site
# Double-click or: bash business/start.command
set -euo pipefail

cd "$(dirname "$0")"
PORT=${PORT:-8086}

echo "Panamerica Auto → http://localhost:${PORT}/"
echo "Press Ctrl+C to stop."

# Open browser on macOS after brief delay (best effort)
if command -v open >/dev/null 2>&1; then
  (sleep 1.2 && open "http://localhost:${PORT}/") &
fi

python3 -m http.server "${PORT}"
