# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_config.py through bland_relay.py")

import base64
import contextlib
import fcntl
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import caller_context
import meeting_check
import post_call
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="[bland-relay] %(levelname)s %(message)s")
log = logging.getLogger("bland-relay")

HOST = os.environ.get("RELAY_HOST", "127.0.0.1")
PORT = int(os.environ.get("RELAY_PORT", "8799"))
MAX_BODY = 2 * 1024 * 1024
MAX_SMS_CHARS = 640
ALLOWLIST_S = 72 * 60 * 60
RECIPIENT_WINDOW_S = 10 * 60
RECIPIENT_MAX = 5
GLOBAL_WINDOW_S = 60 * 60
GLOBAL_MAX = 30
LINE_904 = "+19043343975"
SMSGATE_SKEW_S = 5 * 60
SMSGATE_SEEN_MAX = 512
RCS_PACKAGE = "com.google.android.apps.messaging"
RCS_DEDUPE_S = 120
RCS_WAIT_DEFAULT_S = 8.0
RCS_RECENT_MAX = 512
RCS_NAME_MAX = 64

# Whole inbound message, after trimming punctuation. A later normal text does
# not clear STOP; only these opt-in words do. Carrier-style keywords.
OPT_OUT_WORDS = frozenset({
    "STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT",
})
OPT_IN_WORDS = frozenset({"START", "UNSTOP", "YES"})

SECRET_ENV_KEYS = (
    "ALEXANDRA_ALERT_URL",
    "ALEXANDRA_ALERT_AUTH",
    "BLAND_WEBHOOK_SECRET",
    "RELAY_PATH",
    "FWD_TOKEN",
    "FWD_PATH",
    "SEND_TOKEN",
    "SEND_PATH",
    "SMSGW_URL",
    "SMSGW_USER",
    "SMSGW_PASS",
    "SMSGW_HOOK_PATH",
    "SMSGW_SIGNING_KEY",
    "RCS_HOOK_PATH",
    "RCS_HOOK_TOKEN",
    "FC_PATH",
    "FC_APPROVE_TOKEN_SHA256",
    "HELM_TURO_WEBHOOK_URL",
    "HELM_TURO_WEBHOOK_KEY",
    "TURO_EMAIL_TOKEN",
    "CALLER_CONTEXT_TOKEN",
    "CALLER_FORGET_TOKEN",
    "CRM_API_TOKEN",
    "MEETING_CHECK_TOKEN",
    "MEETING_GCAL_CLIENT_ID",
    "MEETING_GCAL_CLIENT_SECRET",
    "MEETING_GCAL_REFRESH_TOKEN",
    "MEETING_GCAL_CALENDAR_ID",
    "MEETING_SELF_EMAILS",
)

_alert_lock = threading.Lock()
_sms_lock = threading.Lock()
_seen_lock = threading.Lock()
_seen_events = {}  # SMSGate event id -> epoch, successful forwards only (retry dedupe)
_recent_lock = threading.Lock()
# Cross-path dedupe (#1056): {"at", "src" ("sms"|"rcs"), "num", "name", "tkey"}.
# Entries are claimed before forwarding and dropped again if the forward fails.
_recent_msgs = []
ALERT_COOLDOWN_S = 15 * 60
SEND_STATE_EVENTS = {"sms:sent": "Sent", "sms:delivered": "Delivered", "sms:failed": "Failed"}
_DEFAULT_FAILURE_NOTICE_RE = re.compile(r"not\s+sent", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{8,}\d")
TREASURY_DIR = Path.home() / "personal-workspace"


def env(k):
    return (os.environ.get(k) or "").strip()


def _secret_path(name):
    p = env(name)
    return ("/" + p.lstrip("/")) if p else ""


def relay_path():
    return _secret_path("RELAY_PATH")


def fwd_path():
    return _secret_path("FWD_PATH")


def send_path():
    return _secret_path("SEND_PATH")


def smsgate_path():
    return _secret_path("SMSGW_HOOK_PATH")


def rcs_path():
    return _secret_path("RCS_HOOK_PATH")


def _state_dir():
    override = env("BLAND_RELAY_STATE_DIR")
    if override:
        return Path(override)
    return Path.home() / ".local" / "state" / "bland-relay"


def _alert_state_path():
    return _state_dir() / "alert_state.json"


def _sms_state_path():
    return _state_dir() / "sms_send_state.json"


def _save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _load_json(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


