"""Correct the stale stored Leg Press slot in the 2026-11-25 plan row (323 -> planner value from latest log).
Usage: python3 - [dry|apply]. Dry by default. Leg Press slot only; other exercises untouched."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pi_env  # noqa: E402
_pi_env.load()

import json, copy
from rt_dashboard.turso_http import connect
from rt_dashboard.workout_repo import _row_to_session
from rt_dashboard.turso_repo import LIST_SQL
from rt_dashboard.workout_plan_store import plan_write_refusal
from rt_dashboard import workout_planner as wp
MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
UID = _pi_env.single_user_id(); DAY = "2026-11-25"
with connect() as c:
    sessions = [_row_to_session(r, UID) for r in c.execute(LIST_SQL, (UID,)).fetchall()]
    cat = wp.default_catalog(); by_id = {e["id"]: e for e in wp.catalog_exercises(cat, available_only=False)}
    last = wp.last_performance(sessions, "Leg Press", by_id)
    if not last or last["date"] != "2026-11-24" or abs(last["first_weight_lbs"] - 208) > .01 or last["first_reps"] != 10:
        print("ABORT: latest Leg Press is not 11/24 208x10:", last); sys.exit(2)
    rx = wp.prescribe(by_id["leg-press"], last, default_hard_sets=2)
    tgt = float(rx["weight_lbs"])
    r = c.execute("SELECT payload FROM workout_plan WHERE user_id=? AND local_today=?", (UID, DAY)).fetchone()
    p = json.loads(r["payload"]); hits = [e for e in p["exercises"] if e.get("id") == "leg-press"]
    if len(hits) != 1: print("ABORT: leg-press slots", len(hits)); sys.exit(3)
    e = hits[0]; before = copy.deepcopy(e)
    if abs(float(e["weight_lbs"]) - tgt) < .01 and e["last"]["date"] == "2026-11-24": print("already corrected (replay no-op)"); sys.exit(0)
    if (e["weight_lbs"], e["load"], e["prescription"]["weight_lbs"], e["prescription"]["load"]) != (323.0,)*4:
        print("ABORT: unexpected current slot", e["weight_lbs"], e["load"]); sys.exit(4)
    others_before = json.dumps([x for x in p["exercises"] if x is not e], sort_keys=True)
    e["load"] = e["weight_lbs"] = tgt
    # keep the slot's 2 sets (raw prescribe() carries the 1 logged set; the generator sizes sets)
    e["reps"], e["target_reps"] = int(rx["reps"]), int(rx.get("target_reps") or rx["reps"])
    e["progression_reason"] = rx["progression_reason"]
    e["rationale"] = rx["rationale"] + " Squat pattern kept on the legs day."
    pr = e["prescription"]
    pr.update({"weight_lbs": tgt, "load": tgt, "sets": e["sets"], "reps": e["reps"], "target_reps": e["target_reps"], "progression_reason": e["progression_reason"]})
    e["last"] = {k: last[k] for k in before["last"].keys() if k in last}
    assert json.dumps([x for x in p["exercises"] if x is not e], sort_keys=True) == others_before
    keys = ("sets", "reps", "weight_lbs", "load", "progression_reason", "rationale")
    print("BEFORE", {k: before[k] for k in keys}, "last", before["last"]["date"], before["last"]["first_weight_lbs"], "x", before["last"]["first_reps"])
    print("AFTER ", {k: e[k] for k in keys}, "last", e["last"]["date"], e["last"]["first_weight_lbs"], "x", e["last"]["first_reps"])
    print("other exercises unchanged:", True, "| refusal:", plan_write_refusal(DAY))
    if MODE == "apply":
        c.execute_transaction([("UPDATE workout_plan SET payload=? WHERE user_id=? AND local_today=? AND payload=?", (json.dumps(p, separators=(",", ":")), UID, DAY, r["payload"]))])
        print("APPLIED 1 plan row")
    else: print("DRY RUN: no writes")
