"""Rebase the stored 11/24 + 11/25 Leg Press plan slot 205 -> 323 after the +118 backfill (applied 2026-11-25).
Usage: python3 plan_patch.py [dry|apply]. Superseded for 11/25 by plan_fix_213.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pi_env  # noqa: E402
_pi_env.load()
import json
from rt_dashboard.turso_http import connect
from rt_dashboard.workout_plan_store import plan_write_refusal
MODE = sys.argv[1] if len(sys.argv) > 1 else "dry"
OLD_R = "on 2026-11-08 @ 210 lb \u2192 205 lb"; NEW_R = "on 2026-11-08 @ 328 lb \u2192 323 lb"
with connect() as c:
    stmts = []
    for d in ("2026-11-24", "2026-11-25"):
        r = c.execute("SELECT user_id, payload FROM workout_plan WHERE local_today=?", (d,)).fetchone()
        if not r: print(d, "no plan row"); continue
        p = json.loads(r["payload"]); hits = [e for e in p["exercises"] if e.get("id") == "leg-press"]
        if len(hits) != 1: print("ABORT", d, "leg-press entries", len(hits)); sys.exit(2)
        e = hits[0]; pr = e["prescription"]; last = e["last"]
        st = (e["load"], e["weight_lbs"], pr["weight_lbs"], pr["load"], last["weight_lbs"], last["first_weight_lbs"], last["best_working_weight"], last["top_weight_lbs"], last["volume"])
        if st == (323.0,)*4 + (328.0,)*4 + (2624.0,) and NEW_R in e["rationale"]: print(d, "already 323 (replay no-op)"); continue
        if st != (205.0,)*4 + (210.0,)*4 + (1680.0,) or OLD_R not in e["rationale"] or (e["sets"], e["reps"], e["progression_reason"]) != (2, 5, "drop_load"):
            print("ABORT", d, "unexpected state", st, e["rationale"][:80]); sys.exit(3)
        ref = plan_write_refusal(d)
        if ref: print("ABORT refusal", d, ref); sys.exit(4)
        e["load"] = e["weight_lbs"] = pr["weight_lbs"] = pr["load"] = 323.0
        for k in ("weight_lbs", "first_weight_lbs", "best_working_weight", "top_weight_lbs"): last[k] = 328.0
        last["volume"] = 2624.0
        e["rationale"] = e["rationale"].replace(OLD_R, NEW_R)
        print(d, "Leg Press", "2x5 @ 205 -> 2x5 @", e["load"], "|", e["rationale"])
        stmts.append(("UPDATE workout_plan SET payload = ? WHERE user_id = ? AND local_today = ? AND payload = ?", (json.dumps(p, separators=(",", ":")), r["user_id"], d, r["payload"])))
    if MODE == "apply" and stmts:
        c.execute_transaction(stmts); print("APPLIED plan rows", len(stmts))
    else: print("plan rows to write", len(stmts), "(mode", MODE, ")")
