# Horizon source catalog

**Owner:** Meridian · **Issue:** #20 · **Updated:** 2026-08-05  
**Principle:** Prefer primary/official feeds; tag provenance; keep confidence explicit; no social as default.

## Live RSS adapter (`sources/rss.py` → `DEFAULT_FEEDS`)

**Count:** 16 feeds (baseline before #20: Fed + EIA only).

HTTP status probed 2026-08-05 (User-Agent `HorizonMacroBot/0.1`) where noted.  
`RssSource` fails open per feed — 403/timeout does not break the pipeline.

| Name | Domain default | Probe (where known) | Role |
|------|----------------|---------------------|------|
| Federal Reserve Press | macroeconomics | **200** | Monetary policy |
| ECB Press | macroeconomics | **200** | EZ rates / liquidity |
| Bank of England News | macroeconomics | **200** | UK rates |
| BIS Press Releases | macroeconomics | probe | Global banking / liquidity |
| BLS News Releases | macroeconomics | **200** | Labor, CPI |
| BEA News | macroeconomics | probe | GDP / PCE |
| US Treasury Press | capital_flows | probe | Fiscal / debt |
| EIA Today in Energy | energy | **200** | Energy narrative |
| EIA What's New | energy | probe | Energy releases |
| IMF News | macroeconomics | **403** (best-effort) | EM / sovereign |
| World Bank News | macroeconomics | probe | Development / EM |
| US State Department Press | geopolitics | **200** | Diplomacy / sanctions |
| USTR Press | geopolitics | probe | Trade / tariffs |
| US DoD News | military | **200** | Defense posture |
| White House News | geopolitics | **200** | Fiscal / executive policy |
| SEC Press Releases | capital_flows | **200** | Markets / crypto regulation |

## Deferred (next density pass)

| Source class | Examples | Why deferred |
|--------------|----------|--------------|
| BoJ / PBoC / RBA / SNB | Official press | Need stable RSS/Atom endpoints |
| IEA | iea.org feeds | 403 without cookies in probe |
| FRED / market levels | yields, DXY, VIX, copper, oil | Numeric adapters, not RSS titles |
| On-chain / stablecoin flows | public APIs | Crypto macro lens — separate adapter |
| Think tanks | CFR, Bruegel, CSIS | Secondary; lower default confidence |

## Confidence defaults (RSS items)

Live RSS events enter at **confidence 0.45** with interpretation:  
“Live feed item; treat as a lead pending primary-source confirmation.”

Fixtures remain structural priors; regime layer caps confidence when fixture-share is high.

## Curation rules

1. Official > wire > blog > social.  
2. One domain default per feed; title keywords may re-map domain.  
3. Never invent prints — missing data is a coverage note.  
4. Expand feeds only when HTTP-reachable or documented as best-effort.  
5. P1: Pi prod smoke; drop persistently dead feeds.
