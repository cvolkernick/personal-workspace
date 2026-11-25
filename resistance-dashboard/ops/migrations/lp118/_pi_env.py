"""Load the running FitDash service environment (Turso URL/token, key paths) without printing it.

Run on the Pi only: ``python3 resistance-dashboard/ops/migrations/lp118/<script>.py [dry|apply]``.
Values are read from the live resistance-dashboard process and are never echoed.
"""
import os
import subprocess
import sys


def load() -> None:
    pid = subprocess.check_output(
        ["systemctl", "--user", "show", "-p", "MainPID", "--value", "resistance-dashboard"]
    ).decode().strip()
    with open(f"/proc/{pid}/environ", "rb") as fh:
        for kv in fh.read().split(b"\0"):
            if b"=" in kv:
                k, v = kv.split(b"=", 1)
                os.environ.setdefault(k.decode(), v.decode())
    app = os.path.expanduser(os.environ.get("FITDASH_APP_DIR", "~/apps/fitdash"))
    os.chdir(app)
    sys.path.insert(0, app)


def single_user_id() -> str:
    from rt_dashboard.turso_repo import list_workout_user_ids

    ids = list_workout_user_ids()
    if len(ids) != 1:
        raise SystemExit(f"ABORT: expected one workout user, found {len(ids)}")
    return ids[0]
