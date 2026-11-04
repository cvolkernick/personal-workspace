#!/usr/bin/env python3
"""Value-free inventory of X API credential material (#1130).

Prints paths, match type, key name, sha256 fingerprints, and counts.
Credential values are never printed. FAKE-* fixtures are counted and omitted.

Suitable for a weekly cron. Exit 0 when nothing is reported, 1 when a finding
is reported, 2 when the scan itself cannot be trusted.

    python3 scripts/x-analytics/audit_x_creds.py --git
    python3 scripts/x-analytics/audit_x_creds.py --root "$HOME/.openclaw" --git
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

X_ENV_VARS = ("X_CONSUMER_KEY", "X_CONSUMER_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
SENSITIVE_NAMES = {".x_credentials.json", "x-api.env"}
MAX_BYTES = 5_000_000
TOKEN_RE = re.compile(r"[A-Za-z0-9_%+=/.-]{16,256}")
RULE_ID = "x-api-key-name"


class ScanError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class GuardConfig:
    key_regex: re.Pattern[str]
    allow_regex: re.Pattern[str]
    secret_group: int


@dataclass(frozen=True)
class Finding:
    path: str
    commit: str
    match: str
    key: str
    fingerprint: str
    count: int


@dataclass
class Report:
    findings: dict[tuple[str, str, str, str, str], int] = field(default_factory=dict)
    ignored_fixtures: int = 0
    allowed_store: int = 0
    allowed_store_file_mode: str = "unseen"
    skipped_binary: int = 0
    skipped_large: int = 0
    skipped_undecodable: int = 0
    ignored_fingerprint_lines: int = 0

    def add(self, path: str, commit: str, match: str, key: str, fingerprint: str, count: int = 1) -> None:
        item = (path, commit, match, key, fingerprint)
        self.findings[item] = self.findings.get(item, 0) + count

    def rows(self) -> list[Finding]:
        rows = [
            Finding(path, commit, match, key, fingerprint, count)
            for (path, commit, match, key, fingerprint), count in self.findings.items()
        ]
        return sorted(rows, key=lambda row: (row.path, row.commit, row.match, row.key, row.fingerprint))

    def counts(self) -> dict[str, int | str]:
        totals = {"findings": 0, "key-name": 0, "fingerprint": 0, "key-name+fingerprint": 0, "filename": 0, "mode": 0}
        for row in self.rows():
            totals["findings"] += row.count
            if row.match in totals:
                totals[row.match] += row.count
        totals["ignored_fixtures"] = self.ignored_fixtures
        totals["allowed_store"] = self.allowed_store
        totals["skipped_binary"] = self.skipped_binary
        totals["skipped_large"] = self.skipped_large
        totals["skipped_undecodable"] = self.skipped_undecodable
        totals["ignored_fingerprint_lines"] = self.ignored_fingerprint_lines
        return totals

    def render(self, fmt: str) -> str:
        rows = [
            {
                "path": row.path,
                "commit": row.commit,
                "match": row.match,
                "key": row.key or "-",
                "fingerprint": f"sha256:{row.fingerprint}" if row.fingerprint else "-",
                "count": row.count,
            }
            for row in self.rows()
        ]
        counts = self.counts()
        counts["allowed_store_file_mode"] = self.allowed_store_file_mode
        if fmt == "json":
            return json.dumps({"findings": rows, "counts": counts}, indent=2, sort_keys=True) + "\n"
        lines = [
            "path={path} commit={commit} match={match} key={key} fingerprint={fingerprint} count={count}".format(**row)
            for row in rows
        ]
        count_text = " ".join(f"{key}={value}" for key, value in counts.items())
        lines.append(f"counts {count_text}")
        return "\n".join(lines) + "\n"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def config_path(root: Path | None = None) -> Path:
    return (root or repo_root()) / ".gitleaks.toml"


def load_guard_config(path: Path | None = None) -> GuardConfig:
    cfg_path = path or config_path()
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScanError(f"config-{type(exc).__name__}") from None
    rule = next((item for item in data.get("rules", []) if item.get("id") == RULE_ID), None)
    if rule is None or "regex" not in rule:
        raise ScanError("config-missing-rule")
    allow_regex = ""
    for allow in data.get("allowlists", []):
        targets = allow.get("targetRules") or []
        if targets and RULE_ID not in targets:
            continue
        regexes = allow.get("regexes") or []
        if regexes:
            allow_regex = regexes[0]
            break
    if not allow_regex:
        raise ScanError("config-missing-allowlist")
    try:
        return GuardConfig(
            key_regex=re.compile(rule["regex"]),
            allow_regex=re.compile(allow_regex),
            secret_group=int(rule.get("secretGroup") or 0),
        )
    except re.error:
        raise ScanError("config-bad-regex") from None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _is_fixture(value: str, cfg: GuardConfig) -> bool:
    return cfg.allow_regex.fullmatch(value) is not None


def _assignments(text: str, cfg: GuardConfig) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in cfg.key_regex.finditer(text):
        try:
            key = match.group(1)
            value = match.group(cfg.secret_group) if cfg.secret_group else match.group(0)
        except IndexError:
            continue
        if key and value:
            found.append((key.upper(), value))
    return found


def _allowed_store_path() -> Path:
    return Path.home() / ".secrets" / "x-api.env"


def _is_allowed_store(path: Path) -> bool:
    try:
        return path.resolve() == _allowed_store_path().resolve()
    except OSError:
        return False


def _display(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def _note_allowed_mode(path: Path, report: Report) -> None:
    report.allowed_store_file_mode = "unreadable"
    try:
        if path.is_symlink() or path.parent.is_symlink():
            report.allowed_store_file_mode = "symlink"
            report.add(str(path), "WORKTREE", "mode", "-", "", 1)
            return
        file_info = path.lstat()
        dir_info = path.parent.lstat()
    except OSError:
        report.add(str(path), "WORKTREE", "mode", "-", "", 1)
        return
    file_mode = stat.S_IMODE(file_info.st_mode)
    dir_mode = stat.S_IMODE(dir_info.st_mode)
    report.allowed_store_file_mode = f"{file_mode:04o}"
    uid = os.geteuid()
    if file_mode != 0o600 or dir_mode != 0o700 or file_info.st_uid != uid or dir_info.st_uid != uid:
        report.add(str(path), "WORKTREE", "mode", "-", "", 1)


def _scan_text(path: str, commit: str, text: str, cfg: GuardConfig, fingerprints: set[str], report: Report, allowed: bool) -> None:
    seen: set[str] = set()
    for key, value in _assignments(text, cfg):
        if _is_fixture(value, cfg):
            report.ignored_fixtures += 1
            continue
        if allowed:
            report.allowed_store += 1
            continue
        digest = _sha256(value)
        match = "key-name+fingerprint" if digest in fingerprints else "key-name"
        report.add(path, commit, match, key, digest, 1)
        seen.add(digest)
    if not fingerprints or allowed:
        return
    for token_match in TOKEN_RE.finditer(text):
        token = token_match.group(0)
        if _is_fixture(token, cfg):
            continue
        digest = _sha256(token)
        if digest in fingerprints and digest not in seen:
            report.add(path, commit, "fingerprint", "-", digest, 1)
            seen.add(digest)


def _read_text(path: Path, report: Report) -> str | None:
    try:
        info = path.lstat()
    except OSError:
        report.skipped_undecodable += 1
        return None
    if stat.S_ISLNK(info.st_mode):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size > MAX_BYTES:
        report.skipped_large += 1
        return None
    try:
        data = path.read_bytes()
    except OSError:
        report.skipped_undecodable += 1
        return None
    if b"\0" in data[:8192]:
        report.skipped_binary += 1
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        report.skipped_undecodable += 1
        return None


def _scan_file(root: Path, path: Path, commit: str, cfg: GuardConfig, fingerprints: set[str], report: Report) -> None:
    if path.name in SENSITIVE_NAMES and not _is_allowed_store(path):
        try:
            file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            file_hash = ""
        report.add(_display(root, path), commit, "filename", "-", file_hash, 1)
    allowed = _is_allowed_store(path)
    if allowed:
        _note_allowed_mode(path, report)
    text = _read_text(path, report)
    if text is None:
        return
    shown = _display(root, path) if commit == "WORKTREE" else str(path)
    _scan_text(shown, commit, text, cfg, fingerprints, report, allowed)


def iter_worktree(root: Path, tracked: bool):
    if tracked:
        try:
            raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"], stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ScanError(f"ls-files-{type(exc).__name__}") from None
        for rel in raw.split(b"\0"):
            if rel:
                yield root / rel.decode("utf-8", "replace")
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for name in filenames:
            yield Path(dirpath) / name


def _parse_git_grep(data: bytes):
    """Parse ``git grep -n -z`` records.

    Git writes ``<commit:path> NUL <lineno> NUL <line> LF`` and glues the next
    path onto the previous line, with no NUL between them.
    """
    if not data:
        return
    parts = data.split(b"\0")
    if not parts or parts == [b""]:
        return
    loc = parts[0]
    index = 1
    while index < len(parts):
        if index + 1 >= len(parts):
            raise ScanError("git-grep-shape")
        blob = parts[index + 1]
        index += 2
        newline = blob.find(b"\n")
        if newline == -1:
            content_b = blob
            loc_next = b""
        else:
            content_b = blob[:newline]
            loc_next = blob[newline + 1 :]
        try:
            loc_text = loc.decode("utf-8")
            content = content_b.decode("utf-8")
        except UnicodeDecodeError:
            loc = loc_next
            continue
        commit, sep, path = loc_text.partition(":")
        if not sep or not commit or not path:
            raise ScanError("git-grep-loc")
        yield commit, path, content
        loc = loc_next


def _git_grep(root: Path, pattern: str):
    try:
        commits = subprocess.check_output(
            ["git", "-C", str(root), "rev-list", "--all"],
            stderr=subprocess.DEVNULL,
        ).split()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScanError(f"rev-list-{type(exc).__name__}") from None
    if not commits:
        return
    for start in range(0, len(commits), 100):
        chunk = [item.decode() for item in commits[start : start + 100]]
        proc = subprocess.run(
            ["git", "-C", str(root), "grep", "-I", "-n", "-z", "-E", pattern, *chunk],
            capture_output=True,
        )
        if proc.returncode not in (0, 1):
            raise ScanError(f"git-grep-exit-{proc.returncode}")
        yield from _parse_git_grep(proc.stdout)


def _git_messages(root: Path):
    proc = subprocess.run(
        ["git", "-C", str(root), "log", "--all", "--pretty=%H%x1e%B%x1f"],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ScanError(f"git-log-exit-{proc.returncode}")
    text = proc.stdout.decode("utf-8", "replace")
    for record in text.split("\x1f"):
        if not record.strip():
            continue
        commit, sep, body = record.partition("\x1e")
        if sep and commit.strip():
            yield commit.strip(), body


def _assert_private_fingerprint_file(path: Path) -> None:
    """Refuse a fingerprint list stored under ~/.secrets with a loose mode."""
    secrets = Path.home() / ".secrets"
    try:
        path.resolve().relative_to(secrets.resolve())
    except (OSError, ValueError):
        return
    try:
        if path.is_symlink() or path.parent.is_symlink():
            raise ScanError("fingerprint-symlink")
        file_info = path.lstat()
        dir_info = path.parent.lstat()
    except ScanError:
        raise
    except OSError as exc:
        raise ScanError(f"fingerprint-stat-{type(exc).__name__}") from None
    file_mode = stat.S_IMODE(file_info.st_mode)
    dir_mode = stat.S_IMODE(dir_info.st_mode)
    uid = os.geteuid()
    if dir_mode != 0o700:
        raise ScanError(f"fingerprint-directory-mode-{dir_mode:04o}-need-0700")
    if file_mode != 0o600:
        raise ScanError(f"fingerprint-file-mode-{file_mode:04o}-need-0600")
    if file_info.st_uid != uid or dir_info.st_uid != uid:
        raise ScanError("fingerprint-owner")


def load_fingerprints(path: Path, report: Report) -> set[str]:
    _assert_private_fingerprint_file(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScanError(f"fingerprint-read-{type(exc).__name__}") from None
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        token = stripped.split()[0].removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-fA-F]{64}", token):
            found.add(token.lower())
        else:
            report.ignored_fingerprint_lines += 1
    return found


def scan(roots: list[Path], cfg: GuardConfig, fingerprints: set[str], *, git: bool, tracked: bool) -> Report:
    report = Report()
    pattern = "|".join(X_ENV_VARS)
    for root in roots:
        root = root.resolve()
        if not root.exists():
            raise ScanError("root-missing")
        for path in iter_worktree(root, tracked):
            _scan_file(root, path, "WORKTREE", cfg, fingerprints, report)
        if not git:
            continue
        git_dir = root / ".git"
        if not git_dir.exists():
            raise ScanError("not-a-git-repo")
        for commit, path, text in _git_grep(root, pattern):
            _scan_text(path, commit, text, cfg, fingerprints, report, allowed=False)
        for commit, body in _git_messages(root):
            _scan_text("commit-message", commit, body, cfg, fingerprints, report, allowed=False)
    return report


def emit_fingerprints(path: str | None) -> str:
    """sha256 of the four loaded keys. Values stay in memory only."""
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    try:
        from twitter_metrics import X_ENV_VARS as names
        from twitter_metrics import load_x_creds
    except ImportError as exc:
        missing = getattr(exc, "name", None) or type(exc).__name__
        raise ScanError(f"import-{missing}") from None

    creds = load_x_creds(path)
    lines = [f"sha256:{_sha256(creds[name])} key={name}" for name in names]
    return "\n".join(lines) + "\n"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Value-free X credential audit (paths, match type, sha256, counts)."
    )
    parser.add_argument("--root", action="append", default=[], help="directory to scan (repeatable)")
    parser.add_argument("--git", action="store_true", help="also scan git history and commit messages")
    parser.add_argument("--tracked", action="store_true", help="worktree scan is git ls-files only")
    parser.add_argument("--fingerprints", type=Path, help="file of sha256 hex lines to match")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--emit-fingerprints",
        action="store_true",
        help="print sha256 of the four keys in the env file and exit",
    )
    parser.add_argument("--env-file", default=None, help="env file for --emit-fingerprints")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.emit_fingerprints:
            sys.stdout.write(emit_fingerprints(args.env_file))
            return 0
        cfg = load_guard_config()
        report = Report()
        fingerprints: set[str] = set()
        if args.fingerprints:
            fingerprints = load_fingerprints(args.fingerprints, report)
        roots = [Path(item) for item in args.root] or [repo_root()]
        scanned = scan(roots, cfg, fingerprints, git=args.git, tracked=args.tracked)
        scanned.ignored_fingerprint_lines += report.ignored_fingerprint_lines
        sys.stdout.write(scanned.render(args.format))
    except ScanError as exc:
        print(f"audit error: {exc.code}", file=sys.stderr)
        return 2
    except SystemExit as exc:
        message = exc.code if isinstance(exc.code, str) else "refusing X credentials"
        print(message, file=sys.stderr)
        return 2
    return 1 if scanned.findings else 0


if __name__ == "__main__":
    sys.exit(main())
