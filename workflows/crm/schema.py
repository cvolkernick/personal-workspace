"""SQLite schema. One file, WAL, mode 600. Only the service user can open it."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workflows.crm import SCHEMA_VERSION

# Statuses the SMS and call batches are allowed to read. needs-info and
# terminal states stay out of the view. Landline is not excluded here:
# voice is still allowed; the SMS selector filters that tag itself.
_FUNNEL = ("new", "sms_sent")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS credential (
  id TEXT PRIMARY KEY,
  seat TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  scopes TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS party (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL CHECK (type IN ('lead','vendor','supplier','customer','partner','other')),
  display_name TEXT NOT NULL DEFAULT '',
  org_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT '',
  owner_seat TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  ext_json TEXT NOT NULL DEFAULT '{}',
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS party_tag (
  party_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (party_id, tag),
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity (
  id TEXT PRIMARY KEY,
  party_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  value_norm TEXT NOT NULL,
  value_raw TEXT NOT NULL DEFAULT '',
  UNIQUE (kind, value_norm),
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS identity_tag (
  identity_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY (identity_id, tag),
  FOREIGN KEY (identity_id) REFERENCES identity(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS vehicle (
  id TEXT PRIMARY KEY,
  party_id TEXT NOT NULL,
  year TEXT NOT NULL DEFAULT '',
  make TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  vin TEXT NOT NULL DEFAULT '',
  plate TEXT NOT NULL DEFAULT '',
  asking_price TEXT NOT NULL DEFAULT '',
  mileage TEXT NOT NULL DEFAULT '',
  location TEXT NOT NULL DEFAULT '',
  gps TEXT NOT NULL DEFAULT '',
  photos_json TEXT NOT NULL DEFAULT '[]',
  spotted_at TEXT NOT NULL DEFAULT '',
  date_source TEXT NOT NULL DEFAULT '',
  location_source TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interaction (
  id TEXT PRIMARY KEY,
  party_id TEXT NOT NULL,
  channel TEXT NOT NULL,
  direction TEXT NOT NULL,
  provider TEXT NOT NULL DEFAULT '',
  provider_msg_id TEXT UNIQUE,
  from_addr TEXT NOT NULL DEFAULT '',
  to_addr TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '',
  transcript_ref TEXT NOT NULL DEFAULT '',
  outcome TEXT NOT NULL DEFAULT '',
  error_code TEXT NOT NULL DEFAULT '',
  copy_version TEXT NOT NULL DEFAULT '',
  variant_id TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL,
  logged_by TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task (
  id TEXT PRIMARY KEY,
  party_id TEXT,
  title TEXT NOT NULL DEFAULT '',
  due_at TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open',
  assignee_seat TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT '',
  completed_by TEXT NOT NULL DEFAULT '',
  completed_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS note (
  id TEXT PRIMARY KEY,
  party_id TEXT NOT NULL,
  body TEXT NOT NULL,
  author_seat TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  archived INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (party_id) REFERENCES party(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS suppression (
  key_norm TEXT PRIMARY KEY,
  reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL,
  by_seat TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor_seat TEXT NOT NULL,
  actor_cred_id TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  op TEXT NOT NULL CHECK (op IN ('create','update','delete','merge')),
  before_json TEXT,
  after_json TEXT,
  request_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS intake_folder (
  folder_id TEXT PRIMARY KEY,
  lead_id TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload_json TEXT NOT NULL,
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  payload_json TEXT NOT NULL,
  at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sms_day (
  day TEXT PRIMARY KEY,
  n INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_party_type ON party(type);
CREATE INDEX IF NOT EXISTS idx_party_status ON party(status);
CREATE INDEX IF NOT EXISTS idx_party_source ON party(source);
CREATE INDEX IF NOT EXISTS idx_identity_party ON identity(party_id);
CREATE INDEX IF NOT EXISTS idx_interaction_party ON interaction(party_id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit(entity_id);
CREATE INDEX IF NOT EXISTS idx_task_due ON task(due_at);
CREATE INDEX IF NOT EXISTS idx_note_party ON note(party_id);
"""

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS party_fts USING fts5(
  party_id UNINDEXED,
  display_name,
  org_name,
  tags,
  notes,
  vehicle,
  identities,
  tokenize='porter unicode61'
);
"""

OUTREACH_VIEW_SQL = f"""
CREATE VIEW IF NOT EXISTS outreach_eligible AS
SELECT p.id AS id
FROM party p
WHERE p.type = 'lead'
  AND p.archived = 0
  AND p.status IN ({", ".join("'" + s + "'" for s in _FUNNEL)})
  AND EXISTS (
    SELECT 1 FROM identity i
    WHERE i.party_id = p.id AND i.kind = 'phone' AND i.value_norm != ''
  )
  AND NOT EXISTS (
    SELECT 1
    FROM identity i
    JOIN suppression s ON (
      s.key_norm = i.value_norm
      OR s.key_norm = 'phone:' || i.value_norm
      OR (
        i.value_norm LIKE '+1%'
        AND (
          s.key_norm = substr(i.value_norm, 3)
          OR s.key_norm = 'phone:' || substr(i.value_norm, 3)
        )
      )
    )
    WHERE i.party_id = p.id AND i.kind = 'phone'
  );
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_db_path() -> Path:
    return Path.home() / ".local" / "share" / "panamerica-crm" / "crm.db"


def harden(path: Path) -> None:
    """mode 600 file, mode 700 directory. Group and other cannot open the DB."""
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    if path.exists():
        os.chmod(path, 0o600)
    for suffix in ("-wal", "-shm"):
        sibling = Path(str(path) + suffix)
        if sibling.exists():
            os.chmod(sibling, 0o600)


def mode_bits(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    harden(path)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.executescript(FTS_SQL)
    conn.executescript(OUTREACH_VIEW_SQL)
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
    if row is None:
        now = utc_now()
        until = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany(
                "INSERT INTO schema_meta(key, value) VALUES (?, ?)",
                [
                    ("version", str(SCHEMA_VERSION)),
                    ("created_at", now),
                    ("export_until", until),
                    ("owner_uid", str(os.geteuid())),
                ],
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    harden_from_conn(conn)


def harden_from_conn(conn: sqlite3.Connection) -> None:
    rows = conn.execute("PRAGMA database_list").fetchall()
    for row in rows:
        name = row["file"]
        if name:
            harden(Path(name))
