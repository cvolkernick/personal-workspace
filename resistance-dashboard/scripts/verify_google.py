#!/usr/bin/env python3
"""Exercise Google Fit client + live health-metrics fallback + recovery inputs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from rt_dashboard.google_health import (  # noqa: E402
    GoogleHealthClient,
    parse_recorded_sleep_payload,
    parse_recorded_weight_payload,
)
from rt_dashboard.health_metrics_store import (  # noqa: E402
    ensure_local_metrics_from_fitbit_report,
    fetch_metrics_from_github,
    resolve_health_snapshot,
)
from rt_dashboard.recovery import compute_recovery_status  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"


def main() -> int:
    print("=== Parser fixtures (wire format) ===")
    w = parse_recorded_weight_payload(
        json.loads((FIXTURES / "google_weight_aggregate.json").read_text())
    )
    s = parse_recorded_sleep_payload(
        json.loads((FIXTURES / "google_sleep_sessions.json").read_text())
    )
    print(f"fixture_weight_samples={len(w)} first_lbs={w[0].weight_lbs:.2f}")
    print(f"fixture_sleep_samples={len(s)} first_hours={s[0].sleep_hours}")

    print("=== Live Google Fit client ===")
    client = GoogleHealthClient()
    print(f"credentials_present={client.credentials_present()}")
    print(f"client_id_set={bool(client.client_id)}")
    google = client.fetch_health(days=14)
    print(f"google_error={google.error}")
    print(f"google_weight={len(google.weight)} google_sleep={len(google.sleep)}")

    print("=== Live GitHub health-metrics.json ===")
    remote = fetch_metrics_from_github()
    if remote:
        print(f"remote_health weight={len(remote.weight)} sleep={len(remote.sleep)}")
        if remote.weight:
            print(f"latest_weight={remote.weight[-1].to_dict()}")
        if remote.sleep:
            print(f"latest_sleep={remote.sleep[-1].to_dict()}")
    else:
        print("remote_health=None")

    print("=== Resolved health + recovery ===")
    ws = str(ROOT.parent)
    ensure_local_metrics_from_fitbit_report(ws)
    resolved = resolve_health_snapshot(google, workspace_dir=ws)
    print(
        f"resolved_weight={len(resolved.weight)} resolved_sleep={len(resolved.sleep)}"
    )
    print(f"resolved_error={resolved.error}")
    status = compute_recovery_status(
        weight=resolved.weight, sleep=resolved.sleep, sessions=[]
    )
    print(f"recovery_label={status.label} recovery_inputs={status.inputs}")

    live_github = bool(remote and remote.weight and remote.sleep)
    resolved_ok = bool(resolved.weight and resolved.sleep)
    recovery_ok = (
        status.inputs.get("latest_weight_lbs") is not None
        and status.inputs.get("avg_sleep_hours_7d") is not None
    )
    google_live = not google.error and bool(google.weight)
    print(f"LIVE_GITHUB_HEALTH_OK={live_github}")
    print(f"RESOLVED_HEALTH_OK={resolved_ok}")
    print(f"RECOVERY_HAS_WEIGHT_SLEEP={recovery_ok}")
    print(f"GOOGLE_FIT_LIVE_OK={google_live}")

    # Pass if we have live health data feeding recovery (GitHub metrics or Fit)
    return 0 if (live_github or google_live) and recovery_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
