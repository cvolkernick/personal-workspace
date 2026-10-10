"""Keep the owner's book off the Horizon host.

Fail closed: when a line might be the owner's money, it is redacted. Markets,
Business and Books headlines stay, including tickers and ordinary money words
(tax, credit, interest, cash, fund, loan) and news amounts such as
"AAPL tops $180". Plain to-dos stay ("Pay rent", "Review rent ledger").

Withheld everywhere:

  * balances, even with no keyword ("Checking at Northwind is $12,400")
  * holdings, allocation, and orders ("we hold", "BTC 42%", "Buy 0.1 BTC")
  * sends and recipients, with or without a "$", any name casing
    ("900 USDC to Alex Sample", "Send $900 to alex", "Zelle Jordan Example")
  * debt, loan, and P&L amounts, not only next to Turo or Panamerica
  * wallet addresses: EVM, Bitcoin bech32, legacy and P2SH, Solana base58
  * links to FCC or the prism host (prism-gateway, prism:<port>, *.ts.net,
    localhost, 127.0.0.1, 100.x tailnet addresses, ports 8790 and 8795)

On the owner's own lines (to-dos, Mac tasks, fleet, day-ahead, shipped, the
front page, theme, quote, and every section that is not Markets, Business or
Books) any currency amount is removed. The line is dropped when what remains
is not a real to-do.

The public edition is an allowlist of the fields the page renders. Unknown
keys (an ``fcc_snapshot``, an extra weather field) never reach HTML or
``/daily-brief/api/latest``. The stored edition is not modified.
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

# Newspaper desks. Only their item text may keep a news-style amount.
_NEWS_SECTION_RE = re.compile(r"^(markets?|business|books)$", re.I)

# Fields the page actually renders. Anything else is dropped.
_EDITION_KEYS = (
    "date", "edition", "vol", "no", "published_at",
    "theme", "quote", "weather", "front_page", "sections", "todos", "mac_tasks",
)
_FRONT_KEYS = ("headline", "subhead", "body", "image")
_SECTION_KEYS = ("id", "title", "items")
_ITEM_KEYS = ("text", "link")
_TODO_KEYS = ("title", "status", "due", "owner", "priority", "notion_url")
_MAC_KEYS = ("source_url", "fetched_at", "page_found", "items")
_MAC_ITEM_KEYS = ("text", "link", "checked")
_QUOTE_KEYS = ("text", "author")
_WEATHER_KEYS = ("summary", "high", "low", "icon", "rain", "wind", "note")
_LINK_KEYS = ("link", "notion_url", "source_url", "image")

# FCC pages and the prism / tailnet host. A hit strips the link or the line.
_PRIVATE_LINK_RE = re.compile(
    r"financial-command"
    r"|/fcc\b"
    r"|/treasury\b"
    r"|/api/treasury"
    r"|prism-gateway"
    r"|\bprism\s*:"
    r"|\.ts\.net\b"
    r"|localhost"
    r"|127\.0\.0\.1"
    r"|\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"
    r"|:(?:8790|8795)\b",
    re.I,
)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

# Base58 without 0, O, I, l. A real address also carries a non-hex letter so a
# Notion id (pure hex) is not treated as a wallet.
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_CLASS = "[" + _B58 + "]"
_NONHEX_B58_RE = re.compile(r"[g-zGHJKLMNP-Z]")
_WALLET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b0x[a-fA-F0-9]{40}\b", re.I),
    re.compile(r"\bbc1[qpzry9x8gf2tvdw0s3jn54khce6mua7l]{11,87}\b", re.I),
    re.compile(rf"\b[13]{_B58_CLASS}{{25,34}}\b"),
    re.compile(rf"(?<!{_B58_CLASS}){_B58_CLASS}{{32,44}}(?!{_B58_CLASS})"),
)

_CURRENCY_RE = re.compile(
    r"[+\-]?\s*[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbBnN])?"
    r"|\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\s*(?:usd|usdc|usdt|btc|eth|sol|sats?)\b",
    re.I,
)
# "$25 to Alex Sample", "900 USDC to alex", any casing.
_RECIPIENT_RE = re.compile(
    r"[+\-]?\s*[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbBnN])?\s+to\s+[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})?"
    r"|\b\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\s*(?:usd|usdc|usdt|btc|eth|sol|sats?)\s+to\s+"
    r"[A-Za-z]{2,}(?:\s+[A-Za-z]{2,})?",
    re.I,
)

_DANGLING_RE = re.compile(
    r"(?:\s*[,:;(\[{\-–—/]+\s*|\s+(?:before|after|for|to|of|about|with|onto|into|"
    r"from|the|a|an|and|or|then))+\s*$",
    re.I,
)

# Personal book, on a news line or an owner line. "Pay rent" does not match:
# rent is only a send when an amount or a recipient is also present.
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
        r"|\b(?:our|my)\s+positions?\b"
        r"|\bchecking\s+at\b"
        r"|\bsavings\s*:"
        r"|\bbrokerage\s+cash\b"
        r"|\bNW\s+now\b",
        re.I,
    ),
    # Allocation target ("target 8%"), not a macro "inflation target 2%"
    # or a street "price target 20%".
    re.compile(r"\b(?<!inflation )(?<!price )target\s+\d+(?:\.\d+)?\s*%", re.I),
    re.compile(
        r"\b(?:btc|bitcoin|eth|ethereum|sol|solana|usdc|usdt)\s+\d+(?:\.\d+)?\s*%"
        r"|\b\d+(?:\.\d+)?\s*%\s*(?:btc|bitcoin|eth|ethereum|sol|solana|usdc|usdt)\b"
        r"|\ballocation\b",
        re.I,
    ),
    re.compile(
        r"\bavg(?:erage)?\s+cost\b"
        r"|\b(?:long|short)\s+\d"
        r"|\bwallet\s+holds\b"
        r"|\bholds\s+\d"
        r"|\bworth\s+[+\-]?\s*[$€£¥]",
        re.I,
    ),
    re.compile(
        r"\b(?:buy|sell|bought|sold)\s+\d"
        r"|\b(?:buy|sell)\b.{0,48}\b(?:limit|shares)\b"
        r"|\blimit\s+[+\-]?\s*[$€£¥]\s?\d",
        re.I,
    ),
    re.compile(r"\btreasury\s+(?:snapshot|balance|account|book)\b", re.I),
    re.compile(
        r"\b(?:loan|debt)\b.{0,48}[+\-]?\s*[$€£¥]\s?\d"
        r"|[+\-]?\s*[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?.{0,32}\b(?:loan|debt|cards?)\b"
        r"|\bLTV\b"
        r"|\bmorpho\b",
        re.I,
    ),
    re.compile(
        r"\bp\s*&\s*l\b"
        r"|\bpnl\b"
        r"|\b(?:unrealized|realized)\b"
        r"|\b(?:up|down)\s+[+\-]?\s*[$€£¥]\s?\d"
        r"|[+\-][$€£¥]\s?\d"
        r"|\bacross\s+the\s+book\b",
        re.I,
    ),
    re.compile(
        r"\b(?:coinbase|robinhood|plaid|ynab|brokerage)\b.{0,48}"
        r"\b(?:balance|account|send|sent|holding|position|usdc|usd|transfer)\b"
        r"|\b(?:balance|account|send|sent|holding|position|usdc|transfer)\b.{0,48}"
        r"\b(?:coinbase|robinhood|plaid|ynab|brokerage)\b"
        r"|\b(?:check|open|review|move|login|log in)\b.{0,32}\b(?:coinbase|robinhood|plaid|ynab)\b",
        re.I,
    ),
    # Asset sends. "Pay rent" has no asset and no amount, so it does not match.
    re.compile(
        r"\b(?:send|sent|sending|wire|wires|transfer|transferred)\b.{0,40}"
        r"\b(?:usdc|usdt|btc|eth|sol|usd|sats?)\b"
        r"|\b(?:usdc|usdt|btc|eth|sol)\b.{0,32}\b(?:send|sent|sending)\b"
        r"|\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmM])?\s*(?:usdc|usdt|btc|eth|sol|sats?)\b"
        r"|\b\d[\d,.]*\s*(?:btc|eth|sol|usdc|usdt|sats?)\b"
        r"|\b(?:zelle|venmo|cash\s*app)\b"
        r"|\bwire\s+(?!the\b|a\b|an\b|it\b)[A-Za-z]{2,}",
        re.I,
    ),
    re.compile(
        r"\b(?:turo|panamerica)\b.{0,60}\b(?:revenue|payouts?|profits?|p&l|pnl|earned)\b"
        r"|\b(?:revenue|payouts?|profits?|p&l|pnl)\b.{0,60}\b(?:turo|panamerica)\b",
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
_SEND_LEFTOVER_RE = re.compile(
    r"^(?:sample\b[:\s]*)?(?:book\b[:\s]*)?"
    r"(?:send|sent|sending|pay|paid|paying|wire|zelle|venmo)\b[\w\s',.]*$",
    re.I,
)
HELD_HEADLINE = "Front page held for the private edition"
HELD_NOTE = "with money details kept off this page"


def is_private_link(url: Any) -> bool:
    """True for FCC links and anything on the prism / tailnet host."""
    return isinstance(url, str) and bool(_PRIVATE_LINK_RE.search(url))


def _wallet_in(text: str) -> str | None:
    for p in _WALLET_RES:
        for m in p.finditer(text):
            token = m.group(0)
            # EVM and bech32 are specific. Base58 hits must carry a non-hex
            # letter so a 32-char Notion id is left alone.
            if token.lower().startswith(("0x", "bc1")) or _NONHEX_B58_RE.search(token):
                return token
    return None


def _trim_order(text: str) -> bool:
    """'trim SOL' / 'trim 5%' is an owner order. 'trim the hedge' is not."""
    if not _TRIM_RE.search(text):
        return False
    return bool(_TRIM_ASSET_RE.search(text) or _TRIM_TICKER_RE.search(text) or _TRIM_AMOUNT_RE.search(text))


# "Pay rent" stays. "Pay rent $25" does not. The amount has to sit with the
# phrase: a news amount elsewhere on the page is not this to-do.
_RENT_MONEY_RE = re.compile(
    r"\b(?:pay|paid|paying|send|sent|sending|wire)\b.{0,40}\brent\b.{0,48}"
    r"(?:[+\-]?\s*[$€£¥]\s?\d"
    r"|\b\d[\d,]*(?:\.\d+)?\s*(?:usd|usdc|usdt|btc|eth|sol|sats?)\b"
    r"|\bto\s+[A-Za-z]{2,})",
    re.I | re.S,
)


def _rent_with_money(text: str) -> bool:
    return _RENT_MONEY_RE.search(text) is not None


def book_match(text: Any) -> str | None:
    """Return the personal-book fragment in ``text``, or None if it may be shown.

    News amounts ("AAPL tops $180") and plain to-dos ("Pay rent") return None.
    """
    if not isinstance(text, str) or not text:
        return None
    hit = _wallet_in(text)
    if hit:
        return hit
    m = _PRIVATE_LINK_RE.search(text)
    if m:
        return m.group(0)
    if _trim_order(text):
        return "trim"
    if _rent_with_money(text):
        return "rent"
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


def _is_news_section(section: dict) -> bool:
    for key in ("id", "title"):
        v = section.get(key)
        if isinstance(v, str) and _NEWS_SECTION_RE.match(v.replace("-", " ").strip()):
            return True
    return False


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


def _blank_private_urls(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        return " " if is_private_link(m.group(0)) else m.group(0)

    return _URL_RE.sub(repl, text)


def _blank_wallets(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        if token.lower().startswith(("0x", "bc1")) or _NONHEX_B58_RE.search(token):
            return " "
        return token

    out = text
    for p in _WALLET_RES:
        out = p.sub(repl, out)
    return out


def _scrub_line(text: str, *, news: bool) -> tuple[str | None, int]:
    """(kept text or None to drop the line, 1 if anything was withheld)."""
    if book_match(text):
        return None, 1
    cleaned = text
    changed = False
    # Owner lines lose every currency amount and any "$N to name" clause.
    # News lines keep amounts that are not already a book pattern.
    if not news:
        nxt = _RECIPIENT_RE.sub(" ", cleaned)
        nxt = _CURRENCY_RE.sub(" ", nxt)
        if nxt != cleaned:
            changed = True
            cleaned = nxt
    nxt = _blank_private_urls(_blank_wallets(cleaned))
    if nxt != cleaned:
        changed = True
        cleaned = nxt
    if not changed:
        return text, 0
    cleaned = _tidy(cleaned)
    if book_match(cleaned) or _SEND_LEFTOVER_RE.match(cleaned) or not _meaningful(cleaned):
        return None, 1
    return cleaned, 1


def _scrub_link(url: Any) -> tuple[Any, int]:
    if isinstance(url, str) and is_private_link(url):
        return None, 1
    return url, 0


def _scrub_item(it: Any, *, news: bool) -> tuple[Any, int]:
    if isinstance(it, str):
        return _scrub_line(it, news=news)
    if not isinstance(it, dict):
        return None, 1  # unknown shape: fail closed
    held = 0
    text = it.get("text")
    kept_text: str | None
    if isinstance(text, str):
        kept_text, n = _scrub_line(text, news=news)
        held += n
        if kept_text is None:
            return None, held or 1
    elif text is None and "text" not in it:
        kept_text = None
    else:
        return None, 1
    link, n = _scrub_link(it.get("link"))
    held += n
    if kept_text is None:
        return None, held or 1
    out: dict[str, Any] = {"text": kept_text}
    if link:
        out["link"] = link
    if "checked" in it and isinstance(it.get("checked"), bool):
        out["checked"] = it["checked"]
    return out, held


def _scrub_weather(w: Any) -> tuple[Any, int]:
    if isinstance(w, str):
        kept, n = _scrub_line(w, news=False)
        return (kept if kept else ""), n
    if not isinstance(w, dict):
        return None, 1
    out: dict[str, Any] = {}
    held = 0
    for k in _WEATHER_KEYS:
        if k not in w:
            continue
        v = w[k]
        if isinstance(v, str):
            kept, n = _scrub_line(v, news=False)
            held += n
            if kept:
                out[k] = kept
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        else:
            held += 1
    # Unknown weather keys are the owner's book until proven otherwise.
    held += sum(1 for k in w if k not in _WEATHER_KEYS)
    return out, held


def _allow_text(value: Any, *, news: bool) -> tuple[str | None, int]:
    if not isinstance(value, str):
        return None, 1 if value not in (None, "") else 0
    return _scrub_line(value, news=news)


def _project_quote(q: Any) -> tuple[Any, int]:
    if isinstance(q, str):
        kept, n = _scrub_line(q, news=False)
        return (kept, n) if kept else (None, n or 1)
    if not isinstance(q, dict):
        return None, 1
    held = sum(1 for k in q if k not in _QUOTE_KEYS)
    text, n = _allow_text(q.get("text"), news=False) if "text" in q else (None, 0)
    held += n
    if not text:
        return None, held or 1
    out: dict[str, Any] = {"text": text}
    if isinstance(q.get("author"), str):
        author, n = _scrub_line(q["author"], news=False)
        held += n
        if author:
            out["author"] = author
    return out, held


def _project_front(fp: Any) -> tuple[dict, int]:
    if not isinstance(fp, dict):
        return {"headline": HELD_HEADLINE}, 1
    held = sum(1 for k in fp if k not in _FRONT_KEYS)
    out: dict[str, Any] = {}
    headline, n = _allow_text(fp.get("headline"), news=False) if isinstance(fp.get("headline"), str) else (None, 0)
    held += n
    out["headline"] = headline if headline else HELD_HEADLINE
    if isinstance(fp.get("subhead"), str):
        sub, n = _scrub_line(fp["subhead"], news=False)
        held += n
        if sub:
            out["subhead"] = sub
    body = fp.get("body")
    if isinstance(body, str) and body.strip():
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        keep = []
        for p in paras:
            kept, n = _scrub_line(p, news=False)
            held += n
            if kept:
                keep.append(kept)
        if keep:
            out["body"] = "\n\n".join(keep)
    image, n = _scrub_link(fp.get("image"))
    held += n
    if isinstance(image, str):
        out["image"] = image
    return out, held


def _project_sections(sections: Any) -> tuple[list, int]:
    if not isinstance(sections, list):
        return [], 1
    held = 0
    out = []
    for s in sections:
        if not isinstance(s, dict):
            held += 1
            continue
        if is_fcc_section(s):
            held += max(1, len(s.get("items") or []))
            continue
        held += sum(1 for k in s if k not in _SECTION_KEYS)
        news = _is_news_section(s)
        items = s.get("items") or []
        if not isinstance(items, list):
            held += 1
            items = []
        keep = []
        for it in items:
            kept, n = _scrub_item(it, news=news)
            held += n
            if kept is not None:
                keep.append(kept)
        if items and not keep:
            continue
        title = s.get("title") if isinstance(s.get("title"), str) else ""
        sid = s.get("id") if isinstance(s.get("id"), str) else ""
        # A title that is itself a balance does not head a public section.
        if not news and isinstance(title, str) and book_match(title):
            held += 1
            continue
        out.append({"id": sid, "title": title, "items": keep})
    return out, held


def _project_todos(todos: Any) -> tuple[list, int]:
    if not isinstance(todos, list):
        return [], 1
    held = 0
    out = []
    for t in todos:
        if not isinstance(t, dict):
            held += 1
            continue
        held += sum(1 for k in t if k not in _TODO_KEYS)
        title, n = _allow_text(t.get("title"), news=False) if isinstance(t.get("title"), str) else (None, 0)
        held += n
        if not title:
            held += 0 if n else 1
            continue
        item: dict[str, Any] = {"title": title}
        for k in ("status", "due", "owner", "priority"):
            if k in t and t[k] is not None:
                if k in ("owner", "priority") and isinstance(t[k], str) and book_match(t[k]):
                    held += 1
                    continue
                item[k] = t[k]
        url, n = _scrub_link(t.get("notion_url"))
        held += n
        if isinstance(url, str):
            item["notion_url"] = url
        out.append(item)
    return out, held


def _project_mac(mt: Any) -> tuple[Any, int]:
    if mt is None:
        return None, 0
    if not isinstance(mt, dict):
        return None, 1
    held = sum(1 for k in mt if k not in _MAC_KEYS)
    out: dict[str, Any] = {}
    url, n = _scrub_link(mt.get("source_url"))
    held += n
    if isinstance(url, str):
        out["source_url"] = url
    if "fetched_at" in mt:
        out["fetched_at"] = mt.get("fetched_at")
    if "page_found" in mt and isinstance(mt.get("page_found"), bool):
        out["page_found"] = mt["page_found"]
    items = mt.get("items") or []
    keep = []
    if isinstance(items, list):
        for it in items:
            kept, n = _scrub_item(it, news=False)
            held += n
            if kept is not None:
                keep.append(kept)
    else:
        held += 1
    out["items"] = keep
    return out, held


def scrub_edition(edition: dict) -> tuple[dict, int]:
    """(allowlisted edition safe for the Horizon host, parts withheld).

    The same object is rendered to HTML and returned by ``/daily-brief/api/latest``.
    """
    if not isinstance(edition, dict):
        return {"front_page": {"headline": HELD_HEADLINE}, "sections": [], "todos": []}, 1
    src = copy.deepcopy(edition)
    held = sum(1 for k in src if k not in _EDITION_KEYS)

    sections, n = _project_sections(src.get("sections"))
    held += n
    front, n = _project_front(src.get("front_page"))
    held += n
    todos, n = _project_todos(src.get("todos"))
    held += n

    out: dict[str, Any] = {
        "front_page": front,
        "sections": sections,
        "todos": todos,
    }
    for k in ("date", "edition", "vol", "no", "published_at"):
        if k in src:
            out[k] = src[k]
    if isinstance(src.get("theme"), str):
        theme, n = _scrub_line(src["theme"], news=False)
        held += n
        if theme:
            out["theme"] = theme
    elif "theme" in src and src.get("theme") not in (None, ""):
        held += 1
    if "quote" in src and src.get("quote") not in (None, ""):
        quote, n = _project_quote(src.get("quote"))
        held += n
        if quote:
            out["quote"] = quote
    if "mac_tasks" in src and src.get("mac_tasks") is not None:
        mac, n = _project_mac(src.get("mac_tasks"))
        held += n
        if mac is not None:
            out["mac_tasks"] = mac
    if "weather" in src and src.get("weather") not in (None, ""):
        weather, n = _scrub_weather(src.get("weather"))
        held += n
        if weather:
            out["weather"] = weather
    return out, held
