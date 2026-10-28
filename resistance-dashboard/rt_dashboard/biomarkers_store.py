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
    """{(marker, draw_date, panel_id)} already stored for this user.

    Read from the labs blob (plus legacy rows unioned in). ``conn`` is accepted
    for older callers and ignored — the biomarker tables are not the store.
    """
    del conn
    data = load_all(user_id)
    return {(r["marker"], r["draw_date"], r["panel_id"]) for r in data.get("rows") or []}


def insert_panel(panel: dict, rows: List[dict], *, user_id: str = "") -> dict:
    """Write one panel into the labs blob. Skips markers already stored.

    Returns {"inserted": n, "skipped": [marker,...], "panel_id": ...}.
    Does not write biomarker_results — that table is legacy.
    """
    from .lab_panels import rows_to_labs_panel
    from .labs_store import merge_panel

    pid = str(panel.get("panel_id") or "")
    labs_panel = rows_to_labs_panel(dict(panel, panel_id=pid), rows)
    res = merge_panel(labs_panel, user_id=_uid(user_id))
    if not res.get("panel_id"):
        res["panel_id"] = pid
    return res


def update_context(panel_id: str, context: dict, *, user_id: str = "") -> None:
    """Store per-draw context on the labs panel with this id."""
    from .labs_store import _read_store, _write_store

    uid = _uid(user_id)
    pid = str(panel_id or "")
    store = _read_store(uid)
    found = False
    for panel in store.get("panels") or []:
        if str(panel.get("panel_id") or "") == pid:
            ctx = dict(panel.get("context") or {})
            ctx.update(context or {})
            panel["context"] = ctx
            if ctx.get("draw_time"):
                panel["draw_time"] = ctx["draw_time"]
            if "fasted" in ctx:
                panel["fasting"] = bool(ctx.get("fasted"))
            found = True
            break
    if not found:
        return
    store["updated_at"] = _now()
    _write_store(uid, store)


def load_legacy_tables(user_id: str = "") -> Dict[str, Any]:
    """Read biomarker_panels / biomarker_results. Does not create a database.

    New imports do not write these tables. Migration and the read-union use
    this so a panel that only exists here is not invisible, and so a dry-run
    does not touch disk.
    """
    uid = _uid(user_id)
    empty = {"panels": [], "rows": [], "storage": "absent"}
    if backend() != "turso" and not db_path().is_file():
        return empty
    try:
        conn = _connect()
    except Exception:
        return empty
    try:
        try:
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
        except Exception:
            return empty
    finally:
        conn.close()
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
            try:
                d.update(_open_payload(uid, r["sealed"]))
            except Exception:
                continue
        rows.append(d)
    return {"panels": panels, "rows": rows, "storage": backend()}


def load_all(user_id: str = "") -> Dict[str, Any]:
    """Panels and rows from the labs blob, with legacy biomarker rows unioned in."""
    from .lab_panels import attach_legacy, project_biomarker
    from .labs_store import _read_store

    uid = _uid(user_id)
    try:
        store = attach_legacy(_read_store(uid), uid)
    except Exception:
        store = {"panels": []}
    return project_biomarker(store)


def delete_legacy_matches(
    user_id: str = "",
    *,
    panel_id: str = "",
    date: str = "",
    lab: str = "",
) -> int:
    """Drop legacy rows so a deleted panel does not reappear through the union."""
    uid = _uid(user_id)
    if backend() != "turso" and not db_path().is_file():
        return 0
    data = load_legacy_tables(uid)
    date_s = str(date or "")[:10]
    lab_s = str(lab or "").strip().lower()
    ids = []
    for p in data.get("panels") or []:
        pid = str(p.get("panel_id") or "")
        if panel_id and pid == panel_id:
            ids.append(pid)
            continue
        if date_s and str(p.get("draw_date") or "")[:10] == date_s:
            if not lab_s or str(p.get("lab") or "").strip().lower() == lab_s:
                ids.append(pid)
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return 0
    n = sum(1 for r in data.get("rows") or [] if r.get("panel_id") in ids)
    try:
        conn = _connect()
    except Exception:
        return 0
    try:
        stmts = []
        for pid in ids:
            stmts.append(("DELETE FROM biomarker_results WHERE user_id = ? AND panel_id = ?", (uid, pid)))
            stmts.append(("DELETE FROM biomarker_panels WHERE user_id = ? AND panel_id = ?", (uid, pid)))
        conn.execute_transaction(stmts)
    except Exception:
        return 0
    finally:
        conn.close()
    return n


def delete_panel(panel_id: str, *, user_id: str = "") -> int:
    """Remove one panel from the labs blob and from the legacy tables."""
    from .labs_store import _read_store, _write_store

    uid = _uid(user_id)
    pid = str(panel_id or "")
    store = _read_store(uid)
    kept = []
    removed_markers = 0
    for panel in store.get("panels") or []:
        if str(panel.get("panel_id") or "") == pid:
            raw = panel.get("markers") or {}
            removed_markers += len(raw) if isinstance(raw, (dict, list)) else 0
            continue
        kept.append(panel)
    if len(kept) != len(store.get("panels") or []):
        store["panels"] = kept
        store["updated_at"] = _now()
        _write_store(uid, store)
    legacy_n = delete_legacy_matches(uid, panel_id=pid)
    return removed_markers or legacy_n
