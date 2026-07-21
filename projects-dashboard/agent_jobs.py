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
    _run("git", "-C", str(repo), "fetch", "origin", "master", timeout=180)
    code, _out, err = _run("git", "-C", str(repo), "checkout", "master", timeout=60)
    code2, out2, err2 = _run(
        "git",
        "-C",
        str(repo),
        "pull",
        "--rebase",
        "--autostash",
        "origin",
        "master",
        timeout=180,
    )
    _run("git", "-C", str(repo), "remote", "set-url", "origin", clean)
    return {
        "ok": code2 == 0,
        "checkout": {"code": code, "err": err},
        "pull": {"code": code2, "out": (out2 or "")[:300], "err": (err2 or "")[:300]},
    }


def create_job_branch(item: dict[str, Any], job_id: str, repo: Path = WORKSPACE_ROOT) -> dict[str, Any]:
    """Create work/auto/<slug>-<id> from master."""
    area = (item.get("area") or "auto").strip() or "auto"
    short = (job_id or "job").replace("job-", "")[:8]
    slug = _slug(item.get("title") or "task")
    branch = f"work/auto/{slug}-{short}"
    # start from master tip
    pull_latest(repo)
    # if area-based start_work is cleaner for known areas:
    code, _, err = _run("git", "-C", str(repo), "checkout", "-B", branch, "master")
    if code != 0:
        # try start_work fallback
        sw = start_work(area if area != "auto" else "misc", repo=repo, from_branch="master")
        if not sw.get("ok"):
            return {"ok": False, "error": err or sw.get("error"), "branch": branch}
        # rename? keep work/<area> if start_work succeeded
        branch = sw.get("branch") or branch
        return {"ok": True, "branch": branch, "created": sw.get("created"), "via": "start_work"}
    return {"ok": True, "branch": branch, "created": True, "via": "checkout -B"}


def build_agent_prompt(item: dict[str, Any], *, seed_path: str = "", job_id: str = "") -> str:
    title = item.get("title") or "Backlog task"
    mvp = item.get("mvp_scope") or "(define a minimal shippable slice)"
    notes = item.get("notes") or ""
    desc = item.get("description") or ""
    return (
        f"You are running unattended on a 24/7 worker for personal-workspace.\n"
        f"Implement this backlog item and leave durable git changes ready to commit.\n\n"
        f"## Task\n"
        f"**Title:** {title}\n"
        f"**Priority:** {item.get('priority')}\n"
        f"**Area:** {item.get('area') or 'misc'}\n"
        f"**MVP:** {mvp}\n"
        f"**Description:** {desc}\n"
        f"**Notes:** {notes}\n"
        f"**Backlog id:** {item.get('id')}\n"
        f"**Job id:** {job_id}\n"
        f"**Seed (if any):** {seed_path}\n\n"
        f"## Rules\n"
        f"1. Stay on the current git branch; do not push yourself if tools forbid it — "
        f"still make commits if protect/sync helpers exist.\n"
        f"2. Implement the MVP only; avoid drive-by refactors.\n"
        f"3. Run tests relevant to your changes when feasible.\n"
        f"4. Prefer small, reviewable diffs.\n"
        f"5. When done, summarize files changed and how to verify.\n"
        f"6. Do not print secrets or tokens.\n"
    )


def run_headless_agent(
    prompt: str,
    *,
    repo: Path = WORKSPACE_ROOT,
    max_turns: int = MAX_AGENT_TURNS,
) -> dict[str, Any]:
    """Run Grok Build headless if installed."""
    grok = which_grok()
    if not grok:
        return {
            "ok": False,
            "skipped": True,
            "error": "grok CLI not installed on this host",
            "hint": "Install Grok Build on the Pi, or auto-claim on Mac Terminal",
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
    return {
        "ok": code == 0,
        "code": code,
        "method": "grok --single --always-approve",
        "stdout_tail": (out or "")[-2000:],
        "stderr_tail": (err or "")[-1000:],
        "error": None if code == 0 else (err or out or f"exit {code}"),
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
        return {"ok": False, "error": f"HTTP {e.code}: {body_txt}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def execute_agent_job(
    item_id: str,
    *,
    job_id: str,
    repo: Path = WORKSPACE_ROOT,
    try_spawn_prepare: bool = True,
) -> dict[str, Any]:
    """Full unattended pipeline for one backlog item.

    Steps: ensure git → pull → branch → prepare seed → headless agent →
    protect/commit → push → open PR.
    """
    repo = Path(repo).resolve()
    steps: list[dict[str, Any]] = []
    item = get_item(item_id)
    if not item:
        return {"ok": False, "error": "backlog item not found", "steps": steps}

    # 1) git
    eg = ensure_git_repo(repo)
    steps.append({"step": "ensure_git", **{k: eg.get(k) for k in ("ok", "error", "cloned", "initialized")}})
    if not eg.get("ok"):
        return {"ok": False, "error": eg.get("error") or "git unavailable", "steps": steps}

    pl = pull_latest(repo)
    steps.append({"step": "pull", "ok": pl.get("ok"), "detail": pl.get("pull")})

    # 2) branch
    br = create_job_branch(item, job_id, repo=repo)
    steps.append({"step": "branch", **br})
    if not br.get("ok"):
        return {"ok": False, "error": br.get("error") or "branch failed", "steps": steps, "branch": br.get("branch")}
    branch = br["branch"]

    # 3) prepare seed (no terminal spawn)
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

    prompt = build_agent_prompt(item, seed_path=seed_path, job_id=job_id)
    if prompt_path:
        try:
            # Prefer full goal prompt from initiate
            pfile = repo / prompt_path
            if pfile.is_file():
                prompt = pfile.read_text(encoding="utf-8")
        except OSError:
            pass

    # 4) agent
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

    # 5) commit whatever changed (even partial progress)
    dirty = dirty_paths(repo)
    commit_result: dict[str, Any] = {"ok": True, "dirty": dirty, "committed": False}
    if dirty:
        msg = f"auto({job_id}): {item.get('title') or item_id}"
        prot = protect_work(
            repo,
            message=msg,
            push=False,
            ensure_work_branch=False,
        )
        commit_result = {
            "ok": bool(prot.get("ok")),
            "committed": bool(prot.get("committed")),
            "sha": prot.get("sha"),
            "error": prot.get("error"),
            "dirty_before": dirty,
        }
        # ensure we're still on job branch
        _run("git", "-C", str(repo), "checkout", branch)
    steps.append({"step": "commit", **commit_result})

    if agent.get("skipped") and not dirty:
        # Nothing the agent could do and no local edits
        return {
            "ok": False,
            "error": agent.get("error") or "agent unavailable and no changes",
            "needs_terminal": True,
            "branch": branch,
            "seed_path": seed_path,
            "launch_script": launch_script,
            "prompt_path": prompt_path,
            "steps": steps,
        }

    if not agent.get("ok") and not dirty and not commit_result.get("committed"):
        return {
            "ok": False,
            "error": agent.get("error") or "agent failed with no commits",
            "needs_terminal": True,
            "branch": branch,
            "seed_path": seed_path,
            "launch_script": launch_script,
            "steps": steps,
        }

    # 6) push
    push = push_branch(branch, repo=repo)
    steps.append({"step": "push", **push})
    if not push.get("ok"):
        return {
            "ok": False,
            "error": push.get("error") or "push failed",
            "branch": branch,
            "steps": steps,
            "partial": True,
        }

    # 7) PR
    pr_body = (
        f"## Autonomous job\n\n"
        f"- **Backlog:** {item.get('title')}\n"
        f"- **Job:** `{job_id}`\n"
        f"- **Branch:** `{branch}`\n"
        f"- **MVP:** {item.get('mvp_scope') or 'n/a'}\n\n"
        f"### Agent\n"
        f"- method: {agent.get('method') or 'n/a'}\n"
        f"- ok: {agent.get('ok')}\n\n"
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
    steps.append({"step": "pr", **{k: pr.get(k) for k in ("ok", "url", "number", "error", "existing")}})

    if pr.get("ok"):
        update_item(item_id, {"status": "planning", "last_pr_url": pr.get("url"), "last_job_id": job_id})
        return {
            "ok": True,
            "branch": branch,
            "pr_url": pr.get("url"),
            "pr_number": pr.get("number"),
            "seed_path": seed_path,
            "launch_script": launch_script,
            "agent": agent,
            "steps": steps,
            "status": "pr_ready",
            "message": f"PR ready: {pr.get('url')}",
        }

    return {
        "ok": False,
        "error": pr.get("error") or "PR creation failed",
        "branch": branch,
        "pushed": True,
        "steps": steps,
        "partial": True,
    }
