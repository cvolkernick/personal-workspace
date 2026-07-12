#!/usr/bin/env python3
"""Exercise GitHub lift-log pull + append/readback (remote + local mirror)."""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.github_client import GitHubLiftClient  # noqa: E402
from rt_dashboard.models import ExerciseEntry, Session, SetEntry  # noqa: E402


def main() -> int:
    ok = True

    remote = GitHubLiftClient(prefer_local=False)
    sessions = remote.pull_sessions()
    print(
        f"REMOTE_PULL ok sessions={len(sessions)} owner={remote.owner}/{remote.repo}"
    )
    if not sessions:
        print("REMOTE_PULL returned zero sessions")
        ok = False
    else:
        print(
            f"REMOTE_HEAD date={sessions[0].date} type={sessions[0].session_type} vol={sessions[0].volume}"
        )

    canary_names = [
        e.name
        for s in sessions
        for e in s.exercises
        if "Canary" in e.name
    ]
    print(f"REMOTE_CANARY_NAMES={canary_names}")
    if canary_names:
        print("REMOTE_CANARY_IN_SESSIONS=True")
    else:
        paths = remote.list_workout_paths()
        print(f"REMOTE_PATHS={paths}")
        if any("_e2e_canary" in p for p in paths):
            print("REMOTE_CANARY_FILE_LISTED=True")
        else:
            print("REMOTE_CANARY_MISSING")
            ok = False

    local_ws = str(ROOT.parent)
    with tempfile.TemporaryDirectory() as td:
        for path in (
            "fitness/workouts/push.md",
            "fitness/workouts/pull.md",
            "fitness/workouts/legs.md",
        ):
            src = Path(local_ws) / path
            dest = Path(td) / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

        client = GitHubLiftClient(prefer_local=True, local_fallback_dir=td)
        before = client.pull_sessions()
        canary_date = datetime.utcnow().strftime("%Y-%m-%d")
        session = Session(
            date=canary_date,
            session_type="push",
            exercises=[
                ExerciseEntry(
                    name="Dashboard Canary Press",
                    sets=[SetEntry(weight_lbs=42, sets=3, reps=8)],
                )
            ],
            notes="verify_github canary — local mirror",
        )
        result = client.append_workout_safe(session)
        after = client.pull_sessions()
        found = any(
            e.name == "Dashboard Canary Press"
            for s in after
            for e in s.exercises
        )
        disk = (Path(td) / "fitness/workouts/push.md").read_text(encoding="utf-8")
        disk_has = "Dashboard Canary Press" in disk
        print(
            f"LOCAL_MIRROR_ROUNDTRIP before={len(before)} after={len(after)} "
            f"found={found} verified_flag={result.get('verified_on_readback')} "
            f"disk_has={disk_has}"
        )
        if not (found and result.get("verified_on_readback") and disk_has):
            ok = False

    print(f"OVERALL_OK={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
