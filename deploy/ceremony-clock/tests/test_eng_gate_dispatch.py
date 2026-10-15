"""pi-dispatch (ceremony_eng_gate_dispatch) - issue #1195.

Worker line, 24h re-staff cooldown, stale-claim release, live-only WIP,
claim/kick ordering and the consumer health gate. No network: a fake GitHub
client and an injected kick sender / relay query are used throughout.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import ceremony_eng_gate_dispatch as d

NOW = datetime(2026, 10, 15, 21, 0, 0, tzinfo=timezone.utc)
CHANNEL = "00000000-0000-0000-0000-00000000c0de"
ROSTER = d.Roster(
    channel_id=CHANNEL,
    agents={"Forge": "aa" * 32, "Frankenfit": "bb" * 32, "Nakatoshi": "cc" * 32, "Cadence": "dd" * 32},
)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def issue(n, title="Something", body="", labels=("status:ready",), updated=None):
    return {
        "number": n,
        "title": title,
        "body": body,
        "labels": [{"name": x} for x in labels],
        "html_url": f"https://example.invalid/issues/{n}",
        "updated_at": iso(updated or ago(hours=1)),
    }


def comment(body, at):
    return {"body": body, "created_at": iso(at)}


def claim_comment(owner, at):
    return comment(f"[pi-dispatch] started \u2014 staffed to {owner} in #workflow. WIP=1. Do not self-merge.", at)


class FakeGH:
    def __init__(self, issues=(), comments=None, prs=None, branches=None, branch_times=None, pr_error=False):
        self.issues = {i["number"]: i for i in issues}
        self.cmts = {k: list(v) for k, v in (comments or {}).items()}
        self.prs = prs or {}
        self.branch_names = branches or []
        self.branch_times = branch_times or {}
        self.pr_error = pr_error
        self.log: list[tuple] = []

    def _with(self, label):
        return [i for i in self.issues.values() if label in {x["name"] for x in i["labels"]}]

    def list_labeled(self, label):
        return self._with(label)

    def comments(self, n):
        return list(self.cmts.get(n, []))

    def label_events(self, n):
        return []

    def related_prs(self, n):
        if self.pr_error:
            raise RuntimeError("search down")
        return self.prs.get(n, [])

    def branches(self):
        return list(self.branch_names)

    def branch_commit_time(self, name):
        return self.branch_times.get(name)

    def set_status(self, n, *, remove, add):
        self.log.append(("status", n, remove, add))
        labels = [x for x in self.issues[n]["labels"] if x["name"] != remove]
        labels.append({"name": add})
        self.issues[n]["labels"] = labels

    def comment(self, n, body):
        self.log.append(("comment", n, body))
        self.cmts.setdefault(n, []).append(comment(body, NOW))
        return {"id": 1}

    def mutations(self):
        return [e for e in self.log]


def run(gh, **kw):
    sent = []

    def send(**k):
        sent.append(k)
        return {"accepted": True, "id": "evt1"}

    opts = d.Options(**{"health_check": False, **kw})
    res = d.run_dispatch(gh, ROSTER, opts, now=NOW, send=send, health=lambda o: (True, "ok"))
    return res, sent


# ── Worker line ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body,kind,name",
    [
        ("**Worker:** Buzz/GrokBuild\n\n## Why", "grokbuild", None),
        ("**Worker:** Buzz/GrokBuild. Pi dispatcher/ops steps may hand off to Forge", "grokbuild", None),
        ("**Worker:** Forge (in-chat). Do not let pi-dispatch staff this one.", "in-chat", None),
        ("**Worker:** Forge (direct handoff \u2014 this cannot go through the Ready pickup)", "in-chat", None),
        ("**Worker:** Frankenfit", "agent", "Frankenfit"),
        ("Worker: forge", "agent", "forge"),
        ("intro\n> **Worker:** Nakatoshi / Cadence", "agent", "Nakatoshi"),
        ("## Problem\nno worker here", "none", None),
        ("", "none", None),
    ],
)
def test_parse_worker(body, kind, name):
    w = d.parse_worker(body)
    assert (w.kind, w.name) == (kind, name)


def test_grokbuild_worker_is_not_staffed_even_for_fitdash_title():
    i = issue(1, title="FitDash: fix chip", body="**Worker:** Buzz/GrokBuild", labels=("status:ready", "fitdash"))
    assert d.resolve_owner(i, ROSTER)[0] is None
    gh = FakeGH([i])
    res, sent = run(gh)
    assert res["skipped"] and not sent and gh.log == []
    assert "Buzz/GrokBuild" in res["skip_note"]


def test_in_chat_and_unknown_workers_are_not_staffed():
    assert d.resolve_owner(issue(1, body="**Worker:** Forge (in-chat)"), ROSTER)[0] is None
    assert d.resolve_owner(issue(2, body="**Worker:** Somebody Else"), ROSTER)[0] is None
    assert d.skip_reason(issue(3, body="Do not let pi-dispatch staff this one."))
    res, sent = run(FakeGH([issue(3, body="Do not let pi-dispatch staff this one.")]))
    assert res["skipped"] and not sent


def test_worker_line_overrides_title_routing():
    # A FitDash title would route to Frankenfit; the Worker line says Forge.
    i = issue(4, title="FitDash: x", body="**Worker:** Forge", labels=("status:ready", "fitdash"))
    assert d.resolve_owner(i, ROSTER) == ("Forge", None)
    j = issue(5, title="plain", body="**Worker:** Frankenfit")
    assert d.resolve_owner(j, ROSTER) == ("Frankenfit", None)


def test_no_worker_line_keeps_legacy_routing():
    assert d.resolve_owner(issue(6, title="FitDash: x"), ROSTER) == ("Frankenfit", None)
    assert d.resolve_owner(issue(7, title="[Agents] Naka: x"), ROSTER) == ("Nakatoshi", None)
    assert d.resolve_owner(issue(8, title="plain"), ROSTER) == ("Forge", None)


def test_grokbuild_is_staffed_only_when_configured_in_roster():
    i = issue(9, body="**Worker:** Buzz/GrokBuild")
    cfg = d.Roster(CHANNEL, dict(ROSTER.agents), grokbuild_pubkey="ee" * 32)
    assert d.resolve_owner(i, cfg) == ("GrokBuild", None)


# ── re-staff cooldown ────────────────────────────────────────────────────────


def test_no_reclaim_within_24h_of_grok_restaff():
    # #1077 on 10/14: re-staffed 4:28 PM ET, pi-dispatch took it back 5 minutes later.
    i = issue(1077, title="FitDash: 403", body="no worker line", labels=("status:ready", "fitdash"))
    restaff = comment("Re-staffed (Grok eng-gate, 2026-10-14 4:26 PM ET Autonomous SDLC).\n...", ago(minutes=5))
    gh = FakeGH([i], {1077: [restaff]})
    res, sent = run(gh)
    assert res["skipped"] and not sent and gh.log == []
    assert "re-staff" in res["skip_note"]


def test_reclaim_allowed_after_cooldown():
    i = issue(1077, title="FitDash: 403", labels=("status:ready", "fitdash"))
    gh = FakeGH([i], {1077: [comment("Re-staffed (Grok eng-gate, x)", ago(hours=25))]})
    res, sent = run(gh)
    assert res["issue"] == 1077 and res["owner"] == "Frankenfit" and sent


def test_cooldown_applies_to_other_candidates_not_just_first():
    a = issue(10, labels=("status:ready", "P1"))
    b = issue(11, labels=("status:ready", "P2"))
    gh = FakeGH([a, b], {10: [comment("Re-staffed (Grok eng-gate, y)", ago(hours=2))]})
    res, _ = run(gh)
    assert res["issue"] == 11


# ── stale claim release ──────────────────────────────────────────────────────


def stale_gh(extra_comments=(), **kw):
    i = issue(50, title="Dead", labels=("status:in-progress",))
    cmts = [claim_comment("Frankenfit", ago(hours=30)), *extra_comments]
    return FakeGH([i], {50: cmts}, **kw)


def test_stale_claim_is_released_with_comment_naming_previous_holder():
    gh = stale_gh()
    res, _ = run(gh)
    assert res["released"][0]["issue"] == 50 and res["released"][0]["released"] is True
    assert ("status", 50, "status:in-progress", "status:ready") in gh.log
    body = [e for e in gh.log if e[0] == "comment"][0][2]
    assert body.startswith("[pi-dispatch] released")
    assert "Frankenfit" in body and "Previous holder: Frankenfit" in body and "24h" in body


def test_claim_under_24h_is_live():
    gh = FakeGH([issue(50, labels=("status:in-progress",))], {50: [claim_comment("Forge", ago(hours=23))]})
    res, _ = run(gh)
    assert res["released"] == [] and res["live_claims"] == ["#50"]


def test_gate_bookkeeping_is_not_holder_activity():
    gate = [
        comment("Status check (Grok eng-gate, 2026-10-14 4:26 PM ET). Please post status.", ago(hours=10)),
        comment("Deadline check (Grok eng-gate, Thu).", ago(hours=2)),
    ]
    res, _ = run(stale_gh(gate))
    assert res["released"] and res["released"][0]["issue"] == 50


def test_holder_comment_renews_the_clock():
    res, _ = run(stale_gh([comment("Started #50 on branch x", ago(hours=3))]))
    assert res["released"] == [] and res["live_claims"] == ["#50"]


def test_gate_comment_that_keeps_the_label_renews_the_clock():
    keep = comment("Deadline check (Grok eng-gate). Label stays `status:in-progress`.", ago(hours=1))
    res, _ = run(stale_gh([keep]))
    assert res["released"] == []


def test_open_pr_keeps_claim_live():
    pr = {"number": 7, "state": "open", "updated_at": iso(ago(hours=40))}
    res, _ = run(stale_gh(prs={50: [pr]}))
    assert res["released"] == []


def test_recent_branch_keeps_claim_live_and_old_branch_does_not():
    gh = stale_gh(branches=["fix/thing-50", "other-500"], branch_times={"fix/thing-50": ago(hours=2)})
    assert run(gh)[0]["released"] == []
    gh = stale_gh(branches=["fix/thing-50"], branch_times={"fix/thing-50": ago(hours=48)})
    assert run(gh)[0]["released"]


def test_api_error_while_checking_activity_never_releases():
    res, _ = run(stale_gh(pr_error=True))
    assert res["released"] == []
    assert gh_unchanged(res)


def gh_unchanged(res):
    return res["live_claims"] == ["#50"]


def test_dry_run_reports_but_does_not_mutate():
    gh = stale_gh()
    res, sent = run(gh, dry_run=True)
    assert res["released"][0]["released"] is False
    assert gh.log == [] and not sent


def test_only_scopes_the_release_sweep():
    gh = stale_gh()
    res, _ = run(gh, only={999})
    assert res["released"] == [] and gh.log == []


# ── WIP: dead claims cannot block ────────────────────────────────────────────


def test_stale_claim_does_not_block_its_owner_but_live_claim_does():
    dead = issue(60, labels=("status:in-progress",))
    ready = issue(61, title="plain", labels=("status:ready",))
    cmts = {60: [claim_comment("Forge", ago(hours=30))]}
    # Sweep disabled via --only so #60 stays labelled in-progress, yet is not busy.
    res, sent = run(FakeGH([dead, ready], cmts), only={61})
    assert res["issue"] == 61 and res["busy_owners"] == [] and sent

    live = {60: [claim_comment("Forge", ago(hours=2))]}
    res, sent = run(FakeGH([issue(60, labels=("status:in-progress",)), issue(61)], live))
    assert res["skipped"] and not sent and "Forge WIP=1" in res["skip_note"]


def test_release_then_pick_in_same_run_with_cooldown_on_released_issue():
    dead = issue(60, title="plain", labels=("status:in-progress",))
    other = issue(61, title="other plain", labels=("status:ready",))
    gh = FakeGH([dead, other], {60: [claim_comment("Forge", ago(hours=30))]})
    res, sent = run(gh)
    assert res["released"][0]["issue"] == 60
    # #60 is back at ready but inside its post-release cooldown; #61 is pulled.
    assert res["issue"] == 61 and sent


def test_repeated_releases_need_human_triage():
    i = issue(70, labels=("status:ready",))
    cmts = [comment("[pi-dispatch] released - x", ago(hours=100)), comment("[pi-dispatch] released - y", ago(hours=50))]
    res, sent = run(FakeGH([i], {70: cmts}))
    assert res["skipped"] and "human re-triage" in res["skip_note"] and not sent


# ── claim ordering ───────────────────────────────────────────────────────────


def test_kick_is_sent_before_the_started_comment_and_carries_owner_tag():
    gh = FakeGH([issue(80, title="FitDash: y", labels=("status:ready", "fitdash"))])
    res, sent = run(gh)
    assert res["channel_accepted"] and res["owner"] == "Frankenfit"
    assert ("status", 80, "status:ready", "status:in-progress") in gh.log
    started = [e for e in gh.log if e[0] == "comment"][0][2]
    assert started == "[pi-dispatch] started \u2014 staffed to Frankenfit in #workflow. WIP=1. Do not self-merge."
    assert sent[0]["channel_id"] == CHANNEL
    assert ["p", "bb" * 32] in sent[0]["extra_tags"] and ["p", "dd" * 32] in sent[0]["extra_tags"]


def test_rejected_kick_rolls_back_and_writes_no_started_comment():
    gh = FakeGH([issue(81)])
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW,
                         send=lambda **k: {"accepted": False}, health=lambda o: (True, ""))
    assert res["channel_accepted"] is False and res["labels_flipped"] is False
    assert gh.log[-1] == ("status", 81, "status:in-progress", "status:ready")
    assert not [e for e in gh.log if e[0] == "comment"]


def test_kick_exception_rolls_back():
    gh = FakeGH([issue(82)])

    def boom(**k):
        raise SystemExit("relay HTTP 500")

    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, send=boom,
                         health=lambda o: (True, ""))
    assert res["labels_flipped"] is False
    assert gh.issues[82]["labels"][-1]["name"] == "status:ready"


# ── consumer health gate ─────────────────────────────────────────────────────

ERR = "\u26a0\ufe0f I couldn't process the last request after multiple retries (Agent reported error (code -32000): Authentication required)."


def ev(content, at):
    return {"content": content, "created_at": int(at.timestamp())}


def test_health_unhealthy_when_owner_newest_message_is_harness_error():
    ok, detail = d.consumer_health("Forge", ROSTER, NOW, query=lambda f: [ev(ERR, ago(hours=2)), ev("PR up", ago(days=9))])
    assert ok is False and "-32000" in detail


def test_health_ok_when_newest_is_real_work_error_is_old_or_relay_down():
    assert d.consumer_health("Forge", ROSTER, NOW, query=lambda f: [ev("PR up", ago(hours=1)), ev(ERR, ago(hours=5))])[0]
    assert d.consumer_health("Forge", ROSTER, NOW, query=lambda f: [ev(ERR, ago(hours=30))])[0]
    assert d.consumer_health("Forge", ROSTER, NOW, query=lambda f: [])[0]

    def down(f):
        raise OSError("relay down")

    ok, detail = d.consumer_health("Forge", ROSTER, NOW, query=down)
    assert ok and "unknown" in detail


def test_unhealthy_owner_is_not_claimed_and_labels_stay_ready():
    gh = FakeGH([issue(90, title="FitDash: z", labels=("status:ready", "fitdash")), issue(91, title="plain")])
    sent = []
    res = d.run_dispatch(
        gh, ROSTER, d.Options(health_check=True), now=NOW,
        send=lambda **k: sent.append(k) or {"accepted": True},
        health=lambda o: (False, "harness replied with an error") if o == "Frankenfit" else (True, "ok"),
    )
    assert res["issue"] == 91 and res["owner"] == "Forge"
    assert "consumer unhealthy" in res["skip_note"]
    assert ("status", 90, "status:ready", "status:in-progress") not in gh.log


def test_no_health_check_flag_bypasses_the_gate():
    gh = FakeGH([issue(92, title="plain")])
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW,
                         send=lambda **k: {"accepted": True}, health=lambda o: (False, "dead"))
    assert res["issue"] == 92


# ── roster ───────────────────────────────────────────────────────────────────


def test_roster_loads_from_json_and_requires_the_file(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"workflow_channel_id": "c", "agents": {"Forge": {"pubkey": "k1"}, "Cadence": "k2"}}))
    r = d.load_roster(p)
    assert r.channel_id == "c" and r.pubkey("Forge") == "k1" and r.pubkey("Cadence") == "k2"
    assert r.canonical("forge") == "Forge" and r.staffable == d.DEFAULT_STAFFABLE
    with pytest.raises(SystemExit):
        d.load_roster(tmp_path / "missing.json")


def test_example_roster_is_valid_json_with_no_real_identities():
    from pathlib import Path

    ex = json.loads((Path(__file__).resolve().parents[1] / "dispatch-roster.example.json").read_text())
    assert set(ex["agents"]) >= {"Forge", "Frankenfit", "Nakatoshi", "Cadence"}
    assert ex["workflow_channel_id"].startswith("00000000")
