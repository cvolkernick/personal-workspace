#!/usr/bin/env python3
"""Refresh Robinhood snapshot. SoT producer is prism/Pi (#518).

Issue #518 model — Pi (prism) is the always-on live producer:

  1) On the producer host: local Grok + robinhood-trading MCP writes
     ``robinhood_latest.json``. Mac offline/revoked does not block this.
  2) On Mac (consumer): pull the Pi snapshot. Do not push RH back (no dual-write).
  3) Mac launchd is backup-only / unloaded. Optional ``TREASURY_RH_ROLE=backup``
     may run local MCP for laptop FCC but still must not overwrite Pi RH.
  4) Auth/MCP failure leaves the existing as_of untouched (honest stale).
  5) Failure alert names producer host + error class (GitHub #701).

Env (optional):
  TREASURY_RH_ROLE=producer|backup|consumer
  TREASURY_RH_PRODUCER=1 / TREASURY_RH_BACKUP=1
  TREASURY_RH_SOT_HOST=prism
  TREASURY_RH_PUSH=1           allow Mac→Pi robinhood_latest.json (off by default)
  TREASURY_BRAIINS_PUSH=1      allow Mac→Pi braiins_latest.json (off by default; #689)
  TREASURY_COINBASE_PUSH=1     allow Mac→Pi coinbase_latest.json (off by default; #695)
  TREASURY_PI_SSH     e.g. prism-agent@192.168.100.98
  TREASURY_PI_ROOT    e.g. /home/prism-agent/personal-workspace
  TREASURY_PI_CONNECT_TIMEOUT  (seconds, default 5)
  TREASURY_RH_MAX_AGE_HOURS    accept remote only if as_of younger than this (default 6)
  TREASURY_RH_MCP_TIMEOUT_S    local Grok/MCP wall timeout (default 240)
  TREASURY_SKIP_PI=1           do not pull from Pi (producer systemd sets this)
  TREASURY_SKIP_LOCAL_MCP=1    do not run grok/MCP (Mac consumer launchd)
  TREASURY_SKIP_PUSH_PI=1      do not push non-RH snapshots to Pi

Config: ``rh_producer.sot_host`` + ``pi_sync`` (ssh / remote_root / timeouts).

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
RH_PRODUCER_STATUS = "rh_producer_status.json"
DEFAULT_SOT_HOST = "prism"
BRAIINS_SNAP = "braiins_latest.json"
COINBASE_SNAP = "coinbase_latest.json"
# Mac may still push YNAB/Sheet/Solana. RH (#518), Braiins (#689), and
# Coinbase price (#695) are Pi SoT.
DEFAULT_PUSH_FILES = (
    "fund_manager_latest.json",
    "treasury_latest.json",
    "one_card_latest.json",
    "rh_checking_latest.json",
    "x_money_latest.json",
    "solana_latest.json",
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


def classify_rh_error(err: Optional[str]) -> str:
    """Map a refresh error string to a stable error class (AC4)."""
    e = (err or "").lower()
    if not e.strip():
        return "unknown"
    auth_needles = (
        "auth_fail",
        "oauth",
        "revok",
        "unauthorized",
        "401",
        "login required",
        "re-auth",
        "reauth",
        "authentication",
        "token expired",
        "not authenticated",
        "mcp auth",
        "consent",
    )
    if any(n in e for n in auth_needles):
        return "auth_fail"
    if "timeout" in e:
        return "timeout"
    if "grok_not_found" in e or "grok_exit" in e or "grok not" in e:
        return "grok"
    if "stale" in e:
        return "stale"
    if "unreachable" in e:
        return "unreachable"
    if "skipped" in e or e == "no_refresh_path":
        return "skipped"
    if "primary_only" in e:
        return "downgrade"
    if "mcp" in e or "local_mcp_spawn" in e:
        return "mcp"
    return "error"


def _this_host() -> str:
    env = (os.environ.get("FCC_HOST_TAG") or "").strip()
    if env:
        return env.split(".")[0]
    try:
        import socket

        return (socket.gethostname() or "unknown").split(".")[0]
    except OSError:
        return "unknown"


def _sot_host() -> str:
    cfg = load_config() or {}
    rp = cfg.get("rh_producer") if isinstance(cfg.get("rh_producer"), dict) else {}
    return (
        (os.environ.get("TREASURY_RH_SOT_HOST") or "").strip()
        or str((rp or {}).get("sot_host") or (rp or {}).get("host") or "").strip()
        or DEFAULT_SOT_HOST
    )


def _rh_role() -> str:
    """producer = Pi writes SoT; consumer = pull only; backup = Mac MCP, no RH push."""
    raw = (os.environ.get("TREASURY_RH_ROLE") or "").strip().lower()
    if raw in ("producer", "backup", "consumer"):
        return raw
    if os.environ.get("TREASURY_RH_PRODUCER") == "1":
        return "producer"
    if os.environ.get("TREASURY_RH_BACKUP") == "1":
        return "backup"
    cfg = load_config() or {}
    rp = cfg.get("rh_producer") if isinstance(cfg.get("rh_producer"), dict) else {}
    cfg_role = str((rp or {}).get("role") or "").strip().lower()
    if cfg_role in ("producer", "backup", "consumer"):
        return cfg_role
    host = _this_host().lower()
    sot = _sot_host().lower()
    # Gateway / non-SoT prism* hosts are not the producer (#555). Explicit
    # TREASURY_RH_ROLE above still wins. Do not auto-promote prism-gateway.
    if host and "gateway" in host and host != sot:
        return "consumer"
    # Only auto-promote obvious prism hosts. systemd must set TREASURY_RH_ROLE=producer.
    if host and (host == sot or host.startswith("prism")):
        return "producer"
    return "consumer"


def _strip_rh_push(files: List[str]) -> List[str]:
    """Drop Pi-SoT snapshots unless an emergency override env is set.

    RH: omit unless TREASURY_RH_PUSH=1 (#518).
    Braiins: omit unless TREASURY_BRAIINS_PUSH=1 (#689).
    Coinbase: omit unless TREASURY_COINBASE_PUSH=1 (#695).
    """
    out = list(files)
    if os.environ.get("TREASURY_RH_PUSH") != "1":
        out = [f for f in out if str(f) != RH_SNAP]
    if os.environ.get("TREASURY_BRAIINS_PUSH") != "1":
        out = [f for f in out if str(f) != BRAIINS_SNAP]
    if os.environ.get("TREASURY_COINBASE_PUSH") != "1":
        out = [f for f in out if str(f) != COINBASE_SNAP]
    return out


def write_producer_status(status: Dict[str, Any]) -> Optional[Path]:
    dest = SNAPSHOTS_DIR / RH_PRODUCER_STATUS
    payload = {
        "ok": bool(status.get("ok")),
        "as_of": status.get("as_of"),
        "age_hours": status.get("age_hours"),
        "source": status.get("source"),
        "role": status.get("role") or _rh_role(),
        "producer_host": status.get("producer_host") or _sot_host(),
        "this_host": status.get("this_host") or _this_host(),
        "error": status.get("error"),
        "error_class": status.get("error_class")
        or classify_rh_error(str(status.get("error") or "")),
        "written_at": _now().isoformat(),
    }
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        save_json(dest, payload)
        return dest
    except OSError:
        return None


def existing_rh_snapshot_fresh(
    *,
    max_age_hours: Optional[float] = None,
    status: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, Optional[float], Optional[str]]:
    """True when robinhood_latest.json as_of is inside the freshness window."""
    if max_age_hours is None:
        max_age_hours = DEFAULT_MAX_AGE_H
    dest = SNAPSHOTS_DIR / RH_SNAP
    as_of = None
    if dest.is_file():
        ok, parsed, _ = _valid_rh_snapshot(dest)
        if ok:
            as_of = parsed
    if as_of is None and status:
        as_of = _parse_as_of(status.get("as_of"))
    age = _age_hours(as_of)
    iso = as_of.isoformat() if as_of else None
    if age is None:
        return False, None, iso
    return age < float(max_age_hours), round(age, 2), iso


def local_mcp_is_live_producer_path(status: Dict[str, Any]) -> bool:
    """True only when this host is the producer and local MCP is the live path."""
    role = str(status.get("role") or _rh_role() or "").lower()
    this_host = str(status.get("this_host") or _this_host() or "").lower()
    if role != "producer":
        return False
    if "gateway" in this_host:
        return False
    if os.environ.get("TREASURY_SKIP_LOCAL_MCP") == "1":
        return False
    if status.get("rh_mcp_enabled") is False:
        return False
    local_mcp = status.get("local_mcp")
    if isinstance(local_mcp, dict) and str(local_mcp.get("error") or "") in (
        "local_mcp_skipped",
        "no_refresh_path",
    ):
        return False
    return True


def rh_failure_should_page(status: Dict[str, Any]) -> Dict[str, Any]:
    """Gate RH #701 comments. Status/log stays; alert only for real producer failures.

    #555: skipped / no_refresh_path never alert (expected on gateway / non-producer).
    local_mcp_timeout does not alert when SoT as_of is under 6h, or when local MCP
    is not the live producer path (rh_mcp_enabled false/null + no MCP run).
    """
    if status.get("ok"):
        return {"page": False, "reason": "success"}
    err = str(status.get("error") or "")
    err_class = str(status.get("error_class") or classify_rh_error(err) or "")
    fresh, age, as_of = existing_rh_snapshot_fresh(status=status)
    if (
        err_class == "skipped"
        or "no_refresh_path" in err
        or err == "local_mcp_skipped"
        or "local_mcp_skipped" in err
    ):
        return {
            "page": False,
            "reason": "skipped_or_no_refresh_path",
            "error_class": err_class or "skipped",
            "age_hours": age,
            "as_of": as_of,
        }
    timeout = err_class == "timeout" or "local_mcp_timeout" in err
    mcp_live = local_mcp_is_live_producer_path(status)
    if timeout:
        if fresh:
            return {
                "page": False,
                "reason": "timeout_as_of_fresh",
                "age_hours": age,
                "as_of": as_of,
            }
        if not mcp_live:
            return {
                "page": False,
                "reason": "timeout_non_producer_mcp",
                "age_hours": age,
                "as_of": as_of,
            }
    role = status.get("role") or _rh_role()
    if role != "producer":
        return {"page": False, "reason": "not_producer", "error_class": err_class}
    return {"page": True, "reason": "producer_failure", "error_class": err_class}


def notify_rh_producer_failure(
    status: Dict[str, Any], *, force: bool = False
) -> Dict[str, Any]:
    """Page on producer-host refresh failure. Title includes host + error class."""
    role = status.get("role") or _rh_role()
    if status.get("ok"):
        return {"ok": True, "notified": False, "reason": "success"}
    gate = rh_failure_should_page(status)
    if not gate.get("page") and not force:
        return {
            "ok": True,
            "notified": False,
            "reason": gate.get("reason") or "gated",
            "gate": gate,
        }
    if role != "producer" and not force:
        return {"ok": True, "notified": False, "reason": "not_producer"}
    try:
        from treasury.fund_manager import notify_if_needed
    except Exception as exc:  # pragma: no cover - import guard
        return {"ok": False, "notified": False, "error": f"notify_import:{exc}"}
    host = status.get("producer_host") or _sot_host()
    err_class = status.get("error_class") or classify_rh_error(
        str(status.get("error") or "")
    )
    err = status.get("error") or "rh_refresh_failed"
    return notify_if_needed(
        decision_or_review={"kind": "hold", "outcome": "hold"},
        treasury_eval={
            "data_quality": {
                "stale": [
                    f"robinhood producer={host} error_class={err_class}: {err}"
                ]
            }
        },
        force=force,
    )


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
    push_files = _strip_rh_push([str(x) for x in push_files if x])
    return {
        "ssh": ssh,
        "remote_root": root,
        "connect_timeout_s": timeout,
        "max_age_hours": max_age,
        "mcp_timeout_s": mcp_timeout,
        "enabled": bool(enabled),
        "push_enabled": bool(push_enabled),
        "push_files": push_files,
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


def _snapshot_has_agentic(data: Any) -> bool:
    """True if the snapshot can satisfy fund_manager's agentic-book requirement."""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("agentic"), dict):
        return True
    return data.get("agentic_allowed") is True


def _is_primary_only_downgrade(remote: Any, local: Any) -> bool:
    """True when accepting remote would drop an existing agentic block."""
    return _snapshot_has_agentic(local) and not _snapshot_has_agentic(remote)


def _local_dual_fresh(
    max_age_hours: Optional[float] = None,
) -> Tuple[bool, Optional[datetime], Optional[float], Path]:
    """Local robinhood snapshot has agentic and is within max_age_hours."""
    dest = SNAPSHOTS_DIR / RH_SNAP
    if not dest.is_file():
        return False, None, None, dest
    if not _snapshot_has_agentic(load_json(dest)):
        return False, None, None, dest
    ok, as_of, _ = _valid_rh_snapshot(dest)
    if not ok:
        return False, as_of, None, dest
    age = _age_hours(as_of)
    if max_age_hours is None:
        max_age_hours = float(_pi_settings().get("max_age_hours") or DEFAULT_MAX_AGE_H)
    if age is not None and age > float(max_age_hours):
        return False, as_of, age, dest
    return True, as_of, age, dest


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

        local = SNAPSHOTS_DIR / RH_SNAP
        if _is_primary_only_downgrade(
            load_json(tmp), load_json(local) if local.is_file() else None
        ):
            out["error"] = "primary_only_downgrade"
            out["as_of"] = as_of.isoformat() if as_of else None
            out["age_hours"] = round(_age_hours(as_of), 2) if as_of else None
            out["remote_path"] = remote_used
            return out

        age = _age_hours(as_of)
        out["as_of"] = as_of.isoformat() if as_of else None
        out["age_hours"] = round(age, 2) if age is not None else None
        out["remote_path"] = remote_used

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
        out["error_class"] = "timeout"
        _attach_existing_as_of(out, dest)
        return out
    except OSError as e:
        out["error"] = f"local_mcp_spawn:{e}"
        out["error_class"] = "mcp"
        _attach_existing_as_of(out, dest)
        return out

    blob = " ".join(
        [
            out.get("error") or "",
            (r.stdout if r is not None else "") or "",
            (r.stderr if r is not None else "") or "",
        ]
    )
    err_class = classify_rh_error(blob)
    grok_failed = r is not None and r.returncode != 0
    if grok_failed or err_class == "auth_fail":
        if err_class == "auth_fail":
            out["error"] = "auth_fail"
            out["error_class"] = "auth_fail"
        else:
            out["error"] = out.get("error") or f"grok_exit_{r.returncode}"
            out["error_class"] = classify_rh_error(out["error"] + " " + blob)
        out["ok"] = False
        out["note"] = "left_existing_snapshot"
        _attach_existing_as_of(out, dest)
        return out

    if dest.is_file():
        ok, as_of, why = _valid_rh_snapshot(dest)
        after_mtime = dest.stat().st_mtime
        out["as_of"] = as_of.isoformat() if as_of else None
        out["age_hours"] = round(_age_hours(as_of) or 0.0, 2) if as_of else None
        if ok and (before_mtime is None or after_mtime > before_mtime + 0.5):
            out["ok"] = True
            out["error"] = None
            out["error_class"] = None
            return out
        if ok and as_of and (_age_hours(as_of) or 99) < 6:
            # grok exited 0 and file is still inside the freshness window
            out["ok"] = True
            out["error"] = None
            out["error_class"] = None
            out["note"] = "snapshot_already_fresh"
            return out
        out["error"] = out.get("error") or f"snapshot_not_updated:{why}"
        out["error_class"] = classify_rh_error(out["error"])
    else:
        out["error"] = "snapshot_missing_after_mcp"
        out["error_class"] = "mcp"
    return out


def _attach_existing_as_of(out: Dict[str, Any], dest: Path) -> None:
    """Keep the last honest as_of on failure — never invent a new stamp."""
    if not dest.is_file():
        return
    ok, as_of, _ = _valid_rh_snapshot(dest)
    if ok and as_of:
        out["as_of"] = as_of.isoformat()
        out["age_hours"] = round(_age_hours(as_of) or 0.0, 2)


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
    notify: bool = True,
) -> Dict[str, Any]:
    """Role-aware RH refresh. Producer (Pi) writes locally; Mac does not overwrite Pi."""
    role = _rh_role()
    sot = _sot_host()
    host = _this_host()
    result: Dict[str, Any] = {
        "ok": False,
        "source": None,
        "pi": None,
        "local_mcp": None,
        "push": None,
        "error": None,
        "error_class": None,
        "role": role,
        "producer_host": sot,
        "this_host": host,
    }

    if role == "producer":
        # Pi must not SSH to itself or echo RH back over SCP.
        prefer_pi = False
        push_to_pi = False
    elif role == "consumer" and os.environ.get("TREASURY_RH_BACKUP") != "1":
        # Scheduled Mac path pulls Pi. Local MCP is laptop-only backup.
        pass

    def _finish(payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("error") and not payload.get("error_class"):
            payload["error_class"] = classify_rh_error(str(payload.get("error") or ""))
        if payload.get("local_mcp") and isinstance(payload["local_mcp"], dict):
            lm = payload["local_mcp"]
            if lm.get("error") and not payload.get("error"):
                payload["error"] = lm.get("error")
                payload["error_class"] = lm.get("error_class") or classify_rh_error(
                    str(lm.get("error") or "")
                )
        try:
            write_producer_status(payload)
        except Exception:
            pass
        if notify and (not payload.get("ok")):
            try:
                payload["notify"] = notify_rh_producer_failure(payload)
            except Exception as exc:
                payload["notify"] = {"ok": False, "error": str(exc)}
        return payload

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
            return _finish(result)
        if str(pi.get("error") or "") == "primary_only_downgrade":
            fresh, as_of, age, path = _local_dual_fresh()
            if fresh:
                result["ok"] = True
                result["source"] = "local_existing"
                result["as_of"] = as_of.isoformat() if as_of else None
                result["age_hours"] = round(age, 2) if age is not None else None
                result["path"] = str(path)
                result["error"] = None
                result["note"] = "rejected_pi_primary_only_downgrade"
                return _finish(result)

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
            return _finish(result)
        result["error"] = local.get("error") or (result.get("pi") or {}).get("error")
        result["error_class"] = local.get("error_class") or classify_rh_error(
            str(result.get("error") or "")
        )
        return _finish(result)

    result["error"] = (result.get("pi") or {}).get("error") or "no_refresh_path"
    result["error_class"] = classify_rh_error(str(result.get("error") or ""))
    # Status/log keep the last honest as_of; #701 comments are gated (#555).
    _attach_existing_as_of(result, SNAPSHOTS_DIR / RH_SNAP)
    return _finish(result)


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
