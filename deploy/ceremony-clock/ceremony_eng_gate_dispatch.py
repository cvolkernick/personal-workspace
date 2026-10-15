#!/usr/bin/env python3
"""Mechanical eng-gate pickup dispatcher (pi-dispatch).

Runs after the Path B eng-gate-sweep kick (``ceremony-clock@eng-gate-sweep``,
``ExecStartPost``). Not an LLM. Each run:

0. **Preflight** - before any label is touched: the local state file must be readable
   (a corrupt one raises, it is never reset to empty), the roster must be valid and the
   kick sender must be importable. Any failure ends the run loudly (exit 3).
1. **Release sweep** - a ``status:in-progress`` claim *that pi-dispatch made itself* and that
   shows no branch, PR or holder comment for ``--stale-hours`` (24h) is returned to
   ``status:ready`` with a comment naming the previous holder. Claims made by Buzz/GrokBuild,
   Grok or a human are never counted as WIP and never released.
2. **Pick** - at most one ``status:ready`` issue, flips it to
   ``status:in-progress`` and posts a staffing kick in #workflow.

Pick rules (#1195):

* The issue's Worker line is authoritative (``Worker``/``Workers``/``Assigned worker``/
  ``Dispatch``..., case-insensitive, bold/colon/dash/table variants). Fenced code, blockquotes
  and HTML comments are ignored. Buzz/GrokBuild, in-chat, direct handoff, human, a malformed,
  unknown, ambiguous or conflicting Worker line, and an empty body are **not** staffed here.
  A Worker line that names Forge/Frankenfit/Nakatoshi routes to that bot. A non-empty body
  with no Worker line keeps the legacy title/label routing.
* No re-claim within ``--cooldown-hours`` (24h) of a Grok eng-gate re-staff
  comment (``Re-staffed (Grok eng-gate``) or of a pi-dispatch release.
* WIP is still one issue per owner, but only **live claims pi-dispatch made itself** count
  (identified by its exact ``[pi-dispatch] started ... staffed to <owner>`` marker, or the local
  claim record for a claim whose marker comment failed). A stale claim, or a Buzz/GrokBuild,
  Grok or human claim, no longer blocks the owner's queue.
* Claim order (exactly what the code does): the sender is built first, then add
  ``status:in-progress``, remove ``status:ready`` (add first, so a failed call can never
  leave the issue without a status label), send the kick, and only then post
  ``[pi-dispatch] started``. A rejected or failed kick swaps the labels back (again add
  first), posts nothing and puts the issue on a local ~1h back-off (``--kick-cooldown-hours``)
  so it is not flipped and rolled back every sweep.
* A failed release comment is not retried every sweep: the issue gets a local
  cooldown (state dir) for ``--cooldown-hours``.
* The claim clock starts at the LATEST of the ``started`` marker, the last release
  comment and the latest ``labeled status:in-progress`` event, so a re-label
  restarts it; a re-label by anyone else also ends pi-dispatch's ownership of the claim.
* ``Buzz/GrokBuild`` issues are never staffed here (no flag, no roster entry).
* Consumer health gate: when the owner's newest message in #workflow inside the
  last 24h is the harness error ("I couldn't process the last request ..."), the
  owner is not staffed. The harness that turns a kick into a session is outside
  this script; claiming into a dead harness is what stalled the queue.
  Fail-open when the relay cannot be read.

State files (``CEREMONY_STATE_DIR``) are written atomically (temp file in the same directory,
flush + fsync, ``os.replace``, directory fsync).

Exit codes: 0 ok / nothing to do, 2 kick not accepted, 3 loud failure (corrupt state, bad
roster, no sender, or a state write that failed after a mutation), 4 unexpected error.

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
import shutil
import sys
import tempfile
import unicodedata
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

PAGE_SIZE = 100
MAX_PAGES = 200  # hard stop (20,000 items); exceeding it is an error, never a silent cut-off
DEFAULT_STALE_HOURS = 24.0
DEFAULT_COOLDOWN_HOURS = 24.0
DEFAULT_KICK_COOLDOWN_HOURS = 1.0
HEALTH_WINDOW_HOURS = 24.0
MAX_AUTO_RELEASES = 2

CLAIM_MARK = "[pi-dispatch] started"
RELEASE_MARK = "[pi-dispatch] released"
RESTAFF_MARK = "Re-staffed (Grok eng-gate"
# The exact text pi-dispatch posts (see ``run_dispatch``). Anything else - Grok, a human, Buzz,
# a quote of the marker - is not a pi-dispatch claim.
CLAIM_RE = re.compile(
    r"\[pi-dispatch\] started [\u2014-] staffed to (?P<owner>[A-Za-z][\w.-]*) in #workflow\. "
    r"WIP=1\. Do not self-merge\."
)
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

# ── Worker line grammar ──────────────────────────────────────────────────────
# Variant spellings (case-insensitive; bold / colon / dash / table / bullet variants):
#   Worker / Workers / Assigned worker / Assigned to / Dispatch(ed) (to) / Staffed to|by  (primary keys)
#   Owner(s) / Assignee(s)                                                                (weak keys)
# A weak key only counts when its value names a staffable agent, Buzz/GrokBuild or an in-chat
# marker, so "Owner: Chris" (a human owner) is not a Worker declaration.
PRIMARY_KEY = (
    r"assigned[ _-]*(?:to|workers?|owners?|agents?|bots?)?|workers?|"
    r"dispatch(?:ed)?(?:[ _-]*to)?|staffed[ _-]*(?:to|by)|staffing"
)
WEAK_KEY = r"owners?|assignees?"
_EMPH = r"(?:\*\*|__|\*|_|`)*"
WORKER_DECL_RE = re.compile(
    r"^[ \t]*(?:\|[ \t]*)?(?:[-*+][ \t]+|\d+[.)][ \t]+)?(?:#{1,6}[ \t]+)?" + _EMPH + r"[ \t]*"
    r"(?P<key>(?P<primary>" + PRIMARY_KEY + r")|(?P<weak>" + WEAK_KEY + r"))(?![A-Za-z0-9])" + _EMPH + r"[ \t]*"
    r"(?:(?P<sep>:|\||[-\u2013\u2014=]+>?(?=[ \t]|$)|\u2192)[ \t]*" + _EMPH + r"[ \t]*)?"
    r"(?P<value>[^\n]*)$",
    re.I | re.M,
)
GROKBUILD_RE = re.compile(r"grok[\s_-]*build|\bbuzz\b", re.I)
NO_DISPATCH_BODY_RE = re.compile(
    r"do not let pi-dispatch|pi-dispatch:\s*(?:skip|no)\b|no pi-dispatch", re.I
)
IN_CHAT_RE = re.compile(
    r"in[- ]chat|direct hand-?off|hand-?off only|human|chris[- ]gate|do not (?:pull|staff)", re.I
)
STAFFABLE_WORDS = {"forge", "frankenfit", "nakatoshi"}
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad"), None)
FENCE_RE = re.compile(r"^[ \t]{0,3}(?P<fence>`{3,}|~{3,})")
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.S)

PRIORITY_RANK = {"p0": 0, "p1": 1, "p2": 2, "p3": 3}
DEFAULT_STAFFABLE = ("Forge", "Frankenfit", "Nakatoshi")
# Buzz/GrokBuild is staffed by a human/Buzz harness, never by pi-dispatch. These names are
# refused even if a roster lists them.
NEVER_STAFF = {"grokbuild", "buzz"}


def never_staff(name: str | None) -> bool:
    """True for any spelling of Buzz/GrokBuild (``Grok-Build``, ``BUZZ``, ``buzz agent``)."""
    squashed = re.sub(r"[^a-z]", "", (name or "").lower())
    return squashed in NEVER_STAFF or "grokbuild" in squashed or squashed.startswith("buzz")

SCHEDULER_ENV = Path.home() / ".config" / "workflow-scheduler.env"
STATE_DIR = Path(os.environ.get("CEREMONY_STATE_DIR") or Path.home() / ".local" / "state" / "ceremony-clock")
LOCK_PATH = STATE_DIR / "dispatch.lock"
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
    claim_authors: tuple[str, ...] = ()  # optional: only trust started markers by these GitHub logins

    def pubkey(self, name: str) -> str:
        return self.agents.get(name, "")

    def canonical(self, name: str) -> str | None:
        for known in self.agents:
            if known.lower() == name.lower():
                return known
        return None


class RosterError(RuntimeError):
    """The dispatch roster is missing, unreadable or invalid."""


HEX64_RE = re.compile(r"[0-9a-fA-F]{64}")


def load_roster(path: Path | None = None) -> Roster:
    path = path or ROSTER_PATH
    if not path.is_file():
        raise SystemExit(f"roster missing: {path} (see dispatch-roster.example.json)")
    try:
        data = json.loads(path.read_text())
        agents = {str(k): str(v.get("pubkey") if isinstance(v, dict) else v) for k, v in data["agents"].items()}
        return Roster(
            channel_id=str(data["workflow_channel_id"]),
            agents=agents,
            staffable=tuple(data.get("staffable") or DEFAULT_STAFFABLE),
            claim_authors=tuple(str(x) for x in (data.get("claim_authors") or ())),
        )
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as e:
        raise RosterError(f"roster {path} is unreadable or invalid ({type(e).__name__}: {e})") from e


def validate_roster(roster: Roster) -> None:
    """Fail before any label is touched if the roster cannot produce a valid kick."""
    if not roster.channel_id.strip():
        raise RosterError("roster has no workflow_channel_id")
    if not any(a.lower() not in NEVER_STAFF and a in roster.agents for a in roster.staffable):
        raise RosterError("roster lists no staffable agent that is also in agents")


def valid_pubkey(pubkey: str) -> bool:
    return bool(HEX64_RE.fullmatch(pubkey or ""))


class SenderUnavailable(RuntimeError):
    """The kick sender cannot be built (import or setup failed)."""


def resolve_sender(send: Callable[..., dict] | None = None) -> Callable[..., dict]:
    """The function that posts the kick, built BEFORE any label is touched."""
    if send is not None:
        return send
    try:
        from ceremony_clock import send_channel_message
    except BaseException as e:  # SystemExit from a half-configured module counts too
        raise SenderUnavailable(f"cannot import ceremony_clock.send_channel_message: {type(e).__name__}: {e}") from e
    if not callable(send_channel_message):
        raise SenderUnavailable("ceremony_clock.send_channel_message is not callable")
    return send_channel_message



# ── Worker line and routing ──────────────────────────────────────────────────


@dataclass
class Worker:
    # agent | grokbuild | in-chat | unknown | empty | none  (only "agent" can be staffed, and
    # only when the name is a staffable roster agent; grokbuild is never staffed)
    kind: str
    name: str | None = None
    raw: str | None = None


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")


def split_noise(body: str) -> tuple[str, str]:
    """(authoritative text, ignored text). Fenced code blocks (an unclosed fence runs to the
    end), blockquote lines and HTML comments are not authoritative."""
    body = _normalize(body)
    noise: list[str] = [m.group(0) for m in HTML_COMMENT_RE.finditer(body)]
    body = HTML_COMMENT_RE.sub("\n", body)
    keep: list[str] = []
    fence: str | None = None
    for line in body.split("\n"):
        m = FENCE_RE.match(line)
        if fence is not None:
            noise.append(line)
            if m and m.group("fence")[0] == fence[0] and len(m.group("fence")) >= len(fence) and not line.strip(" \t`~"):
                fence = None
            continue
        if m:
            fence = m.group("fence")
            noise.append(line)
            continue
        if line.lstrip().startswith(">"):
            noise.append(line)
            continue
        keep.append(line)
    return "\n".join(keep), "\n".join(noise)


def _clean_value(value: str) -> str:
    return re.sub(r"[*_`|]+", " ", value).strip(" \t.-\u2013\u2014:")


def classify_value(raw: str) -> Worker:
    """One Worker value. Anything that is not clearly one staffable agent is not staffed."""
    value = _clean_value(raw)
    if not value:
        return Worker("unknown", raw=raw.strip() or "(empty)")
    low = value.lower()
    if IN_CHAT_RE.search(low):
        return Worker("in-chat", raw=value)
    if GROKBUILD_RE.search(low):
        return Worker("grokbuild", raw=value)
    words = set(re.findall(r"[a-z]+", low)) & STAFFABLE_WORDS
    if len(words) > 1:
        return Worker("unknown", raw=value)  # "Forge / Frankenfit" is ambiguous
    first = re.split(r"[\s(/,;:.&+\-\u2014]+", value, maxsplit=1)[0]
    return Worker("agent", name=first or None, raw=value) if first else Worker("unknown", raw=value)


def declarations(text: str) -> list[Worker]:
    """Every Worker declaration in ``text`` (already normalized), as classified Workers."""
    lines = text.split("\n")
    primary: list[Worker] = []
    weak: list[Worker] = []
    for i, line in enumerate(lines):
        m = WORKER_DECL_RE.match(line)
        if not m:
            continue
        value = m.group("value")
        if not m.group("sep"):
            # Prose such as "Worker nodes ..." is not a declaration; a Buzz/GrokBuild mention is
            # still honoured (``Worker Buzz``) because the safe reading is "not staffed".
            if not GROKBUILD_RE.search(value):
                continue
        if not _clean_value(value):  # ``Workers:`` + bullet list on the next lines
            items: list[str] = []
            j = i + 1
            if j < len(lines) and not lines[j].strip():
                j += 1
            while j < len(lines) and re.match(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S", lines[j]):
                items.append(re.sub(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+", "", lines[j]))
                j += 1
            value = " / ".join(items)
        w = classify_value(value)
        if m.group("primary"):
            primary.append(w)
        elif w.kind == "grokbuild" or (w.kind == "agent" and (w.name or "").lower() in STAFFABLE_WORDS):
            weak.append(w)
    if primary:
        return primary + [w for w in weak if w.kind == "grokbuild"]
    return weak


def parse_worker(body: str | None) -> Worker:
    """The issue's Worker (see the grammar above). Unknown or ambiguous means not staffed.

    * Fenced code, blockquotes and HTML comments are ignored. If a Worker line exists only
      there, the issue is ``unknown`` (never routed by label/title).
    * Conflicting Worker lines are ``unknown``; repeated identical ones are fine.
    * A body with nothing in it is ``empty``; a non-empty body with no Worker line is ``none``
      (legacy label/title routing).
    """
    body = body or ""
    text, noise = split_noise(body)
    found = declarations(text)
    if not found:
        ignored = declarations(re.sub(r"(?m)^[ \t]*(?:>[ \t]*)+", "", noise))
        if ignored:
            return Worker("unknown", raw="Worker line only inside a quote, code block or comment")
        if not text.strip():
            return Worker("empty")
        return Worker("none")
    keys = {(w.kind, (w.name or "").lower() if w.kind == "agent" else "") for w in found}
    if len(keys) > 1:
        names = sorted(f"{w.kind}:{w.name or w.raw}" for w in found)
        return Worker("unknown", raw="conflicting Worker lines: " + " | ".join(names))
    return found[0]


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
    if worker.kind == "empty":
        return None, "empty issue body - not staffed by pi-dispatch"
    if worker.kind == "none":
        legacy = roster.canonical(legacy_owner_name(issue))
        if legacy and legacy in roster.staffable and not never_staff(legacy):
            return legacy, None
        return None, f"no Worker line and {legacy_owner_name(issue)} is not in the roster"
    if worker.kind == "grokbuild":
        return None, f"Worker: {worker.raw} - Buzz/GrokBuild is never staffed by pi-dispatch"
    if worker.kind == "unknown":
        return None, f"Worker: {worker.raw} - unknown or ambiguous Worker line, not staffed by pi-dispatch"
    if worker.kind == "in-chat":
        return None, f"Worker: {worker.raw} - in-chat, not staffed by pi-dispatch"
    canon = roster.canonical(worker.name or "")
    if canon and canon in roster.staffable and not never_staff(canon):
        return canon, None
    return None, f"Worker: {worker.raw} - unknown worker, not staffed by pi-dispatch"


def owner_pubkey(roster: Roster, owner: str) -> str:
    return roster.pubkey(owner)


# ── GitHub client ────────────────────────────────────────────────────────────


def next_link(header: str | None) -> str | None:
    """URL of ``rel="next"`` from a GitHub ``Link`` header, if any."""
    for part in (header or "").split(","):
        m = re.match(r'\s*<([^>]+)>\s*;\s*rel="?next"?', part)
        if m:
            return m.group(1)
    return None


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def _request(self, method: str, path: str, body: dict | None = None) -> tuple[object, dict]:
        """One HTTP call. Returns (parsed JSON, response headers with lower-case keys)."""
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
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return (json.loads(raw) if raw else {}), headers
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")[:400]
            raise RuntimeError(f"GitHub HTTP {e.code} {method} {path}: {err}") from e

    def call(self, method: str, path: str, body: dict | None = None) -> object:
        return self._request(method, path, body)[0]

    def paginate(self, path: str, *, max_pages: int = MAX_PAGES) -> list:
        """Every item of a list endpoint: follow ``Link rel=next``; when the response has no
        Link header at all, keep requesting the next page while pages come back full.
        Exceeding ``max_pages`` raises (callers treat an error as "do not release")."""
        sep = "&" if "?" in path else "?"
        base = f"{path}{sep}per_page={PAGE_SIZE}"
        url = base
        out: list = []
        for _ in range(max_pages):
            data, headers = self._request("GET", url)
            if not isinstance(data, list):
                raise RuntimeError(f"expected a list from GET {path}, got {type(data).__name__}")
            out.extend(data)
            nxt = next_link(headers.get("link"))
            if nxt:
                url = nxt
            elif "link" not in headers and len(data) >= PAGE_SIZE:
                m = re.search(r"[?&]page=(\d+)", url)
                page = (int(m.group(1)) if m else 1) + 1
                url = f"{base}&page={page}"
            else:
                return out
        raise RuntimeError(f"pagination for {path} exceeded {max_pages} pages")

    def list_labeled(self, label: str) -> list[dict]:
        q = urllib.parse.urlencode({"state": "open", "labels": label})
        data = self.paginate(f"/issues?{q}")
        return [i for i in data if "pull_request" not in i]  # PRs share /issues

    def comments(self, number: int) -> list[dict]:
        return self.paginate(f"/issues/{number}/comments")

    def label_events(self, number: int) -> list[dict]:
        return self.paginate(f"/issues/{number}/events")

    def related_prs(self, number: int) -> list[dict]:
        q = urllib.parse.quote(f"repo:{REPO} type:pr {number} in:title,body")
        data = self.call("GET", f"https://api.github.com/search/issues?q={q}&per_page=30")
        items = data.get("items", []) if isinstance(data, dict) else []
        pat = re.compile(rf"(?<![\w/])#{number}(?!\d)|/issues/{number}(?!\d)")
        return [p for p in items if pat.search(f"{p.get('title') or ''}\n{p.get('body') or ''}")]

    def branches(self) -> list[str]:
        """All branch names (the repo has 500+; one page of 100 is never enough)."""
        return [str(b.get("name")) for b in self.paginate("/branches")]

    def branch_commit_time(self, name: str) -> datetime | None:
        data = self.call("GET", f"/branches/{urllib.parse.quote(name, safe='')}")
        if isinstance(data, dict):
            commit = (data.get("commit") or {}).get("commit") or {}
            return parse_ts((commit.get("committer") or {}).get("date"))
        return None

    def remove_label(self, number: int, label: str) -> None:
        self.call("DELETE", f"/issues/{number}/labels/{urllib.parse.quote(label, safe='')}")

    def set_status(self, number: int, *, remove: str, add: str) -> None:
        """Swap status labels, add first. If the add fails nothing changed and the old label
        stays; if only the remove fails the issue briefly carries both (raises), never none."""
        self.call("POST", f"/issues/{number}/labels", {"labels": [add]})
        self.remove_label(number, remove)

    def comment(self, number: int, body: str) -> dict:
        data = self.call("POST", f"/issues/{number}/comments", {"body": body})
        return data if isinstance(data, dict) else {}


class StateFileError(RuntimeError):
    """A local state file is unreadable or corrupt (or cannot be written).

    Raised, never papered over: treating a damaged cooldown file as empty could silently
    re-enable releases and re-claims."""


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return  # platform without directory fds; the file itself is already fsynced
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    """Crash-safe replace: temp file in the SAME directory, flush + fsync, ``os.replace``, then
    fsync the directory. A crash at any point leaves either the old file or the new one, never
    a truncated mix; a leftover ``.<name>.*.tmp`` after a hard crash is harmless."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class CooldownStore:
    """Local state that must survive between sweeps (``release-cooldowns.json``), written
    atomically and read strictly.

    * ``release``: issue -> cooldown end after a release whose comment failed (no on-issue marker).
    * ``kick``: issue -> cooldown end (~1h) after a failed or rejected kick, so one sick issue
      is not flipped and rolled back every sweep.
    * ``claims``: issue -> {owner, at} for every claim pi-dispatch made itself, so a claim whose
      ``started`` comment failed to post is still recognised as ours.

    A missing file is an empty store. An unreadable or corrupt file raises ``StateFileError``
    (a copy of the bad content is kept next to it for forensics; the original is left in place so
    the sweep keeps failing loudly until a human fixes or removes it)."""

    SECTIONS = ("release", "kick", "claims")

    def __init__(self, path: Path | None = None):
        self.path = path or STATE_DIR / "release-cooldowns.json"

    def _corrupt(self, why: str, raw: bytes | None = None) -> StateFileError:
        kept = ""
        if raw:
            copy = self.path.with_name(f"{self.path.name}.corrupt-{hashlib.sha256(raw).hexdigest()[:8]}")
            try:
                if not copy.exists():
                    shutil.copyfile(self.path, copy)
                kept = f" A copy of the bad content is at {copy}."
            except OSError:
                pass
        msg = (
            f"state file {self.path} is corrupt or unreadable ({why}); refusing to continue with an "
            f"empty state because that could re-enable releases and re-claims.{kept} "
            f"Fix or remove the file to resume."
        )
        print(f"ERROR pi-dispatch: {msg}", file=sys.stderr)
        return StateFileError(msg)

    def load(self) -> dict[str, dict]:
        empty: dict[str, dict] = {k: {} for k in self.SECTIONS}
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return empty
        except OSError as e:
            raise self._corrupt(f"unreadable: {e}") from e
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise self._corrupt(f"not valid JSON: {e}", raw) from e
        if not isinstance(data, dict) or data.get("version") != 2:
            raise self._corrupt("unexpected structure (want a version 2 object)", raw)
        out: dict[str, dict] = {}
        for sec in self.SECTIONS:
            part = data.get(sec, {})
            if not isinstance(part, dict):
                raise self._corrupt(f"section {sec!r} is not an object", raw)
            for k, v in part.items():
                ok = isinstance(k, str) and k.isdigit()
                if sec == "claims":
                    ok = ok and isinstance(v, dict) and isinstance(v.get("owner"), str) and parse_ts(v.get("at")) is not None
                else:
                    ok = ok and isinstance(v, str) and parse_ts(v) is not None
                if not ok:
                    raise self._corrupt(f"bad entry {sec}.{k!r}", raw)
            out[sec] = dict(part)
        return out

    def validate(self) -> None:
        """Raise ``StateFileError`` now if the file is damaged (called before any mutation)."""
        self.load()

    def _save(self, data: dict[str, dict]) -> None:
        now = datetime.now(timezone.utc)
        for sec in ("release", "kick"):
            data[sec] = {k: v for k, v in data[sec].items() if (parse_ts(v) or now) > now - timedelta(days=7)}
        data["claims"] = {k: v for k, v in data["claims"].items()
                          if (parse_ts(v.get("at")) or now) > now - timedelta(days=30)}
        try:
            atomic_write_text(self.path, json.dumps({"version": 2, **data}, indent=2, sort_keys=True) + "\n")
        except OSError as e:
            raise StateFileError(f"cannot write state file {self.path}: {e}") from e

    def until(self, number: int, kind: str = "release") -> datetime | None:
        return parse_ts(self.load()[kind].get(str(number)))

    def set(self, number: int, until: datetime, kind: str = "release") -> bool:
        """Record a cooldown. Returns True; raises ``StateFileError`` if it cannot be saved."""
        data = self.load()
        data[kind][str(number)] = _iso(until)
        self._save(data)
        return True

    def claim(self, number: int) -> dict | None:
        return self.load()["claims"].get(str(number))

    def record_claim(self, number: int, owner: str, at: datetime) -> None:
        data = self.load()
        data["claims"][str(number)] = {"owner": owner, "at": _iso(at)}
        self._save(data)

    def forget_claim(self, number: int) -> None:
        data = self.load()
        if data["claims"].pop(str(number), None) is not None:
            self._save(data)


# ── claim assessment ─────────────────────────────────────────────────────────


@dataclass
class Claim:
    number: int
    holder: str | None
    claimed_at: datetime | None
    last_activity: datetime | None = None
    state: str = "live"  # live | stale | foreign
    reason: str = ""
    owner_for_wip: str = ""  # only ever set for a claim pi-dispatch made itself
    own: bool = False  # made by pi-dispatch (started marker / local claim ledger)


def latest_claim_marker(comments: list[dict], roster: Roster | None = None) -> tuple[dict | None, str | None]:
    """Newest valid ``[pi-dispatch] started`` marker and the owner it names.

    Valid means the exact text pi-dispatch posts, naming a staffable roster agent that is not
    Buzz/GrokBuild, and (only when the roster sets ``claim_authors``) posted by one of those
    GitHub logins. The dispatcher's token has changed account over time and Grok/humans comment
    under the same logins, so the marker text is the identity, not the login."""
    found: dict | None = None
    holder: str | None = None
    for c in comments:
        m = CLAIM_RE.fullmatch(str(c.get("body") or "").strip())
        if not m:
            continue
        owner = m.group("owner")
        if never_staff(owner):
            continue
        if roster is not None:
            canon = roster.canonical(owner)
            if not canon or canon not in roster.staffable or never_staff(canon):
                continue
            owner = canon
            if roster.claim_authors:
                login = str((c.get("user") or {}).get("login") or "").lower()
                if login not in {a.lower() for a in roster.claim_authors}:
                    continue
        found, holder = c, owner
    return found, holder


def latest_release_comment(comments: list[dict]) -> datetime | None:
    best: datetime | None = None
    for c in comments:
        if str(c.get("body") or "").lstrip().startswith(RELEASE_MARK):
            ts = parse_ts(c.get("created_at"))
            if ts and (best is None or ts > best):
                best = ts
    return best


def latest_claim_label(events: list[dict]) -> datetime | None:
    """Newest ``labeled status:in-progress`` event (a re-label counts as a new claim)."""
    best: datetime | None = None
    for ev in events:
        if ev.get("event") == "labeled" and (ev.get("label") or {}).get("name") == "status:in-progress":
            ts = parse_ts(ev.get("created_at"))
            if ts and (best is None or ts > best):
                best = ts
    return best


# pi-dispatch posts its marker seconds after it flips the label; allow this much slack
# before a newer label event is treated as a different claim than the marker's.
MARKER_SLACK = timedelta(minutes=10)


def claim_start(
    comments: list[dict],
    events: list[dict],
    issue: dict,
    now: datetime,
    roster: Roster | None = None,
    ledger: dict | None = None,
) -> tuple[datetime, str | None]:
    """(start of the current claim, holder if pi-dispatch made this claim else None).

    The start is the LATEST of the ``started`` marker, the last release comment and the
    latest ``labeled status:in-progress`` event, so re-labelling an issue restarts the
    clock instead of inheriting the age of an old marker.

    The claim is pi-dispatch's own only when its marker (or its local claim-ledger entry, for
    a claim whose marker comment failed to post) is the newest thing: not followed by a
    release comment, and not followed by a later ``labeled status:in-progress`` event (a
    re-label by Grok, a human or Buzz). Anything else has no holder."""
    marker, holder = latest_claim_marker(comments, roster)
    marker_ts = parse_ts(marker.get("created_at")) if marker else None
    release_ts = latest_release_comment(comments)
    label_ts = latest_claim_label(events)
    ledger_ts = parse_ts(ledger.get("at")) if ledger else None
    times = [t for t in (marker_ts, release_ts, label_ts, ledger_ts) if t]
    start = max(times) if times else (parse_ts(issue.get("updated_at")) or now)

    def ours(ts: datetime | None) -> bool:
        if ts is None:
            return False
        if release_ts and release_ts >= ts:
            return False
        return not (label_ts and label_ts - ts > MARKER_SLACK)

    if holder and ours(marker_ts):
        return start, holder
    if ledger and ledger_ts and ours(ledger_ts):
        owner = str(ledger.get("owner") or "")
        canon = roster.canonical(owner) if roster is not None else owner
        if canon and not never_staff(canon):
            return start, canon
    return start, None


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
    cooldowns: "CooldownStore | None" = None,
) -> Claim:
    """Classify one ``status:in-progress`` issue.

    ``foreign`` = not made by pi-dispatch (Buzz/GrokBuild, Grok, a human): not counted as WIP
    for anyone and never released. Only pi-dispatch's own claims are ``live`` or ``stale``."""
    n = int(issue["number"])
    owner, _ = resolve_owner(issue, roster)
    try:
        comments = gh.comments(n)
        events = gh.label_events(n)
    except Exception as e:  # fail safe: never release on an API error
        return Claim(
            number=n,
            holder=None,
            claimed_at=now,
            owner_for_wip=owner or "",  # a Buzz/unknown issue pins nobody
            reason=f"live: could not read claim history ({e})"[:200],
        )
    ledger = cooldowns.claim(n) if cooldowns is not None else None
    claimed_at, holder = claim_start(comments, events, issue, now, roster, ledger)
    claim = Claim(number=n, holder=holder, claimed_at=claimed_at, own=holder is not None, owner_for_wip=holder or "")
    if holder is None:
        claim.state = "foreign"
        claim.reason = "not a pi-dispatch claim (no matching started marker): not counted as WIP, never released"
        return claim
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
    holder = claim.holder or "unknown"
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


def write_state(payload: dict, path: Path | None = None) -> None:
    """Atomic: a crash mid-write leaves the previous last-dispatch.json intact."""
    atomic_write_text(path or STATE_DIR / "last-dispatch.json", json.dumps(payload, indent=2) + "\n")


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
    kick_cooldown_hours: float = DEFAULT_KICK_COOLDOWN_HOURS
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
    cooldowns: CooldownStore | None = None,
) -> dict:
    """One pass. Raises ``StateFileError`` / ``RosterError`` / ``SenderUnavailable`` BEFORE any
    label is touched when local state is corrupt, the roster is invalid or the kick sender
    cannot be built. ``result["errors"]`` lists problems that happened after a mutation
    (for example a cooldown that could not be saved); the caller must treat them as failures."""
    now = now or datetime.now(timezone.utc)
    cooldowns = cooldowns or CooldownStore()
    generated_at = _iso(now)
    stale_after = timedelta(hours=opts.stale_hours)
    cooldown = timedelta(hours=opts.cooldown_hours)
    kick_cooldown = timedelta(hours=opts.kick_cooldown_hours)
    errors: list[str] = []

    # 0. Preflight. Nothing below mutates until these pass (a corrupt cooldown file must never
    #    read as "no cooldowns", and a rejected kick must not be discovered after flipping labels).
    cooldowns.validate()
    validate_roster(roster)
    sender = None if opts.dry_run else resolve_sender(send)

    def note_error(msg: str) -> None:
        errors.append(msg)
        print(f"ERROR pi-dispatch: {msg}", file=sys.stderr)

    ready = gh.list_labeled("status:ready")
    in_progress = gh.list_labeled("status:in-progress")
    only = opts.only

    # 1. assess every claim; release the dead ones. Only claims pi-dispatch made itself can be
    #    stale: Buzz/GrokBuild, Grok and human claims are never counted and never released.
    branch_cache: dict = {}
    claims = [assess_claim(gh, i, roster, now, stale_after, branch_cache, cooldowns) for i in in_progress]
    released: list[dict] = []
    no_pick: set[int] = set()
    for c in claims:
        if c.state != "stale" or not c.own:
            continue
        if only and c.number not in only:
            continue
        entry = {"issue": c.number, "holder": c.holder, "reason": c.reason}
        released.append(entry)
        if opts.dry_run:
            entry["released"] = False
            continue
        try:  # add ready first, then remove in-progress
            gh.set_status(c.number, remove="status:in-progress", add="status:ready")
        except Exception as e:
            entry.update({"released": False, "error": f"label swap failed: {type(e).__name__}"})
            continue
        entry["released"] = True
        try:
            cooldowns.forget_claim(c.number)
        except StateFileError as e:
            note_error(f"#{c.number}: could not clear local claim record: {e}")
        try:
            gh.comment(c.number, release_comment(c, opts.stale_hours, opts.cooldown_hours))
        except Exception as e:
            # No on-issue release marker now, so remember the cooldown locally; otherwise the
            # issue would be re-staffed on the very next sweep.
            until = now + cooldown
            entry.update({"comment_failed": f"{type(e).__name__}", "cooldown_until": _iso(until)})
            no_pick.add(c.number)
            try:
                entry["cooldown_saved"] = cooldowns.set(c.number, until)
            except StateFileError as se:
                entry["cooldown_saved"] = False
                note_error(f"#{c.number}: release comment failed and the local cooldown could not be saved: {se}")

    live = [c for c in claims if c.state == "live"]
    foreign = [c for c in claims if c.state == "foreign"]
    busy_owners = {c.owner_for_wip for c in live if c.owner_for_wip}
    stale_nums = {c.number for c in claims if c.state == "stale"}
    own_nums = {c.number for c in claims if c.own}
    in_progress_nums = {int(i["number"]) for i in in_progress}

    # A swap that failed half-way can leave an issue with both status labels. When the claim is
    # pi-dispatch's own it is treated as a claim (in-progress wins): never pick it, and drop the
    # leftover ready label. A foreign claim's labels are not ours to touch.
    healed: list[int] = []
    for i in ready:
        n = int(i["number"])
        if n in own_nums and n not in stale_nums and not opts.dry_run and (not only or n in only):
            try:
                gh.remove_label(n, "status:ready")
                healed.append(n)
            except Exception:
                pass

    # 2. pick.
    pool = [
        i for i in ready
        if int(i["number"]) not in in_progress_nums and int(i["number"]) not in no_pick
        and (not only or int(i["number"]) in only)
    ]
    comment_cache: dict[int, list[dict]] = {}
    health_cache: dict[str, tuple[bool, str]] = {}
    health_fn = health or (lambda o: consumer_health(o, roster, now))

    def blocked(issue: dict, owner: str) -> str | None:
        n = int(issue["number"])
        if not valid_pubkey(roster.pubkey(owner)):
            return f"roster has no valid pubkey for {owner}"
        kick_until = cooldowns.until(n, "kick")
        if kick_until and kick_until > now:
            return f"kick failed earlier; local cooldown until {_iso(kick_until)}"
        until = cooldowns.until(n)
        if until and until > now:
            return f"release comment failed earlier; local cooldown until {_iso(until)}"
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
        "foreign_claims": [f"#{c.number}" for c in foreign],
        "stale_claims": sorted(f"#{n}" for n in stale_nums),
        "busy_owners": sorted(busy_owners),
        "released": released,
        "healed_dual_labels": healed,
        "skip_note": skip_note,
        "generated_at": generated_at,
        "dry_run": bool(opts.dry_run),
        "errors": errors,
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

    def back_off(why: str) -> None:
        """A failed claim attempt: report it and keep this issue out of the next sweeps (~1h) so
        the same top candidate is not flipped and rolled back every 15 minutes."""
        result.update({"labels_flipped": False, "channel_accepted": False, "error": why})
        until = now + kick_cooldown
        result["kick_cooldown_until"] = _iso(until)
        try:
            result["kick_cooldown_saved"] = cooldowns.set(number, until, "kick")
        except StateFileError as e:
            result["kick_cooldown_saved"] = False
            note_error(f"#{number}: {why}; local kick cooldown could not be saved: {e}")

    def remember_own_claim() -> None:
        try:
            cooldowns.record_claim(number, owner or "", now)
        except StateFileError as e:
            note_error(f"#{number}: could not record the claim locally: {e}")

    # 3. claim: add in-progress, remove ready, kick, then record. A rejected or failed kick
    #    swaps the labels back (add first again), posts nothing and backs the issue off.
    try:
        gh.set_status(number, remove="status:ready", add="status:in-progress")
    except Exception as e:
        # Add-first: if the add failed nothing changed; if only the remove failed, undo the add.
        try:
            gh.remove_label(number, "status:in-progress")
        except Exception:
            remember_own_claim()  # both labels may now be present: the dual-label heal needs to know it is ours
        back_off(f"label swap failed, no kick sent: {type(e).__name__}")
        return result

    def roll_back(why: str) -> None:
        try:
            gh.set_status(number, remove="status:in-progress", add="status:ready")
        except Exception as e:
            result["rollback_failed"] = type(e).__name__
            remember_own_claim()  # the claim stays; the 24h release will clear it
        back_off(why)

    try:
        extra = [["p", owner_pubkey(roster, owner or "")], ["p", roster.pubkey("Cadence")],
                 ["ceremony-dispatch", ALIAS], ["buzz:workflow", "true"]]
        kick = sender(channel_id=roster.channel_id, content=content,
                      extra_tags=[t for t in extra if t[1]])
        accepted = bool(kick.get("accepted"))
    except (Exception, SystemExit) as e:
        roll_back(f"kick failed, label rolled back: {type(e).__name__}")
        return result
    if not accepted:
        roll_back("kick not accepted, label rolled back")
        return result
    result.update({"labels_flipped": True, "channel_accepted": True,
                   "event_id": kick.get("id") or kick.get("event_id")})
    remember_own_claim()  # so the claim is ours even if the started comment below fails
    try:
        comment = gh.comment(
            number,
            f"{CLAIM_MARK} \u2014 staffed to {owner} in #workflow. WIP=1. Do not self-merge.",
        )
        result["comment_id"] = comment.get("id")
    except Exception as e:
        # The kick is out, so the claim stands. The labeled event starts the 24h clock and the
        # local claim record keeps it recognisable as pi-dispatch's.
        result["claim_comment_failed"] = type(e).__name__
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Mechanical eng-gate pickup after Path B kick.")
    parser.add_argument("name", help="workflow alias (same as ceremony_clock.py)")
    parser.add_argument("--dry-run", action="store_true", help="select only; do not mutate or post")
    parser.add_argument("--stale-hours", type=float, default=DEFAULT_STALE_HOURS)
    parser.add_argument("--cooldown-hours", type=float, default=DEFAULT_COOLDOWN_HOURS)
    parser.add_argument("--kick-cooldown-hours", type=float, default=DEFAULT_KICK_COOLDOWN_HOURS,
                        help="back-off for an issue after a failed or rejected kick (default 1)")
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
            kick_cooldown_hours=args.kick_cooldown_hours,
            health_check=not args.no_health_check,
            only={int(x) for x in args.only.split(",") if x.strip()},
        )
        result = run_dispatch(GitHub(github_token()), load_roster(), opts)
        if not result.get("skipped") and not args.dry_run and result.get("channel_accepted"):
            try:
                write_state(result)
            except OSError as e:
                msg = f"could not write last-dispatch.json: {e}"
                result["errors"].append(msg)
                print(f"ERROR pi-dispatch: {msg}", file=sys.stderr)
        print(json.dumps(result, indent=2 if args.dry_run else None, separators=None if args.dry_run else (",", ":")))
        if result.get("errors"):
            return 3
        if result.get("skipped") or args.dry_run:
            return 0
        return 0 if result.get("channel_accepted") else 2
    except (StateFileError, RosterError, SenderUnavailable) as e:
        # Loud, before any label was touched: the unit fails and the message is in the journal.
        print(json.dumps({"alias": alias, "error": str(e), "kind": type(e).__name__, "generated_at": utc_now()}))
        return 3
    except Exception as e:
        print(json.dumps({"alias": alias, "error": str(e), "generated_at": utc_now()}))
        return 4
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
