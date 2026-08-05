"""Public RSS source adapter with graceful offline failure."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from research.horizon import REQUIRED_DOMAINS

# High-credibility official / primary feeds (titles only; domain inferred heuristically).
# Prefer government and multilaterals over wire narrative. Social excluded by design.
# HTTP probe 2026-08-05 (HorizonMacroBot/0.1): keep 200s; drop 404/timeout; IMF 403 best-effort.
# See docs/SOURCE_CATALOG.md for deferred / dead URLs.
DEFAULT_FEEDS: list[dict[str, str]] = [
    # --- Monetary / rates ---
    {
        "name": "Federal Reserve Press",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "domain": "macroeconomics",
        "tags": "rates,fed,monetary-policy",
    },
    {
        "name": "ECB Press",
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "domain": "macroeconomics",
        "tags": "rates,ecb,monetary-policy,europe",
    },
    {
        "name": "Bank of England News",
        "url": "https://www.bankofengland.co.uk/rss/news",
        "domain": "macroeconomics",
        "tags": "rates,boe,monetary-policy,uk",
    },
    {
        "name": "BIS Publications",
        "url": "https://www.bis.org/doclist/rss_all_categories.rss",
        "domain": "macroeconomics",
        "tags": "bis,rates,liquidity,banking",
    },
    # --- Growth / labor / prices (US official) ---
    {
        "name": "BLS News Releases",
        "url": "https://www.bls.gov/feed/bls_latest.rss",
        "domain": "macroeconomics",
        "tags": "labor,cpi,payroll,inflation,bls",
    },
    {
        "name": "BEA News",
        "url": "https://apps.bea.gov/rss/rss.xml",
        "domain": "macroeconomics",
        "tags": "gdp,bea,growth,pce",
    },
    # --- Energy ---
    {
        "name": "EIA Today in Energy",
        "url": "https://www.eia.gov/rss/todayinenergy.xml",
        "domain": "energy",
        "tags": "energy,oil,gas,power",
    },
    # --- Multilateral (best-effort; may 403 depending on edge) ---
    {
        "name": "IMF News",
        "url": "https://www.imf.org/en/News/RSS",
        "domain": "macroeconomics",
        "tags": "imf,sovereign,emerging-markets,fiscal",
    },
    # --- Geopolitics / security / markets (official US) ---
    {
        "name": "US State Department Press",
        "url": "https://www.state.gov/rss-feed/press-releases/feed/",
        "domain": "geopolitics",
        "tags": "diplomacy,sanctions,geopolitics,us",
    },
    {
        "name": "US DoD News",
        "url": "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=1&Site=945&max=10",
        "domain": "military",
        "tags": "defense,military,indo-pacific",
    },
    {
        "name": "White House News",
        "url": "https://www.whitehouse.gov/news/feed/",
        "domain": "geopolitics",
        "tags": "fiscal,policy,white-house,us",
    },
    {
        "name": "SEC Press Releases",
        "url": "https://www.sec.gov/news/pressreleases.rss",
        "domain": "capital_flows",
        "tags": "regulation,sec,markets,crypto",
    },
]

DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "geopolitics": ["sanction", "diplomat", "alliance", "summit", "election", "border"],
    "macroeconomics": ["inflation", "gdp", "rate", "fed", "ecb", "recession", "cpi", "payroll"],
    "energy": ["oil", "gas", "opec", "nuclear", "lng", "refinery", "power grid", "uranium"],
    "technology_ai": ["artificial intelligence", "ai model", "chip", "semiconductor", "gpu", "openai"],
    "military": ["military", "defense", "missile", "nato", "troop", "warship"],
    "demographics": ["population", "migration", "birth rate", "aging", "labor force"],
    "supply_chains": ["supply chain", "port", "shipping", "logistics", "chip fab", "bottleneck"],
    "capital_flows": ["capital flow", "fdi", "treasury auction", "etf inflow", "carry trade"],
    "climate_resources": ["climate", "drought", "flood", "critical mineral", "copper", "lithium"],
    "narrative_information": ["disinformation", "propaganda", "media narrative", "censorship"],
}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:48] or "item"


def _infer_domain(title: str, default: str) -> str:
    t = title.lower()
    scores: dict[str, int] = {}
    for domain, kws in DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for k in kws if k in t)
    best = max(scores, key=lambda d: scores[d])
    if scores[best] > 0 and best in REQUIRED_DOMAINS:
        return best
    return default if default in REQUIRED_DOMAINS else "macroeconomics"


def _parse_rss(xml_text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    # RSS 2.0
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        if title:
            items.append({"title": title, "link": link, "description": desc, "pubDate": pub})
    if items:
        return items
    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = entry.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        summary = (entry.findtext("a:summary", default="", namespaces=ns) or "").strip()
        pub = (entry.findtext("a:updated", default="", namespaces=ns) or "").strip()
        if title:
            items.append({"title": title, "link": link, "description": summary, "pubDate": pub})
    return items


def _normalize_date(pub: str) -> str:
    if not pub:
        return datetime.now(timezone.utc).isoformat()
    try:
        return parsedate_to_datetime(pub).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        try:
            return datetime.fromisoformat(pub.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).isoformat()


class RssSource:
    name = "rss"

    def __init__(
        self,
        feeds: Optional[list[dict[str, str]]] = None,
        *,
        timeout: float = 12.0,
        max_per_feed: int = 5,
    ) -> None:
        self.feeds = list(feeds or DEFAULT_FEEDS)
        self.timeout = timeout
        self.max_per_feed = max_per_feed
        self.last_errors: list[str] = []

    def _fetch_url(self, url: str) -> Optional[str]:
        req = Request(url, headers={"User-Agent": "HorizonMacroBot/0.1 (+local research)"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except (URLError, HTTPError, TimeoutError, OSError) as exc:
            self.last_errors.append(f"{url}: {exc}")
            return None

    def fetch(self) -> list[dict[str, Any]]:
        self.last_errors = []
        events: list[dict[str, Any]] = []
        for feed in self.feeds:
            url = feed.get("url") or ""
            if not url:
                continue
            body = self._fetch_url(url)
            if not body:
                continue
            default_domain = feed.get("domain") or "macroeconomics"
            base_tags = [t.strip() for t in (feed.get("tags") or "").split(",") if t.strip()]
            feed_name = feed.get("name") or url
            for item in _parse_rss(body)[: self.max_per_feed]:
                title = item["title"]
                domain = _infer_domain(title, default_domain)
                desc = re.sub(r"<[^>]+>", "", item.get("description") or "").strip()
                fact = desc[:400] if desc else f"Headline reported by {feed_name}: {title}"
                events.append(
                    {
                        "id": f"rss-{_slug(feed_name)}-{_slug(title)}",
                        "domain": domain,
                        "title": title[:200],
                        "facts": [fact],
                        "interpretation": (
                            "Live feed item; treat as a lead pending primary-source confirmation."
                        ),
                        "confidence": 0.45,
                        "impact": "medium",
                        "tags": base_tags + [domain],
                        "related_domains": [],
                        "sources": [{"name": feed_name, "url": item.get("link") or url}],
                        "updated_at": _normalize_date(item.get("pubDate") or ""),
                    }
                )
        return events
