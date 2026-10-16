# Loaded by bland_relay.py into the bland_relay module namespace (#1107).
# Do not import this file directly.
if __name__ not in ("bland_relay", "__main__"):
    raise RuntimeError("load relay_audit.py through bland_relay.py")

def _post_github(title, text):
    """Same sink as treasury alerts since #704: treasury.pi_ops_alert.post_ops_github
    (issue #701, token from ~/.config/workflow-scheduler.env). Falls back to an
    equivalent inline post if the module can't be imported (e.g. mid workspace-sync)."""
    try:
        if str(TREASURY_DIR) not in sys.path:
            sys.path.insert(0, str(TREASURY_DIR))
        from treasury.pi_ops_alert import post_ops_github, kill_switch  # type: ignore
        if kill_switch():
            return {"ok": True, "posted": False, "skipped": "FCC_ALERT_KILL_SWITCH"}
        return post_ops_github(title, text, as_markdown=True)
    except ImportError as e:
        log.warning("pi_ops_alert import failed (%s); using inline fallback", type(e).__name__)
    envf = Path.home() / ".config" / "workflow-scheduler.env"
    tok = ""
    try:
        for line in envf.read_text().splitlines():
            k, _, v = line.strip().removeprefix("export ").partition("=")
            if k.strip() in ("GITHUB_TOKEN", "GH_TOKEN") and v.strip():
                tok = v.strip().strip("'\"")
                break
    except OSError:
        pass
    if not tok:
        return {"ok": True, "posted": False, "skipped": "no-github-token"}
    issue = (os.environ.get("PI_OPS_ALERT_ISSUE") or "701").strip()
    req = urllib.request.Request(
        f"https://api.github.com/repos/cvolkernick/personal-workspace/issues/{issue}/comments",
        data=json.dumps({"body": f"**{title}**\n\n{text.rstrip()}\n"}).encode(),
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "personal-workspace-pi-alerts",
                 "X-GitHub-Api-Version": "2022-11-28"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return {"ok": True, "posted": True, "status": r.status, "issue": issue}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "posted": False, "error": type(e).__name__, "issue": issue}


def _scrub(msg):
    for k in SECRET_ENV_KEYS:
        v = env(k)
        if v:
            msg = msg.replace(v, "<redacted>")
    return msg[:300]


@contextlib.contextmanager
def _alert_file_lock():
    """Thread lock plus a flock, so the relay and gmail-turo-watch (#1142), which
    share alert_state.json, never interleave read-modify-write or the .tmp file."""
    with _alert_lock:
        fd = None
        try:
            path = _alert_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(path.with_suffix(".lock")), os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError as e:
            log.warning("alert state lock unavailable: %s", type(e).__name__)
        try:
            yield
        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


def alert_failure(call_id, err, attempts, title=None, id_label=None, attempts_label=None, source=None):
    """Journal ALERT always; GitHub #701 comment at most once per 15 min.
    id_label/attempts_label/source let another service (gmail-turo-watch) word its own alert."""
    err = _scrub(err)
    now = time.time()
    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(now))
    log.error("ALERT forward failed: call_id=%s error=%s attempts=%d", call_id or "-", err, attempts)
    with _alert_file_lock():
        st = _load_json(_alert_state_path())
        last = float(st.get("last_posted_epoch") or 0)
        if now - last < ALERT_COOLDOWN_S:
            st["suppressed"] = int(st.get("suppressed") or 0) + 1
            log.warning("GitHub alert suppressed (cooldown; %d suppressed since last comment)", st["suppressed"])
            _save_state(st)
            return
        suppressed = int(st.get("suppressed") or 0)
        id_label = id_label or ("Send id" if str(call_id).startswith("sms-") else "Bland call_id")
        text = (
            f"- Time: {ts}\n"
            f"- {id_label}: `{call_id or 'n/a'}`\n"
            f"- Error: {err}\n"
            f"- {attempts_label or 'Forward attempts'}: {attempts}\n"
            f"- Failures suppressed since previous relay comment (15 min dedupe): {suppressed}\n"
            f"- Source: {source or 'prism `bland-relay.service` (`journalctl --user-unit bland-relay.service`)'}"
        )
        res = _post_github(title or "Bland relay: Alexandra alert forward failed", text)
        if res.get("posted"):
            log.info("GitHub alert posted to #%s (status %s)", res.get("issue"), res.get("status"))
            _save_state({"last_posted_epoch": now, "last_posted_at": ts, "suppressed": 0})
        else:
            log.error("GitHub alert NOT posted: %s", res.get("skipped") or res.get("error"))


def _save_state(st):
    try:
        _save_json(_alert_state_path(), st)
    except OSError as e:
        log.error("alert state write failed: %s", type(e).__name__)


RATE_LIMIT_BACKOFF_S = (15, 30, 60, 120)  # extra tries after a rate-limit hit (~3.75 min total)
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9._~+/=-]+")


def _scrub_body(text, n=200):
    """Collapse whitespace, strip bearer tokens / known secrets, cap length."""
    text = " ".join(str(text or "").split())
    text = _BEARER_RE.sub("<redacted>", text)
    return _scrub(text)[:n]


def _is_rate_limited(status, body_text):
    return status == 429 or "rate limit" in (body_text or "").lower()


def _call_id_of(body):
    try:
        cid = str(json.loads(body).get("call_id") or "")
    except Exception:  # noqa: BLE001
        cid = ""
    return re.sub(r"[^A-Za-z0-9_.:-]", "", cid)[:64] or "-"


def _post_once(url, headers, body, timeout):
    """One POST. Returns (status_or_None, err_text, http_code, body_text)."""
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if 200 <= r.status < 300:
                return r.status, "", r.status, ""
            return None, f"HTTP {r.status}", r.status, ""
    except urllib.error.HTTPError as e:
        try:
            rb = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            rb = ""
        err = f"HTTP {e.code}" + (" body=" + " ".join(rb.split()) if rb else "")
        return None, err, e.code, rb
    except Exception as e:  # noqa: BLE001
        return None, type(e).__name__ + ": " + str(e)[:200], 0, ""


def _log_rate_limit(call_id, attempt, code, body_text):
    log.warning("RATE_LIMIT call_id=%s attempt=%d status=%s body=%s",
                call_id, attempt, code or "-", _scrub_body(body_text))


def _rate_limit_retry(url, headers, body, timeout, call_id, attempts_so_far, sleep=None):
    """Background retries after a rate-limit hit. Returns True on success."""
    sleep = sleep or time.sleep
    attempt, last = attempts_so_far, ""
    for wait in RATE_LIMIT_BACKOFF_S:
        sleep(wait)
        attempt += 1
        status, err, code, rb = _post_once(url, headers, body, timeout)
        if status is not None:
            log.info("RATE_LIMIT_FINAL call_id=%s result=success attempts=%d status=%d",
                     call_id, attempt, status)
            return True
        last = err
        if _is_rate_limited(code, rb):
            _log_rate_limit(call_id, attempt, code, rb)
        else:
            log.warning("forward retry attempt %d failed: %s", attempt, _scrub_body(err, 300))
    log.error("RATE_LIMIT_FINAL call_id=%s result=failed attempts=%d last=%s",
              call_id, attempt, _scrub_body(last))
    alert_failure(call_id, "rate-limit retries exhausted: " + last, attempt)
    return False


def forward(body, timeout=10, call_id=None):
    """POST to Alexandra. Non-rate-limit errors: 2 inline attempts (unchanged).
    Rate-limit (HTTP 429 or body mentioning 'rate limit'): hand off to a background
    thread with 15/30/60/120s backoff and return (202, "", n) so callers ack fast."""
    url, auth = env("ALEXANDRA_ALERT_URL"), env("ALEXANDRA_ALERT_AUTH")
    if not url:
        return None, "ALEXANDRA_ALERT_URL unset", 0
    headers = {"Content-Type": "application/json", "User-Agent": "bland-relay/1"}
    if auth:
        headers["Authorization"] = auth
    last = ""
    for attempt in (1, 2):
        status, last, code, rb = _post_once(url, headers, body, timeout)
        if status is not None:
            return status, "", attempt
        if _is_rate_limited(code, rb):
            cid = call_id or _call_id_of(body)
            _log_rate_limit(cid, attempt, code, rb)
            _spawn(_rate_limit_retry, (url, headers, body, timeout, cid, attempt))
            return 202, "", attempt
        log.warning("forward attempt %d failed: %s (req %d bytes)", attempt, _scrub_body(last, 300), len(body or b""))
        if attempt == 1:
            time.sleep(1)
    return None, last, 2


def token_from_headers(headers):
    got = (headers.get("X-Relay-Token") or "").strip()
    if not got:
        auth = (headers.get("Authorization") or "").strip()
        got = auth[7:].strip() if auth.lower().startswith("bearer ") else auth
    return got


def path_is(path, secret):
    if not secret:
        return False
    try:
        a = path.encode()
        b = secret.encode()
    except Exception:  # noqa: BLE001
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def token_ok(got, expected):
    if not got or not expected:
        return False
    try:
        a = got.encode()
        b = expected.encode()
    except Exception:  # noqa: BLE001
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


