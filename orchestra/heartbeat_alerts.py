"""Threshold alerts for the Pi heartbeat → standing ops issue #701.

Transport follows #704: a GitHub comment on #701 only (no ntfy), silent to
the owner (no @mention). A comment is posted only when the alert state
changes, and a recovery note is posted when an alert clears.

Thresholds (env overrides, or a JSON file named by HEARTBEAT_THRESHOLDS):
  disk_warn_pct 80, disk_crit_pct 90       / filesystem Use%
  load_per_core 1.5                        15-minute load average per core
                                           (the kernel's 15m mean = sustained 15 min)
  temp_c 75                                SoC temperature
  unit_down_checks 2                       consecutive failed checks before a
                                           service counts as down
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DEFAULTS: dict[str, float] = {
    "disk_warn_pct": 80.0,
    "disk_crit_pct": 90.0,
    "load_per_core": 1.5,
    "temp_c": 75.0,
    "unit_down_checks": 2,
}
ENV_KEYS = {
    "disk_warn_pct": "HEARTBEAT_DISK_WARN_PCT",
    "disk_crit_pct": "HEARTBEAT_DISK_CRIT_PCT",
    "load_per_core": "HEARTBEAT_LOAD_PER_CORE",
    "temp_c": "HEARTBEAT_TEMP_C",
    "unit_down_checks": "HEARTBEAT_UNIT_DOWN_CHECKS",
}
OPS_REPO = "cvolkernick/personal-workspace"
DEFAULT_OPS_ISSUE = "701"
STATE_NAME = "alerts_state.json"
LEVEL_RANK = {"green": 0, "amber": 1, "red": 2}


def load_thresholds(env: Optional[dict[str, str]] = None) -> dict[str, float]:
    env = os.environ if env is None else env
    out = dict(DEFAULTS)
    path = (env.get("HEARTBEAT_THRESHOLDS") or "").strip()
    if path:
        try:
            data = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
            for k in DEFAULTS:
                if isinstance(data, dict) and isinstance(data.get(k), (int, float)):
                    out[k] = float(data[k])
        except (OSError, json.JSONDecodeError):
            pass
    for k, name in ENV_KEYS.items():
        raw = (env.get(name) or "").strip()
        if raw:
            try:
                out[k] = float(raw)
            except ValueError:
                pass
    out["unit_down_checks"] = max(1, int(out["unit_down_checks"]))
    return out


def _service_failing(svc: dict[str, Any]) -> bool:
    if svc.get("health_ok") is not None:
        return not bool(svc.get("health_ok"))
    return not bool(svc.get("active"))


def evaluate(
    doc: dict[str, Any],
    state: Optional[dict[str, Any]],
    thresholds: Optional[dict[str, float]] = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (active_alerts, new_state). Pure: no I/O.

    active_alerts maps a stable key to {"level", "text"}.
    """
    th = thresholds or load_thresholds()
    state = dict(state or {})
    counts: dict[str, int] = dict(state.get("down_counts") or {})
    alerts: dict[str, dict[str, Any]] = {}
    v = doc.get("vitals") or {}

    disk = v.get("disk_pct")
    if isinstance(disk, (int, float)):
        mount = v.get("disk_mount") or "/"
        if disk >= th["disk_crit_pct"]:
            alerts["disk"] = {"level": "red", "text": f"disk {mount} {disk}% ≥ {th['disk_crit_pct']:g}%"}
        elif disk >= th["disk_warn_pct"]:
            alerts["disk"] = {"level": "amber", "text": f"disk {mount} {disk}% ≥ {th['disk_warn_pct']:g}%"}

    load15 = (v.get("load_per_core") or {}).get("15m")
    if isinstance(load15, (int, float)) and load15 > th["load_per_core"]:
        alerts["load"] = {
            "level": "amber",
            "text": f"load {load15}/core (15m) > {th['load_per_core']:g}",
        }

    temp = v.get("temp_c")
    if isinstance(temp, (int, float)) and temp >= th["temp_c"]:
        alerts["temp"] = {"level": "amber", "text": f"temp {temp}°C ≥ {th['temp_c']:g}°C"}

    sev_by_name = {d.get("service"): d.get("severity") for d in doc.get("degraded") or []}
    seen = set()
    for svc in doc.get("services") or []:
        name = str(svc.get("name"))
        seen.add(name)
        if _service_failing(svc):
            counts[name] = counts.get(name, 0) + 1
        else:
            counts[name] = 0
        if counts[name] >= th["unit_down_checks"]:
            level = "red" if sev_by_name.get(name) == "red" else "amber"
            note = svc.get("note") or "unhealthy"
            alerts[f"svc:{name}"] = {
                "level": level,
                "text": f"{name} down {counts[name]} checks ({note})",
            }
    counts = {k: c for k, c in counts.items() if k in seen}
    state["down_counts"] = counts
    return alerts, state


def overall_level(alerts: dict[str, dict[str, Any]], doc: dict[str, Any]) -> str:
    level = "green"
    for a in alerts.values():
        if LEVEL_RANK[a["level"]] > LEVEL_RANK[level]:
            level = a["level"]
    return level


def transition(
    prev: dict[str, dict[str, Any]], cur: dict[str, dict[str, Any]]
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Diff two alert maps. Unchanged levels are not a change."""
    new = [(k, a) for k, a in cur.items() if k not in prev]
    changed = [(k, a) for k, a in cur.items() if k in prev and prev[k]["level"] != a["level"]]
    recovered = [(k, a) for k, a in prev.items() if k not in cur]
    return {"new": new, "changed": changed, "recovered": recovered}


def format_comment(host: str, level: str, diff: dict[str, list], cur: dict[str, dict[str, Any]]) -> tuple[str, str]:
    icon = {"green": "🟢", "amber": "🟠", "red": "🔴"}[level]
    if level == "green":
        title = f"{icon} {host}: recovered, all clear"
    else:
        title = f"{icon} {host}: {level} ({len(cur)} alert{'s' if len(cur) != 1 else ''})"
    lines = []
    for k, a in diff["new"]:
        lines.append(f"NEW      [{a['level']}] {a['text']}")
    for k, a in diff["changed"]:
        lines.append(f"CHANGED  [{a['level']}] {a['text']}")
    for k, a in diff["recovered"]:
        lines.append(f"RECOVERED  {a['text']}")
    still = [a for k, a in cur.items() if k not in {x for x, _ in diff["new"] + diff["changed"]}]
    for a in still:
        lines.append(f"ONGOING  [{a['level']}] {a['text']}")
    return title, "\n".join(lines) or "no alerts"


def build_ops_comment_body(title: str, text: str) -> str:
    """#704/#659: comment on #701, silent to owner (no @mention)."""
    routing = (
        "Silent to owner (#659): no owner mention. Team path: this #701 "
        "comment (#704 transport). Posted on state change only."
    )
    return f"**{title}**\n\n{routing}\n\n```\n{text}\n```\n"


def _github_token(env: Optional[dict[str, str]] = None) -> str:
    env = os.environ if env is None else env
    for k in ("GITHUB_TOKEN", "GH_TOKEN", "BUZZ_BOARD_GITHUB_TOKEN"):
        v = (env.get(k) or "").strip()
        if v:
            return v
    return ""


def load_env_file(path: Path) -> None:
    """KEY=VALUE loader that never logs values and never overrides the env."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:]
        k, _, val = line.partition("=")
        k = k.strip()
        if k and k not in os.environ:
            os.environ[k] = val.strip().strip("'").strip('"')


def post_ops_comment(title: str, text: str, *, http_post: Any = None) -> dict[str, Any]:
    issue = (os.environ.get("PI_OPS_ALERT_ISSUE") or DEFAULT_OPS_ISSUE).strip()
    token = _github_token()
    if not token:
        return {"posted": False, "skipped": "no-github-token", "issue": issue}
    url = f"https://api.github.com/repos/{OPS_REPO}/issues/{issue}/comments"
    body = json.dumps({"body": build_ops_comment_body(title, text)}).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "personal-workspace-pi-heartbeat",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        if http_post is not None:
            http_post(url, body, headers)
        else:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15):
                pass
        return {"posted": True, "issue": issue}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"posted": False, "error": type(e).__name__, "issue": issue}


def run_alerts(
    doc: dict[str, Any],
    state_path: Path,
    *,
    thresholds: Optional[dict[str, float]] = None,
    post: Any = None,
    now: Optional[str] = None,
) -> dict[str, Any]:
    """Evaluate, annotate ``doc`` (alerts/status), post on change, persist state.

    If posting fails the previous alert set is kept, so the next check retries.
    """
    try:
        prev_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prev_state = {}
    alerts, state = evaluate(doc, prev_state, thresholds)
    level = overall_level(alerts, doc)
    doc["status"] = level
    doc["alerts"] = [dict(key=k, **a) for k, a in sorted(alerts.items())]
    prev_alerts = prev_state.get("alerts") or {}
    diff = transition(prev_alerts, alerts)
    result: dict[str, Any] = {"status": level, "changed": False}
    state["alerts"] = alerts
    if diff["new"] or diff["changed"] or diff["recovered"]:
        result["changed"] = True
        title, text = format_comment(str(doc.get("host") or "?"), level, diff, alerts)
        out = (post or post_ops_comment)(title, text)
        result["post"] = out
        if not out.get("posted") and out.get("skipped") != "no-github-token":
            state["alerts"] = prev_alerts  # retry next check
    state["updated_at"] = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_name(state_path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, state_path)
    return result
