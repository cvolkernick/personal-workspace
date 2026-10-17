#!/usr/bin/env python3
"""Read-only public smoke for bland-relay (#1229).

Stdlib only. GET, and POST with an empty body. Never DELETE.
One JSON report goes to stdout: status codes, pass/fail, and
p50/p95/max milliseconds. The report has no token, no request body,
and no response body.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# The relay window is 60s (CALLER_CONTEXT_RPM). Wait a little longer so
# the samples from this run have fallen out before the 429 burst.
WINDOW_S = 62.0
SHAPE_KEYS = (
    "caller_context",
    "last_contact_at",
    "open_followups",
    "caller_type",
    "backend",
)
EMPTY_PROSE = "No previous contact on file."
UNKNOWN_PN = "+10000000000"
TOKEN_ENV = "CALLER_CONTEXT_TOKEN"
BASE_ENV = "RELAY_PUBLIC_BASE"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _opener():
    # Ignore env proxies so the token is not sent off-host.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def _ms(samples, fraction):
    if not samples:
        return None
    ordered = sorted(samples)
    return round(ordered[int(fraction * (len(ordered) - 1))], 1)


def _read_doc(res):
    try:
        raw = res.read(65536)
    except Exception:  # noqa: BLE001 — a bad body is a failed case, not a crash
        return None, "bad_json"
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None, "bad_json"
    if not isinstance(parsed, dict):
        return None, "bad_json"
    return parsed, "json"


class Client:
    def __init__(self, sleep=None, now=None, opener=None):
        self.sleep = time.sleep if sleep is None else sleep
        self.now = time.time if now is None else now
        self.opener = opener if opener is not None else _opener()
        self.stamps = []

    def exchange(self, method, url, headers, timeout=5.0):
        method = str(method or "").upper()
        if method not in ("GET", "POST"):
            raise ValueError("read-only")
        # POST carries no payload. DELETE is not offered: it forgets a caller.
        data = b"" if method == "POST" else None
        req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
        self.stamps.append(self.now())
        started = time.perf_counter()
        try:
            with self.opener.open(req, timeout=timeout) as res:
                status = getattr(res, "status", 200)
                doc, kind = _read_doc(res)
        except urllib.error.HTTPError as exc:
            status = exc.code
            doc, kind = _read_doc(exc)
        except Exception:  # noqa: BLE001 — timeout or connect; do not echo the URL
            status, doc, kind = 0, None, "error"
        elapsed = (time.perf_counter() - started) * 1000.0
        return status, doc, kind, elapsed

    def wait_for_fresh_window(self):
        if not self.stamps:
            return 0.0
        delay = WINDOW_S - (self.now() - max(self.stamps))
        if delay > 0:
            self.sleep(delay)
        self.stamps.clear()
        return delay if delay > 0 else 0.0


def _url(base, pn):
    return base.rstrip("/") + "/bland/caller-context?" + urllib.parse.urlencode({"pn": pn})


def _auth(token):
    return {"Accept": "application/json", "Authorization": "Bearer " + token}


def _plain():
    return {"Accept": "application/json"}


def _shape(doc):
    return isinstance(doc, dict) and all(key in doc for key in SHAPE_KEYS)


def _empty(doc):
    return (
        _shape(doc)
        and doc.get("known") is False
        and doc.get("caller_context") == EMPTY_PROSE
        and doc.get("last_contact_at") == ""
        and doc.get("open_followups") == []
        and doc.get("caller_type") == "unknown"
    )


def _case(name, status, passed, **extra):
    row = {"name": name, "status": status, "pass": bool(passed)}
    for key, value in extra.items():
        row[key] = value
    return row


def _status_case(client, name, url, headers, accept):
    status, doc, _kind, _ms_one = client.exchange("GET", url, headers)
    return _case(name, status, status == accept), doc, status


def _wrong(token):
    return "wrong-token" if token != "wrong-token" else "still-wrong"


def check_rate_limit(client, base, token):
    url = _url(base, UNKNOWN_PN)
    headers = _auth(token)
    statuses = []
    samples = []
    for _ in range(61):
        status, _doc, _kind, elapsed = client.exchange("GET", url, headers)
        statuses.append(status)
        samples.append(elapsed)
    passed = (
        len(statuses) == 61
        and statuses[60] == 429
        and all(code == 200 for code in statuses[:60])
    )
    return _case(
        "rate_limit",
        statuses[60] if statuses else 0,
        passed,
        sent=61,
        p50_ms=_ms(samples, 0.50),
        p95_ms=_ms(samples, 0.95),
        max_ms=_ms(samples, 1.0),
    )


def run_caller_context(base, token, n=50, limit_ms=1000, include_rate_limit=True, client=None):
    client = client if client is not None else Client()
    cases = []
    no_token, _doc, _status = _status_case(client, "no_token", _url(base, UNKNOWN_PN), _plain(), 401)
    cases.append(no_token)
    wrong, _doc, _status = _status_case(
        client, "wrong_token", _url(base, UNKNOWN_PN), _auth(_wrong(token)), 401
    )
    cases.append(wrong)

    status, doc, _kind, _elapsed = client.exchange("GET", _url(base, UNKNOWN_PN), _auth(token))
    known = doc.get("known") if isinstance(doc, dict) else None
    cases.append(_case(
        "unknown_e164", status,
        status == 200 and known is False and _shape(doc),
        known=known,
        shape=_shape(doc),
    ))

    for name, pn in (("garbage_pn", "garbage"), ("empty_pn", "")):
        status, doc, _kind, _elapsed = client.exchange("GET", _url(base, pn), _auth(token))
        cases.append(_case(name, status, status == 200 and _empty(doc), empty_shape=_empty(doc)))

    samples = []
    ok = 0
    first_bad = None
    count = int(n)
    for _ in range(count):
        status, doc, _kind, elapsed = client.exchange("GET", _url(base, UNKNOWN_PN), _auth(token))
        samples.append(elapsed)
        good = status == 200 and isinstance(doc, dict) and doc.get("known") is False and _shape(doc)
        if good:
            ok += 1
        elif first_bad is None:
            first_bad = status
    p95 = _ms(samples, 0.95)
    latency_pass = ok == count and p95 is not None and p95 < limit_ms
    cases.append(_case(
        "latency",
        200 if first_bad is None else first_bad,
        latency_pass,
        n=count,
        ok=ok,
        p50_ms=_ms(samples, 0.50),
        p95_ms=p95,
        max_ms=_ms(samples, 1.0),
        limit_ms=limit_ms,
    ))

    if include_rate_limit:
        client.wait_for_fresh_window()
        cases.append(check_rate_limit(client, base, token))

    return {
        "check": "caller-context",
        "pass": all(row["pass"] for row in cases),
        "cases": cases,
    }


def _base_ok(base):
    try:
        parts = urllib.parse.urlsplit(base)
        port = parts.port
    except ValueError:
        return False
    if parts.username or parts.password or parts.query or parts.fragment:
        return False
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    return True


def _emit(report, token):
    text = json.dumps(report, sort_keys=True, separators=(",", ":"))
    if token and len(token) >= 8 and token in text:
        text = '{"error":"report_withheld","pass":false}'
        sys.stdout.write(text + "\n")
        return 2
    sys.stdout.write(text + "\n")
    if report.get("error"):
        return 2
    return 0 if report.get("pass") else 1


def _token(name):
    return (os.environ.get(name) or "").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only bland-relay smoke (#1229)")
    parser.add_argument("--base", default="")
    parser.add_argument("--check", action="append", required=True)
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--limit-ms", type=int, default=1000)
    parser.add_argument("--token-env", default=TOKEN_ENV)
    args = parser.parse_args(argv)
    token = _token(args.token_env)
    base = (args.base or "").strip() or (os.environ.get(BASE_ENV) or "").strip()
    checks = list(args.check or [])
    if checks != ["caller-context"]:
        return _emit({"error": "unknown_check", "pass": False}, token)
    if not token:
        return _emit({"error": "token_unset", "pass": False}, "")
    if not _base_ok(base):
        return _emit({"error": "base_rejected", "pass": False}, token)
    if args.n < 1 or args.n > 200 or args.limit_ms < 1:
        return _emit({"error": "bad_args", "pass": False}, token)
    report = run_caller_context(base, token, n=args.n, limit_ms=args.limit_ms, include_rate_limit=True)
    return _emit(report, token)


if __name__ == "__main__":
    sys.exit(main())
