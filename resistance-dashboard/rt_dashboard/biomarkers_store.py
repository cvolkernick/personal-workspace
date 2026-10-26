"""Row-per-marker biomarker store (blood panels over time).

Backends (same pattern as labs_store):
- Turso/libSQL when ``TURSO_*`` is set (Vercel prod and the Pi service).
  Values + ranges + context are sealed with crypto_box; only the routing
  columns (user, panel, marker id, draw date, unit, source) stay plaintext.
- Local SQLite file otherwise (``FITDASH_BIOMARKERS_DB`` or
  ``~/.config/resistance-dashboard/biomarkers.sqlite3``), plaintext, 0600.

``FITDASH_BIOMARKERS_BACKEND=sqlite`` forces the local file even when
Turso env is present — use it for dev runs, previews and tests so nothing
touches prod.

Migration is additive only: two ``CREATE TABLE IF NOT EXISTS`` plus a
unique index. Nothing existing (labs_panels etc.) is altered.
Duplicate protection: UNIQUE(user_id, marker, draw_date, panel_id).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

MIGRATION_SQL: Tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS biomarker_panels (
  user_id TEXT NOT NULL,
  panel_id TEXT NOT NULL,
  lab TEXT,
  draw_date TEXT NOT NULL,
  source TEXT,
  context_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, panel_id)
)""",
    """CREATE TABLE IF NOT EXISTS biomarker_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL,
  panel_id TEXT NOT NULL,
  marker TEXT NOT NULL,
  marker_name TEXT,
  value REAL,
  value_text TEXT,
  comparator TEXT,
  unit TEXT,
  ref_low REAL,
  ref_high REAL,
  ref_text TEXT,
  draw_date TEXT NOT NULL,
  source TEXT,
  sealed TEXT,
  created_at TEXT NOT NULL
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS biomarker_results_dedupe
  ON biomarker_results (user_id, marker, draw_date, panel_id)""",
)

_SEALED_FIELDS = ("value", "value_text", "comparator", "ref_low", "ref_high", "ref_text", "marker_name")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _uid(user_id: str) -> str:
    return (user_id or "").strip() or "local"


def backend() -> str:
    forced = (os.environ.get("FITDASH_BIOMARKERS_BACKEND") or "").strip().lower()
    if forced in ("sqlite", "local"):
        return "sqlite"
    from .turso_http import turso_enabled

    return "turso" if turso_enabled() else "sqlite"


def db_path() -> Path:
    override = (os.environ.get("FITDASH_BIOMARKERS_DB") or "").strip()
    if override:
        return Path(override).expanduser()
    cfg = (os.environ.get("RESISTANCE_DASHBOARD_CONFIG_DIR") or "").strip()
    base = Path(cfg).expanduser() if cfg else Path.home() / ".config" / "resistance-dashboard"
    return base / "biomarkers.sqlite3"


class _Sqlite:
    """sqlite3 wrapper with the TursoConnection surface we use."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._c = sqlite3.connect(str(path))
        self._c.row_factory = sqlite3.Row
        if new:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass

    def execute(self, sql: str, params: Sequence[Any] = ()):
        cur = self._c.execute(sql, tuple(params))
        self._c.commit()
        return cur

    def execute_transaction(self, statements: Sequence[Tuple[str, Sequence[Any]]]) -> None:
        with self._c:
            for sql, params in statements:
                self._c.execute(sql, tuple(params))

    def close(self) -> None:
        self._c.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def _connect():
    if backend() == "turso":
        from .turso_http import connect

        return connect()
    return _Sqlite(db_path())


def migrate(conn=None) -> None:
    """Additive-only schema. Safe to run repeatedly."""
    own = conn is None
    conn = conn or _connect()
    try:
        for stmt in MIGRATION_SQL:
            conn.execute(stmt)
    finally:
        if own:
            conn.close()


def _seal_payload(uid: str, row: dict) -> str:
    from .crypto_box import seal_str

    data = {k: row.get(k) for k in _SEALED_FIELDS}
    return seal_str(json.dumps(data, separators=(",", ":")), aad=f"user:{uid}:biomarkers")


def _open_payload(uid: str, blob: str) -> dict:
    from .crypto_box import open_str

    return json.loads(open_str(blob, aad=f"user:{uid}:biomarkers"))


def _seal_ctx(uid: str, ctx: dict) -> str:
    from .crypto_box import seal_str

    return seal_str(json.dumps(ctx or {}, separators=(",", ":")), aad=f"user:{uid}:biomarkers:ctx")


def _open_ctx(uid: str, blob: str) -> dict:
    if not blob:
        return {}
    if blob.lstrip().startswith("{"):
        return json.loads(blob)
    from .crypto_box import open_str

    return json.loads(open_str(blob, aad=f"user:{uid}:biomarkers:ctx"))


def existing_keys(user_id: str = "", conn=None) -> set:
    """{(marker, draw_date, panel_id)} already stored for this user."""
    uid = _uid(user_id)
    own = conn is None
    conn = conn or _connect()
    try:
        migrate(conn)
        rows = conn.execute(
            "SELECT marker, draw_date, panel_id FROM biomarker_results WHERE user_id = ?",
            (uid,),
        ).fetchall()
        return {(r["marker"], r["draw_date"], r["panel_id"]) for r in rows}
    finally:
        if own:
            conn.close()


def insert_panel(panel: dict, rows: List[dict], *, user_id: str = "") -> dict:
    """Insert one panel + its rows atomically, skipping duplicates.

    Returns {"inserted": n, "skipped": [marker,...], "panel_id": ...}.
    """
    uid = _uid(user_id)
    seal = backend() == "turso"
    now = _now()
    pid = str(panel["panel_id"])
    with _connect() as conn:
        migrate(conn)
        have = existing_keys(uid, conn)
        stmts: List[Tuple[str, Sequence[Any]]] = []
        ctx = panel.get("context") or {}
        stmts.append((
            """INSERT INTO biomarker_panels(user_id, panel_id, lab, draw_date, source, context_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, panel_id) DO UPDATE SET
                 context_json = CASE WHEN excluded.context_json IS NULL OR excluded.context_json = ''
                                     THEN biomarker_panels.context_json ELSE excluded.context_json END""",
            (uid, pid, panel.get("lab") or "", panel["draw_date"], panel.get("source") or "",
             (_seal_ctx(uid, ctx) if seal else json.dumps(ctx)) if ctx else "", now),
        ))
        inserted, skipped = 0, []
        for r in rows:
            key = (r["marker"], r["draw_date"], pid)
            if key in have:
                skipped.append(r["marker"])
                continue
            have.add(key)
            if seal:
                vals = (None, None, None, None, None, None, None, _seal_payload(uid, r))
            else:
                vals = (r.get("marker_name"), r.get("value"), r.get("value_text"), r.get("comparator") or "",
                        r.get("ref_low"), r.get("ref_high"), r.get("ref_text"), None)
            stmts.append((
                """INSERT INTO biomarker_results(user_id, panel_id, marker, marker_name, value, value_text,
                     comparator, ref_low, ref_high, ref_text, sealed, unit, draw_date, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (uid, pid, r["marker"], *vals, r.get("unit") or "", r["draw_date"],
                 r.get("source") or panel.get("source") or "", now),
            ))
            inserted += 1
        conn.execute_transaction(stmts)
    return {"inserted": inserted, "skipped": skipped, "panel_id": pid}


def update_context(panel_id: str, context: dict, *, user_id: str = "") -> None:
    uid = _uid(user_id)
    blob = _seal_ctx(uid, context) if backend() == "turso" else json.dumps(context or {})
    with _connect() as conn:
        migrate(conn)
        conn.execute(
            "UPDATE biomarker_panels SET context_json = ? WHERE user_id = ? AND panel_id = ?",
            (blob, uid, str(panel_id)),
        )


def load_all(user_id: str = "") -> Dict[str, Any]:
    """{"panels": [...], "rows": [...]} decrypted, sorted by draw_date."""
    uid = _uid(user_id)
    with _connect() as conn:
        migrate(conn)
        prow = conn.execute(
            "SELECT panel_id, lab, draw_date, source, context_json, created_at "
            "FROM biomarker_panels WHERE user_id = ? ORDER BY draw_date, panel_id",
            (uid,),
        ).fetchall()
        rrow = conn.execute(
            "SELECT panel_id, marker, marker_name, value, value_text, comparator, unit, ref_low, ref_high, "
            "ref_text, draw_date, source, sealed, created_at FROM biomarker_results "
            "WHERE user_id = ? ORDER BY draw_date, id",
            (uid,),
        ).fetchall()
    panels = []
    for p in prow:
        panels.append({
            "panel_id": p["panel_id"], "lab": p["lab"], "draw_date": p["draw_date"],
            "source": p["source"], "context": _open_ctx(uid, p["context_json"] or ""),
            "created_at": p["created_at"],
        })
    rows = []
    for r in rrow:
        d = {k: r[k] for k in ("panel_id", "marker", "marker_name", "value", "value_text", "comparator",
                               "unit", "ref_low", "ref_high", "ref_text", "draw_date", "source", "created_at")}
        if r["sealed"]:
            d.update(_open_payload(uid, r["sealed"]))
        rows.append(d)
    return {"panels": panels, "rows": rows, "storage": backend()}


def delete_panel(panel_id: str, *, user_id: str = "") -> int:
    uid = _uid(user_id)
    with _connect() as conn:
        migrate(conn)
        n = len(conn.execute(
            "SELECT id FROM biomarker_results WHERE user_id = ? AND panel_id = ?", (uid, panel_id)
        ).fetchall())
        conn.execute_transaction([
            ("DELETE FROM biomarker_results WHERE user_id = ? AND panel_id = ?", (uid, panel_id)),
            ("DELETE FROM biomarker_panels WHERE user_id = ? AND panel_id = ?", (uid, panel_id)),
        ])
    return n
