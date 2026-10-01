"""Server-side job runner: branch → agent work → push → open PR.

Used by the Raspberry Pi (or any headless host) so backlog work can complete
without a Mac Terminal session. Falls back with clear errors when Grok CLI or
git credentials are missing.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlparse

from backlog import WORKSPACE_ROOT, get_item, initiate_item, update_item
from git_workflow import dirty_paths, protect_work, start_work

DEFAULT_REMOTE = "https://github.com/cvolkernick/personal-workspace.git"
MAX_AGENT_TURNS = 40


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (title or "job").strip().lower()).strip("-")
    return (s[:40] or "job")


def _run(
    *args: str,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    timeout: float = 120.0,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            list(args),
            cwd=str(cwd or WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or {**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, "", str(e)


def github_token() -> Optional[str]:
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "GH_PAT"):
        v = (os.environ.get(k) or "").strip()
        if v:
            return v
    # Optional non-committed local env on Pi/Mac
    for p in (
        Path.home() / ".config" / "workflow-scheduler.env",
        WORKSPACE_ROOT / "ops" / "backlog" / ".github_token",
    ):
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("GITHUB_TOKEN="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
                    if line and not line.startswith("#") and "=" not in line:
                        return line.strip()
            except OSError:
                pass
    return None


def which_grok() -> Optional[str]:
    g = shutil.which("grok")
    if g:
        return g
    home = Path.home() / ".grok" / "bin" / "grok"
    if home.is_file() and os.access(home, os.X_OK):
        return str(home)
    return None


def load_scheduler_env() -> dict[str, str]:
    """Load ~/.config/workflow-scheduler.env into os.environ (non-destructive for unset keys)."""
    loaded: dict[str, str] = {}
    path = Path.home() / ".config" / "workflow-scheduler.env"
    if not path.is_file():
        return loaded
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if not k:
                continue
            # Prefer file for secrets used by agent; do not clobber existing non-empty env
            if k in ("XAI_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "GH_PAT") or not os.environ.get(k):
                if v:
                    os.environ[k] = v
                    loaded[k] = "<set>" if k.endswith("TOKEN") or "KEY" in k or "PAT" in k else v
    except OSError:
        pass
    return loaded


def ensure_git_repo(
    repo: Path = WORKSPACE_ROOT,
    *,
    remote_url: Optional[str] = None,
) -> dict[str, Any]:
    """Ensure *repo* is a git checkout (clone if missing)."""
    repo = Path(repo).resolve()
    remote_url = remote_url or os.environ.get("WORKSPACE_REMOTE") or DEFAULT_REMOTE
    git_dir = repo / ".git"
    if git_dir.exists():
        code, out, err = _run("git", "-C", str(repo), "remote", "get-url", "origin")
        return {
            "ok": True,
            "existed": True,
            "path": str(repo),
            "origin": out if code == 0 else None,
            "error": err if code != 0 else None,
        }

    token = github_token()
    clone_url = remote_url
    if token and remote_url.startswith("https://"):
        # https://github.com/org/repo.git → https://x-access-token:TOKEN@github.com/...
        parsed = urlparse(remote_url)
        clone_url = (
            f"https://x-access-token:{quote(token, safe='')}@"
            f"{parsed.netloc}{parsed.path}"
        )

    parent = repo.parent
    parent.mkdir(parents=True, exist_ok=True)
    # If directory has files (rsync tree), init + remote instead of clone into non-empty
    if any(repo.iterdir()) if repo.is_dir() else False:
        repo.mkdir(parents=True, exist_ok=True)
        steps = []
        for cmd in (
            ["git", "init"],
            ["git", "remote", "add", "origin", remote_url],
            ["git", "fetch", "origin", "master", "--depth", "50"],
            ["git", "checkout", "-B", "master", "origin/master"],
        ):
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            if token and cmd[0] == "git" and "fetch" in cmd:
                env["GIT_ASKPASS"] = "echo"
                # use token URL for fetch
                _run("git", "-C", str(repo), "remote", "set-url", "origin", clone_url)
            code, out, err = _run(*cmd, cwd=repo, env=env, timeout=180)
            steps.append({"cmd": cmd, "code": code, "err": err[:200] if err else ""})
            if code != 0 and cmd[1] not in ("remote",):
                # continue best-effort
                pass
        # restore clean remote without token in config if possible
        if token:
            _run("git", "-C", str(repo), "remote", "set-url", "origin", remote_url)
        ok = (repo / ".git").exists()
        return {
            "ok": ok,
            "existed": False,
            "initialized": True,
            "path": str(repo),
            "steps": steps,
            "error": None if ok else "git init/fetch failed",
        }

    code, out, err = _run(
        "git",
        "clone",
        "--branch",
        "master",
        clone_url,
        str(repo),
        timeout=300,
    )
    if code != 0:
        return {"ok": False, "error": err or out or "git clone failed", "path": str(repo)}
    # scrub token from remote
    _run("git", "-C", str(repo), "remote", "set-url", "origin", remote_url)
    return {"ok": True, "existed": False, "cloned": True, "path": str(repo)}


def _clean_remote_url(url: str) -> str:
    u = (url or DEFAULT_REMOTE).strip()
    u = re.sub(r"https://x-access-token:[^@]+@", "https://", u)
    u = re.sub(r"https://[^:/@]+@github", "https://github", u)
    return u or DEFAULT_REMOTE


def _auth_remote_url(url: str, token: str) -> str:
    clean = _clean_remote_url(url)
    parsed = urlparse(clean if "://" in clean else DEFAULT_REMOTE)
    return (
        f"https://x-access-token:{quote(token, safe='')}@"
        f"{parsed.netloc}{parsed.path}"
    )


def pull_latest(repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Hard-sync the clone to ``origin/master`` for agent job branches.

    Important: the Pi schedule branch is often ``work/holistic`` (many commits
    ahead of master). A plain ``git pull --rebase origin master`` *from that
    branch* tries to rebase the whole history onto master and explodes in
    conflicts. Agent work must always start from a clean ``origin/master`` tip.
    """
    repo = Path(repo).resolve()
    token = github_token()
    code_o, origin, _ = _run("git", "-C", str(repo), "remote", "get-url", "origin")
    clean = _clean_remote_url(origin if code_o == 0 and origin else DEFAULT_REMOTE)
    if token:
        _run(
            "git",
            "-C",
            str(repo),
            "remote",
            "set-url",
            "origin",
            _auth_remote_url(clean, token),
        )

    # Clear mid-rebase / mid-merge left by a previous failed agent tick
    if (repo / ".git" / "rebase-merge").exists() or (repo / ".git" / "rebase-apply").exists():
        _run("git", "-C", str(repo), "rebase", "--abort")
    if (repo / ".git" / "MERGE_HEAD").exists():
        _run("git", "-C", str(repo), "merge", "--abort")

    fetch_code, fetch_out, fetch_err = _run(
        "git", "-C", str(repo), "fetch", "origin", "master", timeout=180
    )
    # Force master to match remote tip (no rebase of schedule-branch history)
    code, out, err = _run(
        "git",
        "-C",
        str(repo),
        "checkout",
        "-B",
        "master",
        "origin/master",
        timeout=60,
    )
    if code != 0:
        # dirty tree blocking checkout — throw away local junk for unattended agent
        _run("git", "-C", str(repo), "reset", "--hard")
        _run("git", "-C", str(repo), "clean", "-fd")
        code, out, err = _run(
            "git",
            "-C",
            str(repo),
            "checkout",
            "-B",
            "master",
            "origin/master",
            timeout=60,
        )

    _run("git", "-C", str(repo), "remote", "set-url", "origin", clean)
    return {
        "ok": code == 0 and fetch_code == 0,
        "checkout": {"code": code, "out": (out or "")[:200], "err": (err or "")[:300]},
        "fetch": {
            "code": fetch_code,
            "out": (fetch_out or "")[:200],
            "err": (fetch_err or "")[:200],
        },
        "pull": {
            "code": code,
            "out": "hard-sync origin/master (no rebase of schedule branch)",
            "err": (err or "")[:300],
        },
    }


def create_job_branch(item: dict[str, Any], job_id: str, repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Create work/auto/<slug>-<id> from a clean origin/master tip."""
    area = (item.get("area") or "auto").strip() or "auto"
    short = (job_id or "job").replace("job-", "")[:8]
    slug = _slug(item.get("title") or "task")
    branch = f"work/auto/{slug}-{short}"
    # start from master tip (hard-sync — never rebase schedule-branch history)
    pl = pull_latest(repo)
    if not pl.get("ok"):
        return {
            "ok": False,
            "error": (pl.get("checkout") or {}).get("err")
            or (pl.get("pull") or {}).get("err")
            or "pull_latest failed",
            "branch": branch,
            "pull": pl,
        }
    code, _, err = _run(
        "git", "-C", str(repo), "checkout", "-B", branch, "origin/master"
    )
    if code != 0:
        sw = start_work(area if area != "auto" else "misc", repo=repo, from_branch="master")
        if not sw.get("ok"):
            return {"ok": False, "error": err or sw.get("error"), "branch": branch}
        branch = sw.get("branch") or branch
        return {"ok": True, "branch": branch, "created": sw.get("created"), "via": "start_work"}
    return {"ok": True, "branch": branch, "created": True, "via": "checkout -B origin/master"}


def build_agent_prompt(item: dict[str, Any], *, seed_path: str = "", job_id: str = "") -> str:
    title = item.get("title") or "Backlog task"
    mvp = item.get("mvp_scope") or "(define a minimal shippable slice)"
    notes = item.get("notes") or ""
    desc = item.get("description") or ""
    area = item.get("area") or "misc"
    return (
        f"You are running unattended on a 24/7 worker for personal-workspace.\n"
        f"Your output must be the **implemented MVP** as real project files — "
        f"not a plan, seed, or prompt file.\n\n"
        f"## Task\n"
        f"**Title:** {title}\n"
        f"**Priority:** {item.get('priority')}\n"
        f"**Area:** {area}\n"
        f"**MVP (ship this):** {mvp}\n"
        f"**Description:** {desc}\n"
        f"**Notes:** {notes}\n"
        f"**Backlog id:** {item.get('id')}\n"
        f"**Job id:** {job_id}\n"
        f"**Seed path (reference only — do not treat writing seeds as done):** "
        f"{seed_path or 'n/a'}\n\n"
        f"## Required outcome\n"
        f"1. Implement the MVP under an appropriate project directory "
        f"(e.g. a new folder or existing area for `{area}`).\n"
        f"2. Include enough code/HTML/config that a human can open or run it.\n"
        f"3. Add a short README in the project folder with how to run/verify.\n"
        f"4. Prefer tests when they fit; otherwise leave clear verify steps.\n\n"
        f"## Rules\n"
        f"1. Stay on the current git branch; make file changes in the working tree.\n"
        f"2. Do **not** only update `ops/backlog/**` seeds/prompts — that is scaffolding.\n"
        f"3. Implement the MVP only; avoid drive-by refactors.\n"
        f"4. Prefer small, reviewable diffs.\n"
        f"5. When done, summarize files changed and how to verify.\n"
        f"6. Do not print secrets or tokens.\n"
    )


def is_scaffold_path(path: str) -> bool:
    """Paths that must not alone count as 'implemented work' for a PR."""
    p = (path or "").replace("\\", "/").lstrip("./")
    if p.startswith("ops/backlog/seeds/"):
        return True
    if p.startswith("ops/backlog/reports/"):
        return True
    if p.startswith("ops/session-index/"):
        return True
    if p in {
        "ops/backlog/items.json",
        "ops/backlog/jobs.json",
        "ops/backlog/scheduler.json",
        "ops/backlog/suggestions.json",
        "ops/backlog/workflow-session.json",
    }:
        return True
    return False


def split_dirty_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    impl = [p for p in paths if not is_scaffold_path(p)]
    scaffold = [p for p in paths if is_scaffold_path(p)]
    return impl, scaffold


def _rev_parse(repo: Path, rev: str = "HEAD") -> Optional[str]:
    code, out, _ = _run("git", "-C", str(repo), "rev-parse", rev)
    return out.strip() if code == 0 and out else None


def changed_paths_since(repo: Path, base_sha: str) -> list[str]:
    """Files changed by commits after *base_sha* plus uncommitted dirty paths.

    Headless agents often commit mid-session (Agents.md auto-protect), so the
    working tree can be clean while the branch still has real work.
    """
    repo = Path(repo).resolve()
    paths: list[str] = []
    seen: set[str] = set()
    if base_sha:
        code, out, _ = _run(
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            f"{base_sha}...HEAD",
        )
        if code == 0 and out:
            for line in out.splitlines():
                p = line.strip()
                if p and p not in seen:
                    seen.add(p)
                    paths.append(p)
    for p in dirty_paths(repo):
        if p not in seen:
            seen.add(p)
            paths.append(p)
    return paths


def commits_ahead(repo: Path, base_sha: str) -> int:
    if not base_sha:
        return 0
    code, out, _ = _run(
        "git", "-C", str(repo), "rev-list", "--count", f"{base_sha}..HEAD"
    )
    if code != 0:
        return 0
    try:
        return int((out or "0").strip() or "0")
    except ValueError:
        return 0


def grok_auth_status() -> dict[str, Any]:
    """Best-effort check that Grok CLI can authenticate (auth.json / env)."""
    auth = Path.home() / ".grok" / "auth.json"
    info: dict[str, Any] = {
        "ok": False,
        "has_auth_file": auth.is_file(),
        "has_xai_api_key": bool((os.environ.get("XAI_API_KEY") or "").strip()),
        "auth_path": str(auth) if auth.is_file() else None,
    }
    if info["has_xai_api_key"]:
        info["ok"] = True
        info["via"] = "XAI_API_KEY"
        return info
    if not auth.is_file():
        info["error"] = "no ~/.grok/auth.json and no XAI_API_KEY"
        info["hint"] = (
            "On Mac: grok login. Copy ~/.grok/auth.json to the Pi "
            "(chmod 600), or set XAI_API_KEY in ~/.config/workflow-scheduler.env"
        )
        return info
    try:
        data = json.loads(auth.read_text(encoding="utf-8"))
        expires = None
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, dict) and v.get("expires_at"):
                    expires = v.get("expires_at")
                    break
        info["expires_at"] = expires
        if expires:
            try:
                exp = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp <= datetime.now(timezone.utc):
                    info["error"] = f"auth expired at {expires}"
                    info["hint"] = (
                        "Refresh on a logged-in Mac (`grok login`) and scp "
                        "~/.grok/auth.json to the Pi"
                    )
                    return info
            except (TypeError, ValueError):
                pass
        info["ok"] = True
        info["via"] = "auth.json"
        return info
    except (OSError, json.JSONDecodeError) as e:
        info["error"] = str(e)
        return info


def run_headless_agent(
    prompt: str,
    *,
    repo: Path = WORKSPACE_ROOT,
    max_turns: int = MAX_AGENT_TURNS,
) -> dict[str, Any]:
    """Run Grok Build headless if installed."""
    load_scheduler_env()  # XAI_API_KEY / PATH from Pi env file when not in systemd
    grok = which_grok()
    if not grok:
        return {
            "ok": False,
            "skipped": True,
            "error": "grok CLI not installed on this host",
            "hint": "Install Grok Build on the Pi, or auto-claim on Mac Terminal",
        }
    auth = grok_auth_status()
    if not auth.get("ok"):
        return {
            "ok": False,
            "skipped": True,
            "error": auth.get("error") or "Grok not authenticated",
            "hint": auth.get("hint")
            or (
                "On Pi: grok login --device-auth. "
                "On Mac: bash projects-dashboard/sync_pi_grok_auth.sh"
            ),
            "auth": auth,
        }
    # Headless single-session with auto-approve for unattended work
    cmd = [
        grok,
        "--cwd",
        str(repo),
        "--single",
        prompt,
        "--max-turns",
        str(max_turns),
        "--always-approve",
    ]
    code, out, err = _run(*cmd, cwd=repo, timeout=3600)
    combined = f"{out or ''}\n{err or ''}"
    auth_fail = any(
        s in combined.lower()
        for s in (
            "not signed in",
            "authenticate",
            "grok login",
            "xai_api_key",
            "unauthorized",
            "401",
        )
    )
    ok = code == 0 and not auth_fail
    error = None
    if not ok:
        error = (err or out or f"exit {code}").strip()
        if auth_fail and "not signed in" in combined.lower():
            error = (
                "Grok not signed in on this host. "
                "Refresh ~/.grok/auth.json from a logged-in Mac or set XAI_API_KEY."
            )
    return {
        "ok": ok,
        "code": code,
        "method": "grok --single --always-approve",
        "stdout_tail": (out or "")[-2000:],
        "stderr_tail": (err or "")[-1000:],
        "error": error,
        "auth": auth,
    }


def push_branch(branch: str, repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    token = github_token()
    code_o, origin, _ = _run("git", "-C", str(repo), "remote", "get-url", "origin")
    remote_url = origin if code_o == 0 else DEFAULT_REMOTE
    clean = re.sub(r"https://x-access-token:[^@]+@", "https://", remote_url)
    clean = re.sub(r"https://[^:/@]+@github", "https://github", clean)
    if token and clean.startswith("https://"):
        parsed = urlparse(clean)
        auth = (
            f"https://x-access-token:{quote(token, safe='')}@"
            f"{parsed.netloc}{parsed.path}"
        )
        _run("git", "-C", str(repo), "remote", "set-url", "origin", auth)
    code, out, err = _run(
        "git",
        "-C",
        str(repo),
        "push",
        "-u",
        "origin",
        branch,
        timeout=180,
    )
    _run("git", "-C", str(repo), "remote", "set-url", "origin", clean)
    return {
        "ok": code == 0,
        "code": code,
        "out": out[:400],
        "error": None if code == 0 else (err or out),
    }


def parse_pr_number(pr_url: Optional[str] = None, pr_number: Any = None) -> Optional[int]:
    if pr_number is not None:
        try:
            return int(pr_number)
        except (TypeError, ValueError):
            pass
    if not pr_url:
        return None
    m = re.search(r"/pull/(\d+)", str(pr_url))
    if m:
        return int(m.group(1))
    return None


def fetch_pull_request(
    *,
    pr_url: Optional[str] = None,
    pr_number: Any = None,
    head: Optional[str] = None,
    repo_slug: str = "cvolkernick/personal-workspace",
) -> dict[str, Any]:
    """Fetch PR state from GitHub (merged / open / closed)."""
    token = github_token()
    if not token:
        return {"ok": False, "error": "GITHUB_TOKEN not set"}

    num = parse_pr_number(pr_url, pr_number)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "personal-workspace-scheduler",
    }

    def _get(url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "data": data}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:400]
            return {"ok": False, "error": f"HTTP {e.code}: {body}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    if num:
        out = _get(f"https://api.github.com/repos/{repo_slug}/pulls/{num}")
        if not out.get("ok"):
            return out
        data = out["data"]
        return {
            "ok": True,
            "number": data.get("number"),
            "url": data.get("html_url"),
            "state": data.get("state"),  # open | closed
            "merged": bool(data.get("merged")),
            "merged_at": data.get("merged_at"),
            "title": data.get("title"),
            "head": (data.get("head") or {}).get("ref"),
            "base": (data.get("base") or {}).get("ref"),
        }

    # Fallback: find PR by head branch
    if head:
        owner = repo_slug.split("/")[0]
        ref = head if ":" in head else f"{owner}:{head}"
        out = _get(
            f"https://api.github.com/repos/{repo_slug}/pulls"
            f"?state=all&head={quote(ref, safe=':')}&per_page=5"
        )
        if not out.get("ok"):
            return out
        arr = out.get("data") or []
        if not isinstance(arr, list) or not arr:
            return {"ok": False, "error": f"no PR found for head {head}"}
        data = arr[0]
        return {
            "ok": True,
            "number": data.get("number"),
            "url": data.get("html_url"),
            "state": data.get("state"),
            "merged": bool(data.get("merged")),
            "merged_at": data.get("merged_at"),
            "title": data.get("title"),
            "head": (data.get("head") or {}).get("ref"),
            "base": (data.get("base") or {}).get("ref"),
        }

    return {"ok": False, "error": "need pr_url, pr_number, or head branch"}


def create_pull_request(
    *,
    title: str,
    body: str,
    head: str,
    base: str = "master",
    repo_slug: str = "cvolkernick/personal-workspace",
) -> dict[str, Any]:
    """Open a PR via GitHub REST API (no gh CLI required)."""
    token = github_token()
    if not token:
        return {
            "ok": False,
            "error": "GITHUB_TOKEN not set — cannot create PR",
            "hint": "Export GITHUB_TOKEN on the Pi (~/.config/workflow-scheduler.env)",
        }
    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_slug}/pulls",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "personal-workspace-scheduler",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "ok": True,
            "number": data.get("number"),
            "url": data.get("html_url"),
            "state": data.get("state"),
            "api": data.get("url"),
        }
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", errors="replace")[:800]
        # 422 often means PR already exists
        if e.code == 422 and "already exists" in body_txt.lower():
            # try to find existing
            q = urllib.request.Request(
                f"https://api.github.com/repos/{repo_slug}/pulls?head="
                f"{repo_slug.split('/')[0]}:{head}&state=open",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "personal-workspace-scheduler",
                },
            )
            try:
                with urllib.request.urlopen(q, timeout=30) as resp:
                    arr = json.loads(resp.read().decode("utf-8"))
                if arr:
                    return {
                        "ok": True,
                        "number": arr[0].get("number"),
                        "url": arr[0].get("html_url"),
                        "state": arr[0].get("state"),
                        "existing": True,
                    }
            except Exception:
                pass
        hint = None
        if e.code == 403:
            hint = (
                "GITHUB_TOKEN needs Contents:write + Pull requests:write "
                "(fine-grained) or classic scope `repo`. Re-issue the token "
                "and update ~/.config/workflow-scheduler.env on the Pi."
            )
        return {
            "ok": False,
            "error": f"HTTP {e.code}: {body_txt}",
            "hint": hint,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _current_branch(repo: Path) -> Optional[str]:
    code, out, _ = _run("git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not out or out == "HEAD":
        return None
    return out.strip()


def _git_has_unmerged(repo: Path) -> bool:
    code, out, _ = _run("git", "-C", str(repo), "diff", "--name-only", "--diff-filter=U")
    return code == 0 and bool((out or "").strip())


def _restore_branch(repo: Path, branch: Optional[str]) -> dict[str, Any]:
    """Return the workspace to the schedule/integration branch after agent work.

    Pi systemd post-steps commit ``ops/backlog`` on ``work/holistic`` (or similar).
    Agent work uses ``work/auto/*`` from master — without restore, post-push fails
    or leaves the clone detached from the schedule branch.

    Preserves uncommitted ``ops/backlog/**`` (jobs, items, reports) across the
    checkout so tick results are not discarded by ``checkout -f``.
    """
    if not branch:
        return {"ok": False, "skipped": True, "reason": "no home branch"}

    stash_ref = None
    # Stage/stash only ops/backlog so job status + auto_start flags survive
    _run("git", "-C", str(repo), "add", "-A", "ops/backlog")
    code_s, out_s, _ = _run(
        "git",
        "-C",
        str(repo),
        "stash",
        "push",
        "-m",
        "agent-jobs: preserve ops/backlog",
        "--",
        "ops/backlog",
    )
    if code_s == 0 and "No local changes" not in (out_s or ""):
        stash_ref = "stash@{0}"

    code, _out, err = _run("git", "-C", str(repo), "checkout", "-f", branch)
    via = "checkout -f"
    if code != 0:
        _run("git", "-C", str(repo), "fetch", "origin", branch, timeout=120)
        code, _out, err = _run(
            "git", "-C", str(repo), "checkout", "-B", branch, f"origin/{branch}"
        )
        via = "origin-reset"

    stash_pop = None
    if stash_ref:
        code_p, out_p, err_p = _run(
            "git", "-C", str(repo), "stash", "pop", stash_ref
        )
        stash_pop = {
            "ok": code_p == 0,
            "out": (out_p or "")[:200],
            "error": None if code_p == 0 else (err_p or out_p),
        }
        # If pop conflicts, keep stash contents in working tree best-effort
        if code_p != 0:
            _run("git", "-C", str(repo), "checkout", "--theirs", "ops/backlog")
            _run("git", "-C", str(repo), "add", "ops/backlog")

    return {
        "ok": code == 0,
        "branch": branch,
        "via": via,
        "stash_pop": stash_pop,
        "error": None if code == 0 else err,
    }


def execute_agent_job(
    item_id: str,
    *,
    job_id: str,
    repo: Path = WORKSPACE_ROOT,
    try_spawn_prepare: bool = True,
) -> dict[str, Any]:
    """Full unattended pipeline for one backlog item.

    Steps: ensure git → pull → branch → prepare seed → headless agent →
    protect/commit → push → open PR → restore schedule branch.
    """
    repo = Path(repo).resolve()
    steps: list[dict[str, Any]] = []
    item = get_item(item_id)
    if not item:
        return {"ok": False, "error": "backlog item not found", "steps": steps}

    home_branch = _current_branch(repo) or "work/holistic"

    # Refuse to start on a broken index (mid-rebase / conflict)
    if _git_has_unmerged(repo):
        return {
            "ok": False,
            "error": "git has unmerged paths — resolve or reset the Pi clone",
            "steps": steps,
            "home_branch": home_branch,
        }

    # 1) git
    eg = ensure_git_repo(repo)
    steps.append({"step": "ensure_git", **{k: eg.get(k) for k in ("ok", "error", "cloned", "initialized")}})
    if not eg.get("ok"):
        return {"ok": False, "error": eg.get("error") or "git unavailable", "steps": steps}

    pl = pull_latest(repo)
    steps.append({"step": "pull", "ok": pl.get("ok"), "detail": pl.get("pull")})
    if not pl.get("ok"):
        _restore_branch(repo, home_branch)
        return {
            "ok": False,
            "error": (pl.get("checkout") or {}).get("err")
            or (pl.get("pull") or {}).get("err")
            or "pull_latest failed",
            "steps": steps,
            "home_branch": home_branch,
        }

    # 2) branch
    br = create_job_branch(item, job_id, repo=repo)
    steps.append({"step": "branch", **br})
    if not br.get("ok"):
        _restore_branch(repo, home_branch)
        return {
            "ok": False,
            "error": br.get("error") or "branch failed",
            "steps": steps,
            "branch": br.get("branch"),
        }
    branch = br["branch"]
    base_sha = _rev_parse(repo, "HEAD")  # tip before agent (should be origin/master)
    result: dict[str, Any]

    try:
        # 3) Optional seed files for Mac claim fallback — never count as MVP work.
        # Prefer in-memory implement-MVP prompt so PRs are not seed-only.
        seed_path = ""
        prompt_path = ""
        launch_script = ""
        if try_spawn_prepare:
            init = initiate_item(item_id, try_spawn_grok=False)
            steps.append(
                {
                    "step": "initiate_seed",
                    "ok": init.get("ok"),
                    "seed_path": init.get("seed_path"),
                }
            )
            if init.get("ok"):
                seed_path = init.get("seed_path") or ""
                prompt_path = init.get("prompt_path") or ""
                launch_script = init.get("launch_script") or ""
                item = get_item(item_id) or item

        # Always use implementation-focused prompt (not interactive /goal seed text)
        prompt = build_agent_prompt(item, seed_path=seed_path, job_id=job_id)

        # 4) agent — must succeed for a real PR
        agent = run_headless_agent(prompt, repo=repo)
        steps.append(
            {
                "step": "agent",
                "ok": agent.get("ok"),
                "skipped": agent.get("skipped"),
                "method": agent.get("method"),
                "error": agent.get("error"),
            }
        )

        # Ensure we are still on the job branch (agent may have switched)
        _run("git", "-C", str(repo), "checkout", branch)

        # Include commits the agent made mid-session (not only uncommitted dirt)
        all_changed = changed_paths_since(repo, base_sha or "")
        impl_files, scaffold_files = split_dirty_paths(all_changed)
        ahead = commits_ahead(repo, base_sha or "")
        steps.append(
            {
                "step": "classify_changes",
                "ok": True,
                "base_sha": (base_sha or "")[:12],
                "commits_ahead": ahead,
                "implementation": impl_files,
                "scaffold": scaffold_files,
                "uncommitted": dirty_paths(repo),
            }
        )

        if not agent.get("ok") and not impl_files:
            result = {
                "ok": False,
                "error": agent.get("error") or "agent failed",
                "needs_terminal": True,
                "branch": branch,
                "seed_path": seed_path,
                "launch_script": launch_script,
                "prompt_path": prompt_path,
                "implementation_files": impl_files,
                "steps": steps,
                "hint": agent.get("hint")
                or "Fix Grok auth on the worker, or claim on Mac Terminal",
            }
            return result

        if not impl_files:
            result = {
                "ok": False,
                "error": (
                    "agent finished without implementation files "
                    "(only seed/scaffold or no changes) — refusing empty/scaffold PR"
                ),
                "needs_terminal": True,
                "branch": branch,
                "seed_path": seed_path,
                "launch_script": launch_script,
                "prompt_path": prompt_path,
                "implementation_files": [],
                "scaffold_files": scaffold_files,
                "steps": steps,
            }
            return result

        # Agent may have already committed implementation; commit any leftover impl dirt.
        # Never stage scaffold-only paths into the PR commit.
        uncommitted = dirty_paths(repo)
        uncommitted_impl, uncommitted_scaffold = split_dirty_paths(uncommitted)
        commit_result: dict[str, Any] = {
            "ok": True,
            "committed": ahead > 0 and not uncommitted_impl,
            "already_committed_by_agent": ahead > 0,
            "commits_ahead_before": ahead,
            "dirty_impl": uncommitted_impl,
        }
        if uncommitted_impl:
            msg = f"auto({job_id}): {item.get('title') or item_id}"
            for p in uncommitted_scaffold:
                _run("git", "-C", str(repo), "restore", "--staged", "--worktree", "--", p)
            for p in uncommitted_impl:
                _run("git", "-C", str(repo), "add", "--", p)
            code_c, _out_c, err_c = _run(
                "git", "-C", str(repo), "commit", "-m", msg, timeout=120
            )
            if code_c != 0:
                prot = protect_work(
                    repo,
                    message=msg,
                    push=False,
                    ensure_work_branch=False,
                )
                commit_result.update(
                    {
                        "ok": bool(prot.get("ok")),
                        "committed": bool(prot.get("committed")),
                        "sha": prot.get("sha"),
                        "error": prot.get("error"),
                        "via": "protect_work",
                    }
                )
            else:
                code_s, sha, _ = _run(
                    "git", "-C", str(repo), "rev-parse", "--short", "HEAD"
                )
                commit_result.update(
                    {
                        "ok": True,
                        "committed": True,
                        "sha": sha if code_s == 0 else None,
                        "via": "git commit",
                    }
                )
            _run("git", "-C", str(repo), "checkout", branch)
            # refresh file list after commit
            all_changed = changed_paths_since(repo, base_sha or "")
            impl_files, scaffold_files = split_dirty_paths(all_changed)
        steps.append({"step": "commit", **commit_result})

        ahead = commits_ahead(repo, base_sha or "")
        if ahead < 1 and not dirty_paths(repo):
            # Should not happen if impl_files non-empty, but be safe
            result = {
                "ok": False,
                "error": "implementation detected but nothing to push",
                "branch": branch,
                "implementation_files": impl_files,
                "steps": steps,
            }
            return result

        # 6) push (agent may have already pushed; still ensure remote is up to date)
        push = push_branch(branch, repo=repo)
        steps.append({"step": "push", **push})
        if not push.get("ok"):
            # If remote already has the branch tip, treat as ok
            code_r, rem, _ = _run(
                "git", "-C", str(repo), "ls-remote", "--heads", "origin", branch
            )
            if code_r != 0 or not (rem or "").strip():
                result = {
                    "ok": False,
                    "error": push.get("error") or "push failed",
                    "branch": branch,
                    "implementation_files": impl_files,
                    "steps": steps,
                    "partial": True,
                }
                return result
            steps[-1]["remote_exists"] = True
            steps[-1]["ok"] = True

        # 7) PR — only with implementation files (committed and/or pushed)
        files_list = "\n".join(f"- `{p}`" for p in impl_files[:40])
        pr_body = (
            f"## Autonomous implementation\n\n"
            f"- **Backlog:** {item.get('title')}\n"
            f"- **Job:** `{job_id}`\n"
            f"- **Branch:** `{branch}`\n"
            f"- **MVP:** {item.get('mvp_scope') or 'n/a'}\n\n"
            f"### Files (implementation)\n"
            f"{files_list or '_see diff_'}\n\n"
            f"### Agent\n"
            f"- method: {agent.get('method') or 'n/a'}\n"
            f"- ok: {agent.get('ok')}\n"
            f"- commits_ahead_of_base: {ahead}\n\n"
            f"### Verify\n"
            f"Review the diff, run tests if applicable, then approve/merge.\n\n"
            f"_Opened by workflow scheduler agent_jobs on {_now()}_\n"
        )
        pr = create_pull_request(
            title=f"[auto] {item.get('title') or job_id}",
            body=pr_body,
            head=branch,
            base="master",
        )
        steps.append(
            {"step": "pr", **{k: pr.get(k) for k in ("ok", "url", "number", "error", "existing")}}
        )

        if pr.get("ok"):
            update_item(
                item_id,
                {
                    "status": "planning",
                    "last_pr_url": pr.get("url"),
                    "last_job_id": job_id,
                },
            )
            result = {
                "ok": True,
                "branch": branch,
                "pr_url": pr.get("url"),
                "pr_number": pr.get("number"),
                "seed_path": seed_path,
                "launch_script": launch_script,
                "implementation_files": impl_files,
                "agent": agent,
                "steps": steps,
                "status": "pr_ready",
                "message": f"PR ready: {pr.get('url')}",
            }
            return result

        result = {
            "ok": False,
            "error": pr.get("error") or "PR creation failed",
            "branch": branch,
            "pushed": True,
            "implementation_files": impl_files,
            "steps": steps,
            "partial": True,
        }
        return result
    finally:
        # Always return to schedule branch so Pi post-commit can push ops/backlog
        restored = _restore_branch(repo, home_branch)
        steps.append({"step": "restore_home_branch", **restored})
