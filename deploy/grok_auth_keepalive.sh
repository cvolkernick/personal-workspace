#!/usr/bin/env bash
# Renew ~/.grok/auth.json via the Grok CLI so FitDash never sees an expired
# SuperGrok token (#1086).
#
# The access token lasts about 6h. FitDash only reads this file. The CLI
# refreshes it when GROK_AUTH_EARLY_INVALIDATION_SECS says the token is due.
# The window is 3h (10800s): shorter than the ~6h token life and longer than
# the 2h timer gap, so a fresh token is never "due" again and each run does at
# most one refresh. A run where the token is not due does not start the CLI.
#
# One refresh per run, behind a flock. If the lock or another grok CLI
# process is busy, the run exits 0 (outcome=busy) and leaves it to them.
# A started CLI is never abandoned: the run waits for it to exit, and only
# stops it after GROK_AUTH_HARD_TIMEOUT_SECS. There is no retry in a run.
#
# Logged out posts one alert per state change and exits 1. That is:
# auth.json missing before or after the run, or the CLI said "You are not
# authenticated." / wrote invalid_grant / RefreshTokenRejected / revoked to
# its log during this run AND expires_at did not advance. With the 3h window
# the CLI prints "not authenticated" for a due token and then renews it; an
# advanced expires_at wins and the run is "renewed".
#
# Log fields: exit= is this script's exit status; cli_exit= is the CLI's.
# The script never echoes CLI output or CLI log lines (the CLI log carries
# rt_prefix / key_prefix fields); it only greps them for fixed words.
#
# Install (user units). deploy/ is not on the FCC work/treasury pin, so the
# timer runs a copy outside the git tree:
#
#   install -m 0755 deploy/grok_auth_keepalive.sh \
#     ~/.config/personal-workspace/grok_auth_keepalive.sh
#   cp deploy/units/grok-auth-keepalive.service \
#      deploy/units/grok-auth-keepalive.timer \
#      ~/.config/systemd/user/
#   systemctl --user daemon-reload
#   systemctl --user enable --now grok-auth-keepalive.timer
#
# Rollback: systemctl --user disable --now grok-auth-keepalive.timer
#
# Testing must never touch the live auth.json. Point GROK_AUTH_JSON (or HOME)
# at a temp copy, and never save and restore over the live file: refresh
# tokens rotate, and a restored copy gets the session revoked.
#
# ntfy is retired (#704). Alerts comment on GitHub #701 through
# treasury.pi_ops_alert on the live FCC tree. The comment carries the reason
# and expires_at only. Never prints key, refresh_token, or a JWT.
set -euo pipefail

GROK_BIN="${GROK_AUTH_BIN:-${HOME}/.grok/bin/grok}"
AUTH_JSON="${GROK_AUTH_JSON:-${HOME}/.grok/auth.json}"
CLI_LOG="${GROK_AUTH_CLI_LOG:-$(dirname "$AUTH_JSON")/logs/unified.jsonl}"
EARLY="${GROK_AUTH_EARLY_INVALIDATION_SECS:-10800}"
TIMEOUT_SECS="${GROK_AUTH_TIMEOUT_SECS:-45}"
HARD_TIMEOUT_SECS="${GROK_AUTH_HARD_TIMEOUT_SECS:-120}"
MIN_REMAINING="${GROK_AUTH_MIN_REMAINING_SECS:-3600}"
DEDUPE_SECS="${GROK_AUTH_ALERT_DEDUPE_SECS:-21600}"
STATE_DIR="${GROK_AUTH_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/grok-auth-keepalive}"
LOCK_FILE="${GROK_AUTH_LOCK_FILE:-${XDG_RUNTIME_DIR:-${HOME}/.cache}/grok-auth-keepalive.lock}"
WORKSPACE="${GROK_AUTH_WORKSPACE:-${HOME}/personal-workspace}"
RELOGIN_MSG="Chris must re-login: grok login --device-code"

# Test guard: the suite sets GROK_AUTH_REFUSE_PATH to the runner's real
# ~/.grok/auth.json. Refuse before reading anything.
if [[ -n "${GROK_AUTH_REFUSE_PATH:-}" ]] \
  && [[ "$(realpath -m -- "$AUTH_JSON")" == "$(realpath -m -- "$GROK_AUTH_REFUSE_PATH")" ]]; then
  printf '%s\n' "outcome=refused-live-auth exit=3"
  exit 3
fi

export GROK_AUTH_EARLY_INVALIDATION_SECS="$EARLY"
export GROK_AUTH_JSON

mkdir -p "$STATE_DIR" "$(dirname "$LOCK_FILE")"
chmod 700 "$STATE_DIR" 2>/dev/null || true

log() {
  printf '%s\n' "$*"
}

read_expires() {
  python3 - "$1" << 'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("")
    raise SystemExit(0)
best = ""
if isinstance(data, dict):
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        if not (entry.get("key") or entry.get("access_token")):
            continue
        raw = str(entry.get("expires_at") or "")
        if raw and raw > best:
            best = raw
print(best)
PY
}

# Prints "<due 0|1|x> <seconds remaining>"; x = unparseable.
due_check() {
  python3 - "$1" "$EARLY" << 'PY'
import sys
from datetime import datetime, timezone


def parse(raw):
    if not raw:
        return None
    text = str(raw).strip().replace("Z", "+00:00")
    if "." in text:
        head, rest = text.split(".", 1)
        digits = []
        tail = ""
        for index, ch in enumerate(rest):
            if ch.isdigit():
                digits.append(ch)
            else:
                tail = rest[index:]
                break
        text = head + "." + ("".join(digits) + "000000")[:6] + tail
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


when = parse(sys.argv[1])
if when is None:
    print("x 0")
    raise SystemExit(0)
left = int((when - datetime.now(timezone.utc)).total_seconds())
print(("1" if left < int(sys.argv[2]) else "0") + f" {left}")
PY
}

alert_text() {
  local reason="$1" exp="$2" prev="$3"
  local text
  text="reason=${reason}"$'\n'"expires_at=${exp}"$'\n'"previous_expires_at=${prev}"$'\n'"cli_exit=${rc}"$'\n'
  if [[ "$reason" == "logged-out" ]]; then
    text+="action=${RELOGIN_MSG}"$'\n'
  fi
  printf '%s' "$text"
}

post_alert() {
  local reason="$1" exp="$2" prev="$3"
  local text
  text="$(alert_text "$reason" "$exp" "$prev")"
  if [[ -n "${GROK_AUTH_ALERT_CMD:-}" ]]; then
    GROK_AUTH_ALERT_REASON="$reason" \
      GROK_AUTH_ALERT_TEXT="$text" \
      GROK_AUTH_EXPIRES_AT="$exp" \
      GROK_AUTH_PREVIOUS_EXPIRES_AT="$prev" \
      GROK_AUTH_EXIT="$rc" \
      bash -c "$GROK_AUTH_ALERT_CMD" \
      || log "outcome=alert-failed reason=${reason} cli_exit=${rc}"
    return 0
  fi
  GROK_AUTH_ALERT_TEXT="$text" \
    GROK_AUTH_WORKSPACE="$WORKSPACE" \
    python3 - << 'PY' || log "outcome=alert-failed reason=${reason} cli_exit=${rc}"
import os
import sys

workspace = os.environ.get("GROK_AUTH_WORKSPACE") or ""
if workspace:
    sys.path.insert(0, workspace)
text = os.environ.get("GROK_AUTH_ALERT_TEXT") or ""
if "eyJ" in text or len(text) > 600:
    print("skipped:unsafe-text")
    raise SystemExit(0)
try:
    from treasury.pi_ops_alert import post_ops_github
except ImportError:
    print("skipped:no-module")
    raise SystemExit(0)
result = post_ops_github("grok-auth-keepalive", text, as_markdown=True)
if result.get("posted"):
    print("posted")
else:
    print("skipped:" + str(result.get("skipped") or "not-posted"))
PY
}

# Generic failures: at most one alert per DEDUPE_SECS.
send_alert() {
  local reason="$1" exp="$2" prev="$3"
  local now dedupe_until
  now="$(date -u +%s)"
  dedupe_until=0
  if [[ -f "$STATE_DIR/alert-until" ]]; then
    dedupe_until="$(tr -cd '0-9' <"$STATE_DIR/alert-until" || true)"
    dedupe_until="${dedupe_until:-0}"
  fi
  if (( now < dedupe_until )); then
    log "outcome=alert-suppressed reason=${reason} expires_at=${exp} previous_expires_at=${prev} cli_exit=${rc}"
    return 0
  fi
  printf '%s\n' "$((now + DEDUPE_SECS))" >"$STATE_DIR/alert-until"
  post_alert "$reason" "$exp" "$prev"
  log "outcome=alerted reason=${reason} expires_at=${exp} previous_expires_at=${prev} cli_exit=${rc}"
}

# Logged out: one alert per state change. A healthy run clears the marker.
logged_out() {
  local why="$1" prev="$2"
  if [[ -f "$STATE_DIR/logged-out" ]]; then
    log "outcome=alert-suppressed reason=logged-out detail=${why} cli_exit=${rc}"
  else
    printf '%s\n' "$(date -u +%s) ${why}" >"$STATE_DIR/logged-out"
    post_alert "logged-out" "" "$prev"
    log "outcome=alerted reason=logged-out detail=${why} cli_exit=${rc}"
  fi
  log "outcome=logged-out detail=${why} exit=1 cli_exit=${rc} previous_expires_at=${prev} action=${RELOGIN_MSG}"
  exit 1
}

healthy() {
  rm -f "$STATE_DIR/alert-until" "$STATE_DIR/logged-out"
  log "$1"
  exit 0
}

# CLI exit status; "none" until the CLI has actually run.
rc=none

# One run at a time. Held by our CLI child too, so a run never overlaps a
# refresh another run started.
exec 9>>"$LOCK_FILE"
if ! flock -n 9; then
  log "outcome=busy detail=lock-held exit=0"
  exit 0
fi

if [[ ! -e "$AUTH_JSON" ]]; then
  logged_out "auth-missing" ""
fi

before="$(read_expires "$AUTH_JSON")"
read -r due left <<<"$(due_check "$before")"
if [[ "$due" == "x" ]]; then
  send_alert "unreadable-auth" "" "$before"
  log "outcome=unreadable-auth exit=1 previous_expires_at=${before}"
  exit 1
fi
if [[ "$due" == "0" ]]; then
  healthy "outcome=fresh exit=0 expires_at=${before} remaining_secs=${left} window_secs=${EARLY}"
fi

if [[ ! -x "$GROK_BIN" ]]; then
  send_alert "missing-bin" "$before" "$before"
  log "outcome=missing-bin exit=1 cli_exit=none expires_at=${before}"
  exit 1
fi

# Another grok CLI process will refresh on its own; two refreshers racing on
# a rotating refresh token is what revoked the 10/07 session.
if pgrep -u "$(id -u)" -f -- "$GROK_BIN" >/dev/null 2>&1; then
  log "outcome=busy detail=cli-running exit=0 expires_at=${before}"
  exit 0
fi

log_size=0
if [[ -f "$CLI_LOG" ]]; then
  log_size="$(stat -c %s "$CLI_LOG" 2>/dev/null || echo 0)"
fi
out="$(mktemp "$STATE_DIR/cli-out.XXXXXX")"
trap 'rm -f "$out"' EXIT

# Own session/process group (pgid == cli_pid; bash runs this background job
# without job control, so setsid execs in place) so the hard timeout can stop
# the CLI and anything it spawned.
setsid "$GROK_BIN" models </dev/null >"$out" 2>&1 &
cli_pid=$!
start="$(date +%s)"
soft_logged=0
while kill -0 "$cli_pid" 2>/dev/null; do
  elapsed=$(( $(date +%s) - start ))
  if (( elapsed >= HARD_TIMEOUT_SECS )); then
    kill -TERM -- "-$cli_pid" 2>/dev/null || kill -TERM "$cli_pid" 2>/dev/null || true
    for _ in $(seq 1 25); do
      kill -0 -- "-$cli_pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -KILL -- "-$cli_pid" 2>/dev/null || true
    break
  fi
  if (( elapsed >= TIMEOUT_SECS && soft_logged == 0 )); then
    log "outcome=waiting detail=cli-still-running elapsed_secs=${elapsed}"
    soft_logged=1
  fi
  sleep 0.2
done
set +e
wait "$cli_pid"
rc=$?
set -e
elapsed=$(( $(date +%s) - start ))
if (( elapsed >= HARD_TIMEOUT_SECS )) && (( rc != 0 )); then
  rc=124
fi

new_log=""
if [[ -f "$CLI_LOG" ]]; then
  cur_size="$(stat -c %s "$CLI_LOG" 2>/dev/null || echo 0)"
  if (( cur_size > log_size )); then
    new_log="$(tail -c +"$((log_size + 1))" "$CLI_LOG" \
      | grep -oE 'invalid_grant|RefreshTokenRejected|Refresh token has been revoked|permanent_failure' \
      | head -1 || true)"
  fi
fi
cli_said_logout=0
if grep -qiE "You are not authenticated|not logged in|logged out" "$out" 2>/dev/null; then
  cli_said_logout=1
fi
if [[ ! -e "$AUTH_JSON" ]]; then
  logged_out "auth-deleted" "$before"
fi

after="$(read_expires "$AUTH_JSON")"
# Advanced only when both dates parse and after is really later. due_check
# prints "x 0" for an unparseable date; an unparseable after is never
# "advanced", so logout text / rejection lines then mean logged-out.
advanced=0
if [[ -n "$after" ]]; then
  read -r before_ok before_left_now <<<"$(due_check "$before")"
  read -r after_ok after_left_now <<<"$(due_check "$after")"
  if [[ "$before_ok" != "x" && "$after_ok" != "x" ]] \
    && (( after_left_now > before_left_now )); then
    advanced=1
  fi
fi
# CLI text and rejection lines count only when the token did not move.
if (( advanced == 0 )); then
  if [[ -n "$new_log" ]]; then
    logged_out "refresh-rejected" "$before"
  fi
  if (( cli_said_logout == 1 )); then
    logged_out "cli-not-authenticated" "$before"
  fi
fi
if (( rc == 124 )); then
  send_alert "timeout" "$after" "$before"
  log "outcome=timeout exit=1 cli_exit=124 expires_at=${after} previous_expires_at=${before}"
  exit 1
fi
if (( rc != 0 )); then
  send_alert "exit-${rc}" "$after" "$before"
  log "outcome=exit-${rc} exit=1 cli_exit=${rc} expires_at=${after} previous_expires_at=${before}"
  exit 1
fi
if (( advanced == 0 )); then
  send_alert "expires-not-advanced" "$after" "$before"
  log "outcome=expires-not-advanced exit=1 cli_exit=${rc} expires_at=${after} previous_expires_at=${before}"
  exit 1
fi
read -r _ after_left <<<"$(due_check "$after")"
if (( after_left < MIN_REMAINING )); then
  send_alert "expires-short" "$after" "$before"
  log "outcome=expires-short exit=1 cli_exit=${rc} expires_at=${after} previous_expires_at=${before}"
  exit 1
fi
note=""
if (( cli_said_logout == 1 || ${#new_log} > 0 )); then
  note=" note=cli-logout-text-ignored-expires-advanced"
fi
healthy "outcome=renewed exit=0 cli_exit=${rc} expires_at=${after} previous_expires_at=${before}${note}"
