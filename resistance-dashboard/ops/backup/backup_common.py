"""Shared helpers for FitDash backup and restore.

Read-only against Turso. Plaintext staging stays in a 0700 temp directory.
The archive is gzip (stdlib), then age. No third-party Python packages.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, Sequence

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_DEFERRED = 75

SCHEMA_VERSION = 1
DEFER_WINDOW = timedelta(minutes=30)
EXCLUDED_TABLES = frozenset({"grok_sessions"})
SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Filenames and path parts that must never enter an archive. Matched
# case-insensitively. ``token`` / ``secret`` / ``credential`` cover token
# files without listing every product name.
_EXACT_NAMES = frozenset(
    {
        "env",
        "env.bak",
        ".env",
        ".env.bak",
        ".env.local",
        "master.key",
        "master.vercel.key",
        "grok_sessions",
        "grok_sessions.json",
        "grok_sessions.jsonl",
        "id_rsa",
        "id_dsa",
        "id_ed25519",
        "id_ecdsa",
    }
)
_OAUTH_CLIENT = re.compile(r"oauth.*client.*\.json$")
_TOKEN = re.compile(r"token")
_SECRET = re.compile(r"secret")
_CREDENTIAL = re.compile(r"credential")
_SKIP_DIRS = frozenset({"cache", "__pycache__", ".git", "node_modules"})
_READ_HEAD = frozenset({"select", "pragma", "with", "explain"})


def note(message: str) -> None:
    print(message, file=sys.stderr)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_utc(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timestamp_is_recent(
    value: Any, now: datetime, window: timedelta = DEFER_WINDOW
) -> bool:
    parsed = parse_timestamp(value)
    if parsed is None:
        return False
    return parsed >= now.astimezone(timezone.utc) - window


def is_denied(path: Path) -> bool:
    """True when this file or any path part is on the backup denylist."""
    parts = list(path.parts)
    # A relative path can start with "." — ignore that one component.
    for part in parts:
        if part in (".", "/", ""):
            continue
        if _name_denied(part):
            return True
    return False


def _name_denied(name: str) -> bool:
    lower = name.lower()
    if lower in _EXACT_NAMES:
        return True
    if lower.endswith(".key") or lower.endswith(".pem"):
        return True
    if _OAUTH_CLIENT.search(lower):
        return True
    if _TOKEN.search(lower) or _SECRET.search(lower) or _CREDENTIAL.search(lower):
        return True
    return False


def path_is_skipped(relative: str) -> bool:
    parts = Path(relative).parts
    return any(part in _SKIP_DIRS or part.endswith(".pyc") for part in parts)


def assert_read_only(sql: str) -> None:
    """Reject anything that is not a single read statement.

    The backup process must not write to Turso. Callers run only their own
    SQL; this is a backstop so a mistake cannot become a write.
    """
    text = (sql or "").strip()
    if not text:
        raise RuntimeError("refusing empty Turso statement")
    if ";" in text:
        raise RuntimeError("refusing multi-statement Turso SQL")
    head = text.split(None, 1)[0].lower()
    if head not in _READ_HEAD:
        raise RuntimeError(f"refusing non-read Turso statement: {head}")
    if head == "pragma":
        pragma = text.split(None, 1)[1].split("(", 1)[0].strip().lower()
        if pragma not in {"table_info", "index_list"}:
            raise RuntimeError(f"refusing Turso pragma: {pragma}")


def read_execute(conn: Any, sql: str, params: Sequence[Any] = ()) -> Any:
    assert_read_only(sql)
    return conn.execute(sql, params)


def quote_ident(name: str) -> str:
    if not SAFE_IDENT.fullmatch(name or ""):
        raise ValueError(f"unexpected SQL identifier: {name!r}")
    return '"' + name + '"'


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def encode_cell(value: Any) -> dict:
    """Typed JSON cell so a later SQLite round-trip hashes the same way."""
    if value is None:
        return {"t": "null"}
    if isinstance(value, bool):
        return {"t": "int", "v": 1 if value else 0}
    if isinstance(value, int):
        return {"t": "int", "v": value}
    if isinstance(value, float):
        return {"t": "float", "v": value}
    if isinstance(value, (bytes, bytearray)):
        return {"t": "blob", "v": base64.b64encode(bytes(value)).decode("ascii")}
    return {"t": "text", "v": str(value)}


def decode_cell(cell: Any) -> Any:
    if not isinstance(cell, dict) or "t" not in cell:
        return cell
    kind = cell.get("t")
    if kind == "null":
        return None
    if kind == "int":
        return int(cell["v"])
    if kind == "float":
        return float(cell["v"])
    if kind == "blob":
        return base64.b64decode(cell["v"])
    return cell.get("v")


def dump_rows(rows: Iterable[dict]) -> bytes:
    """Canonical JSONL. Line order is sorted so the hash ignores row order."""
    lines: list[str] = []
    for row in rows:
        encoded = {str(key): encode_cell(value) for key, value in dict(row).items()}
        lines.append(
            json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        )
    lines.sort()
    if not lines:
        return b""
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    raw = path.read_bytes()
    if not raw:
        return rows
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"jsonl row is not an object: {path}")
        rows.append({key: decode_cell(value) for key, value in payload.items()})
    return rows


def row_field(row: Any, name: str, index: int) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(name)
    try:
        return row[name]
    except (KeyError, IndexError, TypeError):
        pass
    try:
        return row[index]
    except (IndexError, TypeError):
        return None


def fetch_dicts(cursor: Any) -> list[dict]:
    description = getattr(cursor, "description", None) or []
    columns = [item[0] for item in description]
    rows: list[dict] = []
    fetched = cursor.fetchall()
    for raw in fetched:
        if isinstance(raw, dict):
            rows.append(dict(raw))
            continue
        if columns and not isinstance(raw, dict):
            try:
                rows.append({columns[i]: raw[i] for i in range(len(columns))})
                continue
            except (IndexError, KeyError, TypeError):
                pass
        raise RuntimeError("could not read Turso row shape")
    return rows


def env_var_names(path: Path) -> list[str]:
    """Names only. Values are discarded and never returned."""
    if not path.is_file():
        return []
    names: set[str] = set()
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        name = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            names.add(name)
    return sorted(names)


def write_json_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = path.with_name(path.name + ".tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _backup_connection(src: Path, dest: Path, source: sqlite3.Connection) -> None:
    replica = sqlite3.connect(str(dest))
    try:
        source.backup(replica)
    finally:
        replica.close()


def snapshot_sqlite(src: Path, dest: Path) -> None:
    """Consistent copy via sqlite3's online backup API.

    Safe while the app has the database open. The first attempt is read-only
    so the backup connection itself does not write the source. WAL databases
    on some builds reject that; the fallback still uses the backup API and
    does not run writer SQL.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    uri = f"file:{src.resolve().as_posix()}?mode=ro"
    try:
        source = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        source = sqlite3.connect(str(src))
    try:
        try:
            source.execute("PRAGMA query_only=ON")
        except sqlite3.Error:
            pass
        try:
            _backup_connection(src, dest, source)
        except sqlite3.Error:
            source.close()
            if dest.exists():
                dest.unlink()
            source = sqlite3.connect(str(src))
            _backup_connection(src, dest, source)
    finally:
        source.close()
    os.chmod(dest, 0o600)


def make_tar_gz(source_dir: Path, dest: Path) -> None:
    skip = dest.resolve()
    with tarfile.open(dest, "w:gz") as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if path.resolve() == skip:
                continue
            archive.add(path, arcname=path.relative_to(source_dir).as_posix(), recursive=False)


def safe_tar_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    for member in archive.getmembers():
        name = member.name
        parts = Path(name).parts
        if not name or name.startswith("/") or ".." in parts:
            raise ValueError(f"unsafe path in archive: {name}")
        if member.issym() or member.islnk():
            raise ValueError(f"refusing link in archive: {name}")
        if member.isfile() or member.isdir():
            members.append(member)
    return members


def extract_tar_gz(archive_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = safe_tar_members(archive)
        try:
            archive.extractall(dest, members=members, filter="data")
        except TypeError:
            archive.extractall(dest, members=members)


class StagingDir:
    """0700 temp directory removed in ``close`` (use as a context manager)."""

    def __init__(self, prefix: str = "fitdash-backup-") -> None:
        self.path = Path(tempfile.mkdtemp(prefix=prefix))
        os.chmod(self.path, 0o700)

    def close(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> "StagingDir":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def load_recipient_keys(path: Path) -> list[str]:
    """Public age recipients only. Rejects secret keys and blank files."""
    if not path.is_file():
        raise RuntimeError(f"recipients file is missing: {path}")
    keys: list[str] = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "AGE-SECRET-KEY" in stripped or stripped.startswith("AGE-SECRET"):
            raise RuntimeError(
                "recipients file contains an age secret key; refusing to use it"
            )
        if not stripped.startswith("age1"):
            raise RuntimeError(
                "recipients file has a line that is not an age public key"
            )
        keys.append(stripped)
    if not keys:
        raise RuntimeError(f"recipients file is empty: {path}")
    return keys


def require_age(explicit: Optional[str] = None) -> str:
    found: Optional[str] = None
    if explicit:
        candidate = Path(explicit)
        if candidate.is_file():
            found = str(candidate)
        else:
            found = shutil.which(explicit)
    else:
        found = shutil.which("age")
    if not found:
        raise RuntimeError(
            "age is not installed. Install the age CLI and retry. "
            "Refusing to write an unencrypted archive."
        )
    return found


def encrypt_with_age(
    plaintext: Path, recipients_file: Path, dest: Path, *, age: Optional[str] = None
) -> None:
    """Encrypt ``plaintext`` to ``dest``. ``plaintext`` must already sit in a temp dir."""
    binary = require_age(age)
    keys = load_recipient_keys(recipients_file)
    filtered = plaintext.parent / "recipients.public"
    filtered.write_text("\n".join(keys) + "\n", encoding="utf-8")
    os.chmod(filtered, 0o600)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    proc = subprocess.run(
        [binary, "-R", str(filtered), "-o", str(dest), str(plaintext)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        if dest.exists():
            dest.unlink()
        detail = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise RuntimeError(f"age encrypt failed ({proc.returncode}): {detail or 'no output'}")
    os.chmod(dest, 0o600)
    header = dest.read_bytes()[:32]
    if not header.startswith(b"age-encryption.org/"):
        dest.unlink()
        raise RuntimeError("age did not produce an encrypted file; removed the output")


def decrypt_with_age(
    archive: Path, identity: Path, dest: Path, *, age: Optional[str] = None
) -> None:
    binary = require_age(age)
    if not identity.is_file():
        raise RuntimeError(f"age identity file is missing: {identity}")
    raw = identity.read_text(encoding="utf-8", errors="replace")
    if "AGE-SECRET-KEY" not in raw:
        raise RuntimeError("identity file has no AGE-SECRET-KEY line")
    proc = subprocess.run(
        [binary, "-d", "-i", str(identity), "-o", str(dest), str(archive)],
        capture_output=True,
        timeout=180,
        check=False,
    )
    if proc.returncode != 0 or not dest.is_file():
        if dest.exists():
            dest.unlink()
        detail = proc.stderr.decode("utf-8", "replace").strip()[:500]
        raise RuntimeError(f"age decrypt failed ({proc.returncode}): {detail or 'no output'}")


def assert_create_table(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise ValueError("missing CREATE TABLE statement")
    body = text[:-1].strip() if text.endswith(";") else text
    if ";" in body:
        raise ValueError("refusing stacked SQL in a saved CREATE statement")
    if not re.match(r"CREATE\s+TABLE(\s+IF\s+NOT\s+EXISTS)?\s+", body, re.IGNORECASE):
        raise ValueError("refusing a saved statement that is not CREATE TABLE")
    return body


def iter_regular_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    if root.is_file() and not root.is_symlink():
        yield root
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def relative_under(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def chmod_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
