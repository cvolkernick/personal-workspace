"""Keep FCC money off the Horizon host.

Hard rule from the Horizon custodian: no FCC marks, balances, or capital
so-whats may render on the Horizon host. Editions are written by the publisher
for the private paper, so they can carry money lines (balances, sends, fund
notes). This module returns a copy of an edition with those parts withheld.
The full edition stays untouched in the store.

What is withheld:
  * whole sections whose id or title is money-flavoured (business, money,
    treasury, fund, markets, portfolio, ...);
  * any line (section item, todo, Mac task, quote, front-page paragraph,
    theme) whose text or link carries an amount, a ticker or account word,
    or points at FCC;
  * a front-page headline that matches is replaced by a neutral one.

The match is deliberately broad. A false positive only hides a line from this
page; a false negative would break the rule.
"""

from __future__ import annotations

import copy
import re
from typing import Any

# Section ids/titles that are FCC territory as a whole.
FCC_SECTION_RE = re.compile(
    r"\b(business|money|financ\w*|treasury|funds?|fund-\w+|markets?|portfolio|capital|books?|cash|"
    r"crypto|accounts?|balances?|budget|bills?|payments?|income|spend\w*|invest\w*|trading|wallet)\b",
    re.I,
)

# Amounts, tickers, account/book vocabulary, FCC itself.
MONEY_RE = re.compile(
    r"[$\u20ac\u00a3\u00a5]\s?\d"
    r"|\b\d[\d,.]*\s?(?:k|m|bn)?\s?(?:usd|usdc|usdt|btc|eth|sol|sats?)\b"
    r"|\b(?:usd|usdc|usdt|btc|bitcoin|eth|ethereum|stablecoin|nav|p&l|pnl|mark-to-market|"
    r"balance|balances|treasury|runway|burn rate|portfolio|margin|buying power|net worth|"
    r"cash|cash ?flow|capital|coinbase|robinhood|plaid|ynab|brokerage|fund|funds|fund manager|"
    r"live trading|rebalance\w*|holdings?|positions?|dividends?|apy|apr|loan|loans|debt|idr|"
    r"payoff|buyout|invoice|refund|paycheck|salary|tax|taxes|credit|interest|fcc|"
    r"financial command|so-what)\b",
    re.I,
)

# Links that lead into FCC or its book pages.
FCC_LINK_RE = re.compile(r"financial-command|/fcc\b|/treasury\b|/api/treasury", re.I)

HELD_HEADLINE = "Front page held for the private edition"
HELD_NOTE = "with money details kept off this page"


def is_fcc_text(text: Any) -> bool:
    return isinstance(text, str) and bool(MONEY_RE.search(text))


def is_fcc_section(section: Any) -> bool:
    if not isinstance(section, dict):
        return False
    for key in ("id", "title"):
        v = section.get(key)
        if isinstance(v, str) and FCC_SECTION_RE.search(v.replace("-", " ")):
            return True
    return False


def _item_is_fcc(it: Any) -> bool:
    if isinstance(it, str):
        return is_fcc_text(it)
    if isinstance(it, dict):
        link = it.get("link")
        if isinstance(link, str) and FCC_LINK_RE.search(link):
            return True
        return is_fcc_text(it.get("text"))
    return False


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
        keep = [i for i in items if not _item_is_fcc(i)]
        held += len(items) - len(keep)
        if items and not keep:
            continue  # every line was money: drop the section, do not show an empty shell
        s = dict(s)
        s["items"] = keep
        secs.append(s)
    ed["sections"] = secs

    fp = dict(ed.get("front_page") or {})
    if is_fcc_text(fp.get("headline")):
        fp["headline"] = HELD_HEADLINE
        held += 1
    if is_fcc_text(fp.get("subhead")):
        fp.pop("subhead", None)
        held += 1
    body = fp.get("body")
    if isinstance(body, str) and body.strip():
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        keep_p = [p for p in paras if not is_fcc_text(p)]
        held += len(paras) - len(keep_p)
        fp["body"] = "\n\n".join(keep_p)
    if isinstance(fp.get("image"), str) and FCC_LINK_RE.search(fp["image"]):
        fp.pop("image", None)
        held += 1
    ed["front_page"] = fp

    if is_fcc_text(ed.get("theme")):
        ed.pop("theme", None)
        held += 1

    q = ed.get("quote")
    qtext = q.get("text") if isinstance(q, dict) else q
    if is_fcc_text(qtext):
        ed.pop("quote", None)
        held += 1

    todos = ed.get("todos") or []
    keep_t = [t for t in todos if not (isinstance(t, dict) and is_fcc_text(t.get("title")))]
    held += len(todos) - len(keep_t)
    ed["todos"] = keep_t

    mt = ed.get("mac_tasks")
    if isinstance(mt, dict) and isinstance(mt.get("items"), list):
        mt = dict(mt)
        items = mt["items"]
        keep_m = [i for i in items if not _item_is_fcc(i)]
        held += len(items) - len(keep_m)
        mt["items"] = keep_m
        ed["mac_tasks"] = mt

    # Never needed on this page; drop rather than risk a money string in it.
    ed.pop("weather", None)
    return ed, held
