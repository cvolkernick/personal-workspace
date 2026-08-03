#!/usr/bin/env python3
"""Refresh local Robinhood snapshot: Mac MCP primary, optional Pi pull, push to Pi.

P0 (2026-08-03) model — Mac is the live producer; Pi is a consumer of snapshots:

  1) Local Grok + Robinhood MCP (default when TREASURY_SKIP_PI=1 / Mac launchd).
  2) Optional Pi pull if enabled and remote is fresher / within max_age_hours.
  3) After a successful Mac write, push snapshot files to Pi (offline FCC UI).

Legacy order (prefer_pi=True, skip not set): try Pi SCP first, then local MCP.

Env (optional):
  TREASURY_PI_SSH     e.g. prism-agent@192.168.100.98
  TREASURY_PI_ROOT    e.g. /home/prism-agent/personal-workspace
  TREASURY_PI_CONNECT_TIMEOUT  (seconds, default 5)
  TREASURY_RH_MAX_AGE_HOURS    accept remote only if as_of younger than this (default 6)
  TREASURY_RH_MCP_TIMEOUT_S    local Grok/MCP wall timeout (default 240)
  TREASURY_SKIP_PI=1           force local MCP only (Mac launchd default)
  TREASURY_SKIP_LOCAL_MCP=1    do not fall back to grok/MCP
  TREASURY_SKIP_PUSH_PI=1      do not push snapshots to Pi after success

Config (treasury/config.json → pi_sync):
  ssh, remote_root, connect_timeout_s, max_age_hours, mcp_timeout_s,
  enabled, push_enabled, push_files

Usage:
  python3 -m treasury.rh_snapshot_sync
  python3 -m treasury.rh_snapshot_sync --pi-only
  python3 -m treasury.rh_snapshot_sync --local-only
  python3 -m treasury.rh_snapshot_sync --push-only
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from treasury.adapters import SNAPSHOTS_DIR, load_config, load_json, save_json  # noqa: E402

DEFAULT_SSH = "prism-agent@192.168.100.98"
DEFAULT_REMOTE_ROOTS = (
    "/home/prism-agent/personal-workspace",
    "/home/pi/personal-workspace",
)
DEFAULT_CONNECT_S = 5
# Align with FCC policy stale_after_hours (6h), not the old 12h window that
# accepted "ok" pulls while the dashboard still painted RH red/stale.
DEFAULT_MAX_AGE_H = 6.0
# Grok + Robinhood MCP multi-step refresh often exceeds 90s (primary fail mode).
DEFAULT_MCP_TIMEOUT_S = 240.0
RH_SNAP = "robinhood_latest.json"
FM_SNAP = "fund_manager_latest.json"
DEFAULT_PUSH_FILES = (
    "robinhood_latest.json",
    "braiins_latest.json",
    "fund_manager_latest.json",
    "treasury_latest.json",
    # YNAB-sourced cash feeds (token lives on Mac; Pi is offline consumer)
    "one_card_latest.json",
    "rh_checking_latest.json",
    "x_money_latest.json",
    "expenses_latest.json",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_as_of(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return t
    except ValueError:
        return None


def _age_hours(as_of: Optional[datetime]) -> Optional[float]:
    if not as_of:
        return None
    return max(0.0, (_now() - as_of).total_seconds() / 3600.0)


def _pi_settings() -> Dict[str, Any]:
    cfg = load_config() or {}
    ps = dict(cfg.get("pi_sync") or {})
    ssh = (
        (os.environ.get("TREASURY_PI_SSH") or "").strip()
        or (ps.get("ssh") or ps.get("ssh_host") or "").strip()
        or DEFAULT_SSH
    )
    root = (
        (os.environ.get("TREASURY_PI_ROOT") or "").strip()
        or (ps.get("remote_root") or "").strip()
        or ""
    )
    try:
        timeout = float(
            os.environ.get("TREASURY_PI_CONNECT_TIMEOUT")
            or ps.get("connect_timeout_s")
            or DEFAULT_CONNECT_S
        )
    except (TypeError, ValueError):
        timeout = float(DEFAULT_CONNECT_S)
    try:
        max_age = float(
            os.environ.get("TREASURY_RH_MAX_AGE_HOURS")
            or ps.get("max_age_hours")
            or DEFAULT_MAX_AGE_H
        )
    except (TypeError, ValueError):
        max_age = DEFAULT_MAX_AGE_H
    try:
        mcp_timeout = float(
            os.environ.get("TREASURY_RH_MCP_TIMEOUT_S")
            or ps.get("mcp_timeout_s")
            or DEFAULT_MCP_TIMEOUT_S
        )
    except (TypeError, ValueError):
        mcp_timeout = float(DEFAULT_MCP_TIMEOUT_S)
    enabled = ps.get("enabled")
    if enabled is None:
        enabled = True
    if os.environ.get("TREASURY_SKIP_PI") == "1":
        enabled = False
    push_enabled = ps.get("push_enabled")
    if push_enabled is None:
        push_enabled = True
    if os.environ.get("TREASURY_SKIP_PUSH_PI") == "1":
        push_enabled = False
    push_files = ps.get("push_files") or list(DEFAULT_PUSH_FILES)
    if not isinstance(push_files, list):
        push_files = list(DEFAULT_PUSH_FILES)
    return {
        "ssh": ssh,
        "remote_root": root,
        "connect_timeout_s": timeout,
        "max_age_hours": max_age,
        "mcp_timeout_s": mcp_timeout,
        "enabled": bool(enabled),
        "push_enabled": bool(push_enabled),
        "push_files": [str(x) for x in push_files if x],
    }


def _ssh_base(ssh_host: str, timeout: float) -> List[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={int(max(1, timeout))}",
        "-o",
        "StrictHostKeyChecking=accept-new",
        ssh_host,
    ]


def pi_reachable(ssh_host: str, timeout: float = DEFAULT_CONNECT_S) -> bool:
    if not shutil.which("ssh"):
        return False
    try:
        r = subprocess.run(
            _ssh_base(ssh_host, timeout) + ["true"],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _remote_paths(remote_root: str) -> List[str]:
    roots: List[str] = []
    if remote_root:
        roots.append(remote_root.rstrip("/"))
    for d in DEFAULT_REMOTE_ROOTS:
        if d not in roots:
            roots.append(d)
    # Also try worktree layout on Pi
    extra: List[str] = []
    for r in roots:
        extra.append(f"{r}/treasury/snapshots/{RH_SNAP}")
        extra.append(
            f"{r}/personal-workspace-worktrees/treasury/treasury/snapshots/{RH_SNAP}"
        )
        # if remote_root already is the worktree
        extra.append(f"{r}/../treasury/snapshots/{RH_SNAP}")
    # unique preserve order
    seen = set()
    out: List[str] = []
    for p in extra:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _scp_file(ssh_host: str, remote_path: str, dest: Path, timeout: float) -> bool:
    if not shutil.which("scp"):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # -p preserves remote mtime so stale detection works when as_of is missing
        r = subprocess.run(
            [
                "scp",
                "-p",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={int(max(1, timeout))}",
                "-o",
                "StrictHostKeyChecking=accept-new",
                f"{ssh_host}:{remote_path}",
                str(dest),
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 15,
        )
        return r.returncode == 0 and dest.is_file() and dest.stat().st_size > 20
    except (subprocess.TimeoutExpired, OSError):
        return False


def _valid_rh_snapshot(
    path: Path, *, allow_mtime_as_of: bool = True
) -> Tuple[bool, Optional[datetime], str]:
    data = load_json(path)
    if not data:
        return False, None, "invalid_json"
    # dual snapshot or flat portfolio (Pi may write a slim primary-only dict)
    as_of = _parse_as_of(data.get("as_of"))
    if not as_of:
        prim = data.get("primary") or data.get("agentic") or {}
        if isinstance(prim, dict):
            as_of = _parse_as_of(prim.get("as_of"))
            if not as_of and isinstance(prim.get("data"), dict):
                as_of = _parse_as_of(
                    prim["data"].get("as_of") or prim["data"].get("updated_at")
                )
    used_mtime = False
    if not as_of and allow_mtime_as_of:
        # last resort: file mtime (prefer scp -p so this is remote mtime)
        try:
            as_of = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            used_mtime = True
        except OSError:
            return False, None, "missing_as_of"
    if not as_of:
        return False, None, "missing_as_of"
    ok_shape = bool(
        data.get("primary")
        or data.get("agentic")
        or data.get("buying_power") is not None
        or data.get("cash") is not None
        or data.get("equity_value") is not None
        or data.get("accounts")
        or data.get("source")
    )
    if not ok_shape:
        return False, as_of, "unexpected_shape"
    return True, as_of, "ok_mtime" if used_mtime else "ok"


def pull_from_pi(
    *,
    ssh_host: Optional[str] = None,
    remote_root: Optional[str] = None,
    timeout: Optional[float] = None,
    max_age_hours: Optional[float] = None,
    also_fund_manager: bool = True,
) -> Dict[str, Any]:
    """Pull RH snapshot from Pi into local SNAPSHOTS_DIR. Returns status dict."""
    settings = _pi_settings()
    ssh_host = ssh_host or settings["ssh"]
    remote_root = remote_root if remote_root is not None else settings["remote_root"]
    timeout = float(timeout if timeout is not None else settings["connect_timeout_s"])
    max_age = float(
        max_age_hours if max_age_hours is not None else settings["max_age_hours"]
    )

    out: Dict[str, Any] = {
        "ok": False,
        "source": "pi",
        "ssh": ssh_host,
        "path": None,
        "as_of": None,
        "age_hours": None,
        "error": None,
    }

    if not settings["enabled"] and remote_root is None:
        out["error"] = "pi_sync disabled"
        return out

    if not pi_reachable(ssh_host, timeout):
        out["error"] = f"pi_unreachable:{ssh_host}"
        return out

    with tempfile.TemporaryDirectory(prefix="rh_pi_") as td:
        tmp = Path(td) / RH_SNAP
        remote_used = None
        for rpath in _remote_paths(remote_root or ""):
            if _scp_file(ssh_host, rpath, tmp, timeout):
                remote_used = rpath
                break
        if not remote_used:
            out["error"] = "remote_snapshot_not_found"
            return out

        ok, as_of, why = _valid_rh_snapshot(tmp)
        if not ok:
            out["error"] = f"remote_invalid:{why}"
            return out

        age = _age_hours(as_of)
        out["as_of"] = as_of.isoformat() if as_of else None
        out["age_hours"] = round(age, 2) if age is not None else None
        out["remote_path"] = remote_used

        local = SNAPSHOTS_DIR / RH_SNAP
        local_as = None
        if local.is_file():
            _, local_as, _ = _valid_rh_snapshot(local)

        # Prefer falling back to local MCP when Pi snapshot is too old,
        # unless it is strictly newer than what we already have.
        if age is not None and age > max_age:
            if local_as and as_of and as_of <= local_as:
                out["error"] = f"remote_stale:{age:.1f}h>{max_age}h"
                return out
            if not local_as or (as_of and local_as and as_of > local_as):
                out["note"] = f"remote_stale_but_newer_than_local:{age:.1f}h"
            else:
                out["error"] = f"remote_stale:{age:.1f}h>{max_age}h"
                return out

        dest = SNAPSHOTS_DIR / RH_SNAP
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmp, dest)
        # annotate provenance; ensure as_of present for FCC feedClass
        try:
            data = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if not data.get("as_of") and as_of:
                    data["as_of"] = as_of.isoformat()
                data["pulled_from_pi"] = {
                    "ssh": ssh_host,
                    "remote_path": remote_used,
                    "pulled_at": _now().isoformat(),
                }
                save_json(dest, data)
        except (OSError, json.JSONDecodeError):
            pass

        out["ok"] = True
        out["path"] = str(dest)
        out["error"] = None

        if also_fund_manager:
            # best-effort companion snapshot
            for rpath in _remote_paths(remote_root or ""):
                fm_remote = rpath.replace(RH_SNAP, FM_SNAP)
                fm_tmp = Path(td) / FM_SNAP
                if _scp_file(ssh_host, fm_remote, fm_tmp, timeout):
                    shutil.copy2(fm_tmp, SNAPSHOTS_DIR / FM_SNAP)
                    out["fund_manager_pulled"] = True
                    break

    return out


def refresh_via_local_mcp(*, timeout_s: Optional[float] = None) -> Dict[str, Any]:
    """Invoke grok headless with rh_refresh_prompt (local Robinhood MCP)."""
    if timeout_s is None:
        timeout_s = float(_pi_settings().get("mcp_timeout_s") or DEFAULT_MCP_TIMEOUT_S)
    out: Dict[str, Any] = {
        "ok": False,
        "source": "local_mcp",
        "error": None,
        "path": str(SNAPSHOTS_DIR / RH_SNAP),
        "timeout_s": timeout_s,
    }
    if os.environ.get("TREASURY_SKIP_LOCAL_MCP") == "1":
        out["error"] = "local_mcp_skipped"
        return out

    prompt_path = ROOT / "treasury" / "rh_refresh_prompt.txt"
    grok = shutil.which("grok")
    if not grok:
        out["error"] = "grok_not_found"
        return out
    if not prompt_path.is_file():
        out["error"] = "missing_rh_refresh_prompt"
        return out

    before_mtime = None
    dest = SNAPSHOTS_DIR / RH_SNAP
    if dest.is_file():
        before_mtime = dest.stat().st_mtime

    prompt = prompt_path.read_text(encoding="utf-8")
    r = None
    try:
        r = subprocess.run(
            [
                grok,
                "-p",
                prompt,
                "--cwd",
                str(ROOT),
                "--yolo",
                "--output-format",
                "plain",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(ROOT),
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        out["returncode"] = r.returncode
        out["stdout_tail"] = (r.stdout or "")[-500:]
        out["stderr_tail"] = (r.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        out["error"] = f"local_mcp_timeout:{timeout_s}s"
        return out
    except OSError as e:
        out["error"] = f"local_mcp_spawn:{e}"
        return out

    if dest.is_file():
        ok, as_of, why = _valid_rh_snapshot(dest)
        after_mtime = dest.stat().st_mtime
        out["as_of"] = as_of.isoformat() if as_of else None
        out["age_hours"] = round(_age_hours(as_of) or 0.0, 2) if as_of else None
        if ok and (before_mtime is None or after_mtime > before_mtime + 0.5):
            out["ok"] = True
            out["error"] = None
            return out
        if ok and as_of and (_age_hours(as_of) or 99) < 6:
            # file already fresh enough
            out["ok"] = True
            out["error"] = None
            out["note"] = "snapshot_already_fresh"
            return out
        out["error"] = out.get("error") or f"snapshot_not_updated:{why}"
    else:
        out["error"] = "snapshot_missing_after_mcp"

    if r is not None and r.returncode != 0 and not out["ok"]:
        out["error"] = out.get("error") or f"grok_exit_{r.returncode}"
    return out


def push_snapshots_to_pi(
    files: Optional[List[str]] = None,
    *,
    ssh_host: Optional[str] = None,
    remote_root: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """SCP selected snapshot files Mac → Pi (no secrets; offline FCC consumer)."""
    settings = _pi_settings()
    ssh_host = ssh_host or settings["ssh"]
    remote_root = (
        remote_root
        if remote_root is not None
        else (settings["remote_root"] or DEFAULT_REMOTE_ROOTS[0])
    )
    timeout = float(timeout if timeout is not None else settings["connect_timeout_s"])
    names = files if files is not None else list(settings["push_files"])

    out: Dict[str, Any] = {
        "ok": False,
        "source": "push_pi",
        "ssh": ssh_host,
        "remote_root": remote_root,
        "pushed": [],
        "skipped": [],
        "error": None,
    }

    if os.environ.get("TREASURY_SKIP_PUSH_PI") == "1" or not settings.get("push_enabled"):
        out["error"] = "push_disabled"
        out["ok"] = True  # not a hard failure for callers that treat soft-skip
        out["note"] = "push_disabled"
        return out

    if not shutil.which("scp") or not shutil.which("ssh"):
        out["error"] = "scp_or_ssh_missing"
        return out

    if not pi_reachable(ssh_host, timeout):
        out["error"] = f"pi_unreachable:{ssh_host}"
        return out

    remote_snap = f"{remote_root.rstrip('/')}/treasury/snapshots"
    try:
        mk = subprocess.run(
            _ssh_base(ssh_host, timeout) + [f"mkdir -p {remote_snap}"],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if mk.returncode != 0:
            out["error"] = f"remote_mkdir_failed:{(mk.stderr or mk.stdout or '')[:200]}"
            return out
    except (subprocess.TimeoutExpired, OSError) as e:
        out["error"] = f"remote_mkdir:{e}"
        return out

    for name in names:
        local = SNAPSHOTS_DIR / name
        if not local.is_file():
            out["skipped"].append({"file": name, "reason": "missing_local"})
            continue
        remote = f"{ssh_host}:{remote_snap}/{name}"
        try:
            r = subprocess.run(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={int(max(1, timeout))}",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    str(local),
                    remote,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 30,
            )
            if r.returncode == 0:
                out["pushed"].append(name)
            else:
                out["skipped"].append(
                    {
                        "file": name,
                        "reason": f"scp_exit_{r.returncode}",
                        "stderr": (r.stderr or "")[:200],
                    }
                )
        except (subprocess.TimeoutExpired, OSError) as e:
            out["skipped"].append({"file": name, "reason": str(e)})

    # FCC server also serves financial-command/treasury_latest.json — keep in sync
    if "treasury_latest.json" in out["pushed"]:
        local_tre = SNAPSHOTS_DIR / "treasury_latest.json"
        remote_fcc = (
            f"{ssh_host}:{remote_root.rstrip('/')}/financial-command/treasury_latest.json"
        )
        try:
            r = subprocess.run(
                [
                    "scp",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    f"ConnectTimeout={int(max(1, timeout))}",
                    "-o",
                    "StrictHostKeyChecking=accept-new",
                    str(local_tre),
                    remote_fcc,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 30,
            )
            if r.returncode == 0:
                out["pushed"].append("financial-command/treasury_latest.json")
            else:
                out["skipped"].append(
                    {
                        "file": "financial-command/treasury_latest.json",
                        "reason": f"scp_exit_{r.returncode}",
                        "stderr": (r.stderr or "")[:200],
                    }
                )
        except (subprocess.TimeoutExpired, OSError) as e:
            out["skipped"].append(
                {"file": "financial-command/treasury_latest.json", "reason": str(e)}
            )

    out["ok"] = len(out["pushed"]) > 0
    if not out["ok"] and not out["error"]:
        out["error"] = "nothing_pushed"
    return out


def reevaluate_offline() -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "treasury.fund_manager", "--write"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass
    try:
        subprocess.run(
            [sys.executable, "-m", "treasury.run_treasury", "--offline"],
            cwd=str(ROOT),
            capture_output=True,
            timeout=90,
        )
    except (subprocess.TimeoutExpired, OSError):
        pass


def sync_rh_snapshot(
    *,
    prefer_pi: bool = True,
    allow_local_mcp: bool = True,
    reevaluate: bool = True,
    push_to_pi: bool = True,
) -> Dict[str, Any]:
    """Optional Pi pull, then local MCP. Push Mac snapshots to Pi on local success."""
    result: Dict[str, Any] = {
        "ok": False,
        "source": None,
        "pi": None,
        "local_mcp": None,
        "push": None,
        "error": None,
    }

    if prefer_pi and os.environ.get("TREASURY_SKIP_PI") != "1":
        pi = pull_from_pi()
        result["pi"] = pi
        if pi.get("ok"):
            result["ok"] = True
            result["source"] = "pi"
            result["as_of"] = pi.get("as_of")
            result["age_hours"] = pi.get("age_hours")
            result["path"] = pi.get("path")
            if reevaluate:
                reevaluate_offline()
            # Pi was source — no push (would echo same files back)
            return result

    if allow_local_mcp and os.environ.get("TREASURY_SKIP_LOCAL_MCP") != "1":
        local = refresh_via_local_mcp()
        result["local_mcp"] = local
        if local.get("ok"):
            result["ok"] = True
            result["source"] = "local_mcp"
            result["as_of"] = local.get("as_of")
            result["age_hours"] = local.get("age_hours")
            result["path"] = local.get("path")
            if reevaluate:
                reevaluate_offline()
            if push_to_pi:
                result["push"] = push_snapshots_to_pi()
            return result
        result["error"] = local.get("error") or (result.get("pi") or {}).get("error")
        return result

    result["error"] = (result.get("pi") or {}).get("error") or "no_refresh_path"
    return result


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pi-only", action="store_true", help="Only pull from Pi")
    p.add_argument("--local-only", action="store_true", help="Only local MCP")
    p.add_argument(
        "--push-only",
        action="store_true",
        help="Only push local snapshots to Pi (no RH refresh)",
    )
    p.add_argument("--no-reeval", action="store_true", help="Skip fund_manager/run_treasury")
    p.add_argument("--no-push", action="store_true", help="Do not push to Pi after success")
    p.add_argument("--print", action="store_true", help="Print JSON status")
    args = p.parse_args(argv)

    if args.push_only:
        status = push_snapshots_to_pi()
        print(json.dumps(status, indent=2, default=str))
        # soft-skip (push_disabled / pi down) → exit 0 if disabled; else require pushed
        if status.get("note") == "push_disabled":
            return 0
        return 0 if status.get("ok") else 1

    prefer_pi = not args.local_only
    allow_local = not args.pi_only
    status = sync_rh_snapshot(
        prefer_pi=prefer_pi,
        allow_local_mcp=allow_local,
        reevaluate=not args.no_reeval,
        push_to_pi=not args.no_push,
    )
    if args.print or True:
        print(json.dumps(status, indent=2, default=str))
    return 0 if status.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
