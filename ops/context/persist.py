#!/usr/bin/env python3
"""Agent context persistence (#580): backup, restore, drift, contradict.

Backup is git-shaped and pushes to a **private** remote (not this public
monorepo). Secrets are refused on the way in and the way out.

Subcommands:
  backup      copy allowlisted home context → private git remote
  restore     private remote → dest (fresh VM)
  drift       SoT pair diffs; clean runs stay silent
  contradict  MEMORY.md / memory/ duplicate-fact scan (optional --apply)
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent


def _load_exclude():
    path = ROOT / "exclude.py"
    spec = importlib.util.spec_from_file_location("ctx_exclude", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ctx_exclude"] = mod
    spec.loader.exec_module(mod)
    return mod


ex = _load_exclude()

DEFAULT_INCLUDE = (
    "MEMORY.md",
    "USER.md",
    "AGENTS.md",
    "TOOLS.md",
    "SOUL.md",
    "CONTEXT.md",
    "memory/",
    "goals/",
    "skills/",
    "cron.d/",
    "workspace/goals/",
    "workspace/skills/",
    "workspace/cron.d/",
)
DEFAULT_GROK_INCLUDE = ("skills/", "memory/", "workflows/")
GROK_SKIP_TOP = {
    "bundled",
    "sessions",
    "mcp_credentials.json",
    "auth.json",
    "config.toml",
    "logs",
    "downloads",
    "vendor",
    "marketplace-cache",
    "installed-plugins",
}
FACT_RE = re.compile(
    r"^\s*[-*]\s+(?:\*\*(.+?)\*\*|`(.+?)`|([^:]+))\s*:\s*(.+?)\s*$"
)
FRONT_RE = re.compile(r"^---\n(.*?)\n---", re.S)
PWA_ASSETS = (
    "financial-command/manifest.webmanifest",
    "financial-command/sw.js",
    "financial-command/pwa.js",
    "financial-command/favicon.svg",
    "financial-command/favicon.ico",
    "financial-command/favicon-32.png",
    "financial-command/apple-touch-icon.png",
    "financial-command/icon-192.png",
    "financial-command/icon-512.png",
)


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p))).resolve()


def load_config(path: Optional[Path]) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def git(cwd: Path, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=capture,
        text=True,
    )


def notify_failure(message: str, *, config: dict[str, Any], out: Optional[Path]) -> None:
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "FAILURE.txt").write_text(message + "\n", encoding="utf-8")
    hook = config.get("notify_hook")
    if hook:
        cmd = list(hook) if isinstance(hook, list) else [hook]
        cmd.append(message)
        subprocess.run(cmd, check=False)
    if config.get("post_buzz"):
        channel = config.get("channel") or "db0e8f97-0c81-4976-b299-1c460b87134e"
        body = f"**context-backup failed**\n\n{message}"
        subprocess.run(
            [
                "buzz",
                "messages",
                "send",
                "--channel",
                str(channel),
                "--content",
                body,
            ],
            check=False,
        )
    print(message, file=sys.stderr)


def collect_from_home(home: Path, includes: Iterable[str]) -> list[tuple[Path, str]]:
    """Return (src_file, rel_posix) pairs that passed the exclude list."""
    out: list[tuple[Path, str]] = []
    if not home.exists():
        return out
    for inc in includes:
        src = home / inc
        if not src.exists():
            continue
        if src.is_file():
            c = ex.classify_file(src, root=home)
            if c.allowed:
                out.append((src, Path(inc).as_posix()))
            continue
        for path, c in ex.walk_allowed(src):
            if not c.allowed:
                continue
            rel = path.relative_to(home).as_posix()
            out.append((path, rel))
    return out


def collect_grok(grok_home: Path, includes: Iterable[str]) -> list[tuple[Path, str]]:
    if not grok_home.exists():
        return []
    out: list[tuple[Path, str]] = []
    for inc in includes:
        src = grok_home / inc
        if not src.exists():
            continue
        if src.name in GROK_SKIP_TOP:
            continue
        prefix = Path("grok") / inc
        if src.is_file():
            c = ex.classify_file(src, root=grok_home)
            if c.allowed:
                out.append((src, prefix.as_posix()))
            continue
        for path, c in ex.walk_allowed(src):
            if not c.allowed:
                continue
            rel = Path("grok") / path.relative_to(grok_home)
            out.append((path, rel.as_posix()))
    return out


def stage_copy(items: list[tuple[Path, str]], dest: Path) -> list[str]:
    copied: list[str] = []
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for src, rel in items:
        c = ex.classify_file(src)
        if not c.allowed:
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        copied.append(rel)
    return sorted(copied)


def ensure_clone(clone_dir: Path, remote: str, branch: str) -> None:
    if (clone_dir / ".git").exists():
        git(clone_dir, "remote", "set-url", "origin", remote, check=False)
        git(clone_dir, "fetch", "origin", check=False)
        git(clone_dir, "checkout", "-B", branch, check=False)
        return
    clone_dir.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "clone", "--branch", branch, remote, str(clone_dir)],
        capture_output=True,
        text=True,
    )
    if r.returncode == 0:
        return
    subprocess.run(["git", "clone", remote, str(clone_dir)], check=True, capture_output=True, text=True)
    git(clone_dir, "checkout", "-B", branch)


def cmd_backup(args: argparse.Namespace, config: dict[str, Any]) -> int:
    homes = [expand(h) for h in (args.home or config.get("context_homes") or ["~"])]
    includes = tuple(config.get("include") or DEFAULT_INCLUDE)
    grok_home = expand(args.grok_home or config.get("grok_home") or "~/.grok")
    grok_include = tuple(config.get("grok_include") or DEFAULT_GROK_INCLUDE)
    items: list[tuple[Path, str]] = []
    for home in homes:
        items.extend(collect_from_home(home, includes))
    items.extend(collect_grok(grok_home, grok_include))
    # De-dupe by rel, first wins.
    seen: set[str] = set()
    uniq: list[tuple[Path, str]] = []
    for src, rel in items:
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append((src, rel))

    staging = expand(args.staging) if args.staging else Path(args.workspace) / "ops/context/.staging"
    copied = stage_copy(uniq, staging)
    refused_note = "exclude-list enforced (vault/wallet/ssh/credentials/jobs.json)"
    print(f"backup plan: {len(copied)} files · {refused_note}")
    for rel in copied:
        print(f"  + {rel}")
    if not args.apply:
        print("dry-run (pass --apply to commit + push)")
        return 0

    remote = args.remote or config.get("remote")
    if not remote:
        notify_failure("backup --apply requires config.remote (private git URL)", config=config, out=Path(args.out) if args.out else None)
        return 2
    branch = args.branch or config.get("branch") or "main"
    clone_dir = expand(args.clone_dir or config.get("clone_dir") or "~/.cache/personal-workspace/agent-context")
    try:
        ensure_clone(clone_dir, remote, branch)
        # Replace tracked tree with staging (keep .git).
        for child in clone_dir.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for rel in copied:
            src = staging / rel
            dest = clone_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        git(clone_dir, "add", "-A")
        status = git(clone_dir, "status", "--porcelain").stdout.strip()
        if not status:
            print("backup: nothing to commit")
            return 0
        stamp = utc_now().strftime("%Y-%m-%dT%H:%MZ")
        git(
            clone_dir,
            "commit",
            "-m",
            f"chore(context): nightly backup {stamp}",
        )
        push = git(clone_dir, "push", "-u", "origin", branch, check=False)
        if push.returncode != 0:
            err = (push.stderr or push.stdout or "git push failed").strip()
            notify_failure(f"git push failed: {err}", config=config, out=Path(args.out) if args.out else None)
            return 2
        print(f"backup: pushed {len(copied)} files → {remote} ({branch})")
        return 0
    except (subprocess.CalledProcessError, OSError) as exc:
        notify_failure(f"backup failed: {exc}", config=config, out=Path(args.out) if args.out else None)
        return 2


def cmd_restore(args: argparse.Namespace, config: dict[str, Any]) -> int:
    dest = expand(args.to)
    src = expand(args.source) if args.source else None
    if src is None:
        clone_dir = expand(args.clone_dir or config.get("clone_dir") or "~/.cache/personal-workspace/agent-context")
        remote = args.remote or config.get("remote")
        if remote:
            ensure_clone(clone_dir, remote, args.branch or config.get("branch") or "main")
        src = clone_dir
    if not src.exists():
        print(f"restore source missing: {src}", file=sys.stderr)
        return 1
    copied = 0
    refused = 0
    for path, c in ex.walk_allowed(src):
        if path.name == ".git" or ".git/" in path.as_posix():
            continue
        rel = path.relative_to(src)
        if not c.allowed:
            refused += 1
            print(f"  skip {rel} ({c.reason})")
            continue
        if not args.apply:
            print(f"  would restore {rel}")
            copied += 1
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1
    print(f"restore: {copied} files · refused {refused}" + ("" if args.apply else " · dry-run"))
    return 0


def _parse_front(text: str) -> dict[str, str]:
    m = FRONT_RE.match(text)
    if not m:
        return {}
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def job_units_from_mirrors(jobs_dir: Path) -> set[str]:
    units: set[str] = set()
    if not jobs_dir.exists():
        return units
    for p in jobs_dir.glob("*.md"):
        if p.name.upper() in {"README.md", "FEED_PROMPT.md"}:
            continue
        meta = _parse_front(p.read_text(encoding="utf-8"))
        for key in ("unit", "plist"):
            val = meta.get(key, "").strip().strip('"')
            if val:
                units.add(val)
    return units


def repo_schedule_files(workspace: Path) -> set[str]:
    names: set[str] = set()
    for folder in (workspace / "deploy" / "units", workspace / "deploy" / "macos"):
        if not folder.exists():
            continue
        for p in folder.iterdir():
            # Scheduled jobs only — long-running dashboard .service units are not crons.
            if p.suffix in {".timer", ".plist"}:
                names.add(p.name)
    return names


def ls_tree(git_dir: Path, ref: str, prefix: str) -> set[str]:
    r = git(git_dir, "ls-tree", "-r", "--name-only", ref, prefix, check=False)
    if r.returncode != 0:
        return set()
    return {line for line in r.stdout.splitlines() if line}


def dirty_older_than(git_dir: Path, hours: float = 24.0) -> list[str]:
    r = git(git_dir, "status", "--porcelain", check=False)
    if r.returncode != 0:
        return []
    old: list[str] = []
    cutoff = utc_now().timestamp() - hours * 3600
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        p = git_dir / path
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime <= cutoff:
            old.append(f"{path} (mtime older than {int(hours)}h)")
    return old


def cmd_drift(args: argparse.Namespace, config: dict[str, Any]) -> int:
    workspace = expand(args.workspace)
    findings: list[str] = []

    dirty = dirty_older_than(workspace, hours=float(args.dirty_hours))
    findings.extend(f"dirty>{args.dirty_hours}h: {x}" for x in dirty)

    master_files = ls_tree(workspace, args.master_ref, "financial-command")
    treas_files = ls_tree(workspace, args.treasury_ref, "financial-command")
    if master_files or treas_files:
        only_master = sorted(master_files - treas_files)
        only_treas = sorted(treas_files - master_files)
        missing_pwa = [p for p in PWA_ASSETS if p in master_files and p not in treas_files]
        if missing_pwa:
            findings.append("fcc-pwa missing on work/treasury: " + ", ".join(missing_pwa))
        if only_master or only_treas:
            findings.append(
                "fcc file-list drift master vs work/treasury: "
                + f"+master {len(only_master)} +treasury {len(only_treas)}"
            )
            for p in only_master[:20]:
                findings.append(f"  only master: {p}")
            for p in only_treas[:20]:
                findings.append(f"  only treasury: {p}")

    mirrors = job_units_from_mirrors(workspace / "ops" / "jobs")
    units = repo_schedule_files(workspace)
    # Only flag repo units that have no mirror. Live-only / platform jobs are allowed without a unit file.
    unmirrored = sorted(u for u in units if u not in mirrors)
    if unmirrored:
        findings.append("job-mirror drift (repo unit has no ops/jobs mirror): " + ", ".join(unmirrored))

    context = workspace / "CONTEXT.md"
    if not context.exists():
        findings.append("CONTEXT.md missing (canonical-location rule)")
    else:
        text = context.read_text(encoding="utf-8")
        if "Canonical locations" not in text and "canonical location" not in text.lower():
            findings.append("CONTEXT.md missing canonical-location rule")

    if not findings:
        return 0
    report = "context drift " + utc_now().strftime("%Y-%m-%dT%H:%MZ") + "\n" + "\n".join(findings) + "\n"
    if args.out:
        outp = expand(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(report, encoding="utf-8")
    print(report, end="")
    return 1


def extract_facts(text: str) -> list[tuple[str, str, int]]:
    facts: list[tuple[str, str, int]] = []
    for i, line in enumerate(text.splitlines(), 1):
        m = FACT_RE.match(line)
        if not m:
            continue
        key = (m.group(1) or m.group(2) or m.group(3) or "").strip().lower()
        val = m.group(4).strip()
        if len(key) < 2 or len(val) < 1:
            continue
        facts.append((key, val, i))
    return facts


def cmd_contradict(args: argparse.Namespace, config: dict[str, Any]) -> int:
    paths: list[Path] = []
    for raw in args.paths:
        p = expand(raw)
        if p.is_dir():
            paths.extend(sorted(p.glob("*.md")))
            paths.extend(sorted((p / "bank").glob("*.md")) if (p / "bank").exists() else [])
        elif p.exists():
            paths.append(p)
    hits: list[str] = []
    resolutions: list[str] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        facts = extract_facts(text)
        by_key: dict[str, list[tuple[str, int]]] = {}
        for key, val, line in facts:
            by_key.setdefault(key, []).append((val, line))
        new_lines = text.splitlines()
        changed = False
        for key, pairs in by_key.items():
            uniq_vals = {v for v, _ in pairs}
            if len(uniq_vals) < 2:
                continue
            winner_val, winner_line = pairs[-1]
            hits.append(f"{path}: '{key}' {uniq_vals!r} → keep line {winner_line}")
            if args.apply:
                for val, line in pairs[:-1]:
                    idx = line - 1
                    if 0 <= idx < len(new_lines) and not new_lines[idx].lstrip().startswith("<!--"):
                        new_lines[idx] = (
                            f"<!-- superseded {utc_now().date().isoformat()} "
                            f"by line {winner_line}: {new_lines[idx].strip()} -->"
                        )
                        changed = True
                resolutions.append(f"{path.name}:{key} -> {winner_val}")
        if args.apply and changed:
            path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            log = path.parent / "CONTRADICTION_LOG.md"
            stamp = utc_now().strftime("%Y-%m-%dT%H:%MZ")
            entry = f"- {stamp} {path.name}: " + "; ".join(resolutions) + "\n"
            with log.open("a", encoding="utf-8") as fh:
                fh.write(entry)
    if not hits:
        return 0
    print("contradictions:\n" + "\n".join(hits))
    if args.apply:
        print("reconciled in place (newer/last occurrence won); logged CONTRADICTION_LOG.md")
    else:
        print("report-only (pass --apply to reconcile in place)")
    return 1 if not args.apply else 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=None)
    common.add_argument(
        "--workspace",
        default=os.environ.get("PERSONAL_WORKSPACE", str(ROOT.parents[1])),
    )
    common.add_argument("--out", default=None)
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backup", parents=[common])
    b.add_argument("--apply", action="store_true")
    b.add_argument("--home", action="append", default=None)
    b.add_argument("--grok-home", default=None)
    b.add_argument("--remote", default=None)
    b.add_argument("--branch", default=None)
    b.add_argument("--clone-dir", default=None)
    b.add_argument("--staging", default=None)

    r = sub.add_parser("restore", parents=[common])
    r.add_argument("--to", required=True)
    r.add_argument("--source", default=None)
    r.add_argument("--remote", default=None)
    r.add_argument("--branch", default=None)
    r.add_argument("--clone-dir", default=None)
    r.add_argument("--apply", action="store_true")

    d = sub.add_parser("drift", parents=[common])
    d.add_argument("--dirty-hours", default=24.0, type=float)
    d.add_argument("--master-ref", default="origin/master")
    d.add_argument("--treasury-ref", default="origin/work/treasury")

    c = sub.add_parser("contradict", parents=[common])
    c.add_argument("--paths", nargs="+", default=["MEMORY.md", "memory"])
    c.add_argument("--apply", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(expand(args.config) if args.config else None)
    if args.cmd == "backup":
        return cmd_backup(args, config)
    if args.cmd == "restore":
        return cmd_restore(args, config)
    if args.cmd == "drift":
        return cmd_drift(args, config)
    if args.cmd == "contradict":
        return cmd_contradict(args, config)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
