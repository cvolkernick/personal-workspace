#!/usr/bin/env python3
"""Mechanical eng-gate pickup dispatcher (pi-dispatch).

Runs after the Path B eng-gate-sweep kick (``ceremony-clock@eng-gate-sweep``,
``ExecStartPost``). Not an LLM. Each run:

1. **Release sweep** - a ``status:in-progress`` claim that pi-dispatch made and
   that shows no branch, PR or holder comment for ``--stale-hours`` (24h) is
   returned to ``status:ready`` with a comment naming the previous holder. A dead
   claim therefore cannot hold WIP (#1195).
2. **Pick** - at most one ``status:ready`` issue, flips it to
   ``status:in-progress`` and posts a staffing kick in #workflow.

Pick rules (#1195):

* The issue's ``**Worker:**`` line is authoritative. ``Buzz/GrokBuild`` (or any
  in-chat / direct-handoff / unknown worker) is **not** staffed here. A worker that
  names Forge/Frankenfit/Nakatoshi routes to that bot. No Worker line keeps the
  legacy title/label routing.
* No re-claim within ``--cooldown-hours`` (24h) of a Grok eng-gate re-staff
  comment (``Re-staffed (Grok eng-gate``) or of a pi-dispatch release.
* WIP is still one issue per owner, but only **live** claims count: a stale
  claim no longer blocks the owner's queue.
* The kick is posted before the claim is recorded. A rejected kick leaves the
  issue ``status:ready`` and writes no "started" comment.
* Consumer health gate: when the owner's newest message in #workflow inside the
  last 24h is the harness error ("I couldn't process the last request ..."), the
  owner is not staffed. The harness that turns a kick into a session is outside
  this script; claiming into a dead harness is what stalled the queue.
  Fail-open when the relay cannot be read.

Config (nothing identity-specific is committed):
  ~/.config/buzz-clock.env           clock signer (relay URL, key)
  ~/.config/workflow-scheduler.env   GITHUB_TOKEN (repo scope)
  ~/.config/ceremony-clock/dispatch-roster.json   see dispatch-roster.example.json
  (override with CEREMONY_DISPATCH_ROSTER)
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

ALIAS = "eng-gate-sweep"
REPO = "cvolkernick/personal-workspace"
API = f"https://api.github.com/repos/{REPO}"

DEFAULT_STALE_HOURS = 24.0
DEFAULT_COOLDOWN_HOURS = 24.0
HEALTH_WINDOW_HOURS = 24.0
MAX_AUTO_RELEASES = 2

CLAIM_MARK = "[pi-dispatch] started"
RELEASE_MARK = "[pi-dispatch] released"
RESTAFF_MARK = "Re-staffed (Grok eng-gate"
CLAIM_RE = re.compile(r"^\[pi-dispatch\] started\b.*?staffed to (?P<owner>.+?) in #workflow", re.S)
# Grok eng-gate bookkeeping is not holder activity.
GATE_COMMENT_RE = re.compile(
    r"\(Grok eng-gate|^\s*(?:Re-staffed|Status check|Status \(|Deadline check|Eng-gate)\b", re.I
)
# A gate/COS comment that explicitly keeps the claim renews the 24h clock.
KEEP_RE = re.compile(r"label stays\W{0,3}status:in-progress|pi-dispatch:keep", re.I)
HARNESS_ERROR_RE = re.compile(r"I couldn't process the last request", re.I)

SKIP_LABELS = {"process", "human-only", "idea", "parked", "capital-parked"}
SKIP_ISSUES = {
    110: "is the flight-plan epic - do not pull as amalgam",
    953: "shows live X Money and One Card balances - do not pull",
}
DANGEROUS_TITLE = re.compile(
    r"\b(treasury|secret|wallet|credential|nsec|private.?key|live money)\b", re.I
)
FCC_BRANCH_NOISE = re.compile(r"work/treasury|treasury worktree|fcc/treasury", re.I)
FCC_PRODUCT_TITLE = re.compile(r"^\s*\[?\s*FCC\b", re.I)
FITDASH_TITLE = re.compile(r"^\s*(?:\[\s*fitdash\b[^\]]*\]|fitdash\b)", re.I)
NAKA_TITLE = re.compile(r"\bnakatoshi\b|\bnaka\b", re.I)

WORKER_LINE_RE = re.compile(
    r"^[ \t>]*(?:\*\*)?Worker(?::\*\*|\*\*:|:)[ \t]*(?P<value>.+?)[ \t]*$", re.I | re.M
)
NO_DISPATCH_BODY_RE = re.compile(
    r"do not let pi-dispatch|pi-dispatch:\s*(?:skip|no)\b|no pi-dispatch", re.I
)
IN_CHAT_RE = re.compile(
    r"in[- ]chat|direct hand-?off|hand-?off only|human|chris[- ]gate|do not (?:pull|staff)", re.I
)

PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
DEFAULT_STAFFABLE = ("Forge", "Frankenfit", "Nakatoshi")

SCHEDULER_ENV = Path.home() / ".config" / "workflow-scheduler.env"
STATE_DIR = Path(os.environ.get("CEREMONY_STATE_DIR") or Path.home() / ".local" / "state" / "ceremony-clock")
LOCK_PATH = STATE_DIR / "dispatch.lock"
STATE_PATH = STATE_DIR / "last-dispatch.json"
ROSTER_PATH = Path(
    os.environ.get("CEREMONY_DISPATCH_ROSTER")
    or Path.home() / ".config" / "ceremony-clock" / "dispatch-roster.json"
)
UA = "ceremony-eng-gate-dispatch/2.0"


# ── small helpers ────────────────────────────────────────────────────────────


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_kv_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if (v.startswith("'") and v.endswith("'")) or (v.startswith('"') and v.endswith('"')):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def github_token() -> str:
    env = load_kv_env(SCHEDULER_ENV)
    token = env.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token:
        raise SystemExit("GITHUB_TOKEN missing (workflow-scheduler.env)")
    return token


def label_names(issue: dict) -> set[str]:
    return {str(x.get("name", "")).lower() for x in issue.get("labels") or [] if isinstance(x, dict)}


def priority_rank(labels: set[str]) -> int:
    for name, rank in PRIORITY_RANK.items():
        if name in labels:
            return rank
    return 4


# ── roster (identities live outside the repo) ───────────────────────────────


@dataclass
class Roster:
    channel_id: str
    agents: dict[str, str]  # name -> pubkey
    staffable: tuple[str, ...] = DEFAULT_STAFFABLE
    grokbuild_pubkey: str | None = None

    def pubkey(self, name: str) -> str:
        return self.agents.get(name, "")

    def canonical(self, name: str) -> str | None:
        for known in self.agents:
            if known.lower() == name.lower():
                return known
        return None


def load_roster(path: Path | None = None) -> Roster:
    path = path or ROSTER_PATH
    if not path.is_file():
        raise SystemExit(f"roster missing: {path} (see dispatch-roster.example.json)")
    data = json.loads(path.read_text())
    agents = {str(k): str(v.get("pubkey") if isinstance(v, dict) else v) for k, v in data["agents"].items()}
    gb = data.get("grokbuild")
    gb_key = (gb.get("pubkey") if isinstance(gb, dict) else gb) or None
    return Roster(
        channel_id=str(data["workflow_channel_id"]),
        agents=agents,
        staffable=tuple(data.get("staffable") or DEFAULT_STAFFABLE),
        grokbuild_pubkey=gb_key,
    )


# ── Worker line and routing ──────────────────────────────────────────────────


@dataclass
class Worker:
    kind: str  # agent | grokbuild | in-chat | unknown | none
    name: str | None = None
    raw: str | None = None


def parse_worker(body: str | None) -> Worker:
    """Parse the issue body's ``**Worker:**`` line (first one wins)."""
    body = body or ""
    m = WORKER_LINE_RE.search(body)
    if not m:
        return Worker("none")
    raw = m.group("value").strip()
    low = raw.lower()
    if IN_CHAT_RE.search(low):
        return Worker("in-chat", raw=raw)
    if "grokbuild" in low or re.search(r"\bbuzz\b", low):
        return Worker("grokbuild", raw=raw)
    first = re.split(r"[\s(/,;:.\-\u2014]+", raw.strip("*_` "), maxsplit=1)[0]
    return Worker("agent", name=first or None, raw=raw)


def is_fitdash(issue: dict) -> bool:
    if "fitdash" in label_names(issue):
        return True
    return bool(FITDASH_TITLE.match(str(issue.get("title") or "")))


def is_naka(issue: dict) -> bool:
    labels = label_names(issue)
    if "naka" in labels or "nakatoshi" in labels:
        return True
    return bool(NAKA_TITLE.search(str(issue.get("title") or "")))


def legacy_owner_name(issue: dict) -> str:
    if is_fitdash(issue):
        return "Frankenfit"
    if is_naka(issue):
        return "Nakatoshi"
    return "Forge"


def is_dangerous_title(title: str) -> bool:
    cleaned = FCC_BRANCH_NOISE.sub(" ", title)
    if FCC_PRODUCT_TITLE.search(title):
        cleaned = re.sub(r"\btreasury\b", " ", cleaned, flags=re.I)
    return bool(DANGEROUS_TITLE.search(cleaned))


def skip_reason(issue: dict) -> str | None:
    n = issue.get("number")
    if n in SKIP_ISSUES:
        return f"#{n} {SKIP_ISSUES[n]}"
    hit = label_names(issue) & SKIP_LABELS
    if hit:
        return f"skip labels {sorted(hit)}"
    if is_dangerous_title(str(issue.get("title") or "")):
        return "dangerous-path title - Chris gate"
    if NO_DISPATCH_BODY_RE.search(str(issue.get("body") or "")):
        return "body says pi-dispatch must not staff it"
    return None


def resolve_owner(issue: dict, roster: Roster) -> tuple[str | None, str | None]:
    """Return (owner name, skip reason). Worker line beats title/label routing."""
    worker = parse_worker(issue.get("body"))
    if worker.kind == "none":
        legacy = roster.canonical(legacy_owner_name(issue))
        if legacy and legacy in roster.staffable:
            return legacy, None
        return None, f"no Worker line and {legacy_owner_name(issue)} is not in the roster"
    if worker.kind == "grokbuild":
        if roster.grokbuild_pubkey:
            return "GrokBuild", None
        return None, f"Worker: {worker.raw} - not staffed by pi-dispatch"
    if worker.kind == "in-chat":
        return None, f"Worker: {worker.raw} - in-chat, not staffed by pi-dispatch"
    canon = roster.canonical(worker.name or "")
    if canon and canon in roster.staffable:
        return canon, None
    return None, f"Worker: {worker.raw} - unknown worker, not staffed by pi-dispatch"


def owner_pubkey(roster: Roster, owner: str) -> str:
    if owner == "GrokBuild":
        return roster.grokbuild_pubkey or ""
    return roster.pubkey(owner)


# ── GitHub client ────────────────────────────────────────────────────────────


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def call(self, method: str, path: str, body: dict | None = None) -> object:
        url = path if path.startswith("https://") else f"{API}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": UA,
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"GitHub HTTP {e.code} {method} {path}: {err}") from e

    def list_labeled(self, label: str) -> list[dict]:
        q = urllib.parse.urlencode({"state": "open", "labels": label, "per_page": 100})
        data = self.call("GET", f"/issues?{q}")
        if not isinstance(data, list):
            raise RuntimeError(f"expected list for {label}, got {type(data)}")
        return [i for i in data if "pull_request" not in i]  # PRs share /issues

    def comments(self, number: int) -> list[dict]:
        out: list[dict] = []
        for page in range(1, 6):
            data = self.call("GET", f"/issues/{number}/comments?per_page=100&page={page}")
            if not isinstance(data, list) or not data:
                break
            out.extend(data)
            if len(data) < 100:
                break
        return out

    def label_events(self, number: int) -> list[dict]:
        data = self.call("GET", f"/issues/{number}/events?per_page=100")
        return data if isinstance(data, list) else []

    def related_prs(self, number: int) -> list[dict]:
        q = urllib.parse.quote(f"repo:{REPO} type:pr {number} in:title,body")
        data = self.call("GET", f"https://api.github.com/search/issues?q={q}&per_page=30")
        items = data.get("items", []) if isinstance(data, dict) else []
        pat = re.compile(rf"(?<![\w/])#{number}(?!\d)|/issues/{number}(?!\d)")
        return [p for p in items if pat.search(f"{p.get('title') or ''}\n{p.get('body') or ''}")]

    def branches(self) -> list[str]:
        names: list[str] = []
        for page in range(1, 6):
            data = self.call("GET", f"/branches?per_page=100&page={page}")
            if not isinstance(data, list) or not data:
                break
            names.extend(str(b.get("name")) for b in data)
            if len(data) < 100:
                break
        return names

    def branch_commit_time(self, name: str) -> datetime | None:
        data = self.call("GET", f"/branches/{urllib.parse.quote(name, safe='')}")
        if isinstance(data, dict):
            commit = (data.get("commit") or {}).get("commit") or {}
            return parse_ts((commit.get("committer") or {}).get("date"))
        return None

    def set_status(self, number: int, *, remove: str, add: str) -> None:
        encoded = urllib.parse.quote(remove, safe="")
        self.call("DELETE", f"/issues/{number}/labels/{encoded}")
        self.call("POST", f"/issues/{number}/labels", {"labels": [add]})

    def comment(self, number: int, body: str) -> dict:
        data = self.call("POST", f"/issues/{number}/comments", {"body": body})
        return data if isinstance(data, dict) else {}


# ── claim assessment ─────────────────────────────────────────────────────────


@dataclass
class Claim:
    number: int
    holder: str | None
    claimed_at: datetime | None
    last_activity: datetime | None = None
    state: str = "live"  # live | stale
    reason: str = ""
    owner_for_wip: str = ""


def latest_claim_marker(comments: list[dict]) -> tuple[dict | None, str | None]:
    found: dict | None = None
    holder: str | None = None
    for c in comments:
        m = CLAIM_RE.match(str(c.get("body") or "").lstrip())
        if m:
            found, holder = c, m.group("owner").strip()
    return found, holder


def holder_activity(comments: list[dict], since: datetime) -> datetime | None:
    """Newest comment after ``since`` that is neither pi-dispatch nor eng-gate bookkeeping.

    A gate comment that says the label stays (``KEEP_RE``) renews the clock.
    """
    best: datetime | None = None
    for c in comments:
        ts = parse_ts(c.get("created_at"))
        body = str(c.get("body") or "")
        if ts is None or ts <= since or body.lstrip().startswith("[pi-dispatch]"):
            continue
        if GATE_COMMENT_RE.search(body) and not KEEP_RE.search(body):
            continue
        if best is None or ts > best:
            best = ts
    return best


def assess_claim(
    gh,
    issue: dict,
    roster: Roster,
    now: datetime,
    stale_after: timedelta,
    branch_cache: dict,
) -> Claim:
    n = int(issue["number"])
    comments = gh.comments(n)
    marker, holder = latest_claim_marker(comments)
    claimed_at = parse_ts(marker.get("created_at")) if marker else None
    if claimed_at is None:
        for ev in gh.label_events(n):
            if ev.get("event") == "labeled" and (ev.get("label") or {}).get("name") == "status:in-progress":
                ts = parse_ts(ev.get("created_at"))
                if ts and (claimed_at is None or ts > claimed_at):
                    claimed_at = ts
    claimed_at = claimed_at or parse_ts(issue.get("updated_at")) or now
    owner, _ = resolve_owner(issue, roster)
    claim = Claim(
        number=n,
        holder=holder,
        claimed_at=claimed_at,
        owner_for_wip=holder or owner or legacy_owner_name(issue),
    )
    activity = holder_activity(comments, claimed_at)
    claim.last_activity = activity
    clock = max(t for t in (claimed_at, activity) if t)
    age = now - clock
    if age < stale_after:
        claim.reason = f"live: {age.total_seconds() / 3600:.1f}h since claim/activity"
        return claim
    # Past the window: any PR (open = work in flight) or fresh branch keeps it live.
    try:
        for pr in gh.related_prs(n):
            if pr.get("state") == "open":
                claim.reason = f"live: open PR #{pr.get('number')}"
                return claim
            ts = parse_ts(pr.get("updated_at"))
            if ts and ts > claimed_at:
                claim.reason = f"live: PR #{pr.get('number')} updated after claim"
                return claim
        if "names" not in branch_cache:
            branch_cache["names"] = gh.branches()
        pat = re.compile(rf"(?<!\d){n}(?!\d)")
        for name in branch_cache["names"]:
            if pat.search(name):
                ts = gh.branch_commit_time(name)
                if ts and ts > claimed_at:
                    claim.reason = f"live: branch {name} has commits after claim"
                    return claim
    except Exception as e:  # fail safe: never release on an API error
        claim.reason = f"live: could not verify PR/branch activity ({e})"[:200]
        return claim
    claim.state = "stale"
    claim.reason = f"no branch, PR or holder comment for {age.total_seconds() / 3600:.1f}h"
    return claim


def release_comment(claim: Claim, stale_hours: float, cooldown_hours: float) -> str:
    holder = claim.holder or claim.owner_for_wip or "unknown"
    since = claim.claimed_at.strftime("%Y-%m-%dT%H:%M:%SZ") if claim.claimed_at else "unknown"
    return (
        f"{RELEASE_MARK} - no activity for {stale_hours:g}h after staffing to **{holder}** "
        f"(claimed {since}). No branch, PR, or blocker comment was found, so "
        f"`status:in-progress` -> `status:ready`. Previous holder: {holder}. "
        f"pi-dispatch will not re-claim this issue for {cooldown_hours:g}h."
    )


# ── pick ─────────────────────────────────────────────────────────────────────


def claim_block_reason(
    comments: list[dict], now: datetime, cooldown: timedelta
) -> str | None:
    """Re-staff / release cooldown and repeat-release cap."""
    releases = 0
    for c in comments:
        body = str(c.get("body") or "")
        ts = parse_ts(c.get("created_at"))
        if ts is None:
            continue
        if RESTAFF_MARK in body and now - ts < cooldown:
            return f"Grok eng-gate re-staff {(now - ts).total_seconds() / 3600:.1f}h ago (cooldown {cooldown.total_seconds() / 3600:g}h)"
        if body.lstrip().startswith(RELEASE_MARK):
            releases += 1
            if now - ts < cooldown:
                return f"pi-dispatch released it {(now - ts).total_seconds() / 3600:.1f}h ago (cooldown {cooldown.total_seconds() / 3600:g}h)"
    if releases >= MAX_AUTO_RELEASES:
        return f"released {releases} times with no work; needs human re-triage"
    return None


def pick_candidate(
    ready: list[dict],
    busy_owners: set[str],
    roster: Roster,
    *,
    blocked: Callable[[dict, str], str | None] | None = None,
) -> tuple[dict | None, str | None, str | None]:
    """Return (issue, skip note, owner name). ``blocked`` runs lazily per candidate."""
    scored: list[tuple[int, int, dict, str]] = []
    skipped: list[str] = []
    for issue in ready:
        reason = skip_reason(issue)
        owner = None
        if not reason:
            owner, reason = resolve_owner(issue, roster)
        if reason:
            skipped.append(f"#{issue.get('number')} {reason}")
            continue
        assert owner
        if owner in busy_owners:
            skipped.append(f"#{issue.get('number')} {owner} WIP=1")
            continue
        scored.append((priority_rank(label_names(issue)), int(issue["number"]), issue, owner))
    scored.sort(key=lambda t: (t[0], t[1]))
    for _rank, _n, issue, owner in scored:
        if blocked:
            why = blocked(issue, owner)
            if why:
                skipped.append(f"#{issue.get('number')} {why}")
                continue
        return issue, ("; ".join(skipped) or None), owner
    return None, ("; ".join(skipped) or None), None


def format_kick(issue: dict, owner: str, roster: Roster, generated_at: str) -> str:
    n = issue["number"]
    title = issue.get("title") or "?"
    html = issue.get("html_url") or f"https://github.com/{REPO}/issues/{n}"
    return "\n".join(
        [
            "[pi-dispatch] eng-gate pickup",
            "",
            f"**#{n} {title}** assigned to @{owner}",
            html,
            "",
            f"@{owner} pull and implement. AC is on the Issue. WIP=1. Do not self-merge.",
            "@Cadence board Ready -> In Progress for #" + str(n) + ".",
            "",
            f"generated {generated_at} - GH `status:ready` -> `status:in-progress`",
            "",
            "_Clock dispatcher, not a Grok chat session. "
            "`GUIDES/OPS_ENG_GITHUB_ISSUES_CONTRACT.md` - "
            "`GUIDES/PI_CEREMONY_CLOCK_RUNNER.md`._",
        ]
    )


# ── consumer health (relay) ──────────────────────────────────────────────────


def relay_query(filters: list[dict]) -> list[dict]:
    """POST /query on the relay as the clock identity (NIP-98)."""
    from ceremony_clock import KIND_NIP98, clock_identity, load_clock_env, sign_event

    load_clock_env()
    priv, pubkey, relay, auth_tag = clock_identity()
    url = f"{relay}/query"
    body = json.dumps(filters, separators=(",", ":")).encode()
    tags = [["u", url], ["method", "POST"], ["nonce", str(uuid.uuid4())],
            ["payload", hashlib.sha256(body).hexdigest()]]
    ev = sign_event(priv, pubkey, KIND_NIP98, tags, "")
    headers = {
        "Authorization": "Nostr " + base64.b64encode(json.dumps(ev).encode()).decode(),
        "Content-Type": "application/json",
        "User-Agent": "ceremony-clock/1.0",
    }
    if auth_tag:
        headers["x-auth-tag"] = auth_tag
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode())
    return data if isinstance(data, list) else []


def consumer_health(
    owner: str,
    roster: Roster,
    now: datetime,
    query: Callable[[list[dict]], list[dict]] = relay_query,
    window: timedelta = timedelta(hours=HEALTH_WINDOW_HOURS),
) -> tuple[bool, str]:
    """(ok, detail). Unhealthy only when the owner's newest #workflow message in the
    window is the harness error. Fail-open on any read problem or no signal."""
    pk = owner_pubkey(roster, owner)
    if not pk:
        return True, "no pubkey - health unknown"
    try:
        evs = query([{"kinds": [9], "#h": [roster.channel_id], "authors": [pk], "limit": 5}])
    except Exception as e:
        return True, f"health unknown ({type(e).__name__})"
    evs = sorted(evs, key=lambda e: int(e.get("created_at") or 0), reverse=True)
    if not evs:
        return True, "no recent messages from owner"
    newest = evs[0]
    age = now - datetime.fromtimestamp(int(newest["created_at"]), timezone.utc)
    if age > window:
        return True, "owner's last message is older than the health window"
    if HARNESS_ERROR_RE.search(str(newest.get("content") or "")):
        m = re.search(r"code (-?\d+)\)?:?\s*([^).]{0,60})", str(newest.get("content") or ""))
        code = f"code {m.group(1)}: {m.group(2).strip()}" if m else "harness error"
        return False, f"{owner} harness replied with an error {age.total_seconds() / 3600:.1f}h ago ({code})"
    return True, "owner's last message is not a harness error"


# ── state / lock ─────────────────────────────────────────────────────────────


def write_state(payload: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


def acquire_lock():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


# ── one dispatch pass (testable) ─────────────────────────────────────────────


@dataclass
class Options:
    dry_run: bool = False
    stale_hours: float = DEFAULT_STALE_HOURS
    cooldown_hours: float = DEFAULT_COOLDOWN_HOURS
    health_check: bool = True
    only: set[int] = field(default_factory=set)


def run_dispatch(
    gh,
    roster: Roster,
    opts: Options,
    *,
    now: datetime | None = None,
    send: Callable[..., dict] | None = None,
    health: Callable[[str], tuple[bool, str]] | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    generated_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_after = timedelta(hours=opts.stale_hours)
    cooldown = timedelta(hours=opts.cooldown_hours)

    ready = gh.list_labeled("status:ready")
    in_progress = gh.list_labeled("status:in-progress")
    only = opts.only

    # 1. assess every claim; release the dead ones.
    branch_cache: dict = {}
    claims = [assess_claim(gh, i, roster, now, stale_after, branch_cache) for i in in_progress]
    released: list[dict] = []
    for c in claims:
        if c.state != "stale":
            continue
        if only and c.number not in only:
            continue
        entry = {"issue": c.number, "holder": c.holder or c.owner_for_wip, "reason": c.reason}
        if not opts.dry_run:
            gh.set_status(c.number, remove="status:in-progress", add="status:ready")
            gh.comment(c.number, release_comment(c, opts.stale_hours, opts.cooldown_hours))
        entry["released"] = not opts.dry_run
        released.append(entry)

    live = [c for c in claims if c.state == "live"]
    busy_owners = {c.owner_for_wip for c in live}
    stale_nums = {c.number for c in claims if c.state == "stale"}

    # 2. pick.
    pool = [i for i in ready if not only or int(i["number"]) in only]
    comment_cache: dict[int, list[dict]] = {}
    health_cache: dict[str, tuple[bool, str]] = {}
    health_fn = health or (lambda o: consumer_health(o, roster, now))

    def blocked(issue: dict, owner: str) -> str | None:
        n = int(issue["number"])
        comment_cache[n] = comment_cache.get(n) or gh.comments(n)
        why = claim_block_reason(comment_cache[n], now, cooldown)
        if why:
            return why
        if opts.health_check:
            if owner not in health_cache:
                health_cache[owner] = health_fn(owner)
            ok, detail = health_cache[owner]
            if not ok:
                return f"{owner} consumer unhealthy: {detail}"
        return None

    issue, skip_note, owner = pick_candidate(pool, busy_owners, roster, blocked=blocked)
    result: dict = {
        "alias": ALIAS,
        "ready_n": len(ready),
        "in_progress": [f"#{c.number}" for c in claims],
        "live_claims": [f"#{c.number}" for c in live],
        "stale_claims": sorted(f"#{n}" for n in stale_nums),
        "busy_owners": sorted(busy_owners),
        "released": released,
        "skip_note": skip_note,
        "generated_at": generated_at,
        "dry_run": bool(opts.dry_run),
    }
    if issue is None:
        result.update({"skipped": True, "reason": "no pullable ready issue"})
        return result

    number = int(issue["number"])
    result.update({"issue": number, "title": issue.get("title") or "?", "owner": owner,
                   "owner_pubkey": owner_pubkey(roster, owner or "")})
    content = format_kick(issue, owner or "", roster, generated_at)
    if opts.dry_run:
        result["content_preview"] = content
        return result

    # 3. claim: flip, kick, record. A rejected kick rolls the label back.
    gh.set_status(number, remove="status:ready", add="status:in-progress")
    try:
        sender = send
        if sender is None:
            from ceremony_clock import send_channel_message as sender
        extra = [["p", owner_pubkey(roster, owner or "")], ["p", roster.pubkey("Cadence")],
                 ["ceremony-dispatch", ALIAS], ["buzz:workflow", "true"]]
        kick = sender(channel_id=roster.channel_id, content=content,
                      extra_tags=[t for t in extra if t[1]])
        accepted = bool(kick.get("accepted"))
    except BaseException as e:
        gh.set_status(number, remove="status:in-progress", add="status:ready")
        result.update({"labels_flipped": False, "channel_accepted": False,
                       "error": f"kick failed, label rolled back: {type(e).__name__}"})
        return result
    if not accepted:
        gh.set_status(number, remove="status:in-progress", add="status:ready")
        result.update({"labels_flipped": False, "channel_accepted": False,
                       "error": "kick not accepted, label rolled back"})
        return result
    comment = gh.comment(
        number,
        f"{CLAIM_MARK} \u2014 staffed to {owner} in #workflow. WIP=1. Do not self-merge.",
    )
    result.update({"labels_flipped": True, "comment_id": comment.get("id"),
                   "channel_accepted": True,
                   "event_id": kick.get("id") or kick.get("event_id")})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanical eng-gate pickup after Path B kick.")
    parser.add_argument("name", help="workflow alias (same as ceremony_clock.py)")
    parser.add_argument("--dry-run", action="store_true", help="select only; do not mutate or post")
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    parser.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    parser.add_argument("--no-health-check", action="store_true")
    parser.add_argument("--only", default="", help="comma-separated issue numbers (proof/testing)")
    args = parser.parse_args()
    alias = args.name.strip()

    if alias != ALIAS:
        print(json.dumps({"alias": alias, "skipped": True, "reason": "not eng-gate-sweep"}))
        return 0

    lock = acquire_lock()
    if lock is None:
        print(json.dumps({"alias": alias, "skipped": True, "reason": "lock held"}))
        return 0
    try:
        opts = Options(
            dry_run=args.dry_run,
            stale_hours=args.stale_hours,
            cooldown_hours=args.cooldown_hours,
            health_check=not args.no_health_check,
            only={int(x) for x in args.only.split(",") if x.strip()},
        )
        result = run_dispatch(GitHub(github_token()), load_roster(), opts)
        if not result.get("skipped") and not args.dry_run and result.get("channel_accepted"):
            write_state(result)
        print(json.dumps(result, indent=2 if args.dry_run else None, separators=None if args.dry_run else (",", ":")))
        if result.get("skipped") or args.dry_run:
            return 0
        return 0 if result.get("channel_accepted") else 2
    except Exception as e:
        print(json.dumps({"alias": alias, "error": str(e), "generated_at": utc_now()}))
        return 4
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
