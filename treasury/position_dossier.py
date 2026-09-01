"""Per-ticker knowledge dossier for FCC.

Assembles stance, live book, watchlist, deep-dive, policy excerpts,
and related research for one consider-set (or researched) symbol.

Pi FCC runs `master`; finance policy + dives live on `origin/work/treasury`
and the treasury worktree. Loaders follow the same fallback as Bias Spectrum.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from treasury.bias_spectrum import (  # noqa: E402
    ROOT,
    SLEEVE_BTC,
    SLEEVE_STOCKS,
    _as_list,
    _load_policy,
    _load_watchlist,
    _sym,
    _treasury_roots,
    build_bias_spectrum,
)
from treasury.watchlist_dashboard import (  # noqa: E402
    _parse_deep_dive_text,
)

SYM_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")
ROLE_LABEL = {
    "preferred_core": "preferred-core",
    "core": "core allowlist",
    "watch_high": "watchlist high",
    "watch_med": "watchlist med",
    "watch_low": "watchlist low",
}
SLEEVE_LABEL = {
    SLEEVE_BTC: "BTC / digital credit (~40%)",
    SLEEVE_STOCKS: "stocks / growth (~60%)",
}
NEST_RESEARCH = Path.home() / ".buzz" / "RESEARCH"
B2_VAULT = Path.home() / "B2"
_GIT_TEXT_TTL: Dict[str, Tuple[float, str]] = {}
_MAX_MD = 400_000
_MAX_RELATED = 8
_MAX_JOURNAL = 4
_MAX_POLICY = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _word_pat(sym: str) -> re.Pattern[str]:
    # Lookaround so STRC does not match STRCUSX / JR-strcUSX filenames-as-text.
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(sym) + r"(?![A-Za-z0-9])")


def _filename_tokens(name: str) -> set[str]:
    stem = Path(name).stem.upper()
    return {t for t in re.split(r"[^A-Z0-9]+", stem) if t}


def _git_show_text(rel: str) -> str:
    import time

    now = time.monotonic()
    cached = _GIT_TEXT_TTL.get(rel)
    if cached and now - cached[0] < 30.0:
        return cached[1]
    text = ""
    try:
        raw = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", f"origin/work/treasury:{rel}"],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        text = raw.decode("utf-8")
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeDecodeError,
        TypeError,
    ):
        text = ""
    _GIT_TEXT_TTL[rel] = (now, text)
    return text


def _read_text(rel: str) -> str:
    """Finance SoT is work/treasury; master often has a stub of the same path."""
    ordered: List[Path] = []
    tw = Path.home() / "personal-workspace-worktrees" / "treasury"
    if tw.is_dir():
        ordered.append(tw)
    for root in _treasury_roots():
        if root not in ordered:
            ordered.append(root)
    texts: List[str] = []
    for root in ordered:
        path = root / rel
        if path.is_file():
            try:
                texts.append(path.read_text(encoding="utf-8"))
            except OSError:
                continue
    git_text = _git_show_text(rel)
    if git_text:
        texts.append(git_text)
    if not texts:
        return ""
    return max(texts, key=len)


def _list_rel(dir_rel: str) -> List[str]:
    found: List[str] = []
    seen: set[str] = set()
    ordered: List[Path] = []
    tw = Path.home() / "personal-workspace-worktrees" / "treasury"
    if tw.is_dir():
        ordered.append(tw)
    for root in _treasury_roots():
        if root not in ordered:
            ordered.append(root)
    for root in ordered:
        folder = root / dir_rel
        if not folder.is_dir():
            continue
        try:
            names = sorted(p.name for p in folder.iterdir() if p.is_file())
        except OSError:
            continue
        for name in names:
            rel = f"{dir_rel}/{name}"
            if rel not in seen:
                seen.add(rel)
                found.append(rel)
        if found:
            return found
    try:
        raw = subprocess.check_output(
            [
                "git",
                "-C",
                str(ROOT),
                "ls-tree",
                "--name-only",
                f"origin/work/treasury:{dir_rel}",
            ],
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        for line in raw.decode("utf-8").splitlines():
            name = Path(line.strip()).name
            if not name:
                continue
            rel = f"{dir_rel}/{name}"
            if rel not in seen:
                seen.add(rel)
                found.append(rel)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError, TypeError):
        pass
    return found


def _watch_entry(watchlist: Dict[str, Any], sym: str) -> Optional[Dict[str, Any]]:
    for e in _as_list(watchlist.get("entries")):
        if isinstance(e, dict) and _sym(e.get("symbol")) == sym:
            return e
    return None


def _walk_strings(obj: Any, acc: List[str], *, limit: int = 80) -> None:
    if len(acc) >= limit:
        return
    if isinstance(obj, str):
        blob = obj.strip()
        # Skip ticker lists / short labels so the walk budget is prose.
        if len(blob) >= 40:
            acc.append(blob)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_strings(v, acc, limit=limit)
            if len(acc) >= limit:
                return
        return
    if isinstance(obj, list):
        for v in obj:
            _walk_strings(v, acc, limit=limit)
            if len(acc) >= limit:
                return


def _policy_excerpts(policy: Dict[str, Any], sym: str) -> List[str]:
    pat = _word_pat(sym)
    blobs: List[str] = []
    _walk_strings(policy, blobs, limit=120)
    out: List[str] = []
    seen: set[str] = set()
    for blob in blobs:
        if not pat.search(blob):
            continue
        # Skip raw ticker lists ("STRC", "SATA") — keep prose.
        if len(blob) < 40:
            continue
        key = blob[:180]
        if key in seen:
            continue
        seen.add(key)
        snippet = blob if len(blob) <= 600 else blob[:599] + "…"
        out.append(snippet)
        if len(out) >= _MAX_POLICY:
            break
    return out


def _positions_row(sym: str) -> Optional[Dict[str, str]]:
    text = _read_text("investment/positions.md")
    if not text:
        return None
    pat = _word_pat(sym)
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if pat.search(line):
            return {"path": "investment/positions.md", "row": line.strip()}
    return None


def _snippet_around(text: str, pat: re.Pattern[str], *, width: int = 220) -> str:
    m = pat.search(text)
    if not m:
        return ""
    start = max(0, m.start() - width // 2)
    end = min(len(text), m.end() + width // 2)
    chunk = text[start:end].replace("\n", " ").strip()
    if start > 0:
        chunk = "…" + chunk
    if end < len(text):
        chunk = chunk + "…"
    return chunk


def _title_from_md(text: str, fallback: str) -> str:
    for line in text.splitlines()[:40]:
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or fallback
    return fallback


def _related_from_research(sym: str) -> List[Dict[str, str]]:
    hits: List[Dict[str, str]] = []
    pat = _word_pat(sym)
    own_dive = f"{sym}_DEEP_DIVE.MD"
    for rel in _list_rel("investment/research"):
        name = Path(rel).name
        if name.startswith("."):
            continue
        upper = name.upper()
        if upper == "README.MD":
            continue
        tokens = _filename_tokens(name)
        named = sym in tokens
        is_other_dive = upper.endswith("_DEEP_DIVE.MD") and upper != own_dive
        if is_other_dive:
            continue
        if upper == own_dive:
            continue  # already the Deep dive section
        if not named and not name.lower().endswith(".md"):
            continue
        text = _read_text(rel)
        if not text:
            continue
        if not named and not pat.search(text):
            continue
        hits.append(
            {
                "title": _title_from_md(text, name),
                "path": rel,
                "source": "investment/research",
                "kind": "research",
                "snippet": _snippet_around(text, pat) if pat.search(text) else "",
            }
        )
        if len(hits) >= _MAX_RELATED:
            break
    for rel in _list_rel("investment/research/private"):
        if len(hits) >= _MAX_RELATED:
            break
        name = Path(rel).name
        tokens = _filename_tokens(name)
        if sym not in tokens:
            continue
        text = _read_text(rel)
        hits.append(
            {
                "title": _title_from_md(text, name),
                "path": rel,
                "source": "investment/research/private",
                "kind": "private",
                "snippet": _snippet_around(text, _word_pat(sym)) if text else "",
            }
        )
    return hits


def _related_from_dir(
    folder: Path, *, source: str, sym: str, skip_prefixes: Tuple[str, ...] = ()
) -> List[Dict[str, str]]:
    if not folder.is_dir():
        return []
    pat = _word_pat(sym)
    hits: List[Dict[str, str]] = []
    try:
        paths = sorted(folder.glob("*.md"))
    except OSError:
        return []
    for path in paths:
        name = path.name
        upper = name.upper()
        if any(upper.startswith(p) for p in skip_prefixes):
            continue
        tokens = _filename_tokens(name)
        named = sym in tokens
        if source == "nest" and not named:
            continue
        try:
            if path.stat().st_size > _MAX_MD:
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not named and not pat.search(text):
            continue
        rel = str(path)
        try:
            rel = str(path.resolve().relative_to(Path.home()))
        except ValueError:
            rel = str(path)
        hits.append(
            {
                "title": _title_from_md(text, name),
                "path": rel,
                "source": source,
                "kind": "lock" if "CONSIDER_SET_LOCK" in upper else "note",
                "snippet": _snippet_around(text, pat) if pat.search(text) else "",
            }
        )
        if len(hits) >= 4:
            break
    return hits


def _related_from_b2(sym: str) -> List[Dict[str, str]]:
    vault = Path(os.environ.get("B2_VAULT_PATH") or B2_VAULT).expanduser()
    if not vault.is_dir():
        return []
    pat = _word_pat(sym)
    hits: List[Dict[str, str]] = []
    skip = {"inbox/raw", "inbox/attachments", ".obsidian"}
    try:
        paths = list(vault.rglob("*.md"))
    except OSError:
        return []
    for path in paths:
        rel_parts = path.relative_to(vault).as_posix()
        if any(rel_parts.startswith(s) for s in skip):
            continue
        try:
            if path.stat().st_size > _MAX_MD:
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not pat.search(text):
            continue
        hits.append(
            {
                "title": _title_from_md(text, path.name),
                "path": f"B2/{rel_parts}",
                "source": "b2",
                "kind": "note",
                "snippet": _snippet_around(text, pat),
            }
        )
        if len(hits) >= 3:
            break
    return hits


def _journal_hits(sym: str) -> List[Dict[str, str]]:
    pat = _word_pat(sym)
    out: List[Dict[str, str]] = []
    journal = _read_text("investment/fund_manager_journal.md")
    if journal and pat.search(journal):
        # Last matching paragraphs, newest-ish at the bottom of the file.
        paras = [p.strip() for p in re.split(r"\n\s*\n", journal) if p.strip()]
        for para in reversed(paras):
            if not pat.search(para):
                continue
            snippet = para if len(para) <= 500 else para[:499] + "…"
            out.append(
                {
                    "source": "investment/fund_manager_journal.md",
                    "snippet": snippet,
                }
            )
            if len(out) >= _MAX_JOURNAL:
                break
    decisions = ""
    for root in _treasury_roots():
        p = root / "treasury" / "snapshots" / "fund_manager_decisions.jsonl"
        if p.is_file():
            try:
                # Tail the file — jsonl can grow.
                data = p.read_bytes()
                tail = data[-80_000:] if len(data) > 80_000 else data
                decisions = tail.decode("utf-8", errors="replace")
            except OSError:
                decisions = ""
            break
    if decisions and pat.search(decisions) and len(out) < _MAX_JOURNAL:
        for line in reversed(decisions.splitlines()):
            if not pat.search(line):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rec = None
            snippet = ""
            if isinstance(rec, dict):
                snippet = str(
                    rec.get("summary")
                    or rec.get("why_now")
                    or rec.get("rationale")
                    or line
                )
            else:
                snippet = line.strip()
            if len(snippet) > 400:
                snippet = snippet[:399] + "…"
            out.append(
                {
                    "source": "treasury/snapshots/fund_manager_decisions.jsonl",
                    "snippet": snippet,
                }
            )
            break
    return out


def _stance(
    *,
    sym: str,
    chip: Optional[Dict[str, Any]],
    watch: Optional[Dict[str, Any]],
    dive: Dict[str, Any],
) -> Dict[str, Any]:
    role = (chip or {}).get("role")
    sleeve = (chip or {}).get("sleeve")
    held = bool((chip or {}).get("held"))
    consider = (chip or {}).get("weight_pct")
    book_pct = (chip or {}).get("book_pct")
    role_l = ROLE_LABEL.get(str(role or ""), str(role or "not in consider-set"))
    sleeve_l = SLEEVE_LABEL.get(str(sleeve or ""), str(sleeve or "unmapped"))
    if role == "preferred_core":
        headline = f"Preferred-core of the {sleeve_l} sleeve"
    elif role == "core":
        headline = f"Core allowlist · {sleeve_l}"
    elif role and str(role).startswith("watch_"):
        pri = (chip or {}).get("priority") or (watch or {}).get("priority") or "watch"
        headline = f"Watchlist {pri} · consider-set · {sleeve_l}"
    elif watch:
        headline = f"Watchlist {(watch.get('status') or 'named')} · not currently scored on the axis"
    else:
        headline = "No standing consider-set seat"

    picture_bits = [
        f"{sym} maps to the modernized 60/40 agentic book as {sleeve_l}."
    ]
    if consider is not None:
        picture_bits.append(
            f"Standing new-money consider-share is {consider:g}% of the next dollar "
            f"({role_l}). That is policy bias, not an order ticket."
        )
    if held and book_pct is not None:
        picture_bits.append(
            f"Already held: {book_pct:g}% of deployed agentic equity "
            f"(${(chip or {}).get('market_value')}). Book % is the badge, not the axis."
        )
    elif held:
        picture_bits.append("Held in the agentic book. Book % is annotation only.")
    else:
        picture_bits.append("Unheld. A seat here is consideration, not a fill.")
    picture_bits.append("Auto-buy is off. The fund manager still research/rotates at deploy.")
    if watch and watch.get("last_verdict"):
        picture_bits.append(f"Last watchlist verdict: {watch.get('last_verdict')}.")
    elif dive.get("one_line_conclusion"):
        picture_bits.append(str(dive["one_line_conclusion"]).strip())

    return {
        "headline": headline,
        "picture": " ".join(picture_bits),
        "role": role,
        "role_label": role_l,
        "sleeve": sleeve,
        "sleeve_label": sleeve_l,
        "consider_share_pct": consider,
        "held": held,
        "book_pct": book_pct,
        "market_value": (chip or {}).get("market_value"),
        "quantity": (chip or {}).get("quantity"),
        "auto_buy": False,
        "core_allowlist": role in ("preferred_core", "core"),
        "preferred_core": role == "preferred_core",
        "verdict": (watch or {}).get("last_verdict") or dive.get("status_line"),
        "status": (watch or {}).get("status") or (chip or {}).get("status"),
        "priority": (watch or {}).get("priority") or (chip or {}).get("priority"),
        "one_line": dive.get("one_line_conclusion")
        or (watch or {}).get("thesis_fit")
        or headline,
    }


def _deep_dive_for(sym: str, watch: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    rels: List[str] = []
    if watch and watch.get("last_deep_dive_path"):
        rels.append(str(watch["last_deep_dive_path"]))
    rels.append(f"investment/research/{sym}_deep_dive.md")
    seen: set[str] = set()
    for rel in rels:
        rel = rel.lstrip("./")
        if rel in seen:
            continue
        seen.add(rel)
        text = _read_text(rel)
        if not text:
            continue
        parsed = _parse_deep_dive_text(text)
        parsed["path"] = rel
        return parsed
    return {
        "exists": False,
        "path": f"investment/research/{sym}_deep_dive.md",
        "error": "deep dive not found",
    }


def build_position_dossier(
    symbol: str,
    *,
    fund_manager: Optional[Dict[str, Any]] = None,
    treasury: Optional[Dict[str, Any]] = None,
    policy: Optional[Dict[str, Any]] = None,
    watchlist: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Assemble the knowledge dossier for one ticker."""
    sym = _sym(symbol)
    if not sym or not SYM_RE.fullmatch(sym):
        return {"ok": False, "error": "invalid symbol", "symbol": symbol}

    spec: Dict[str, Any] = {}
    if fund_manager is not None:
        spec["fund_manager"] = fund_manager
    if treasury is not None:
        spec["treasury"] = treasury
    if policy is not None:
        spec["policy"] = policy
    if watchlist is not None:
        spec["watchlist"] = watchlist
    spectrum = build_bias_spectrum(**spec)
    chip = None
    for c in _as_list(spectrum.get("chips")):
        if isinstance(c, dict) and _sym(c.get("symbol")) == sym:
            chip = c
            break

    pol = _load_policy(policy, fund_manager=fund_manager or {})
    wl = _load_watchlist(watchlist)
    watch = _watch_entry(wl, sym)
    dive = _deep_dive_for(sym, watch)
    dive_public = {k: v for k, v in dive.items()}
    related = _related_from_research(sym)
    skip_jr = ("JR_",) if sym in {"STRC", "SATA", "USX"} else ()
    if len(related) < _MAX_RELATED:
        related.extend(
            _related_from_dir(
                NEST_RESEARCH, source="nest", sym=sym, skip_prefixes=skip_jr
            )
        )
    if len(related) < _MAX_RELATED:
        related.extend(_related_from_b2(sym))
    # Dedupe by path, cap.
    deduped: List[Dict[str, str]] = []
    seen_paths: set[str] = set()
    for hit in related:
        key = str(hit.get("path") or "")
        if not key or key in seen_paths:
            continue
        seen_paths.add(key)
        deduped.append(hit)
        if len(deduped) >= _MAX_RELATED:
            break

    stance = _stance(sym=sym, chip=chip, watch=watch, dive=dive)
    watch_public = None
    if watch:
        watch_public = {
            k: v
            for k, v in watch.items()
            if k
            not in (
                # keep the useful fields; drop nothing critical
            )
        }

    return {
        "ok": True,
        "symbol": sym,
        "name": (watch or {}).get("name") or (chip or {}).get("label") or sym,
        "as_of": spectrum.get("as_of") or _now(),
        "in_consider_set": chip is not None,
        "theme": (watch or {}).get("theme") or (chip or {}).get("theme"),
        "themes": (watch or {}).get("themes")
        or ([watch.get("theme")] if watch and watch.get("theme") else []),
        "stance": stance,
        "chip": chip,
        "watchlist": watch_public,
        "deep_dive": dive_public,
        "policy_excerpts": _policy_excerpts(pol, sym),
        "positions": _positions_row(sym),
        "related": deduped,
        "journal": _journal_hits(sym),
        "larger_picture": {
            "book": "agentic_only",
            "sleeve_budget": "~40% BTC/digital-credit · ~60% stocks/growth",
            "axis": "new-money consider-share",
            "not_an_order": True,
            "auto_buy": False,
            "private_watchlist_on_axis": False,
        },
        "links": {
            "bias_spectrum": "bias-spectrum.html",
            "watchlist": "watchlist.html",
            "pretty": f"/financial-command/position/{sym}",
        },
    }


def get_position_dossier(symbol: str) -> Dict[str, Any]:
    return build_position_dossier(symbol)
