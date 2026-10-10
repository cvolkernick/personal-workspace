#!/usr/bin/env bash
# Renew ~/.grok/auth.json via the Grok CLI so FitDash never sees an expired
# SuperGrok token (#1086).
#
# The access token lasts about 6h. FitDash only reads this file. The CLI
# refreshes it when GROK_AUTH_EARLY_INVALIDATION_SECS says the token is due.
# The default here is 7h, which is longer than the token lifetime, so every
# timer run renews. The timer itself is every 2h.
#
# Install on prism (user units). deploy/ is not on the FCC work/treasury pin,
# so the timer runs a copy outside the git tree:
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
# ntfy is retired (#704). A failure comments once per 6h on GitHub #701
# through treasury.pi_ops_alert on the live FCC tree. The comment carries the
# reason and expires_at only. Never prints key, refresh_token, or a JWT.
set -euo pipefail

GROK_BIN="${GROK_AUTH_BIN:-/home/prism-agent/.grok/bin/grok}"
AUTH_JSON="${GROK_AUTH_JSON:-${HOME}/.grok/auth.json}"
EARLY="${GROK_AUTH_EARLY_INVALIDATION_SECS:-25200}"
TIMEOUT_SECS="${GROK_AUTH_TIMEOUT_SECS:-45}"
MIN_REMAINING="${GROK_AUTH_MIN_REMAINING_SECS:-3600}"
DEDUPE_SECS="${GROK_AUTH_ALERT_DEDUPE_SECS:-21600}"
STATE_DIR="${GROK_AUTH_STATE_DIR:-${XDG_STATE_HOME:-${HOME}/.local/state}/grok-auth-keepalive}"
WORKSPACE="${GROK_AUTH_WORKSPACE:-${HOME}/personal-workspace}"
export GROK_AUTH_EARLY_INVALIDATION_SECS="$EARLY"
export GROK_AUTH_JSON

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR" 2>/dev/null || true

log() {
  printf '%s\n' "$*"
}

read_expires() {
  python3 - "$1" << 'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


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


path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    print("")
    raise SystemExit(0)
best = None
best_at = None
if isinstance(data, dict):
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        if not (entry.get("key") or entry.get("access_token")):
            continue
        parsed = parse(entry.get("expires_at"))
        if best is None or (parsed is not None and (best_at is None or parsed > best_at)):
            best = entry
            best_at = parsed
if not best or not best.get("expires_at"):
    print("")
else:
    print(best.get("expires_at"))
PY
}

classify() {
  python3 - "$1" "$2" "$3" "$EARLY" "$MIN_REMAINING" << 'PY'
import sys
from datetime import datetime, timedelta, timezone


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


before_raw, after_raw, rc_raw, early_raw, min_raw = sys.argv[1:]
rc = int(rc_raw)
early = int(early_raw)
minimum = int(min_raw)
before = parse(before_raw)
after = parse(after_raw)
now = datetime.now(timezone.utc)
shown = after_raw or before_raw or ""

if rc == 124:
    print(f"timeout\t1\t{shown}")
    raise SystemExit(0)
if rc == 127:
    print(f"missing-bin\t1\t{shown}")
    raise SystemExit(0)
if rc != 0:
    print(f"exit-{rc}\t1\t{shown}")
    raise SystemExit(0)
if before is None and after is None:
    print("unreadable-auth\t1\t")
    raise SystemExit(0)

due = before is None or (before - now).total_seconds() < early
if not due:
    print(f"fresh\t0\t{after_raw or before_raw}")
    raise SystemExit(0)
if after is None or (before is not None and after <= before):
    print(f"expires-not-advanced\t1\t{shown}")
    raise SystemExit(0)
if after < now + timedelta(seconds=minimum):
    print(f"expires-short\t1\t{after_raw}")
    raise SystemExit(0)
print(f"renewed\t0\t{after_raw}")
PY
}

send_alert() {
  local reason="$1"
  local exp="$2"
  local prev="$3"
  local now until
  now="$(date -u +%s)"
  until=0
  if [[ -f "$STATE_DIR/alert-until" ]]; then
    until="$(tr -cd '0-9' <"$STATE_DIR/alert-until" || true)"
    until="${until:-0}"
  fi
  if (( now < until )); then
    log "outcome=alert-suppressed reason=${reason} expires_at=${exp} previous_expires_at=${prev} exit=${rc}"
    return 0
  fi
  printf '%s\n' "$((now + DEDUPE_SECS))" >"$STATE_DIR/alert-until"
  if [[ -n "${GROK_AUTH_ALERT_CMD:-}" ]]; then
    GROK_AUTH_ALERT_REASON="$reason" \
      GROK_AUTH_EXPIRES_AT="$exp" \
      GROK_AUTH_PREVIOUS_EXPIRES_AT="$prev" \
      GROK_AUTH_EXIT="$rc" \
      bash -c "$GROK_AUTH_ALERT_CMD" \
      || log "outcome=alert-failed reason=${reason} exit=${rc}"
  else
    GROK_AUTH_ALERT_REASON="$reason" \
      GROK_AUTH_EXPIRES_AT="$exp" \
      GROK_AUTH_PREVIOUS_EXPIRES_AT="$prev" \
      GROK_AUTH_EXIT="$rc" \
      GROK_AUTH_WORKSPACE="$WORKSPACE" \
      python3 - << 'PY' || log "outcome=alert-failed reason=${reason} exit=${rc}"
import os
import sys

workspace = os.environ.get("GROK_AUTH_WORKSPACE") or ""
if workspace:
    sys.path.insert(0, workspace)
reason = os.environ.get("GROK_AUTH_ALERT_REASON") or "failed"
exp = os.environ.get("GROK_AUTH_EXPIRES_AT") or ""
prev = os.environ.get("GROK_AUTH_PREVIOUS_EXPIRES_AT") or ""
code = os.environ.get("GROK_AUTH_EXIT") or ""
text = (
    f"reason={reason}\n"
    f"expires_at={exp}\n"
    f"previous_expires_at={prev}\n"
    f"exit={code}\n"
)
if "eyJ" in text or len(text) > 400:
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
  fi
  log "outcome=alerted reason=${reason} expires_at=${exp} previous_expires_at=${prev} exit=${rc}"
}

before="$(read_expires "$AUTH_JSON")"
rc=0
if [[ ! -x "$GROK_BIN" ]]; then
  rc=127
else
  set +e
  if [[ -n "${GROK_AUTH_TIMEOUT_BIN:-}" ]]; then
    "$GROK_AUTH_TIMEOUT_BIN" "$TIMEOUT_SECS" "$GROK_BIN" models </dev/null >/dev/null 2>&1
    rc=$?
  elif command -v timeout >/dev/null 2>&1; then
    timeout --signal=TERM "$TIMEOUT_SECS" "$GROK_BIN" models </dev/null >/dev/null 2>&1
    rc=$?
  else
    "$GROK_BIN" models </dev/null >/dev/null 2>&1
    rc=$?
  fi
  set -e
fi
after="$(read_expires "$AUTH_JSON")"
IFS=$'\t' read -r outcome alert_flag shown <<<"$(classify "$before" "$after" "$rc")"
line="outcome=${outcome} exit=${rc} expires_at=${shown} previous_expires_at=${before}"
if [[ "$alert_flag" == "1" ]]; then
  send_alert "$outcome" "$shown" "$before"
  log "$line"
  exit 1
fi
rm -f "$STATE_DIR/alert-until"
log "$line"
exit 0
