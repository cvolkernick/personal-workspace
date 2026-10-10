"""Keep the owner's book off the Horizon host.

The Daily Brief is a newspaper. Markets, Business and Books headlines stay,
including tickers and ordinary money words (tax, credit, interest, cash, fund,
loan). Plain-language to-dos, Mac tasks and the local weather box stay too.

What is withheld is the owner's own book, not news that mentions money:

  * balances, account names used as accounts, holdings, positions, NAV, runway
  * a line that ties a ticker or asset to the owner's book: "we hold", a
    target %, buy/sell/trim, may_change, or a capital so-what
  * personal money ops: rent or USDC sends and their recipients, debt / loan /
    LTV figures (Morpho and the like), card balances, wallet addresses
  * business P&L figures (Turo / Panamerica revenue or payouts)
  * links into FCC

A to-do worded without an amount or account is kept. "$25 to Alex Sample" is
dropped. Where the amount or recipient is only part of the line, that span is
removed and the rest of the line stays.

Whole sections are withheld only when the section itself is the private book
(YNAB, treasury, portfolio, balances, wallet, and the like). A Markets,
Business or Books section is filtered line by line.

The stored edition is not modified. This module returns a copy.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Section ids/titles that are the private book as a whole, not a newspaper desk.
BOOK_SECTION_RE = re.compile(
    r"\b(ynab|treasury|portfolio|nav|runway|wallets?|balances?|budget|"
    r"accounts?|bills?|payments?|holdings?|positions?)\b",
    re.I,
)

# Links that lead into FCC or its book pages.
FCC_LINK_RE = re.compile(r"financial-command|/fcc\b|/treasury\b|/api/treasury", re.I)

# Ethereum and Bitcoin addresses. A hit drops the line.
_WALLET_RE = re.compile(
    r"\b0x[a-fA-F0-9]{40}\b"
    r"|\bbc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,87}\b",
    re.I,
)

# "$25 to Alex Sample" — amount plus a named recipient. Removed from the line.
_AMOUNT_TO_NAME_RE = re.compile(
    r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\s+to\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)?"
)

_DANGLING_RE = re.compile(
    r"(?:\s*[,:;(\[{\-–—/]+\s*|\s+(?:before|after|for|to|of|about|with|onto|into|"
    r"from|the|a|an|and|or|then))+\s*$",
    re.I,
)

# Each pattern is specific to the owner's book. A news headline that merely
# says "tax", "credit", "interest", "cash", "fund", "loan", or a ticker does
# not match.
_BOOK_RES: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:balances?|runway|burn rate|net worth|buying power)\b"
        r"|\bNAV\b"
        r"|\bmark-to-market\b"
        r"|\bfund manager\b"
        r"|\blive trading\b"
        r"|\brebalances?\b"
        r"|\bso-whats?\b"
        r"|\bmay_change\b"
        r"|\bsleeves?\b"
        r"|\b(?:we|i|our|my)\s+hold\b"
        r"|\bholdings\b"
        r"|\b(?:our|my)\s+positions?\b",
        re.I,
    ),
    # Allocation target ("target 8%"), not a macro "inflation target 2%" or a
    # street "price target 20%".
    re.compile(r"\b(?<!inflation )(?<!price )target\s+\d+(?:\.\d+)?\s*%", re.I),
    re.compile(r"\btreasury\s+(?:snapshot|balance|account|book)\b", re.I),
    re.compile(r"\b(?:card|loan|debt)\s+balances?\b", re.I),
    re.compile(r"\bLTV\b", re.I),
    re.compile(
        r"\bmorpho\b.{0,40}(?:\d|ltv|loan|debt)"
        r"|(?:\d|ltv|loan|debt).{0,40}\bmorpho\b",
        re.I,
    ),
    # Account names only when the line uses them as the owner's account.
    re.compile(
        r"\b(?:coinbase|robinhood|plaid|ynab|brokerage)\b.{0,48}"
        r"\b(?:balance|account|send|sent|holding|position|usdc|usd|transfer)\b"
        r"|\b(?:balance|account|send|sent|holding|position|usdc|transfer)\b.{0,48}"
        r"\b(?:coinbase|robinhood|plaid|ynab|brokerage)\b"
        r"|\b(?:check|open|review|move|login|log in)\b.{0,32}"
        r"\b(?:coinbase|robinhood|plaid|ynab)\b",
        re.I,
    ),
    # Personal sends: rent, stablecoins, or a coin amount on the move.
    re.compile(
        r"\b(?:send|sent|sending|wire|wires|transfer|transferred|pay|paid|paying)\b"
        r".{0,32}\b(?:usdc|usdt|btc|eth|sol|rent)\b"
        r"|\b(?:usdc|usdt|btc|eth|sol)\b.{0,32}\b(?:send|sent|sending)\b"
        r"|\brent\s+(?:send|sent|payment|wire|payout)\b"
        r"|\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\s*(?:usdc|usdt|btc|eth|sol|sats?)\b"
        r"|\b\d[\d,.]*\s*(?:usdc|usdt|btc|eth|sol|sats?)\b.{0,32}"
        r"\b(?:send|sent|sending|move|transfer|wire|pending)\b",
        re.I,
    ),
    # Business P&L figures, not a Turo pickup on the schedule.
    re.compile(
        r"\b(?:turo|panamerica)\b.{0,60}\b(?:revenue|payouts?|profits?|p&l|pnl|earned)\b"
        r"|\b(?:revenue|payouts?|profits?|p&l|pnl)\b.{0,60}\b(?:turo|panamerica)\b"
        r"|\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?.{0,40}\b(?:turo|panamerica)\b"
        r"|\b(?:turo|panamerica)\b.{0,40}\$\s?\d",
        re.I,
    ),
    re.compile(
        r"\b(?:buyout|buy-out)\b.{0,40}\$\s?\d"
        r"|\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?.{0,40}\b(?:buyout|buy-out)\b",
        re.I,
    ),
)

_TRIM_RE = re.compile(r"\btrim\b", re.I)
_TRIM_ASSET_RE = re.compile(
    r"\b(?:btc|bitcoin|eth|ethereum|sol|solana|usdc|usdt|aapl|nvda|tsla|msft|amzn)\b",
    re.I,
)
_TRIM_TICKER_RE = re.compile(r"\b[A-Z]{2,5}\b")
_TRIM_AMOUNT_RE = re.compile(r"\d+\s*%|\$\s?\d")

HELD_HEADLINE = "Front page held for the private edition"
HELD_NOTE = "with money details kept off this page"


def book_match(text: Any) -> str | None:
    """Return the personal-book fragment in ``text``, or None if it may be shown."""
    if not isinstance(text, str) or not text:
        return None
    m = _WALLET_RE.search(text)
    if m:
        return m.group(0)
    m = FCC_LINK_RE.search(text)
    if m:
        return m.group(0)
    if _trim_order(text):
        return "trim"
    for p in _BOOK_RES:
        m = p.search(text)
        if m:
            return m.group(0)
    return None


def is_book_text(text: Any) -> bool:
    return book_match(text) is not None


def is_fcc_text(text: Any) -> bool:
    """Backward-compatible name for :func:`is_book_text`."""
    return is_book_text(text)


def is_fcc_section(section: Any) -> bool:
    """True when the section itself is the private book (YNAB, portfolio, ...).

    Markets, Business and Books are newspaper desks and return False.
    """
    if not isinstance(section, dict):
        return False
    for key in ("id", "title"):
        v = section.get(key)
        if isinstance(v, str) and BOOK_SECTION_RE.search(v.replace("-", " ")):
            return True
    return False


def _trim_order(text: str) -> bool:
    """'trim SOL' / 'trim 5%' is an owner order. 'trim the hedge' is not."""
    if not _TRIM_RE.search(text):
        return False
    return bool(_TRIM_ASSET_RE.search(text) or _TRIM_TICKER_RE.search(text) or _TRIM_AMOUNT_RE.search(text))


def _meaningful(text: str) -> bool:
    return len(re.findall(r"[A-Za-z]{2,}", text)) >= 2


def _tidy(text: str) -> str:
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([,.;:])", r"\1", text)
    prev = None
    while text != prev:
        prev = text
        text = _DANGLING_RE.sub("", text).strip()
    return text.strip(" \t-–—:;,")


def _scrub_text(text: str) -> tuple[str | None, int]:
    """(kept text or None to drop the line, 1 if anything was withheld)."""
    if book_match(text):
        return None, 1
    if _AMOUNT_TO_NAME_RE.search(text):
        cleaned = _tidy(_AMOUNT_TO_NAME_RE.sub(" ", text))
        if _meaningful(cleaned):
            return cleaned, 1
        return None, 1
    return text, 0


def _scrub_item(it: Any) -> tuple[Any, int]:
    if isinstance(it, str):
        kept, n = _scrub_text(it)
        return kept, n
    if isinstance(it, dict):
        link = it.get("link")
        if isinstance(link, str) and FCC_LINK_RE.search(link):
            return None, 1
        if "text" not in it:
            return it, 0
        text = it.get("text")
        if not isinstance(text, str):
            return it, 0
        kept, n = _scrub_text(text)
        if kept is None:
            return None, 1
        if n:
            out = dict(it)
            out["text"] = kept
            return out, n
        return it, 0
    return it, 0


def _scrub_weather(w: Any) -> tuple[Any, int]:
    if isinstance(w, str):
        kept, n = _scrub_text(w)
        return (kept if kept else ""), n
    if not isinstance(w, dict):
        return w, 0
    out: dict[str, Any] = {}
    held = 0
    for k, v in w.items():
        if isinstance(v, str):
            kept, n = _scrub_text(v)
            held += n
            if kept:
                out[k] = kept
        else:
            out[k] = v
    return out, held


def scrub_edition(edition: dict) -> tuple[dict, int]:
    """(copy of edition safe for the Horizon host, number of parts withheld)."""
    ed = copy.deepcopy(edition)
    held = 0

    secs = []
    for s in ed.get("sections") or []:
        if not isinstance(s, dict):
            continue
        if is_fcc_section(s):
            held += max(1, len(s.get("items") or []))
            continue
        items = s.get("items") or []
        keep = []
        for it in items:
            kept, n = _scrub_item(it)
            held += n
            if kept is not None:
                keep.append(kept)
        if items and not keep:
            continue  # every line was the owner's book: drop the empty shell
        s = dict(s)
        s["items"] = keep
        secs.append(s)
    ed["sections"] = secs

    fp = dict(ed.get("front_page") or {})
    headline, n = _scrub_text(fp["headline"]) if isinstance(fp.get("headline"), str) else (fp.get("headline"), 0)
    held += n
    if n and headline is None:
        fp["headline"] = HELD_HEADLINE
    elif isinstance(headline, str):
        fp["headline"] = headline
    if isinstance(fp.get("subhead"), str):
        sub, n = _scrub_text(fp["subhead"])
        held += n
        if sub:
            fp["subhead"] = sub
        else:
            fp.pop("subhead", None)
    body = fp.get("body")
    if isinstance(body, str) and body.strip():
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        keep_p = []
        for p in paras:
            kept, n = _scrub_text(p)
            held += n
            if kept:
                keep_p.append(kept)
        fp["body"] = "\n\n".join(keep_p)
    if isinstance(fp.get("image"), str) and FCC_LINK_RE.search(fp["image"]):
        fp.pop("image", None)
        held += 1
    ed["front_page"] = fp

    if isinstance(ed.get("theme"), str):
        theme, n = _scrub_text(ed["theme"])
        held += n
        if theme:
            ed["theme"] = theme
        else:
            ed.pop("theme", None)

    q = ed.get("quote")
    if isinstance(q, str):
        kept, n = _scrub_text(q)
        held += n
        if kept:
            ed["quote"] = kept
        else:
            ed.pop("quote", None)
    elif isinstance(q, dict) and isinstance(q.get("text"), str):
        kept, n = _scrub_text(q["text"])
        held += n
        if kept:
            q = dict(q)
            q["text"] = kept
            ed["quote"] = q
        else:
            ed.pop("quote", None)

    todos = ed.get("todos") or []
    keep_t = []
    for t in todos:
        if isinstance(t, dict) and isinstance(t.get("title"), str):
            kept, n = _scrub_text(t["title"])
            held += n
            if kept:
                if n:
                    t = dict(t)
                    t["title"] = kept
                keep_t.append(t)
        else:
            keep_t.append(t)
    ed["todos"] = keep_t

    mt = ed.get("mac_tasks")
    if isinstance(mt, dict) and isinstance(mt.get("items"), list):
        mt = dict(mt)
        keep_m = []
        for it in mt["items"]:
            kept, n = _scrub_item(it)
            held += n
            if kept is not None:
                keep_m.append(kept)
        mt["items"] = keep_m
        ed["mac_tasks"] = mt

    if "weather" in ed:
        ed["weather"], n = _scrub_weather(ed.get("weather"))
        held += n
    return ed, held
