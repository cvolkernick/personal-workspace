#!/usr/bin/env python3
"""Commit + push fund-manager journal after each run (#737 / #742).

Single-writer is the Pi producer (#729). Never force-push. A git failure
must not fail the fund-manager run — log it to GitHub #701 and retry next
cycle.

The markdown journal is the must-commit human/assistant record. JSONL is
included in the same commit when dirty.

The FCC serving checkout (``~/personal-workspace`` on prism) is a deployment
target, not a workspace (#661). Live-clone sync writes to origin via the
GitHub git API — never ``git commit`` / ``push`` in that tree. workspace-sync
pulls the journal down on its normal cadence. The live pre-commit hook stays.

Non-serving checkouts (tests, a dedicated writer clone) still use local git
with the workspace-sync ``x-access-token`` insteadOf. Tokens are redacted on
#701. Do not wait for a systemd EnvironmentFile copy.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.pi_ops_alert import (  # noqa: E402
    OPS_REPO,
    github_token,
    load_scheduler_env,
    post_ops_github,
)

JOURNAL_REL = "investment/fund_manager_journal.md"
JSONL_REL = "treasury/snapshots/fund_manager_decisions.jsonl"
COMMIT_PATHS: Tuple[str, ...] = (JOURNAL_REL, JSONL_REL)
DEFAULT_BRANCH = "work/treasury"
_FORCE_FLAGS = ("--force", "--force-with-lease")
_NETWORK_GIT = frozenset({"pull", "push", "fetch", "ls-remote"})
_KIND_RE = re.compile(r"[^a-z0-9_-]+")
_TOKEN_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]+|github_pat_[A-Za-z0-9_]+|x-access-token:[^@\s]+|Bearer\s+\S+)",
    re.I,
)
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_PRODUCER_TAGS = {"prism", "pi"}
_LIVE_TREE_FLAGS = {"main", "clone", "primary", "1", "true", "yes"}
_SERVING_CLONE = Path.home() / "personal-workspace"
_MUTATING_GIT = frozenset({"add", "commit", "push", "pull", "fetch", "rebase"})


def _insteadOf_config(token: str) -> str:
    """Same HTTPS rewrite as deploy/workspace_sync.sh git_auth."""
    return f"url.https://x-access-token:{token}@github.com/.insteadOf=https://github.com/"


def _network_git_extra_args() -> List[str]:
    """Auth for pull/push/fetch from workflow-scheduler.env — not systemd EnvironmentFile."""
    load_scheduler_env()
    token = github_token()
    if not token:
        return []
    return ["-c", _insteadOf_config(token)]


def _redact(text: str) -> str:
    s = text or ""
    token = github_token()
    if token:
        s = s.replace(token, "REDACTED")
    return _TOKEN_RE.sub("REDACTED", s)


def _env_flag(name: str) -> Optional[bool]:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return None


def _in_pytest() -> bool:
    if _env_flag("FM_JOURNAL_SYNC_IN_TEST") is True:
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def producer_allowed() -> bool:
    """Pi systemd sets FCC_HOST_TAG=prism. Mac/manual runs must not commit."""
    explicit = _env_flag("FM_JOURNAL_SYNC")
    if explicit is False:
        return False
    if explicit is True:
        return True
    tag = (os.environ.get("FCC_HOST_TAG") or "").strip().lower()
    return tag in _PRODUCER_TAGS


def _git_dir(repo: Path) -> Optional[Path]:
    git = repo / ".git"
    if git.is_dir():
        return git
    if git.is_file():
        try:
            text = git.read_text(encoding="utf-8")
        except OSError:
            return None
        for line in text.splitlines():
            if line.lower().startswith("gitdir:"):
                p = Path(line.split(":", 1)[1].strip())
                if not p.is_absolute():
                    p = (repo / p).resolve()
                return p
    return None


def live_clone_reason(repo: Path) -> Optional[str]:
    """Why this checkout is the FCC serving clone (#661 / #742), or None."""
    flag = (
        os.environ.get("FCC_LIVE_TREE") or os.environ.get("FM_JOURNAL_LIVE_CLONE") or ""
    ).strip().lower()
    if flag in _LIVE_TREE_FLAGS:
        return "FCC_LIVE_TREE"
    gitdir = _git_dir(repo)
    if gitdir is not None:
        hook = gitdir / "hooks" / "pre-commit"
        try:
            text = hook.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if "refuses local commits" in text:
            return "pre-commit-hook"
    try:
        resolved = repo.resolve()
        serving = _SERVING_CLONE.resolve()
    except OSError:
        return None
    if producer_allowed() and resolved == serving:
        return "serving-path"
    return None


def is_live_clone(repo: Path) -> bool:
    return live_clone_reason(repo) is not None


def _github_json(
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    timeout: float = 30.0,
) -> Tuple[Optional[Any], Optional[Dict[str, Any]]]:
    """GitHub REST helper. Returns (json, error). Never includes the token."""
    load_scheduler_env()
    token = github_token()
    if not token:
        return None, {"error": "no-github-token", "status": None}
    url = "https://api.github.com" + path
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "personal-workspace-fm-journal-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            parsed: Any = json.loads(raw) if raw else {}
            return parsed, None
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        return None, {"status": exc.code, "error": _redact(err_body or str(exc))}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return None, {"status": None, "error": _redact(str(exc))}


def _origin_file_text(rel: str, branch: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (text, error). Missing file → (None, None)."""
    quoted = urllib.parse.quote(rel, safe="/")
    ref = urllib.parse.quote(branch, safe="")
    data, err = _github_json(
        "GET", f"/repos/{OPS_REPO}/contents/{quoted}?ref={ref}"
    )
    if err:
        if err.get("status") == 404:
            return None, None
        return None, str(err.get("error") or "contents GET failed")
    if not isinstance(data, dict):
        return None, "contents GET unexpected payload"
    content = str(data.get("content") or "").replace("\n", "")
    if not content:
        return "", None
    try:
        return base64.b64decode(content).decode("utf-8"), None
    except (ValueError, UnicodeDecodeError) as exc:
        return None, str(exc)


def _local_file_text(repo: Path, rel: str) -> Optional[str]:
    p = repo / rel
    if not p.is_file():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _files_dirty_vs_origin(repo: Path, branch: str) -> Tuple[Dict[str, str], Optional[str]]:
    """Local path → text for files that differ from origin. error if origin unreadable."""
    dirty: Dict[str, str] = {}
    for rel in COMMIT_PATHS:
        local = _local_file_text(repo, rel)
        if local is None:
            continue
        remote, err = _origin_file_text(rel, branch)
        if err:
            return {}, err
        if local != remote:
            dirty[rel] = local
    return dirty, None


def _commit_files_via_github(
    files: Dict[str, str],
    *,
    message: str,
    branch: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Create one origin commit with ``files`` (path → text). Never force-updates."""
    if not files:
        return True, {"skipped": "clean"}
    ref_path = f"/repos/{OPS_REPO}/git/refs/heads/{urllib.parse.quote(branch, safe='')}"
    last_err = "github commit failed"
    for attempt in (1, 2):
        head, err = _github_json("GET", f"/repos/{OPS_REPO}/commits/{urllib.parse.quote(branch, safe='')}")
        if err or not isinstance(head, dict):
            last_err = (err or {}).get("error") or "HEAD GET failed"
            continue
        parent = str(head.get("sha") or "")
        tree_sha = str(((head.get("commit") or {}).get("tree") or {}).get("sha") or "")
        if not parent or not tree_sha:
            last_err = "HEAD missing sha/tree"
            continue
        entries: List[Dict[str, str]] = []
        blob_ok = True
        for rel, text in files.items():
            blob, berr = _github_json(
                "POST",
                f"/repos/{OPS_REPO}/git/blobs",
                {"content": text, "encoding": "utf-8"},
            )
            if berr or not isinstance(blob, dict) or not blob.get("sha"):
                last_err = (berr or {}).get("error") or f"blob failed for {rel}"
                blob_ok = False
                break
            entries.append(
                {
                    "path": rel,
                    "mode": "100644",
                    "type": "blob",
                    "sha": str(blob["sha"]),
                }
            )
        if not blob_ok:
            continue
        tree, terr = _github_json(
            "POST",
            f"/repos/{OPS_REPO}/git/trees",
            {"base_tree": tree_sha, "tree": entries},
        )
        if terr or not isinstance(tree, dict) or not tree.get("sha"):
            last_err = (terr or {}).get("error") or "tree POST failed"
            continue
        commit, cerr = _github_json(
            "POST",
            f"/repos/{OPS_REPO}/git/commits",
            {
                "message": message,
                "tree": tree["sha"],
                "parents": [parent],
            },
        )
        if cerr or not isinstance(commit, dict) or not commit.get("sha"):
            last_err = (cerr or {}).get("error") or "commit POST failed"
            continue
        upd, uerr = _github_json(
            "PATCH",
            ref_path,
            {"sha": commit["sha"], "force": False},
        )
        if uerr:
            status = uerr.get("status")
            last_err = str(uerr.get("error") or "ref PATCH failed")
            if status in {409, 422} and attempt == 1:
                continue
            return False, {"error": last_err, "attempt": attempt}
        if not isinstance(upd, dict):
            last_err = "ref PATCH unexpected payload"
            continue
        return True, {
            "sha": commit["sha"],
            "attempt": attempt,
            "paths": list(files),
        }
    return False, {"error": last_err}


def _sync_via_github_api(
    repo: Path,
    *,
    branch: str,
    message: str,
    dry_run: bool,
    notify: bool,
    reason: str,
) -> Dict[str, Any]:
    """Write journal to origin without mutating the live clone's git history."""
    dirty, err = _files_dirty_vs_origin(repo, branch)
    if err:
        if notify:
            out = _report_failure(err, {"via": "github-api", "live": reason})
            out["via"] = "github-api"
            return out
        return {
            "ok": False,
            "committed": False,
            "pushed": False,
            "via": "github-api",
            "error": err,
        }
    if dry_run:
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "dry-run",
            "dirty": list(dirty),
            "message": message,
            "branch": branch,
            "via": "github-api",
            "live": reason,
        }
    if not dirty:
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "clean",
            "branch": branch,
            "via": "github-api",
            "live": reason,
        }
    ok, meta = _commit_files_via_github(dirty, message=message, branch=branch)
    if not ok:
        detail = str(meta.get("error") or "github-api commit failed")
        if notify:
            out = _report_failure(detail, {"via": "github-api", "live": reason})
            out["via"] = "github-api"
            return out
        return {
            "ok": False,
            "committed": False,
            "pushed": False,
            "via": "github-api",
            "error": detail,
        }
    return {
        "ok": True,
        "committed": True,
        "pushed": True,
        "via": "github-api",
        "live": reason,
        "branch": branch,
        "message": message,
        "paths": list(dirty),
        "sha": meta.get("sha"),
        "attempt": meta.get("attempt"),
    }


def _safe_kind(raw: Any) -> str:
    s = _KIND_RE.sub("", str(raw or "").strip().lower().replace(" ", "-"))
    return s or "decision"


def journal_commit_message(kind: str, when: Optional[datetime] = None) -> str:
    stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime(
        "%Y-%m-%d %H:%M"
    )
    return f"journal: fund-manager {_safe_kind(kind)} {stamp}"


def _git(
    repo: Path,
    *args: str,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    if args and args[0] == "push" and any(
        a in _FORCE_FLAGS or a == "-f" for a in args[1:]
    ):
        return 1, "", "force-push forbidden (#737)"
    if args and args[0] in _MUTATING_GIT and is_live_clone(repo):
        return 1, "", "FCC live clone refuses local git mutation (#742)"
    extra = _network_git_extra_args() if args and args[0] in _NETWORK_GIT else []
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *extra, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if extra:
            out, err = _redact(out), _redact(err)
        return proc.returncode, out, err
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        return 1, "", _redact(str(exc)) if extra else str(exc)


def _host_tag() -> str:
    return (
        os.environ.get("FCC_HOST_TAG")
        or socket.gethostname()
        or "unknown-host"
    ).split(".")[0]


def _report_failure(reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    detail = _redact(reason)
    bits = [f"[{_host_tag()}] journal sync failed: {detail}"]
    if extra:
        for k, v in extra.items():
            bits.append(f"{k}={_redact(str(v))}")
    github = post_ops_github(
        "FCC · fund-manager journal sync failed",
        "\n".join(bits),
    )
    return {
        "ok": False,
        "committed": False,
        "pushed": False,
        "error": detail,
        "github": github,
    }


def _last_jsonl_row(repo: Path) -> Optional[Dict[str, Any]]:
    jsonl = repo / JSONL_REL
    if not jsonl.is_file():
        return None
    last: Optional[Dict[str, Any]] = None
    try:
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                last = row
    except OSError:
        return None
    return last


def infer_kind(repo: Path) -> str:
    last = _last_jsonl_row(repo)
    if last and last.get("kind"):
        return _safe_kind(last.get("kind"))
    journal = repo / JOURNAL_REL
    if journal.is_file():
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in reversed(lines):
            if line.startswith("## ") and " — " in line:
                return _safe_kind(line.split(" — ", 1)[-1].strip())
    return "decision"


def infer_as_of(repo: Path) -> Optional[str]:
    last = _last_jsonl_row(repo)
    if last and last.get("as_of"):
        return str(last.get("as_of"))
    return None


def _dirty_paths(repo: Path) -> List[str]:
    dirty: List[str] = []
    for rel in COMMIT_PATHS:
        code, out, _ = _git(repo, "status", "--porcelain", "--", rel)
        if code == 0 and out.strip():
            dirty.append(rel)
    return dirty


def _current_branch(repo: Path) -> str:
    code, out, _ = _git(repo, "branch", "--show-current")
    return out if code == 0 else ""


def _user_email(repo: Path) -> str:
    code, out, _ = _git(repo, "config", "--get", "user.email")
    return out.strip() if code == 0 else ""


def _unpushed_subjects(repo: Path, branch: str) -> List[str]:
    code, out, _ = _git(repo, "log", "--format=%s", f"origin/{branch}..HEAD")
    if code != 0 or not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _rebase_in_progress(repo: Path) -> bool:
    for name in ("rebase-merge", "rebase-apply"):
        code, out, _ = _git(repo, "rev-parse", "--git-path", name)
        if code != 0 or not out:
            continue
        p = Path(out)
        if not p.is_absolute():
            p = repo / p
        if p.exists():
            return True
    return False


def _abort_rebase_if_needed(repo: Path) -> None:
    if _rebase_in_progress(repo):
        _git(repo, "rebase", "--abort", timeout=15.0)


def _stamp_from_as_of(as_of: Optional[str]) -> datetime:
    if as_of:
        try:
            t = datetime.fromisoformat(str(as_of).replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return t.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc)


def sync_journal(
    *,
    kind: Optional[str] = None,
    repo: Optional[Path] = None,
    as_of: Optional[str] = None,
    dry_run: bool = False,
    notify: bool = True,
) -> Dict[str, Any]:
    """Commit journal (+ JSONL if dirty) to origin/work/treasury.

    Live clone (#742): GitHub git API, zero local ``git commit``/``push``.
    Other checkouts: local git + insteadOf auth (#737). Never force-push.
    Never raises. Failures comment #701 when notify=True; the fund-manager
    run still succeeds.
    """
    repo = Path(repo) if repo is not None else ROOT
    if _in_pytest():
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "pytest",
        }
    if not producer_allowed():
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "not-producer",
        }

    branch = (os.environ.get("FM_JOURNAL_BRANCH") or DEFAULT_BRANCH).strip()
    live_reason = live_clone_reason(repo)
    kind_s = _safe_kind(kind or infer_kind(repo))
    message = journal_commit_message(
        kind_s, _stamp_from_as_of(as_of or infer_as_of(repo))
    )
    if live_reason:
        return _sync_via_github_api(
            repo,
            branch=branch,
            message=message,
            dry_run=dry_run,
            notify=notify,
            reason=live_reason,
        )

    current = _current_branch(repo)
    if current != branch:
        msg = f"branch is {current or 'HEAD'} not {branch}"
        if notify:
            out = _report_failure(msg, {"repo": str(repo)})
            out["skipped"] = "wrong-branch"
            return out
        return {
            "ok": False,
            "committed": False,
            "pushed": False,
            "skipped": "wrong-branch",
            "error": msg,
        }

    email = _user_email(repo)
    if not email:
        msg = "git user.email is empty"
        if notify:
            return _report_failure(msg)
        return {
            "ok": False,
            "committed": False,
            "pushed": False,
            "skipped": "no-email",
            "error": msg,
        }

    dirty = _dirty_paths(repo)
    if dry_run:
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "dry-run",
            "dirty": dirty,
            "message": message,
            "branch": branch,
        }

    committed = False
    if dirty:
        for rel in dirty:
            a_code, a_out, a_err = _git(repo, "add", "--", rel, timeout=15.0)
            if a_code != 0:
                if notify:
                    return _report_failure(
                        "git add failed",
                        {"path": rel, "stderr": a_err or a_out},
                    )
                return {
                    "ok": False,
                    "committed": False,
                    "pushed": False,
                    "error": _redact(a_err or a_out or "git add failed"),
                }
        code, out, err = _git(
            repo,
            "commit",
            "--only",
            "-m",
            message,
            "--",
            *dirty,
            timeout=30.0,
        )
        if code != 0:
            if notify:
                return _report_failure(
                    "commit failed",
                    {"stderr": err or out, "message": message},
                )
            return {
                "ok": False,
                "committed": False,
                "pushed": False,
                "error": _redact(err or out or "commit failed"),
            }
        committed = True

    subjects = _unpushed_subjects(repo, branch)
    if not committed and not subjects:
        return {
            "ok": True,
            "committed": False,
            "pushed": False,
            "skipped": "clean",
            "branch": branch,
        }

    non_journal = [s for s in subjects if not s.startswith("journal: fund-manager ")]
    if non_journal:
        msg = "refusing to push mixed unpushed history"
        if notify:
            out = _report_failure(msg, {"unpushed": "; ".join(subjects[:8])})
            out["committed"] = committed
            return out
        return {
            "ok": False,
            "committed": committed,
            "pushed": False,
            "error": msg,
            "unpushed": subjects,
        }

    last_err = ""
    for attempt in (1, 2):
        p_code, p_out, p_err = _git(
            repo,
            "pull",
            "--rebase",
            "--autostash",
            "origin",
            branch,
            timeout=90.0,
        )
        if p_code != 0:
            _abort_rebase_if_needed(repo)
            last_err = p_err or p_out or "pull --rebase failed"
            continue
        push_args: Sequence[str] = ("push", "origin", f"HEAD:{branch}")
        u_code, u_out, u_err = _git(repo, *push_args, timeout=90.0)
        if u_code == 0:
            return {
                "ok": True,
                "committed": committed,
                "pushed": True,
                "branch": branch,
                "message": message if committed else (subjects[-1] if subjects else message),
                "paths": dirty,
                "attempt": attempt,
            }
        last_err = u_err or u_out or "push failed"

    if notify:
        out = _report_failure(
            last_err or "push failed after retry",
            {"branch": branch, "committed": committed},
        )
        out["committed"] = committed
        out["pushed"] = False
        return out
    return {
        "ok": False,
        "committed": committed,
        "pushed": False,
        "error": _redact(last_err or "push failed after retry"),
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--kind", default="", help="Decision kind (inferred if omitted)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--repo", default="", help="Repo root (default: this checkout)")
    p.add_argument("--no-notify", action="store_true", help="Do not comment #701 on failure")
    args = p.parse_args(argv)
    result = sync_journal(
        kind=args.kind or None,
        repo=Path(args.repo) if args.repo else None,
        dry_run=args.dry_run,
        notify=not args.no_notify,
    )
    print(json.dumps(result, indent=2, default=str))
    # Always 0: fund-manager wrappers must not fail the run (#737).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
