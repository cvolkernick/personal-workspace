"""Repo-backed health metrics (weight/sleep) with optional live remote fetch.

Primary path remains Google Fit OAuth (google_health.py). This module provides:
1. Parse the existing Fitbit report in the workspace into structured metrics
2. Load metrics from fitness/data/health-metrics.json (local or GitHub Contents API)
so recovery status can factor real weight/sleep even when Fit OAuth is not configured.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional, Tuple

from .models import HealthSnapshot, SleepSample, WeightSample

DEFAULT_REL_PATH = "fitness/data/health-metrics.json"
FITBIT_REPORT_REL = "fitness/data/fitbit-report-may2026.md"


def parse_fitbit_report_markdown(text: str) -> Tuple[List[WeightSample], List[SleepSample]]:
    """Extract weight table + average sleep from the Fitbit report markdown."""
    weights: List[WeightSample] = []
    # Lines like: | 04-20 | 86.1 lbs |  or | 05-19 | 83.1 lbs |
    for m in re.finditer(
        r"\|\s*(\d{2})-(\d{2})\s*\|\s*(\d+(?:\.\d+)?)\s*lbs?\s*\|",
        text,
        re.IGNORECASE,
    ):
        mm, dd, w = m.group(1), m.group(2), float(m.group(3))
        # Report period is 2026
        year = 2026
        date = f"{year}-{mm}-{dd}"
        weights.append(
            WeightSample(date=date, weight_lbs=w, source="fitbit_report")
        )

    sleep: List[SleepSample] = []
    avg_m = re.search(
        r"Average Sleep\s*\|\s*\*?\*?(\d+(?:\.\d+)?)\s*hours?",
        text,
        re.IGNORECASE,
    )
    nights_m = re.search(
        r"Total Nights Logged\s*\|\s*\*?\*?(\d+)",
        text,
        re.IGNORECASE,
    )
    if avg_m and weights:
        avg_h = float(avg_m.group(1))
        # synthesize per-night samples for the same dates as weight (up to nights)
        n = int(nights_m.group(1)) if nights_m else min(14, len(weights))
        for ws in weights[-n:]:
            sleep.append(
                SleepSample(
                    date=ws.date,
                    sleep_hours=avg_h,
                    efficiency_pct=None,
                    source="fitbit_report",
                )
            )
    return weights, sleep


def build_metrics_payload(
    weights: List[WeightSample], sleep: List[SleepSample], note: str = ""
) -> dict:
    return {
        "source_note": note,
        "weight": [w.to_dict() for w in weights],
        "sleep": [s.to_dict() for s in sleep],
    }


def metrics_from_payload(data: dict) -> HealthSnapshot:
    weights = [
        WeightSample(
            date=str(w["date"]),
            weight_lbs=float(w["weight_lbs"]),
            source=str(w.get("source") or "health_metrics_store"),
        )
        for w in data.get("weight") or []
    ]
    sleep = [
        SleepSample(
            date=str(s["date"]),
            sleep_hours=float(s["sleep_hours"]),
            efficiency_pct=s.get("efficiency_pct"),
            source=str(s.get("source") or "health_metrics_store"),
        )
        for s in data.get("sleep") or []
    ]
    return HealthSnapshot(weight=weights, sleep=sleep, error=None)


def load_metrics_file(path: str) -> Optional[HealthSnapshot]:
    p = Path(path)
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return metrics_from_payload(data)


def fetch_metrics_from_github(
    owner: str = "cvolkernick",
    repo: str = "personal-workspace",
    path: str = DEFAULT_REL_PATH,
    branch: str = "master",
    token: str = "",
) -> Optional[HealthSnapshot]:
    """Live pull of health-metrics.json via GitHub Contents API or raw URL."""
    # Prefer Contents API (same as lift logs)
    api = f"https://api.github.com/repos/{owner}/{repo}/contents/{path}?ref={branch}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "resistance-dashboard/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(api, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            meta = json.loads(resp.read().decode("utf-8"))
        import base64

        content = base64.b64decode(meta.get("content", "").replace("\n", "")).decode(
            "utf-8"
        )
        return metrics_from_payload(json.loads(content))
    except Exception:
        # raw fallback
        raw = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        try:
            req2 = urllib.request.Request(raw, headers={"User-Agent": "resistance-dashboard/1.0"})
            with urllib.request.urlopen(req2, timeout=30) as resp:
                return metrics_from_payload(json.loads(resp.read().decode("utf-8")))
        except Exception:
            return None


def ensure_local_metrics_from_fitbit_report(workspace_dir: str) -> Optional[str]:
    """
    If health-metrics.json missing, generate it from fitbit report.
    Returns path written or existing path, or None.
    """
    ws = Path(workspace_dir)
    out = ws / DEFAULT_REL_PATH
    report = ws / FITBIT_REPORT_REL
    if out.is_file():
        return str(out)
    if not report.is_file():
        return None
    weights, sleep = parse_fitbit_report_markdown(report.read_text(encoding="utf-8"))
    if not weights:
        return None
    payload = build_metrics_payload(
        weights,
        sleep,
        note="Derived from fitness/data/fitbit-report-may2026.md (scale/sleep summary)",
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return str(out)


def resolve_health_snapshot(
    google_snapshot: HealthSnapshot,
    workspace_dir: str = "",
    github_token: str = "",
) -> HealthSnapshot:
    """
    Prefer live Google Fit data when present; otherwise load repo health metrics
    (local file and/or live GitHub fetch).
    """
    if google_snapshot.weight or google_snapshot.sleep:
        if not google_snapshot.error:
            return google_snapshot
        # partial google data still preferred
        if google_snapshot.weight and google_snapshot.sleep:
            return google_snapshot

    # Ensure local metrics file exists from Fitbit report if needed
    if workspace_dir:
        ensure_local_metrics_from_fitbit_report(workspace_dir)

    local_snap = None
    if workspace_dir:
        local_snap = load_metrics_file(
            str(Path(workspace_dir) / DEFAULT_REL_PATH)
        )

    remote_snap = fetch_metrics_from_github(
        owner=os.environ.get("GITHUB_OWNER", "cvolkernick"),
        repo=os.environ.get("GITHUB_REPO", "personal-workspace"),
        branch=os.environ.get("GITHUB_BRANCH", "master"),
        token=github_token or os.environ.get("GITHUB_TOKEN", ""),
    )

    # Prefer remote live store if it has data, else local
    for snap, label in (
        (remote_snap, "github_health_metrics"),
        (local_snap, "local_health_metrics"),
    ):
        if snap and (snap.weight or snap.sleep):
            # annotate sources
            for w in snap.weight:
                if not w.source or w.source == "health_metrics_store":
                    w.source = label
            for s in snap.sleep:
                if not s.source or s.source == "health_metrics_store":
                    s.source = label
            # Preserve Google error as note if present (clearer product name)
            if google_snapshot.error:
                snap.error = (
                    f"Google Health: {google_snapshot.error}; "
                    f"using {label} weight/sleep for recovery"
                )
            return snap

    # Nothing available
    return google_snapshot
