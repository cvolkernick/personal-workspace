#!/usr/bin/env bash
# FCC private HTTPS origin via Tailscale Serve on prism-gateway (issue #623).
#
# Terminates TLS with the node's Tailscale/Let's Encrypt cert and proxies to
# financial-command on loopback :8000. Tailnet-only. Never Funnel. Never Vercel.
#
# Does NOT rsync the repo onto the FCC live tree (that tree is work/treasury).
# Run this script; do not `install_remote.sh` from master to "fix HTTPS".
#
# Usage:
#   bash deploy/fcc_tailscale_serve.sh                  # SSH default host
#   bash deploy/fcc_tailscale_serve.sh user@host        # SSH that host
#   bash deploy/fcc_tailscale_serve.sh --local          # already on the FCC host
#   bash deploy/fcc_tailscale_serve.sh --install-unit   # also enable systemd oneshot
set -euo pipefail

DEFAULT_REMOTE="prism-agent@prism-gateway"
TAILSCALE="${TAILSCALE:-/usr/bin/tailscale}"
# Current Tailscale CLI (1.98+). The older `serve https / http://…` form is rejected.
SERVE_CMD=("$TAILSCALE" serve --bg --yes http://127.0.0.1:8000)
UNIT_NAME="fcc-tailscale-serve.service"

REMOTE=""
LOCAL=0
INSTALL_UNIT=0

usage() {
  sed -n '2,16p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local) LOCAL=1; shift ;;
    --install-unit) INSTALL_UNIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      if [[ -z "$REMOTE" && "$1" == *@* ]]; then
        REMOTE="$1"; shift
      else
        echo "Unknown arg: $1" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="$ROOT/deploy/units/$UNIT_NAME"

apply_on_host() {
  if [[ ! -x "$TAILSCALE" ]]; then
    echo "tailscale not found at $TAILSCALE" >&2
    exit 1
  fi

  # Never enable Funnel (public internet). Serve must stay tailnet-only.
  if "$TAILSCALE" funnel status --json 2>/dev/null | grep -q '"AllowFunnel"'; then
    if "$TAILSCALE" funnel status --json 2>/dev/null | grep -q '"AllowFunnel": true'; then
      echo "refusing: Tailscale Funnel is enabled — FCC must stay off the public internet" >&2
      exit 1
    fi
  fi

  local err
  err="$(mktemp)"
  if ! "${SERVE_CMD[@]}" >"$err" 2>&1; then
    if sudo -n "${SERVE_CMD[@]}" >"$err" 2>&1; then
      :
    else
      local status
      status="$("$TAILSCALE" serve status 2>/dev/null || true)"
      if echo "$status" | grep -q '127.0.0.1:8000' && echo "$status" | grep -q 'tailnet only'; then
        echo "serve already maps / → http://127.0.0.1:8000 (tailnet only); apply skipped"
      else
        cat "$err" >&2
        rm -f "$err"
        echo "failed to apply tailscale serve" >&2
        exit 1
      fi
    fi
  fi
  rm -f "$err"

  local status
  status="$("$TAILSCALE" serve status)"
  echo "$status"
  if ! echo "$status" | grep -q 'tailnet only'; then
    echo "refusing: serve status is not tailnet-only (Funnel?)" >&2
    exit 1
  fi
  if ! echo "$status" | grep -q '127.0.0.1:8000'; then
    echo "refusing: serve is not proxying FCC :8000" >&2
    exit 1
  fi
  if echo "$status" | grep -qi 'Funnel on'; then
    echo "refusing: Funnel is on" >&2
    exit 1
  fi
  echo "ok: FCC HTTPS origin is tailnet-only → :8000"
}

install_unit_on_host() {
  if [[ ! -f "$UNIT_SRC" ]]; then
    echo "missing unit: $UNIT_SRC" >&2
    exit 1
  fi
  mkdir -p "$HOME/.config/systemd/user"
  cp "$UNIT_SRC" "$HOME/.config/systemd/user/$UNIT_NAME"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT_NAME"
  systemctl --user status "$UNIT_NAME" --no-pager | head -15
}

if [[ "$LOCAL" -eq 1 ]]; then
  apply_on_host
  if [[ "$INSTALL_UNIT" -eq 1 ]]; then
    install_unit_on_host
  fi
  exit 0
fi

REMOTE="${REMOTE:-$DEFAULT_REMOTE}"

echo "→ Applying FCC Tailscale Serve on $REMOTE (SSH only; no tree overlay)"
# shellcheck disable=SC2029
ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTE" bash -s -- <<'REMOTE'
set -euo pipefail
TAILSCALE="${TAILSCALE:-/usr/bin/tailscale}"
if [[ ! -x "$TAILSCALE" ]]; then
  echo "tailscale not found at $TAILSCALE" >&2
  exit 1
fi
if "$TAILSCALE" funnel status --json 2>/dev/null | grep -q '"AllowFunnel": true'; then
  echo "refusing: Tailscale Funnel is enabled — FCC must stay off the public internet" >&2
  exit 1
fi
err="$(mktemp)"
if ! "$TAILSCALE" serve --bg --yes http://127.0.0.1:8000 >"$err" 2>&1; then
  if sudo -n "$TAILSCALE" serve --bg --yes http://127.0.0.1:8000 >"$err" 2>&1; then
    :
  else
    status="$("$TAILSCALE" serve status 2>/dev/null || true)"
    if echo "$status" | grep -q '127.0.0.1:8000' && echo "$status" | grep -q 'tailnet only'; then
      echo "serve already maps / → http://127.0.0.1:8000 (tailnet only); apply skipped"
    else
      cat "$err" >&2
      rm -f "$err"
      echo "failed to apply tailscale serve" >&2
      exit 1
    fi
  fi
fi
rm -f "$err"
status="$("$TAILSCALE" serve status)"
echo "$status"
echo "$status" | grep -q 'tailnet only' || { echo "refusing: not tailnet-only" >&2; exit 1; }
echo "$status" | grep -q '127.0.0.1:8000' || { echo "refusing: not proxying :8000" >&2; exit 1; }
if echo "$status" | grep -qi 'Funnel on'; then
  echo "refusing: Funnel is on" >&2
  exit 1
fi
echo "ok: FCC HTTPS origin is tailnet-only → :8000"
REMOTE

if [[ "$INSTALL_UNIT" -eq 1 ]]; then
  if [[ ! -f "$UNIT_SRC" ]]; then
    echo "missing unit: $UNIT_SRC" >&2
    exit 1
  fi
  echo "→ Installing $UNIT_NAME on $REMOTE (unit file only)"
  ssh -o BatchMode=yes "$REMOTE" "mkdir -p ~/.config/systemd/user"
  scp -q "$UNIT_SRC" "$REMOTE:~/.config/systemd/user/$UNIT_NAME"
  ssh -o BatchMode=yes "$REMOTE" "systemctl --user daemon-reload && systemctl --user enable --now $UNIT_NAME && systemctl --user status $UNIT_NAME --no-pager | head -15"
fi
