#!/usr/bin/env bash
# Start B2 (Brain 2) knowledge-base UX.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PORT="${B2_PORT:-8792}"
HOST="${B2_HOST:-127.0.0.1}"
VAULT="${B2_VAULT_PATH:-$HOME/B2}"

if [[ ! -d "$VAULT" ]]; then
  echo "Vault not found: $VAULT" >&2
  echo "Set B2_VAULT_PATH or create ~/B2" >&2
  exit 1
fi

export B2_VAULT_PATH="$VAULT"
export B2_PORT="$PORT"

echo "B2 vault: $VAULT"
echo "Open:    http://${HOST}:${PORT}/"
exec python3 "$ROOT/server.py" --host "$HOST" --port "$PORT" --vault "$VAULT"
