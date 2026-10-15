"""pi-dispatch (ceremony_eng_gate_dispatch) - issue #1195.

Worker line, 24h re-staff cooldown, stale-claim release (latest-of claim clock),
live-only WIP, claim/kick ordering, add-first label swaps, release-comment cooldown,
Buzz/GrokBuild never staffed, full pagination and the consumer health gate.
No network: a fake GitHub client (and a mocked HTTP layer for the real ``GitHub``
class) plus an injected kick sender / relay query are used throughout.
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


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Cooldowns/state never touch the real ~/.local/state."""
    monkeypatch.setattr(d, "STATE_DIR", tmp_path / "state")


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


def test_relabel_restarts_the_clock_instead_of_inheriting_an_old_marker():
    """Marker is 30h old (would be stale), but the issue was re-labelled 10 minutes ago."""
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Frankenfit", ago(hours=30))]},
        events={50: [labeled(ago(hours=30)), labeled(ago(minutes=10))]},
    )
    res, _ = run(gh)
    assert res["released"] == [] and res["live_claims"] == ["#50"]
    assert gh.log == []


def test_relabel_clock_applies_again_after_24h_from_the_relabel():
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Frankenfit", ago(hours=60))]},
        events={50: [labeled(ago(hours=60)), labeled(ago(hours=25))]},
    )
    res, _ = run(gh)
    assert res["released"] and res["released"][0]["issue"] == 50


def test_release_comment_also_counts_as_a_clock_start():
    gh = FakeGH(
        [issue(50, labels=("status:in-progress",))],
        {50: [claim_comment("Forge", ago(hours=50)), comment("[pi-dispatch] released - x", ago(hours=2))]},
    )
    assert run(gh)[0]["released"] == []


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


def test_cooldown_store_survives_garbage_file(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("not json")
    store = d.CooldownStore(p)
    assert store.until(1) is None
    assert store.set(1, NOW + timedelta(hours=1)) and store.until(1) == NOW + timedelta(hours=1)
