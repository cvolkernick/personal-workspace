#!/usr/bin/env python3
"""Map git-changed paths → systemd units for path-scoped Pi auto-deploy.

Pure logic + CLI. Used by deploy/on_merge.sh and deploy/workspace_sync.sh.
Never selects treasury/secrets for auto-restart. Never expands to thrash-all
unless shared platform glue or multi-prefix changes map many units explicitly.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAP = Path(__file__).resolve().parent / "path_unit_map.json"


def load_map(path: Optional[Path] = None) -> dict[str, Any]:
    p = path or DEFAULT_MAP
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("path_unit_map.json must be an object")
    return data


def _norm(path: str) -> str:
    p = path.strip().lstrip("./")
    return p.replace("\\", "/")


def _matches_prefix(path: str, prefix: str) -> bool:
    path = _norm(path)
    prefix = _norm(prefix)
    if prefix.endswith("/"):
        return path.startswith(prefix) or path == prefix.rstrip("/")
    return path == prefix or path.startswith(prefix + "/")


def _matches_any_glob(path: str, globs: Iterable[str]) -> bool:
    """Glob match with simple `**` support (fnmatch alone does not treat `**` as recursive)."""
    path = _norm(path)
    base = path.rsplit("/", 1)[-1]
    for g in globs:
        g = g.strip()
        if not g:
            continue
        # basename-only patterns
        if "/" not in g.rstrip("/"):
            if fnmatch.fnmatch(base, g):
                return True
        # **/file or **/*.ext → match basename against the suffix pattern
        if g.startswith("**/"):
            suffix = g[3:]
            if fnmatch.fnmatch(base, suffix) or fnmatch.fnmatch(path, suffix):
                return True
            # also allow mid-path: a/b/c matches **/b/c
            if path.endswith("/" + suffix) or path == suffix:
                return True
            if "*" in suffix and fnmatch.fnmatch(path, suffix):
                return True
            # path-wide: **/x/** style
            if fnmatch.fnmatch(path, g.replace("**/", "*")):
                return True
            continue
        if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(base, g):
            return True
    return False


def classify_paths(
    paths: Iterable[str],
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Return deploy decision for a list of changed repo-relative paths."""
    cfg = cfg if cfg is not None else load_map()
    auto_rules: list[dict[str, Any]] = list(cfg.get("auto") or [])
    manual_prefixes: list[str] = list(cfg.get("manual_prefixes") or [])
    manual_globs: list[str] = list(cfg.get("manual_globs") or [])
    ignore_prefixes: list[str] = list(cfg.get("ignore_prefixes") or [])
    ignore_globs: list[str] = list(cfg.get("ignore_globs") or [])
    shared = cfg.get("shared_auto") if isinstance(cfg.get("shared_auto"), dict) else {}
    shared_paths = {_norm(p) for p in (shared.get("paths") or [])}

    units: dict[str, dict[str, str]] = {}  # unit -> meta
    auto_paths: list[str] = []
    manual_paths: list[str] = []
    ignored_paths: list[str] = []
    unmapped_paths: list[str] = []
    shared_hit = False

    for raw in paths:
        path = _norm(raw)
        if not path:
            continue

        # Manual wins (safety) — never auto
        if any(_matches_prefix(path, p) for p in manual_prefixes) or _matches_any_glob(
            path, manual_globs
        ):
            # Exception: config.json under auto dashboard trees is still sensitive
            # when matched by manual_globs — treat as manual always.
            manual_paths.append(path)
            continue

        if any(_matches_prefix(path, p) for p in ignore_prefixes) or _matches_any_glob(
            path, ignore_globs
        ):
            ignored_paths.append(path)
            continue

        if path in shared_paths:
            shared_hit = True
            auto_paths.append(path)
            continue

        matched = False
        for rule in auto_rules:
            prefixes = list(rule.get("prefixes") or [])
            if any(_matches_prefix(path, p) for p in prefixes):
                unit = str(rule.get("unit") or "").strip()
                if unit:
                    units[unit] = {
                        "unit": unit,
                        "service_key": str(rule.get("service_key") or ""),
                        "only": str(rule.get("only") or ""),
                    }
                auto_paths.append(path)
                matched = True
                break
        if not matched:
            unmapped_paths.append(path)

    if shared_hit:
        for rule in auto_rules:
            unit = str(rule.get("unit") or "").strip()
            if not unit:
                continue
            # Shared glue restarts dashboard/platform units only — skip panamerica
            # optional business site? Include all auto rules except none.
            units[unit] = {
                "unit": unit,
                "service_key": str(rule.get("service_key") or ""),
                "only": str(rule.get("only") or ""),
            }

    unit_list = sorted(units.values(), key=lambda u: u["unit"])
    action = "noop"
    if unit_list:
        action = "restart"
    elif manual_paths and not auto_paths:
        action = "manual"
    elif unmapped_paths and not auto_paths:
        action = "unmapped"
    elif ignored_paths and not auto_paths and not manual_paths:
        action = "noop"

    return {
        "action": action,
        "units": [u["unit"] for u in unit_list],
        "unit_meta": unit_list,
        "only": ",".join(sorted({u["only"] for u in unit_list if u.get("only")})),
        "service_keys": [u["service_key"] for u in unit_list if u.get("service_key")],
        "auto_paths": auto_paths,
        "manual_paths": manual_paths,
        "ignored_paths": ignored_paths,
        "unmapped_paths": unmapped_paths,
        "shared_hit": shared_hit,
        "thrash_all": False,
    }


def git_changed_paths(
    before: str,
    after: str,
    repo: Optional[Path] = None,
) -> list[str]:
    repo = repo or ROOT
    # before may be "none" on first clone
    if not before or before in ("none", "0000000000000000000000000000000000000000"):
        cmd = ["git", "-C", str(repo), "diff", "--name-only", "--diff-filter=ACMRT", after]
    else:
        cmd = [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            "--diff-filter=ACMRT",
            f"{before}..{after}",
        ]
    out = subprocess.check_output(cmd, text=True)
    return [line.strip() for line in out.splitlines() if line.strip()]


def decide_from_git(
    before: str,
    after: str,
    repo: Optional[Path] = None,
    map_path: Optional[Path] = None,
) -> dict[str, Any]:
    paths = git_changed_paths(before, after, repo=repo)
    result = classify_paths(paths, cfg=load_map(map_path))
    result["before"] = before
    result["after"] = after
    result["changed_count"] = len(paths)
    return result


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", help="Git SHA before merge/pull")
    parser.add_argument("--after", help="Git SHA after merge/pull (default HEAD)")
    parser.add_argument("--path", action="append", dest="paths", help="Explicit path (repeatable)")
    parser.add_argument("--map", type=Path, default=DEFAULT_MAP, help="path_unit_map.json")
    parser.add_argument("--repo", type=Path, default=ROOT, help="Git repo root")
    parser.add_argument(
        "--format",
        choices=("json", "units", "only", "summary"),
        default="json",
    )
    args = parser.parse_args(argv)

    if args.paths:
        result = classify_paths(args.paths, cfg=load_map(args.map))
    else:
        after = args.after or "HEAD"
        before = args.before
        if not before:
            # default: single commit parent
            try:
                before = subprocess.check_output(
                    ["git", "-C", str(args.repo), "rev-parse", "HEAD^"],
                    text=True,
                ).strip()
            except subprocess.CalledProcessError:
                before = "none"
        result = decide_from_git(before, after, repo=args.repo, map_path=args.map)

    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    elif args.format == "units":
        print("\n".join(result["units"]))
    elif args.format == "only":
        print(result.get("only") or "")
    else:
        print(
            f"action={result['action']} units={','.join(result['units']) or '-'} "
            f"manual={len(result['manual_paths'])} ignored={len(result['ignored_paths'])} "
            f"unmapped={len(result['unmapped_paths'])} thrash_all={result['thrash_all']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
