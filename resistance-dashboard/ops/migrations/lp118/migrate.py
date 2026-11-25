"""One-time Leg Press +118 lb sled backfill (2026-11-25). Usage: python3 migrate.py [dry|apply] (dry by default)"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pi_env  # noqa: E402
_pi_env.load()

import json, copy, re, datetime as dt
from rt_dashboard.turso_http import connect
from rt_dashboard.crypto_box import open_str, seal_str
from rt_dashboard.workout_repo import _exercises_from_json, _epley_pin
from rt_dashboard.models import Session, _fmt_weight_lbs
from rt_dashboard.pr_detect import apply_auto_prs, _pr_name_key
MODE = (sys.argv[1] if len(sys.argv) > 1 else "dry")
SLED = 118.0
KEY = "leg_press_sled_backfill_2026-11-25"
CUTOFF_UTC = "2026-11-25T05:00:00Z"   # 2026-11-25 00:00 ET
LAST_DATE = "2026-11-08"
UID = _pi_env.single_user_id()
# Expected BEFORE state per session id: (date, ex_idx, [(w,sets,reps)...])
EXPECT = {
 44:("2026-03-19",0,[(140,3,10)]), 41:("2026-03-24",0,[(145,3,10)]), 38:("2026-03-30",0,[(150,3,10)]),
 35:("2026-04-04",2,[(155,1,10),(160,1,10),(165,1,10)]), 32:("2026-04-07",2,[(160,3,12)]),
 29:("2026-04-13",2,[(165,3,10)]), 26:("2026-04-17",1,[(180,1,8),(175,1,10),(170,1,12)]),
 23:("2026-04-23",1,[(200,1,12),(190,1,12),(180,1,12)]), 20:("2026-04-27",0,[(220,1,10),(210,2,10)]),
 17:("2026-05-03",1,[(210,2,10),(210,1,8)]), 14:("2026-05-09",1,[(270,6,3)]),
 10:("2026-05-13",0,[(270,1,6),(230,1,8),(220,1,10)]), 7:("2026-05-19",0,[(270,3,8)]),
 4:("2026-05-25",1,[(280,1,8),(275,1,10),(270,1,12)]),
 53:("2026-09-08",1,[(180,2,8)]), 57:("2026-09-16",0,[(180,2,9)]), 64:("2026-09-27",3,[(180,2,10)]),
 68:("2026-09-30",4,[(180,1,11)]), 71:("2026-10-04",4,[(185,1,5)]), 75:("2026-10-10",1,[(190,1,6)]),
 81:("2026-10-14",4,[(190,1,7)]), 84:("2026-10-19",4,[(190,1,8)]), 87:("2026-10-26",3,[(200,2,6)]),
 93:("2026-10-30",3,[(200,2,7)]), 96:("2026-11-04",1,[(200,1,8),(210,1,5)]), 99:("2026-11-08",1,[(210,2,4)]),
}
def utc(s):
    s = s.replace("Z", "+00:00"); d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc)
CUT = utc(CUTOFF_UTC)
def is_lp(n): return bool(re.search(r"leg\s*press", n or "", re.I))
def triples(ex): return [(float(s.get("weight_lbs") or 0), int(s.get("sets") or 1), int(s.get("reps") or 0)) for s in ex.get("sets") or []]
def same(a, b): return len(a) == len(b) and all(abs(x[0]-y[0]) < 0.01 and x[1]==y[1] and x[2]==y[2] for x, y in zip(a, b))
def is_tonight(row, exs):
    # content guard: any Leg Press 208x10, or created at/after cutoff, or date after LAST_DATE
    lp208 = any(is_lp(e.get("name")) and any(abs(t[0]-208) < .01 and t[2]==10 for t in triples(e)) for e in exs)
    return lp208 or utc(row["created_at"]) >= CUT or row["date"] > LAST_DATE
def recompute(ex):
    vol = be = bw = 0.0
    for s in ex["sets"]:
        w, n, r = float(s["weight_lbs"]), int(s.get("sets") or 1), int(s.get("reps") or 0)
        vol += w*n*r; be = max(be, _epley_pin(w, r)); bw = max(bw, w)
    ex["volume"], ex["best_e1rm"], ex["best_working_weight"] = vol, be, bw
    if "sets_label" in ex:
        ex["sets_label"] = ", ".join(f"{_fmt_weight_lbs(float(s['weight_lbs']))} lbs x {int(s.get('sets') or 1)} x {int(s.get('reps') or 0)}" for s in ex["sets"])
def tonnage90(sessions):  # sessions: list of (date, exs) ; window 2026-08-28..2026-11-25
    t = 0.0
    for d, exs in sessions:
        if "2026-08-28" <= d <= "2026-11-25":
            for e in exs:
                if _pr_name_key(e.get("name","")) == _pr_name_key("Leg Press"): t += sum(w*n*r for w, n, r in triples(e))
    return t
with connect() as c:
    meta = c.execute("SELECT value FROM schema_meta WHERE key = ?", (KEY,)).fetchone()
    rows = c.execute("SELECT id,user_id,date,session_type,created_at,updated_at,status,exercises_json FROM workout_sessions ORDER BY date, id").fetchall()
    open_sessions = [(r["id"], r["date"], r["session_type"], r["status"], r["updated_at"]) for r in rows if (r["status"] or "") == "in_progress"]
    print("open_sessions", open_sessions)
    print("marker_present", bool(meta))
    dec = {}; plain_all = []
    for r in rows:
        raw = r["exercises_json"] or "[]"
        enc = not raw.lstrip().startswith("[")
        exs = json.loads(open_str(raw, aad=f"user:{r['user_id']}:workout") if enc else raw)
        dec[r["id"]] = (r, exs, enc)
        plain_all.append((r["date"], exs))
    names = sorted({e.get("name") for _, exs in plain_all for e in exs if is_lp(e.get("name"))})
    print("lp_names", names)
    if names != ["Leg Press"]: print("ABORT: unexpected leg press names"); sys.exit(2)
    # classify every Leg Press occurrence
    targets, excluded, problems = [], [], []
    for sid, (r, exs, enc) in dec.items():
        for i, e in enumerate(exs):
            if not is_lp(e.get("name")): continue
            if is_tonight(r, exs):
                excluded.append((sid, r["date"], r["created_at"], r["status"], i, triples(e), [x.get("name") for x in exs])); continue
            if sid not in EXPECT: problems.append(("unexpected_lp_row", sid, r["date"], triples(e))); continue
            d, idx, before = EXPECT[sid]
            if d != r["date"] or idx != i or r["user_id"] != UID: problems.append(("shape", sid, r["date"], i)); continue
            after = [(w+SLED, n, rr) for w, n, rr in before]
            cur = triples(e)
            state = "before" if same(cur, before) else "after" if same(cur, after) else "neither"
            if state == "neither": problems.append(("neither", sid, r["date"], cur))
            targets.append((sid, i, state, before, after))
    missing = set(EXPECT) - {t[0] for t in targets}
    if missing: problems.append(("missing", sorted(missing)))
    print("excluded (tonight/after-cutoff):"); [print("  ", x) for x in excluded]
    if len(excluded) != 1 or not any(abs(t[0]-208) < .01 and t[2]==10 for t in excluded[0][5]):
        problems.append(("tonight_not_clearly_identified", len(excluded)))
    if problems: print("ABORT problems:", problems); sys.exit(3)
    pending = [t for t in targets if t[2] == "before"]
    print(f"targets={len(targets)} pending={len(pending)} already_after={len(targets)-len(pending)}")
    # build patched copy
    new = {sid: copy.deepcopy(exs) for sid, (r, exs, enc) in dec.items()}
    for sid, i, state, before, after in pending:
        ex = new[sid][i]
        for s in ex["sets"]: s["weight_lbs"] = float(s["weight_lbs"]) + SLED
        recompute(ex)
    # PRs: apply_auto_prs in date order over patched history; take Leg Press flags only
    sess = []
    for sid, (r, exs, enc) in dec.items():
        sess.append((sid, Session(date=r["date"], session_type=r["session_type"], exercises=_exercises_from_json(json.dumps(new[sid])))))
    sess.sort(key=lambda x: (x[1].date, x[0]))
    hist = [s for _, s in sess]
    other_pr_drift = []
    for sid, s in sess:
        if sid not in EXPECT: continue
        apply_auto_prs(s, hist)
        for j, e in enumerate(s.exercises):
            if is_lp(e.name):
                new[sid][j]["is_pr"] = bool(e.is_pr)
            elif bool(e.is_pr) != bool(new[sid][j].get("is_pr")):
                other_pr_drift.append((sid, s.date, e.name))
    print("other_exercise_pr_drift_not_written", other_pr_drift)
    changed = []
    total_sets = 0
    for sid, i, state, before, after in targets:
        r, exs, enc = dec[sid]
        o, n = exs[i], new[sid][i]
        sv_o = sum(float(e.get("volume") or 0) for e in exs); sv_n = sum(float(e.get("volume") or 0) for e in new[sid])
        nsets = sum(t[1] for t in before); total_sets += nsets
        print(f"{r['date']} id={sid} sets={nsets}  {triples(o)} -> {triples(n)}  vol {o.get('volume')}->{n.get('volume')}  e1 {round(o.get('best_e1rm') or 0,2)}->{round(n.get('best_e1rm'),2)}  bww {o.get('best_working_weight')}->{n.get('best_working_weight')}  pr {o.get('is_pr')}->{n.get('is_pr')}  session_vol {sv_o}->{sv_n}")
        # non-LP exercises must be byte-identical
        for j, e in enumerate(exs):
            if j != i and json.dumps(e, sort_keys=True) != json.dumps(new[sid][j], sort_keys=True): print("ABORT other exercise changed", sid, j); sys.exit(4)
        if json.dumps(exs, sort_keys=True) != json.dumps(new[sid], sort_keys=True): changed.append(sid)
    print("total_set_objects_targets", sum(len(t[3]) for t in targets), "total_working_sets", total_sets)
    t_before = tonnage90(plain_all); t_after = tonnage90([(r["date"], new[sid]) for sid, (r, exs, enc) in dec.items()])
    print("tonnage90_before", t_before, "tonnage90_after", t_after)
    allt_b = sum(sum(w*n*rr for w,n,rr in triples(e)) for _, exs in plain_all for e in exs if is_lp(e.get("name")))
    allt_a = sum(sum(w*n*rr for w,n,rr in triples(e)) for sid in new for e in new[sid] if is_lp(e.get("name")))
    print("tonnage_all_time_before", allt_b, "after", allt_a)
    print("rows_to_write", len(changed), changed)
    if MODE != "apply":
        print("DRY RUN: no writes"); sys.exit(0)
    if open_sessions: print("ABORT: open session present; not writing"); sys.exit(5)
    if not changed:
        print("REPLAY: 0 rows changed"); sys.exit(0)
    stmts = []
    for sid in changed:
        r, exs, enc = dec[sid]
        payload = json.dumps(new[sid], separators=(",", ":"))
        payload = seal_str(payload, aad=f"user:{r['user_id']}:workout") if enc else payload
        stmts.append(("UPDATE workout_sessions SET exercises_json = ? WHERE id = ? AND exercises_json = ?", (payload, sid, r["exercises_json"])))
    marker = {"cutoff_utc": CUTOFF_UTC, "last_date": LAST_DATE, "sled_lb": SLED, "sot": "turso",
              "excluded": [{"id": x[0], "date": x[1], "created_at": x[2], "sets": x[5]} for x in excluded],
              "rows": [{"id": sid, "date": dec[sid][0]["date"], "ex_idx": i, "before": before, "after": after} for sid, i, st, before, after in targets],
              "applied_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    stmts.append(("INSERT INTO schema_meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (KEY, json.dumps(marker, separators=(",", ":")))))
    c.execute_transaction(stmts)
    print("APPLIED rows", len(changed), "marker written")
