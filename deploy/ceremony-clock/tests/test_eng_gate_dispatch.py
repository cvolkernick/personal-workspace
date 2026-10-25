"""pi-dispatch (ceremony_eng_gate_dispatch) - issue #1195.

Worker line, 24h re-staff cooldown, stale-claim release (latest-of claim clock),
live-only WIP, claim/kick ordering, add-first label swaps, release-comment cooldown,
Buzz/GrokBuild never staffed, full pagination, the consumer health gate, crash-safe state files
that fail loudly when corrupt, strict Worker parsing, failed-kick back-off, and foreign
in-progress claims counting toward a resolved owner's WIP.
No network: a fake GitHub client (and a mocked HTTP layer for the real ``GitHub``
class) plus an injected kick sender / relay query are used throughout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

import ceremony_eng_gate_dispatch as d

NOW = datetime(2026, 10, 15, 21, 0, 0, tzinfo=timezone.utc)
CHANNEL = "00000000-0000-0000-0000-00000000c0de"
ROSTER = d.Roster(
    channel_id=CHANNEL,
    agents={"Forge": "aa" * 32, "Frankenfit": "bb" * 32, "Nakatoshi": "cc" * 32, "Cadence": "dd" * 32},
)


class _FrozenDatetime(datetime):
    """``datetime`` whose ``now()`` is pinned to the fixture clock ``NOW``."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Cooldowns/state never touch the real ~/.local/state."""
    monkeypatch.setattr(d, "STATE_DIR", tmp_path / "state")


@pytest.fixture(autouse=True)
def _frozen_clock():
    """Pin the module's wall clock to ``NOW`` so date-relative logic (e.g. the 7-day prune in
    ``CooldownStore._save``) sees the same time as the fixtures, whatever today's date is.
    Uses its own MonkeyPatch so a test calling ``monkeypatch.undo()`` cannot unfreeze it."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(d, "datetime", _FrozenDatetime)
        yield


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw) -> datetime:
    return NOW - timedelta(**kw)


def issue(n, title="Something", body="Some description.", labels=("status:ready",), updated=None):
    return {
        "number": n,
        "title": title,
        "body": body,
        "labels": [{"name": x} for x in labels],
        "html_url": f"https://example.invalid/issues/{n}",
        "updated_at": iso(updated or ago(hours=1)),
    }


def comment(body, at, login="GrokBot-BTC"):
    return {"body": body, "created_at": iso(at), "user": {"login": login}}


def claim_comment(owner, at):
    return comment(f"[pi-dispatch] started \u2014 staffed to {owner} in #workflow. WIP=1. Do not self-merge.", at)


class FakeGH:
    def __init__(self, issues=(), comments=None, prs=None, branches=None, branch_times=None, pr_error=False,
                 events=None, fail=()):
        self.events = events or {}
        self.fail = set(fail)  # e.g. {"add:status:in-progress", "remove:status:ready", "comment"}
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
        return list(self.events.get(n, []))

    def related_prs(self, n):
        if self.pr_error:
            raise RuntimeError("search down")
        return self.prs.get(n, [])

    def branches(self):
        return list(self.branch_names)

    def branch_commit_time(self, name):
        return self.branch_times.get(name)

    def set_status(self, n, *, remove, add):
        """Same contract as GitHub.set_status: add first, then remove."""
        self.log.append(("status", n, remove, add))
        if f"add:{add}" in self.fail:
            raise RuntimeError("GitHub HTTP 403 POST labels")
        names = [x["name"] for x in self.issues[n]["labels"]]
        if add not in names:
            names.append(add)
        self.issues[n]["labels"] = [{"name": x} for x in names]
        if f"remove:{remove}" in self.fail:
            raise RuntimeError("GitHub HTTP 502 DELETE label")
        self.issues[n]["labels"] = [{"name": x} for x in names if x != remove]

    def remove_label(self, n, label):
        self.log.append(("remove", n, label))
        self.issues[n]["labels"] = [x for x in self.issues[n]["labels"] if x["name"] != label]

    def comment(self, n, body):
        self.log.append(("comment", n, body))
        if "comment" in self.fail:
            raise RuntimeError("GitHub HTTP 403 POST comment")
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
        ("intro\n**Worker:** Nakatoshi / Cadence", "agent", "Nakatoshi"),
        ("intro\n> **Worker:** Nakatoshi / Cadence", "unknown", None),  # only in a quote: never routed
        ("## Problem\nno worker here", "none", None),
        ("", "empty", None),
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


GROKBUILD_BODIES = [
    "**Worker:** Buzz/GrokBuild",
    "**Worker:** GrokBuild",
    "**Worker:** Buzz",
    "Worker: buzz / grokbuild",
    "> **Worker:** Buzz/GrokBuild",
    "**Owner:** Buzz/GrokBuild",
    "Worker - Buzz",
    "## Spec\n\n**Worker:** Buzz/GrokBuild. Pi dispatcher/ops steps may hand off to Forge",
]


@pytest.mark.parametrize("body", GROKBUILD_BODIES)
def test_buzz_grokbuild_is_never_staffed(body):
    """No flag, no roster entry: not by Worker line, legacy title routing, or roster content."""
    for title, labels in [("plain", ("status:ready",)), ("FitDash: x", ("status:ready", "fitdash")),
                          ("[Agents] Naka: x", ("status:ready",))]:
        i = issue(9, title=title, body=body, labels=labels)
        assert d.resolve_owner(i, ROSTER)[0] is None
        # A roster that tries to enable it (old-style key, agents entry, staffable entry) is ignored.
        rich = d.Roster(CHANNEL, {**ROSTER.agents, "GrokBuild": "ee" * 32, "Buzz": "ff" * 32},
                        staffable=("Forge", "Frankenfit", "Nakatoshi", "GrokBuild", "Buzz"))
        assert d.resolve_owner(i, rich)[0] is None
        gh = FakeGH([i])
        res, sent = run(gh)
        assert res["skipped"] and not sent and gh.log == []
    gh = FakeGH([issue(9, body=body)])
    res = d.run_dispatch(gh, rich, d.Options(health_check=False), now=NOW,
                         send=lambda **k: pytest.fail("kick sent"), health=lambda o: (True, ""))
    assert res["skipped"] and gh.log == []


def test_grokbuild_issue_is_skipped_but_other_ready_issue_is_staffed():
    gb = issue(1, title="first", body="**Worker:** Buzz/GrokBuild", labels=("status:ready", "P0"))
    ok = issue(2, title="second", labels=("status:ready", "P3"))
    res, sent = run(FakeGH([gb, ok]))
    assert res["issue"] == 2 and "#1" in res["skip_note"] and len(sent) == 1


def test_roster_has_no_grokbuild_path():
    assert not hasattr(d.Roster(CHANNEL, {}), "grokbuild_pubkey")
    assert "grokbuild" not in d.DEFAULT_STAFFABLE
    import inspect

    assert "grokbuild_pubkey" not in inspect.getsource(d)


def test_malformed_worker_lines_do_not_fall_through_to_forge():
    assert d.resolve_owner(issue(5, body="Worker:\n"), ROSTER)[0] is None
    assert d.resolve_owner(issue(5, body="**Worker:**\n"), ROSTER)[0] is None
    assert d.resolve_owner(issue(5, body="Worker - Somebody"), ROSTER)[0] is None
    assert d.resolve_owner(issue(5, body="Owner: Chris"), ROSTER) == ("Forge", None)  # not a Worker line


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


# ── claim clock: latest of marker / release comment / labeled event ──────────


def labeled(at, name="status:in-progress"):
    return {"event": "labeled", "label": {"name": name}, "created_at": iso(at)}


def test_relabel_by_someone_else_is_not_a_pi_dispatch_claim_and_is_never_released():
    """Old marker (30h), but the issue was re-labelled 10 minutes ago by someone else (no new marker)."""
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Frankenfit", ago(hours=30))]},
        events={50: [labeled(ago(hours=30)), labeled(ago(minutes=10))]},
    )
    res, _ = run(gh)
    assert res["released"] == [] and res["foreign_claims"] == ["#50"] and res["live_claims"] == []
    # The marker named Frankenfit, but the body has no Worker line, so the current owner is Forge.
    assert res["busy_owners"] == ["Forge"] and gh.log == []
    # ...and it stays foreign (never released) however old the re-label gets.
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Frankenfit", ago(hours=90))]},
        events={50: [labeled(ago(hours=90)), labeled(ago(hours=40))]},
    )
    res, _ = run(gh)
    assert res["released"] == [] and res["foreign_claims"] == ["#50"] and gh.log == []


def test_pi_dispatch_reclaim_restarts_the_clock_from_the_new_marker():
    """pi-dispatch's own second claim (marker + label seconds apart) restarts the 24h clock."""
    two = [claim_comment("Frankenfit", ago(hours=30)), claim_comment("Frankenfit", ago(minutes=10))]
    evs = [labeled(ago(hours=30)), labeled(ago(minutes=10, seconds=1))]
    res, _ = run(FakeGH([issue(50, labels=("status:in-progress",))], {50: two}, events={50: evs}))
    assert res["released"] == [] and res["live_claims"] == ["#50"] and res["busy_owners"] == ["Frankenfit"]
    two = [claim_comment("Frankenfit", ago(hours=60)), claim_comment("Frankenfit", ago(hours=25))]
    evs = [labeled(ago(hours=60)), labeled(ago(hours=25, seconds=1))]
    res, _ = run(FakeGH([issue(50, labels=("status:in-progress",))], {50: two}, events={50: evs}))
    assert res["released"] and res["released"][0]["issue"] == 50 and res["released"][0]["holder"] == "Frankenfit"


def test_release_comment_also_counts_as_a_clock_start():
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Forge", ago(hours=50)), comment("[pi-dispatch] released - x", ago(hours=2))]},
    )
    res = run(gh)[0]
    assert res["released"] == [] and gh.log == []
    # a marker followed by a release is a previous claim: whoever holds the label now is not pi-dispatch
    assert res["foreign_claims"] == ["#50"]


def test_other_labels_and_unlabeled_events_do_not_move_the_clock():
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Forge", ago(hours=30))]},
        events={50: [labeled(ago(minutes=5), "P1"), {"event": "unlabeled", "label": {"name": "status:in-progress"},
                                                     "created_at": iso(ago(minutes=5))}]},
    )
    assert run(gh)[0]["released"]


def test_marker_without_newer_label_event_keeps_its_holder_but_relabel_drops_it():
    cm = [claim_comment("Frankenfit", ago(hours=3))]
    start, holder = d.claim_start(cm, [labeled(ago(hours=3, seconds=-2))], issue(1), NOW)
    assert holder == "Frankenfit" and start == ago(hours=3) + timedelta(seconds=2)
    start, holder = d.claim_start(cm, [labeled(ago(minutes=20))], issue(1), NOW)
    assert holder is None and start == ago(minutes=20)


def test_claim_history_read_error_never_releases():
    class Boom(FakeGH):
        def label_events(self, n):
            raise RuntimeError("events 502")

    gh = Boom([issue(50, labels=("status:in-progress",))], {50: [claim_comment("Forge", ago(hours=90))]})
    res, _ = run(gh)
    assert res["released"] == [] and res["live_claims"] == ["#50"] and gh.log == []


# ── real GitHub class over a mocked HTTP layer: pagination and label order ───


class FakeHTTP:
    """Replaces GitHub._request. Serves list endpoints in pages of 100 with Link headers."""

    def __init__(self, total_branches=508, link_headers=True, fail=()):
        self.branches = [f"b{i:04d}" for i in range(total_branches)]
        self.link_headers = link_headers
        self.fail = set(fail)
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path))
        key = f"{method} {path.split('?')[0].replace(d.API, '')}"
        if any(f in f"{method} {path}" for f in self.fail):
            raise RuntimeError(f"GitHub HTTP 403 {method} {path}")
        if method == "GET" and "/branches" in path:
            q = dict(x.split("=") for x in path.split("?", 1)[1].split("&"))
            page, per = int(q.get("page", 1)), int(q["per_page"])
            chunk = self.branches[(page - 1) * per: page * per]
            headers = {}
            if self.link_headers and page * per < len(self.branches):
                headers["link"] = (f'<{d.API}/branches?per_page={per}&page={page + 1}>; rel="next", '
                                   f'<{d.API}/branches?per_page={per}&page=99>; rel="last"')
            return [{"name": n} for n in chunk], headers
        return {}, {}


def real_gh(monkeypatch, http):
    gh = d.GitHub("tok")
    monkeypatch.setattr(gh, "_request", http)
    return gh


def test_branch_scan_paginates_past_500_with_link_headers(monkeypatch):
    http = FakeHTTP(508)
    names = real_gh(monkeypatch, http).branches()
    assert len(names) == 508 and names[0] == "b0000" and names[-1] == "b0507"
    assert len(http.calls) == 6  # 100 x 5 + 8


def test_branch_scan_paginates_without_link_headers_by_page_loop(monkeypatch):
    http = FakeHTTP(508, link_headers=False)
    assert len(real_gh(monkeypatch, http).branches()) == 508


def test_exact_multiple_of_page_size_and_empty_repo(monkeypatch):
    assert len(real_gh(monkeypatch, FakeHTTP(500)).branches()) == 500
    assert real_gh(monkeypatch, FakeHTTP(0)).branches() == []


def test_pagination_never_silently_truncates(monkeypatch):
    gh = real_gh(monkeypatch, FakeHTTP(508))
    with pytest.raises(RuntimeError, match="exceeded"):
        gh.paginate("/branches", max_pages=3)


def test_claim_on_branch_beyond_the_500th_keeps_issue_live(monkeypatch):
    """The reviewer's case: the only activity is a work/<N>-... branch sorted after 500 others."""
    http = FakeHTTP(0)
    http.branches = [f"zz-old-{i:04d}" for i in range(507)] + ["work/50-fix-thing"]
    gh = real_gh(monkeypatch, http)
    monkeypatch.setattr(gh, "branch_commit_time", lambda name: ago(hours=2) if name == "work/50-fix-thing" else None)
    monkeypatch.setattr(gh, "related_prs", lambda n: [])
    monkeypatch.setattr(gh, "comments", lambda n: [claim_comment("Forge", ago(hours=30))])
    monkeypatch.setattr(gh, "label_events", lambda n: [])
    claim = d.assess_claim(gh, issue(50, labels=("status:in-progress",)), ROSTER, NOW, timedelta(hours=24), {})
    assert claim.state == "live" and "work/50-fix-thing" in claim.reason


def test_branch_api_error_never_releases(monkeypatch):
    http = FakeHTTP(508, fail=("page=3",))
    gh = real_gh(monkeypatch, http)
    monkeypatch.setattr(gh, "related_prs", lambda n: [])
    monkeypatch.setattr(gh, "comments", lambda n: [claim_comment("Forge", ago(hours=30))])
    monkeypatch.setattr(gh, "label_events", lambda n: [])
    claim = d.assess_claim(gh, issue(50, labels=("status:in-progress",)), ROSTER, NOW, timedelta(hours=24), {})
    assert claim.state == "live" and "could not verify" in claim.reason


def test_comments_events_and_issue_lists_paginate_too(monkeypatch):
    pages = {}

    def req(method, path, body=None):
        q = dict(x.split("=") for x in path.split("?", 1)[1].split("&") if "=" in x)
        page = int(q.get("page", 1))
        n_pages = 3
        data = [{"id": page * 1000 + i, "number": page * 1000 + i} for i in range(100 if page < n_pages else 7)]
        pages[path.split("?")[0]] = page
        hdr = {"link": f'<{d.API}{path.split("?")[0].replace(d.API, "")}?{"&".join(f"{k}={v}" for k, v in {**q, "page": page + 1}.items())}>; rel="next"'} if page < n_pages else {}
        return data, hdr

    gh = d.GitHub("tok")
    monkeypatch.setattr(gh, "_request", req)
    assert len(gh.comments(1)) == 207
    assert len(gh.label_events(1)) == 207
    assert len(gh.list_labeled("status:ready")) == 207


def test_next_link_parser():
    h = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=9>; rel="last"'
    assert d.next_link(h) == "https://api.github.com/x?page=2"
    assert d.next_link('<https://api.github.com/x?page=9>; rel="last"') is None
    assert d.next_link(None) is None


def test_set_status_adds_the_new_label_before_removing_the_old(monkeypatch):
    http = FakeHTTP(0)
    real_gh(monkeypatch, http).set_status(5, remove="status:ready", add="status:in-progress")
    assert [(m, p.split("/issues/")[1]) for m, p in http.calls] == [
        ("POST", "5/labels"),
        ("DELETE", "5/labels/status%3Aready"),
    ]


def test_failed_add_never_removes_the_old_label(monkeypatch):
    http = FakeHTTP(0, fail=("POST",))
    with pytest.raises(RuntimeError):
        real_gh(monkeypatch, http).set_status(5, remove="status:ready", add="status:in-progress")
    assert [m for m, _ in http.calls] == ["POST"]  # no DELETE: the issue keeps its status label


# ── label swap and claim failure handling ────────────────────────────────────


def test_claim_when_the_add_fails_leaves_ready_untouched_and_sends_nothing():
    gh = FakeGH([issue(80)], fail={"add:status:in-progress"})
    res, sent = run(gh)
    assert res["labels_flipped"] is False and "label swap failed" in res["error"] and not sent
    assert [x["name"] for x in gh.issues[80]["labels"]] == ["status:ready"]
    assert not [e for e in gh.log if e[0] == "comment"]


def test_claim_when_only_the_remove_fails_undoes_the_add_and_sends_nothing():
    gh = FakeGH([issue(80)], fail={"remove:status:ready"})
    res, sent = run(gh)
    assert res["labels_flipped"] is False and not sent
    assert [x["name"] for x in gh.issues[80]["labels"]] == ["status:ready"]  # in-progress added then undone


def test_rejected_kick_rolls_back_add_first_and_no_status_label_is_ever_missing():
    seen = []

    class Watch(FakeGH):
        def set_status(self, n, *, remove, add):
            super().set_status(n, remove=remove, add=add)
            seen.append({x["name"] for x in self.issues[n]["labels"]})

    gh = Watch([issue(81)])
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW,
                         send=lambda **k: {"accepted": False}, health=lambda o: (True, ""))
    assert res["channel_accepted"] is False
    assert all(s & {"status:ready", "status:in-progress"} for s in seen)
    assert {x["name"] for x in gh.issues[81]["labels"]} == {"status:ready"}


def test_rollback_failure_is_reported_not_raised():
    gh = FakeGH([issue(81)])

    class Flaky(FakeGH):
        pass

    calls = {"n": 0}
    orig = gh.set_status

    def set_status(n, *, remove, add):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("GitHub HTTP 403")
        return orig(n, remove=remove, add=add)

    gh.set_status = set_status
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW,
                         send=lambda **k: {"accepted": False}, health=lambda o: (True, ""))
    assert res["rollback_failed"] == "RuntimeError" and res["labels_flipped"] is False


def test_started_comment_failure_keeps_the_claim_and_reports_it():
    gh = FakeGH([issue(82)], fail={"comment"})
    res, sent = run(gh)
    assert res["channel_accepted"] and res["labels_flipped"] and res["claim_comment_failed"] == "RuntimeError"
    assert len(sent) == 1
    assert {x["name"] for x in gh.issues[82]["labels"]} == {"status:in-progress"}


def test_dual_labeled_issue_is_never_picked_and_loses_its_ready_label():
    both = issue(83, labels=("status:ready", "status:in-progress"))
    cm = {83: [claim_comment("Forge", ago(hours=1))]}
    gh = FakeGH([both], cm)
    res, sent = run(gh)
    assert res["skipped"] and not sent
    assert res["healed_dual_labels"] == [83]
    assert {x["name"] for x in gh.issues[83]["labels"]} == {"status:in-progress"}


def test_stale_release_swap_failure_is_reported_and_does_not_abort_the_sweep():
    dead = issue(50, labels=("status:in-progress",))
    other = issue(61, title="other plain")
    gh = FakeGH([dead, other], {50: [claim_comment("Forge", ago(hours=30))]}, fail={"add:status:ready"})
    res, sent = run(gh)
    assert res["released"][0]["released"] is False and "label swap failed" in res["released"][0]["error"]
    assert res["issue"] == 61 and sent  # the rest of the pass still ran
    assert not [e for e in gh.log if e[0] == "comment" and e[1] == 50]


# ── failed release comment => cooldown, no retry loop ────────────────────────


def test_failed_release_comment_sets_a_cooldown_and_the_issue_is_not_restaffed(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    dead = issue(50, title="plain", labels=("status:in-progress",))
    gh = FakeGH([dead], {50: [claim_comment("Forge", ago(hours=30))]}, fail={"comment"})
    sent = []
    kw = dict(send=lambda **k: sent.append(k) or {"accepted": True}, health=lambda o: (True, ""), cooldowns=store)
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, **kw)
    rel = res["released"][0]
    assert rel["released"] is True and rel["comment_failed"] == "RuntimeError" and rel["cooldown_saved"] is True
    assert store.until(50) == NOW + timedelta(hours=24)
    assert {x["name"] for x in gh.issues[50]["labels"]} == {"status:ready"}

    # Next sweeps (15 min and 23h later): back at ready, no release marker on the issue, still blocked.
    for later in (timedelta(minutes=15), timedelta(hours=23)):
        gh.fail = set()
        res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW + later, **kw)
        assert res["skipped"] and "local cooldown" in res["skip_note"] and not sent
    assert not [e for e in gh.log if e[0] == "comment" and "started" in e[2]]

    # After the cooldown it can be staffed again.
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW + timedelta(hours=25), **kw)
    assert res["issue"] == 50 and sent


def test_successful_release_comment_writes_no_local_cooldown(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = stale_gh()
    d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, send=lambda **k: {"accepted": True},
                   health=lambda o: (True, ""), cooldowns=store)
    assert store.until(50) is None and not (tmp_path / "cool.json").exists()


def test_dry_run_never_writes_a_cooldown(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = stale_gh()
    d.run_dispatch(gh, ROSTER, d.Options(health_check=False, dry_run=True), now=NOW,
                   send=lambda **k: {"accepted": True}, health=lambda o: (True, ""), cooldowns=store)
    assert not (tmp_path / "cool.json").exists()


# ═════════════════════════════════════════════════════════════════════════════
# Must-fix 1: atomic state files and loud handling of corrupt ones
# ═════════════════════════════════════════════════════════════════════════════


def test_atomic_write_replaces_the_file_with_a_same_directory_temp_and_leaves_no_debris(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("old")
    seen = {}
    real_replace = os.replace

    def spy(src, dst):
        seen["src_dir"], seen["dst"] = os.path.dirname(str(src)), str(dst)
        assert target.read_text() == "old"  # nothing visible changed before the replace
        return real_replace(src, dst)

    monkeypatch.setattr(d.os, "replace", spy)
    d.atomic_write_text(target, "new")
    assert target.read_text() == "new"
    assert seen["src_dir"] == str(tmp_path) and seen["dst"] == str(target)  # same directory => same filesystem
    assert sorted(x.name for x in tmp_path.iterdir()) == ["state.json"]


def test_atomic_write_fsyncs_the_file_and_the_directory(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(d.os, "fsync", lambda fd: (synced.append(os.fstat(fd).st_mode), real(fd))[1])
    d.atomic_write_text(tmp_path / "s.json", "x")
    import stat

    assert any(stat.S_ISREG(m) for m in synced) and any(stat.S_ISDIR(m) for m in synced)


def test_crash_between_temp_write_and_replace_leaves_the_old_file_intact(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text('{"old": true}')

    def boom(src, dst):
        raise KeyboardInterrupt("killed between temp write and replace")

    monkeypatch.setattr(d.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        d.atomic_write_text(target, '{"new": true}')
    assert target.read_text() == '{"old": true}'
    assert [x.name for x in tmp_path.iterdir()] == ["state.json"]  # temp cleaned up on the way out


def test_hard_kill_between_temp_write_and_replace_leaves_old_state_loadable(tmp_path):
    """A real process death (os._exit inside os.replace): the old file is untouched, the orphan
    temp file is harmless, and the next write still works."""
    store_path = tmp_path / "release-cooldowns.json"
    d.CooldownStore(store_path).set(7, NOW + timedelta(hours=1))
    script = (
        "import os, sys\\n"
        f"sys.path.insert(0, {str(os.path.dirname(d.__file__))!r})\\n"
        "from datetime import datetime, timezone, timedelta\\n"
        "import ceremony_eng_gate_dispatch as d\\n"
        "os.replace = lambda *a: os._exit(9)\\n"
        f"d.CooldownStore(__import__('pathlib').Path({str(store_path)!r})).set(8, datetime(2030,1,1,tzinfo=timezone.utc))\\n"
    ).replace("\\n", "\n")
    assert subprocess.run([sys.executable, "-c", script]).returncode == 9
    assert any(x.name.endswith(".tmp") for x in tmp_path.iterdir())  # the orphan
    store = d.CooldownStore(store_path)
    assert store.until(7) == NOW + timedelta(hours=1) and store.until(8) is None
    store.set(9, NOW + timedelta(hours=2))
    assert store.until(7) and store.until(9)


def test_cooldown_set_that_crashes_keeps_every_earlier_cooldown(tmp_path, monkeypatch):
    store = d.CooldownStore(tmp_path / "c.json")
    store.set(1, NOW + timedelta(hours=1))
    monkeypatch.setattr(d.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk gone")))
    with pytest.raises(d.StateFileError, match="cannot write"):
        store.set(2, NOW + timedelta(hours=1))
    monkeypatch.undo()
    assert store.until(1) == NOW + timedelta(hours=1) and store.until(2) is None


def test_write_state_is_atomic(tmp_path, monkeypatch):
    path = tmp_path / "last-dispatch.json"
    d.write_state({"issue": 1}, path)
    monkeypatch.setattr(d.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        d.write_state({"issue": 2}, path)
    assert json.loads(path.read_text()) == {"issue": 1}


def test_missing_cooldown_file_is_a_normal_empty_store(tmp_path):
    store = d.CooldownStore(tmp_path / "nope.json")
    store.validate()
    assert store.until(1) is None and store.claim(1) is None


CORRUPT_STATE = [
    b"not json",
    b"",
    b'{"version": 2, "release": {"50": "2026-10-',  # truncated mid-write
    b"[]",
    b'{"version": 1}',
    b'{"version": 2, "release": []}',
    b'{"version": 2, "release": {"50": "garbage"}}',
    b'{"version": 2, "kick": {"x": "2026-10-15T21:00:00Z"}}',
    b'{"version": 2, "claims": {"5": {"owner": 1, "at": "2026-10-15T21:00:00Z"}}}',
    b"\xff\xfe\x00binary",
]


@pytest.mark.parametrize("content", CORRUPT_STATE)
def test_corrupt_cooldown_file_raises_loudly_and_is_never_reset_to_empty(tmp_path, content, capsys):
    path = tmp_path / "release-cooldowns.json"
    path.write_bytes(content)
    store = d.CooldownStore(path)
    with pytest.raises(d.StateFileError, match="corrupt or unreadable") as exc:
        store.until(50)
    assert str(path) in str(exc.value) and "re-claims" in str(exc.value)
    assert "ERROR pi-dispatch" in capsys.readouterr().err  # also on stderr / in the journal
    with pytest.raises(d.StateFileError):
        store.set(1, NOW + timedelta(hours=1))  # a write must not silently replace the damaged file either
    with pytest.raises(d.StateFileError):
        store.validate()
    assert path.read_bytes() == content  # original left in place: the sweep keeps failing until a human acts
    if content:
        assert [x for x in tmp_path.iterdir() if ".corrupt-" in x.name]  # evidence copy kept, one per distinct content


def test_unreadable_cooldown_path_raises(tmp_path):
    path = tmp_path / "release-cooldowns.json"
    path.mkdir()  # reading a directory fails with OSError
    with pytest.raises(d.StateFileError, match="unreadable"):
        d.CooldownStore(path).until(1)


def test_corrupt_cooldown_file_aborts_the_sweep_before_any_label_or_kick(tmp_path):
    """Stale claim + ready issue + corrupt file: nothing is released, claimed or sent."""
    path = tmp_path / "cool.json"
    path.write_text("{truncated")
    gh = FakeGH([issue(50, labels=("status:in-progress",)), issue(61, title="plain")],
                {50: [claim_comment("Forge", ago(hours=30))]})
    sent = []
    with pytest.raises(d.StateFileError):
        d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, cooldowns=d.CooldownStore(path),
                       send=lambda **k: sent.append(k) or {"accepted": True}, health=lambda o: (True, ""))
    assert gh.log == [] and sent == []
    with pytest.raises(d.StateFileError):  # dry-run reads the same state, so it fails the same way
        d.run_dispatch(gh, ROSTER, d.Options(health_check=False, dry_run=True), now=NOW,
                       cooldowns=d.CooldownStore(path))


def test_corrupt_cooldown_file_does_not_re_enable_a_release_cooldown(tmp_path):
    """The dangerous case: #50 had a local cooldown (release comment failed), the file is then
    truncated. The old code read that as empty and re-staffed #50 on the next sweep."""
    path = tmp_path / "cool.json"
    store = d.CooldownStore(path)
    store.set(50, NOW + timedelta(hours=24))
    path.write_bytes(path.read_bytes()[:20])  # truncated
    gh = FakeGH([issue(50, title="plain")])
    with pytest.raises(d.StateFileError):
        d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW + timedelta(minutes=15), cooldowns=store,
                       send=lambda **k: pytest.fail("kick sent"), health=lambda o: (True, ""))
    assert gh.log == []


def test_cooldown_that_cannot_be_saved_is_reported_loudly_and_the_issue_is_not_repicked(tmp_path, monkeypatch, capsys):
    store = d.CooldownStore(tmp_path / "cool.json")
    dead = issue(50, title="plain", labels=("status:in-progress",))
    gh = FakeGH([dead], {50: [claim_comment("Forge", ago(hours=30))]}, fail={"comment"})
    monkeypatch.setattr(d, "atomic_write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))
    sent = []
    res = d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, cooldowns=store,
                         send=lambda **k: sent.append(k) or {"accepted": True}, health=lambda o: (True, ""))
    rel = res["released"][0]
    assert rel["released"] is True and rel["cooldown_saved"] is False
    assert res["errors"] and "cooldown could not be saved" in res["errors"][0]
    assert "ERROR pi-dispatch" in capsys.readouterr().err
    assert not sent and res["skipped"]  # not re-staffed in the same sweep


def _run_main(monkeypatch, tmp_path, result=None, exc=None, argv=("eng-gate-sweep",)):
    monkeypatch.setattr(sys, "argv", ["x", *argv])
    monkeypatch.setattr(d, "github_token", lambda: "tok")
    monkeypatch.setattr(d, "load_roster", lambda *a, **k: ROSTER)
    monkeypatch.setattr(d, "GitHub", lambda tok: object())
    monkeypatch.setattr(d, "LOCK_PATH", tmp_path / "state" / "lock")

    def fake_run(*a, **k):
        if exc:
            raise exc
        return result

    monkeypatch.setattr(d, "run_dispatch", fake_run)
    return d.main()


def test_main_exits_3_on_corrupt_state_with_a_clear_error(monkeypatch, tmp_path, capsys):
    rc = _run_main(monkeypatch, tmp_path, exc=d.StateFileError("state file /x is corrupt or unreadable (bad)"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["kind"] == "StateFileError" and "corrupt" in out["error"]


def test_main_exits_3_when_the_result_carries_errors_even_if_the_kick_was_accepted(monkeypatch, tmp_path, capsys):
    res = {"channel_accepted": True, "errors": ["#1: could not record the claim locally: x"], "issue": 1}
    assert _run_main(monkeypatch, tmp_path, result=res) == 3
    assert (tmp_path / "state" / "last-dispatch.json").exists()


def test_main_reports_a_state_write_failure_instead_of_hiding_it(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(d, "write_state", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    res = {"channel_accepted": True, "errors": [], "issue": 1}
    assert _run_main(monkeypatch, tmp_path, result=res) == 3
    assert "last-dispatch.json" in capsys.readouterr().err


def test_main_exit_codes_for_clean_runs(monkeypatch, tmp_path):
    assert _run_main(monkeypatch, tmp_path, result={"skipped": True, "errors": []}) == 0
    assert _run_main(monkeypatch, tmp_path, result={"channel_accepted": False, "errors": []}) == 2
    assert _run_main(monkeypatch, tmp_path, result={"channel_accepted": True, "errors": []}) == 0


# ═════════════════════════════════════════════════════════════════════════════
# Must-fix 2: Worker parsing
# ═════════════════════════════════════════════════════════════════════════════

NOT_STAFFED_BODIES = [
    # Grok's list
    "Workers: Buzz/GrokBuild",
    "Workers:\n- Buzz/GrokBuild",
    "Workers:\n\n- Buzz/GrokBuild",
    "Assigned worker: Buzz/GrokBuild",
    "**Assigned worker:** GrokBuild",
    "Dispatch: GrokBuild",
    "",
    "   \n\t\n  ",
    "> **Worker:** Forge",  # only in a quote
    "```\nWorker: Forge\n```",  # only in a code fence
    "~~~\nWorker: Forge\n~~~",
    "```\nunclosed fence\nWorker: Forge",
    "<!-- Worker: Forge -->",
    "```\nWorker: Forge\n```\n**Worker:** Buzz/GrokBuild",  # a real Worker line follows the fence
    "> **Worker:** Forge\n\n**Worker:** Buzz/GrokBuild",  # ...follows the quote
    "**Worker:** Buzz/GrokBuild\n\n```\n**Worker:** Forge\n```",
    "Worker: Forge\nWorker: Buzz/GrokBuild",  # conflicting
    "Worker: Forge\nWorkers: Frankenfit",
    "**Worker:** Forge\n\n## Later\n**Worker:** Nakatoshi",
    "Worker: Forge / Frankenfit",
    # variant spellings / punctuation
    "dispatch - grokbuild",
    "DISPATCHED TO: BUZZ",
    "- **Worker:** Buzz",
    "## Worker: Buzz",
    "| Worker | Buzz/GrokBuild |",
    "| **Worker** | Grok Build |",
    "__Worker:__ buzz",
    "*Worker*: GrokBuild",
    "**Worker**: Buzz/GrokBuild",
    "Worker\uff1a Buzz",  # fullwidth colon
    "Wor\u200bker: Buzz",  # zero-width space inside the key
    "Worker:\u00a0Buzz",
    "WORKER : BUZZ",
    "Worker -> Buzz",
    "Worker = GrokBuild",
    "Worker: Grok-Build",
    "Worker: grok_build",
    "Worker: Buzz agent (GrokBuild harness)",
    "Worker: Forge, Buzz/GrokBuild",
    "Worker Buzz",
    "Owner: Buzz/GrokBuild",
    "Assignee: Buzz",
    "**Worker:** Forge\n**Owner:** Buzz/GrokBuild",
    # malformed / unknown
    "Worker:\n",
    "Worker:",
    "Worker - ",
    "**Worker:**",
    "Worker: TBD",
    "Worker: none",
    "Worker: Somebody Else",
    "Worker: Forge (in-chat)",
    "intro\n> **Worker:** Nakatoshi / Cadence",
]


@pytest.mark.parametrize("body", NOT_STAFFED_BODIES)
def test_unknown_ambiguous_or_buzz_worker_bodies_are_not_staffed(body):
    for title, labels in [("plain", ("status:ready",)), ("FitDash: x", ("status:ready", "fitdash")),
                          ("[Agents] Naka: x", ("status:ready",))]:
        i = issue(9, title=title, body=body, labels=labels)
        owner, why = d.resolve_owner(i, ROSTER)
        assert owner is None and why, (body, title)
    gh = FakeGH([issue(9, body=body)])
    res, sent = run(gh)
    assert res["skipped"] and not sent and gh.log == []


STAFFED_BODIES = [
    ("Workers: Forge", "Forge"),
    ("Workers:\n- Frankenfit", "Frankenfit"),
    ("**Assigned worker:** Frankenfit", "Frankenfit"),
    ("Dispatch: Nakatoshi", "Nakatoshi"),
    ("Assigned to: forge", "Forge"),
    ("- **Worker:** forge", "Forge"),
    ("| **Worker** | Frankenfit |", "Frankenfit"),
    ("**Worker**: Forge", "Forge"),
    ("Worker: Forge\nWorker: forge", "Forge"),  # repeated identical lines are not a conflict
    ("> **Worker:** Buzz/GrokBuild\n\n**Worker:** Forge", "Forge"),  # the quote is ignored
    ("```\nWorker: Buzz\n```\nWorker: Forge", "Forge"),
    ("<!-- Worker: Buzz -->\nWorker: Forge", "Forge"),
    ("Owner: Chris\n**Worker:** Frankenfit", "Frankenfit"),  # a human Owner line is not a Worker declaration
    ("Worker nodes are scaled to 4 in prod.", "Forge"),  # prose, no Worker declaration: legacy routing
]


@pytest.mark.parametrize("body,owner", STAFFED_BODIES)
def test_clear_worker_declarations_still_staff(body, owner):
    assert d.resolve_owner(issue(9, body=body), ROSTER) == (owner, None)


def test_parse_worker_kinds_for_the_edge_cases():
    assert d.parse_worker(None).kind == "empty"
    assert d.parse_worker("```\njust code\n```").kind == "empty"  # nothing authoritative in it
    assert d.parse_worker("> **Worker:** Forge").kind == "unknown"
    assert d.parse_worker("Worker: Forge\nWorker: Buzz").raw.startswith("conflicting Worker lines")
    assert d.parse_worker("Workers:\n- Buzz\n- Forge").kind == "grokbuild"
    assert d.parse_worker("Worker: Forge").kind == "agent"
    assert d.parse_worker("## Problem\nno worker here").kind == "none"


def test_empty_body_is_not_staffed_even_with_a_fitdash_title():
    i = issue(9, title="FitDash: x", body="", labels=("status:ready", "fitdash"))
    assert d.resolve_owner(i, ROSTER)[0] is None
    assert "empty" in d.resolve_owner(i, ROSTER)[1]


HOSTILE_ROSTERS = [
    d.Roster(CHANNEL, {**ROSTER.agents, "GrokBuild": "ee" * 32, "Buzz": "ff" * 32},
             staffable=("Forge", "Frankenfit", "Nakatoshi", "GrokBuild", "Buzz")),
    d.Roster(CHANNEL, {**ROSTER.agents, "Grok-Build": "ee" * 32, "BUZZ": "ff" * 32, "buzz agent": "11" * 32},
             staffable=("Grok-Build", "BUZZ", "buzz agent", "Forge")),
    d.Roster(CHANNEL, {"grokbuild": "ee" * 32}, staffable=("grokbuild",)),
]


@pytest.mark.parametrize("roster", HOSTILE_ROSTERS)
@pytest.mark.parametrize("body", [b for b in NOT_STAFFED_BODIES if "Forge" not in b] + ["Worker: GrokBuild", "Worker: grok-build", "Worker: BUZZ"])
def test_hostile_roster_cannot_make_buzz_or_grokbuild_staffable(roster, body):
    for title in ("plain", "FitDash: x", "[Agents] Naka: x"):
        assert d.resolve_owner(issue(9, title=title, body=body), roster)[0] is None
    gh = FakeGH([issue(9, body=body)])
    try:
        res = d.run_dispatch(gh, roster, d.Options(health_check=False), now=NOW,
                             send=lambda **k: pytest.fail("kick sent"), health=lambda o: (True, ""))
    except d.RosterError:  # a roster whose only staffable agents are Buzz/GrokBuild is rejected outright
        res = {"skipped": True}
    assert res["skipped"] and gh.log == []


def test_never_staff_covers_every_spelling():
    for name in ("Buzz", "BUZZ", "GrokBuild", "grok-build", "Grok Build", "grok_build", "buzz agent", "Buzz/GrokBuild"):
        assert d.never_staff(name), name
    for name in ("Forge", "Frankenfit", "Nakatoshi", "Cadence", None, ""):
        assert not d.never_staff(name), name


# ═════════════════════════════════════════════════════════════════════════════
# Must-fix 3: sender / roster built before labels; failed kick backs off ~1h
# ═════════════════════════════════════════════════════════════════════════════


def test_sender_that_cannot_be_built_fails_before_any_label_is_touched(monkeypatch):
    monkeypatch.setitem(sys.modules, "ceremony_clock", None)  # `from ceremony_clock import ...` -> ImportError
    gh = FakeGH([issue(81), issue(50, labels=("status:in-progress",))], {50: [claim_comment("Forge", ago(hours=30))]})
    with pytest.raises(d.SenderUnavailable, match="send_channel_message"):
        d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, health=lambda o: (True, ""))
    assert gh.log == []  # not even the release sweep ran


def test_sender_whose_module_exits_on_import_is_also_caught_before_labels(monkeypatch):
    import types

    mod = types.ModuleType("ceremony_clock")

    def __getattr__(name):
        raise SystemExit("clock env missing")

    mod.__getattr__ = __getattr__
    monkeypatch.setitem(sys.modules, "ceremony_clock", mod)
    gh = FakeGH([issue(81)])
    with pytest.raises(d.SenderUnavailable):
        d.run_dispatch(gh, ROSTER, d.Options(health_check=False), now=NOW, health=lambda o: (True, ""))
    assert gh.log == []


def test_dry_run_does_not_need_a_sender(monkeypatch):
    monkeypatch.setitem(sys.modules, "ceremony_clock", None)
    res = d.run_dispatch(FakeGH([issue(81)]), ROSTER, d.Options(health_check=False, dry_run=True), now=NOW,
                         health=lambda o: (True, ""))
    assert res["issue"] == 81 and "content_preview" in res


@pytest.mark.parametrize("bad", [
    d.Roster("", ROSTER.agents),
    d.Roster("   ", ROSTER.agents),
    d.Roster(CHANNEL, {}),
    d.Roster(CHANNEL, {"GrokBuild": "ee" * 32}, staffable=("GrokBuild",)),
])
def test_invalid_roster_fails_before_any_label_is_touched(bad):
    gh = FakeGH([issue(81)])
    with pytest.raises(d.RosterError):
        d.run_dispatch(gh, bad, d.Options(health_check=False), now=NOW, send=lambda **k: {"accepted": True},
                       health=lambda o: (True, ""))
    assert gh.log == []


def test_owner_without_a_valid_pubkey_is_skipped_before_labels():
    r = d.Roster(CHANNEL, {**ROSTER.agents, "Forge": "not-a-pubkey"})
    gh = FakeGH([issue(81, title="plain"), issue(82, title="FitDash: x", labels=("status:ready", "fitdash"))])
    res = d.run_dispatch(gh, r, d.Options(health_check=False), now=NOW, send=lambda **k: {"accepted": True},
                         health=lambda o: (True, ""))
    assert res["issue"] == 82 and "#81 roster has no valid pubkey for Forge" in res["skip_note"]
    assert not [e for e in gh.log if e[1] == 81]


def test_invalid_roster_file_is_a_clear_error(tmp_path):
    p = tmp_path / "r.json"
    p.write_text("{not json")
    with pytest.raises(d.RosterError, match="unreadable or invalid"):
        d.load_roster(p)
    p.write_text(json.dumps({"agents": {}}))  # missing workflow_channel_id
    with pytest.raises(d.RosterError):
        d.load_roster(p)


def sweep(gh, store, at, send, **opts):
    return d.run_dispatch(gh, ROSTER, d.Options(health_check=False, **opts), now=at, cooldowns=store,
                          send=send, health=lambda o: (True, ""))


def swaps(gh):
    return [e for e in gh.log if e[0] == "status"]


def test_rejected_kick_does_not_thrash_labels_every_sweep(tmp_path):
    """Grok's repro: 8 sweeps with a rejected kick used to give 16 label swaps on the same issue."""
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(81, title="plain")])
    calls = []
    reject = lambda **k: calls.append(k) or {"accepted": False}
    results = [sweep(gh, store, NOW + timedelta(minutes=15 * i), reject) for i in range(8)]  # 0 .. 1h45
    assert len(swaps(gh)) == 4  # sweeps at 0:00 and 1:00 each flip + roll back; 0:15-0:45 and 1:15-1:45 do nothing
    assert len(calls) == 2
    assert results[0]["channel_accepted"] is False and results[0]["kick_cooldown_saved"] is True
    for r in results[1:4]:
        assert r["skipped"] and "kick failed earlier; local cooldown" in r["skip_note"]
    assert {x["name"] for x in gh.issues[81]["labels"]} == {"status:ready"}  # never left in a wrong state
    assert not [e for e in gh.log if e[0] == "comment"]


def test_kick_cooldown_lasts_about_one_hour_and_is_stored_atomically(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(81, title="plain")])
    res = sweep(gh, store, NOW, lambda **k: {"accepted": False})
    assert res["kick_cooldown_until"] == "2026-10-15T22:00:00Z"
    assert store.until(81, "kick") == NOW + timedelta(hours=1)
    assert store.until(81) is None  # separate from the release cooldown
    assert json.loads((tmp_path / "cool.json").read_text())["kick"] == {"81": "2026-10-15T22:00:00Z"}
    assert [x.name for x in tmp_path.iterdir()] == ["cool.json"]
    # after the hour the issue is tried again; a configurable back-off is honoured
    ok = []
    res = sweep(gh, store, NOW + timedelta(hours=1, minutes=1), lambda **k: ok.append(k) or {"accepted": True})
    assert res["issue"] == 81 and res["channel_accepted"] and len(ok) == 1
    gh2 = FakeGH([issue(82, title="plain")])
    store2 = d.CooldownStore(tmp_path / "cool2.json")
    sweep(gh2, store2, NOW, lambda **k: {"accepted": False}, kick_cooldown_hours=3)
    assert store2.until(82, "kick") == NOW + timedelta(hours=3)


def test_kick_exception_also_backs_off_and_the_sweep_moves_on_to_the_next_issue(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(81, title="plain", labels=("status:ready", "P1")), issue(82, title="plain", labels=("status:ready", "P2"))])
    attempts = []

    def flaky(**k):
        attempts.append(k["content"])
        if "#81" in k["content"]:
            raise RuntimeError("relay HTTP 500")
        return {"accepted": True}

    r1 = sweep(gh, store, NOW, flaky)
    assert r1["issue"] == 81 and r1["labels_flipped"] is False and "RuntimeError" in r1["error"]
    r2 = sweep(gh, store, NOW + timedelta(minutes=15), flaky)
    assert r2["issue"] == 82 and r2["channel_accepted"] and "#81 kick failed earlier" in r2["skip_note"]
    assert len(attempts) == 2


def test_failed_label_swap_also_backs_off(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(80)], fail={"add:status:in-progress"})
    r1 = sweep(gh, store, NOW, lambda **k: pytest.fail("kick sent"))
    assert "label swap failed" in r1["error"] and r1["kick_cooldown_saved"] is True
    gh.fail = set()
    r2 = sweep(gh, store, NOW + timedelta(minutes=15), lambda **k: pytest.fail("kick sent"))
    assert r2["skipped"] and "kick failed earlier" in r2["skip_note"]
    assert len(swaps(gh)) == 1


def test_successful_kick_sets_no_back_off(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    sweep(FakeGH([issue(81)]), store, NOW, lambda **k: {"accepted": True})
    assert store.until(81, "kick") is None


def test_dry_run_never_writes_a_kick_cooldown(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    sweep(FakeGH([issue(81)]), store, NOW, lambda **k: {"accepted": False}, dry_run=True)
    assert not (tmp_path / "cool.json").exists()


def test_kick_back_off_that_cannot_be_saved_is_loud(tmp_path, monkeypatch, capsys):
    store = d.CooldownStore(tmp_path / "cool.json")
    monkeypatch.setattr(d, "atomic_write_text", lambda *a, **k: (_ for _ in ()).throw(OSError("ro")))
    res = sweep(FakeGH([issue(81)]), store, NOW, lambda **k: {"accepted": False})
    assert res["kick_cooldown_saved"] is False and res["errors"] and "kick cooldown could not be saved" in res["errors"][0]
    assert "ERROR pi-dispatch" in capsys.readouterr().err


# ═════════════════════════════════════════════════════════════════════════════
# Must-fix 4: only pi-dispatch's own claims get released.
# A foreign in-progress claim still occupies WIP when its owner resolves (#1225).
# ═════════════════════════════════════════════════════════════════════════════

BUZZ_BODY = "**Worker:** Buzz/GrokBuild\n\n## Why\nneeds the harness"


def test_buzz_grokbuild_in_progress_issue_is_neither_counted_nor_released(tmp_path):
    """No marker, 90h old, Worker line says Buzz/GrokBuild: it used to pin Forge and then be
    released with 'staffing to Forge'."""
    buzz = issue(60, title="plain", body=BUZZ_BODY, labels=("status:in-progress",))
    ready = issue(61, title="plain", labels=("status:ready",))
    cmts = {60: [comment("Started on branch work/60-x, will open a PR.", ago(hours=90), "buzz-bot")]}
    gh = FakeGH([buzz, ready], cmts, events={60: [labeled(ago(hours=90))]})
    res, sent = run(gh)
    assert res["busy_owners"] == [] and res["live_claims"] == [] and res["foreign_claims"] == ["#60"]
    assert res["released"] == [] and res["stale_claims"] == []
    assert res["issue"] == 61 and res["owner"] == "Forge" and len(sent) == 1  # Forge is NOT held at WIP=1
    assert [e for e in gh.log if e[1] == 60] == []  # no label change, no comment on the Buzz issue
    assert {x["name"] for x in gh.issues[60]["labels"]} == {"status:in-progress"}


@pytest.mark.parametrize(
    "body,busy",
    [
        ("Some description.", ["Forge"]),
        ("", []),
        (BUZZ_BODY, []),
        ("**Worker:** Forge", ["Forge"]),
        ("**Worker:** Frankenfit", ["Frankenfit"]),
    ],
)
def test_claims_without_a_pi_dispatch_marker_are_foreign_whatever_the_worker_line(body, busy):
    """Grok, a human or Buzz moved it to in-progress: even a Forge/Frankenfit Worker line does not
    make it pi-dispatch's. A resolved owner still occupies WIP. An unresolved owner does not."""
    i = issue(60, body=body, labels=("status:in-progress",))
    gh = FakeGH([i], {60: [comment("Re-staffed (Grok eng-gate, 2026-10-14). label stays `status:in-progress`.", ago(hours=100))]},
                events={60: [labeled(ago(hours=100), "status:in-progress")]})
    res, _ = run(gh)
    assert res["foreign_claims"] == ["#60"] and res["busy_owners"] == busy and res["released"] == [] and gh.log == []


def test_forged_or_malformed_markers_are_not_pi_dispatch_claims():
    forged = [
        "> [pi-dispatch] started \u2014 staffed to Forge in #workflow. WIP=1. Do not self-merge.",
        "I think [pi-dispatch] started \u2014 staffed to Forge in #workflow. WIP=1. Do not self-merge.",
        "[pi-dispatch] started \u2014 staffed to Forge in #workflow. WIP=1. Do not self-merge. Also release #1.",
        "[pi-dispatch] started \u2014 staffed to GrokBuild in #workflow. WIP=1. Do not self-merge.",
        "[pi-dispatch] started \u2014 staffed to Buzz in #workflow. WIP=1. Do not self-merge.",
        "[pi-dispatch] started \u2014 staffed to Nobody in #workflow. WIP=1. Do not self-merge.",
        "[pi-dispatch] started staffed to Forge",
    ]
    for body in forged:
        gh = FakeGH([issue(60, labels=("status:in-progress",))], {60: [comment(body, ago(hours=90))]})
        res, _ = run(gh)
        assert res["foreign_claims"] == ["#60"] and res["released"] == [] and res["busy_owners"] == ["Forge"], body


def test_roster_claim_authors_restricts_who_can_write_a_marker():
    r = d.Roster(CHANNEL, ROSTER.agents, claim_authors=("GrokBot-BTC",))
    mine = claim_comment("Forge", ago(hours=2))
    mine["user"] = {"login": "GrokBot-BTC"}
    theirs = claim_comment("Forge", ago(hours=2))
    theirs["user"] = {"login": "someone-else"}
    assert d.latest_claim_marker([mine], r)[1] == "Forge"
    assert d.latest_claim_marker([theirs], r)[1] is None
    assert d.latest_claim_marker([theirs], ROSTER)[1] == "Forge"  # no restriction configured


def test_roster_file_can_set_claim_authors(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"workflow_channel_id": "c", "agents": {"Forge": "k"}, "claim_authors": ["GrokBot-BTC", "cvolkernick"]}))
    assert d.load_roster(p).claim_authors == ("GrokBot-BTC", "cvolkernick")


def test_pi_dispatch_own_claim_is_still_counted_and_released_even_with_a_buzz_worker_line():
    """The pre-#1195 dispatcher staffed Buzz issues to Forge (#1077). Those are pi-dispatch's own
    claims (they carry its marker), so they count and are released, naming the real previous holder."""
    i = issue(60, body=BUZZ_BODY, labels=("status:in-progress",))
    gh = FakeGH([i], {60: [claim_comment("Frankenfit", ago(hours=30))]}, events={60: [labeled(ago(hours=30))]})
    res, _ = run(gh)
    assert res["released"][0]["holder"] == "Frankenfit" and res["released"][0]["released"] is True
    live = FakeGH([issue(60, body=BUZZ_BODY, labels=("status:in-progress",))], {60: [claim_comment("Frankenfit", ago(hours=2))]},
                  events={60: [labeled(ago(hours=2))]})
    res, _ = run(live)
    assert res["busy_owners"] == ["Frankenfit"] and res["foreign_claims"] == []


def test_release_comment_names_the_marker_holder_never_forge_by_default():
    claim = d.Claim(5, "Frankenfit", NOW, own=True)
    assert "Previous holder: Frankenfit" in d.release_comment(claim, 24, 24)
    assert "Forge" not in d.release_comment(claim, 24, 24)


def test_unreadable_history_of_a_buzz_issue_pins_nobody_and_releases_nothing():
    class Boom(FakeGH):
        def comments(self, n):
            if n == 60:
                raise RuntimeError("502")
            return super().comments(n)

    gh = Boom([issue(60, body=BUZZ_BODY, labels=("status:in-progress",)), issue(61, title="plain")])
    res, sent = run(gh)
    assert res["busy_owners"] == [] and res["released"] == [] and res["issue"] == 61
    # a legacy/Forge issue whose history cannot be read still pins its owner (fail safe)
    gh = Boom([issue(60, title="plain", labels=("status:in-progress",)), issue(61, title="plain")])
    res, sent = run(gh)
    assert res["busy_owners"] == ["Forge"] and res["released"] == [] and res["skipped"]


def test_foreign_in_progress_claim_blocks_another_ready_issue_for_the_same_owner():
    """#1225: a manual eng-gate claim occupies WIP=1 and the sweep does not staff that owner again."""
    held = issue(
        1110,
        title="FitDash: session chip",
        body="**Worker:** Frankenfit\n\nStaffed by hand.",
        labels=("status:in-progress", "fitdash"),
    )
    ready = issue(
        1109,
        title="FitDash: close bar",
        body="**Worker:** Frankenfit\n\nNext card.",
        labels=("status:ready", "fitdash"),
    )
    cmts = {1110: [comment("[eng-gate] staffed to Frankenfit. label stays `status:in-progress`.", ago(hours=2))]}
    gh = FakeGH([held, ready], cmts, events={1110: [labeled(ago(hours=2))]})
    res, sent = run(gh)
    assert res["foreign_claims"] == ["#1110"] and res["live_claims"] == []
    assert res["busy_owners"] == ["Frankenfit"] and res["released"] == []
    assert res["skipped"] is True and "issue" not in res and not sent
    assert "Frankenfit WIP=1" in (res["skip_note"] or "")
    assert gh.log == []


def test_foreign_claim_blocks_only_the_resolved_owner():
    held = issue(70, body="**Worker:** Forge", labels=("status:in-progress",))
    ready = issue(71, title="FitDash: next", body="**Worker:** Frankenfit", labels=("status:ready", "fitdash"))
    gh = FakeGH([held, ready], {70: [comment("manual staff", ago(hours=1))]})
    res, sent = run(gh)
    assert res["foreign_claims"] == ["#70"] and res["busy_owners"] == ["Forge"]
    assert res["issue"] == 71 and res["owner"] == "Frankenfit" and len(sent) == 1


def test_foreign_dual_labeled_issue_is_not_touched():
    both = issue(83, body=BUZZ_BODY, labels=("status:ready", "status:in-progress"))
    gh = FakeGH([both], {83: [comment("on it", ago(hours=1), "buzz-bot")]})
    res, sent = run(gh)
    assert res["skipped"] and not sent and res["healed_dual_labels"] == [] and gh.log == []


def test_claim_ledger_keeps_a_claim_ours_when_the_started_comment_failed(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(82, title="plain")], fail={"comment"})
    res = sweep(gh, store, NOW, lambda **k: {"accepted": True})
    assert res["claim_comment_failed"] == "RuntimeError" and store.claim(82)["owner"] == "Forge"
    gh.issues[82]["labels"] = [{"name": "status:in-progress"}]
    gh.events[82] = [labeled(NOW)]
    gh.fail = set()
    # 15 min later: counted as Forge's live claim
    res = sweep(gh, store, NOW + timedelta(minutes=15), lambda **k: pytest.fail("kick"))
    assert res["busy_owners"] == ["Forge"] and res["foreign_claims"] == []
    # 25h later with no activity: released as pi-dispatch's own claim, ledger entry cleared
    res = sweep(gh, store, NOW + timedelta(hours=25), lambda **k: {"accepted": True})
    assert res["released"][0]["issue"] == 82 and res["released"][0]["released"] is True
    assert store.claim(82) is None


def test_successful_claim_is_recorded_and_a_later_foreign_relabel_ignores_the_record(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(82, title="plain")])
    sweep(gh, store, NOW, lambda **k: {"accepted": True})
    assert store.claim(82)["owner"] == "Forge"
    # The issue is released/handed back, then Buzz re-labels it in-progress an hour later.
    gh2 = FakeGH([issue(82, labels=("status:in-progress",))], {82: gh.cmts[82]},
                 events={82: [labeled(NOW), labeled(NOW + timedelta(hours=1))]})
    res = sweep(gh2, store, NOW + timedelta(hours=30), lambda **k: pytest.fail("kick"))
    assert res["foreign_claims"] == ["#82"] and res["released"] == [] and res["busy_owners"] == ["Forge"]


def test_failed_rollback_records_the_claim_so_the_24h_release_can_clear_it(tmp_path):
    store = d.CooldownStore(tmp_path / "cool.json")
    gh = FakeGH([issue(81, title="plain")])
    orig, n = gh.set_status, {"n": 0}

    def flaky(num, *, remove, add):
        n["n"] += 1
        if n["n"] == 2:
            raise RuntimeError("403")
        return orig(num, remove=remove, add=add)

    gh.set_status = flaky
    res = sweep(gh, store, NOW, lambda **k: {"accepted": False})
    assert res["rollback_failed"] == "RuntimeError" and store.claim(81)["owner"] == "Forge"


def test_corrupt_claim_ledger_also_stops_the_sweep(tmp_path):
    p = tmp_path / "cool.json"
    p.write_text('{"version": 2, "claims": {"5": "oops"}}')
    with pytest.raises(d.StateFileError):
        sweep(FakeGH([issue(81)]), d.CooldownStore(p), NOW, lambda **k: {"accepted": True})


# ── related_prs precision (#1195 live finding): a PR that only mentions #N is not work on #N ──


@pytest.mark.parametrize("title,body", [
    ("fix thing", "Fixes #50"),
    ("fix thing", "closes #50\n\nmore"),
    ("fix thing", "Resolved: #50"),
    ("fix thing", "Refs #50 (not Fixes: blocked)"),
    ("fix thing", "References #50"),
    ("fix thing", "Part of #50"),
    ("fix thing", "Follow-up to #50"),
    ("fix thing", "Fixes owner/repo#50"),
    ("fix thing", "Fixes https://github.com/cvolkernick/personal-workspace/issues/50"),
    ("Fixes #50: thing", ""),
    ("thing (#50)", None),
    ("#50 thing", ""),
    ("[#50] thing", ""),
])
def test_pr_targets_issue_positive(title, body):
    assert d.pr_targets_issue({"title": title, "body": body}, 50)


@pytest.mark.parametrize("title,body", [
    ("pi-dispatch: honor Worker line (#1195)", "dead claims (#50, #51) pinned Forge"),
    ("fix thing", "Release stale #50 on the first pass"),
    ("fix thing", "pi-dispatch kick for #50 was accepted"),
    ("fix thing", "Fixes #500"),
    ("fix thing", "Fixes #5"),
    ("fix thing", "see #50"),
    ("fix thing", "<!-- Fixes #50 -->"),
    ("fix thing", "prefixes #50"),
    ("fix thing", "and #50"),
    ("thing (#500)", ""),
    ("thing", None),
])
def test_pr_targets_issue_negative(title, body):
    assert not d.pr_targets_issue({"title": title, "body": body}, 50)


def test_github_related_prs_drops_prose_mentions(monkeypatch):
    items = [
        {"number": 1200, "state": "closed", "title": "dispatcher fix (#1195)",
         "body": "dead claims (#50, #51). Refs #1195", "updated_at": iso(ago(hours=2))},
        {"number": 1196, "state": "closed", "title": "guard (#51)", "body": "x", "updated_at": iso(ago(hours=2))},
        {"number": 1210, "state": "open", "title": "work", "body": "Fixes #50", "updated_at": iso(ago(hours=2))},
    ]
    gh = d.GitHub("tok")
    monkeypatch.setattr(gh, "_request", lambda method, path, body=None: ({"items": items}, {}))
    assert [p["number"] for p in gh.related_prs(50)] == [1210]
    assert [p["number"] for p in gh.related_prs(51)] == [1196]


def test_stale_claim_released_when_only_a_closed_pr_mentions_it_in_prose(monkeypatch):
    """Live case: merged #1200 mentions #1077 in prose and kept the dead claim live for ever."""
    gh = real_gh(monkeypatch, FakeHTTP(0))
    prose = {"number": 1200, "state": "closed", "title": "dispatcher fix (#1195)",
             "body": "dead claims (#50, #1093) pinned the queue. Refs #1195", "updated_at": iso(ago(hours=2))}
    monkeypatch.setattr(gh, "_request", lambda method, path, body=None: ({"items": [prose]}, {}) if "search/issues" in path else FakeHTTP(0)(method, path, body))
    monkeypatch.setattr(gh, "comments", lambda n: [claim_comment("Frankenfit", ago(hours=40))])
    monkeypatch.setattr(gh, "label_events", lambda n: [])
    claim = d.assess_claim(gh, issue(50, labels=("status:in-progress",)), ROSTER, NOW, timedelta(hours=24), {})
    assert claim.state == "stale" and claim.holder == "Frankenfit"


def test_claim_stays_live_when_a_pr_really_targets_it(monkeypatch):
    gh = real_gh(monkeypatch, FakeHTTP(0))
    real = {"number": 1196, "state": "closed", "title": "guard (#50)", "body": "Refs #50",
            "updated_at": iso(ago(hours=2))}
    monkeypatch.setattr(gh, "_request", lambda method, path, body=None: ({"items": [real]}, {}) if "search/issues" in path else FakeHTTP(0)(method, path, body))
    monkeypatch.setattr(gh, "comments", lambda n: [claim_comment("Forge", ago(hours=40))])
    monkeypatch.setattr(gh, "label_events", lambda n: [])
    claim = d.assess_claim(gh, issue(50, labels=("status:in-progress",)), ROSTER, NOW, timedelta(hours=24), {})
    assert claim.state == "live" and "PR #1196" in claim.reason
