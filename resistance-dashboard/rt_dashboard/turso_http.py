"""Stdlib HTTP client for Turso/libSQL (Vercel: no local replica)."""

from __future__ import annotations

import http.client
import json
import os
import ssl
import threading
import time
from contextlib import contextmanager
from urllib.parse import urlsplit
import urllib.error
import urllib.request
from typing import Any, Iterable, List, Optional, Sequence


def turso_url() -> str:
    return (os.environ.get("TURSO_DATABASE_URL") or "").strip()


def turso_token() -> str:
    return (os.environ.get("TURSO_AUTH_TOKEN") or "").strip()


def turso_enabled() -> bool:
    return bool(turso_url() and turso_token())


def http_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if u.startswith("libsql://"):
        u = "https://" + u[len("libsql://") :]
    elif u.startswith("turso://"):
        u = "https://" + u[len("turso://") :]
    return u


def _arg(value: Any) -> dict:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    if isinstance(value, (bytes, bytearray)):
        import base64

        return {"type": "blob", "base64": base64.b64encode(bytes(value)).decode("ascii")}
    return {"type": "text", "value": str(value)}


def _cell(cell: Any) -> Any:
    if cell is None or not isinstance(cell, dict):
        return cell
    typ = cell.get("type")
    if typ == "null":
        return None
    if typ == "integer":
        try:
            return int(cell.get("value"))
        except (TypeError, ValueError):
            return cell.get("value")
    if typ == "float":
        try:
            return float(cell.get("value"))
        except (TypeError, ValueError):
            return cell.get("value")
    if typ == "blob":
        import base64

        raw = cell.get("base64") or cell.get("value") or ""
        try:
            return base64.b64decode(raw)
        except Exception:
            return raw
    return cell.get("value")


class TursoRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return dict.__getitem__(self, key)


class TursoCursor:
    def __init__(self, cols: Sequence[str], rows: List[TursoRow]):
        self.description = [(c, None, None, None, None, None, None) for c in cols]
        self._rows = rows
        self._i = 0
        self.rowcount = len(rows)

    def fetchall(self) -> List[TursoRow]:
        return list(self._rows)

    def fetchone(self) -> Optional[TursoRow]:
        if self._i >= len(self._rows):
            return None
        row = self._rows[self._i]
        self._i += 1
        return row

    def close(self) -> None:
        return None


class TursoConnection:
    """Minimal sqlite3-shaped wrapper over POST /v2/pipeline."""

    def __init__(self, url: str, token: str, timeout: float = 20.0):
        self._url = http_base_url(url)
        self._token = token
        self._timeout = timeout
        self.row_factory = TursoRow

    def _pipeline(self, sql: str, params: Sequence[Any] = ()) -> TursoCursor:
        stmt: dict[str, Any] = {"sql": sql}
        if params:
            stmt["args"] = [_arg(p) for p in params]
        body = json.dumps(
            {"requests": [{"type": "execute", "stmt": stmt}, {"type": "close"}]}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self._url}/v2/pipeline",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        if _keepalive_active():
            payload = _keepalive_post(
                f"{self._url}/v2/pipeline", body, dict(req.header_items()),
                timeout=self._timeout, sql=sql,
            )
        else:
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                e.read()
                raise RuntimeError(f"turso HTTP {e.code}") from e
        results = payload.get("results") or []
        first = results[0] if results else {}
        if first.get("type") == "error":
            msg = (first.get("error") or {}).get("message") or first
            raise RuntimeError(f"turso execute: {msg}")
        result = ((first.get("response") or {}).get("result")) or {}
        cols = [c.get("name") for c in (result.get("cols") or [])]
        rows = []
        for raw in result.get("rows") or []:
            values = [_cell(c) for c in raw]
            rows.append(TursoRow(zip(cols, values)))
        return TursoCursor(cols, rows)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> TursoCursor:
        return self._pipeline(sql, params)

    def executescript(self, script: str) -> TursoCursor:
        last = TursoCursor([], [])
        for part in _split_sql(script):
            last = self._pipeline(part, ())
        return last

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None

    def __enter__(self) -> "TursoConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _split_sql(script: str) -> Iterable[str]:
    buf: list[str] = []
    for line in (script or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        if stripped.endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                yield stmt
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        yield tail


def connect() -> TursoConnection:
    if not turso_enabled():
        raise RuntimeError("TURSO_DATABASE_URL / TURSO_AUTH_TOKEN missing")
    return TursoConnection(turso_url(), turso_token())


# --- Keep-alive (speed fix #2) ---------------------------------------------
# A dashboard build issues ~37 small Turso statements; each urlopen paid a
# fresh TCP+TLS handshake (~70 ms on the Pi). Inside ``keepalive_scope`` the
# calling thread reuses one HTTPS connection. Outside a scope (and on other
# threads) behavior is unchanged.

_TLS = threading.local()
_IDLE_MAX_SEC = 20.0
_READ_PREFIXES = ("select", "pragma", "with", "explain")


def _keepalive_active() -> bool:
    return int(getattr(_TLS, "depth", 0) or 0) > 0


@contextmanager
def keepalive_scope():
    _TLS.depth = int(getattr(_TLS, "depth", 0) or 0) + 1
    try:
        yield
    finally:
        _TLS.depth -= 1
        if _TLS.depth <= 0:
            _drop_conn()


def _drop_conn() -> None:
    conn = getattr(_TLS, "conn", None)
    _TLS.conn = None
    _TLS.conn_key = None
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _get_conn(scheme: str, netloc: str, timeout: float):
    key = (scheme, netloc)
    conn = getattr(_TLS, "conn", None)
    idle = time.time() - float(getattr(_TLS, "last_used", 0) or 0)
    if conn is not None and (getattr(_TLS, "conn_key", None) != key or idle > _IDLE_MAX_SEC):
        _drop_conn()
        conn = None
    if conn is None:
        if scheme == "https":
            conn = http.client.HTTPSConnection(
                netloc, timeout=timeout, context=ssl.create_default_context()
            )
        else:
            conn = http.client.HTTPConnection(netloc, timeout=timeout)
        _TLS.conn = conn
        _TLS.conn_key = key
        return conn, False
    return conn, True


def _keepalive_post(url: str, body: bytes, headers: dict, *, timeout: float, sql: str) -> dict:
    parts = urlsplit(url)
    path = parts.path + (("?" + parts.query) if parts.query else "")
    is_read = (sql or "").lstrip().lower().startswith(_READ_PREFIXES)
    for attempt in (0, 1):
        conn, reused = _get_conn(parts.scheme, parts.netloc, timeout)
        sent = False
        try:
            conn.request("POST", path, body=body, headers=headers)
            sent = True
            resp = conn.getresponse()
            raw = resp.read()
        except (http.client.RemoteDisconnected, ConnectionError, http.client.BadStatusLine, OSError) as e:
            _drop_conn()
            # Retry only a stale reused socket, and only when the statement
            # cannot have run twice: not yet sent, or a read.
            if attempt == 0 and reused and (not sent or is_read):
                continue
            raise RuntimeError(f"turso connection: {type(e).__name__}") from e
        _TLS.last_used = time.time()
        if resp.getheader("Connection", "").lower() == "close":
            _drop_conn()
        if resp.status >= 400:
            raise RuntimeError(f"turso HTTP {resp.status}")
        return json.loads(raw.decode("utf-8"))
    raise RuntimeError("turso connection: retry exhausted")
