# Horizon source catalog

**Owner:** Meridian · **Issue:** #20 · **Updated:** 2026-08-05  
**Principle:** Prefer primary/official feeds; tag provenance; keep confidence explicit; no social as default.

## Live RSS adapter (`sources/rss.py` → `DEFAULT_FEEDS`)

**Count:** **12** curated feeds (baseline before #20: Fed + EIA only).  
HTTP probe 2026-08-05 with User-Agent `HorizonMacroBot/0.1`.  
`RssSource` fails open per feed — 403/timeout does not break the pipeline.

### Active (in DEFAULT_FEEDS)

| Name | Domain default | Probe | Role |
|------|----------------|-------|------|
| Federal Reserve Press | macroeconomics | **200** | Monetary policy |
| ECB Press | macroeconomics | **200** | EZ rates / liquidity |
| Bank of England News | macroeconomics | **200** | UK rates |
| BIS Publications | macroeconomics | **200** (`rss_all_categories`) | Global banking / liquidity research |
| BLS News Releases | macroeconomics | **200** | Labor, CPI |
| BEA News | macroeconomics | **200** | GDP / PCE |
| EIA Today in Energy | energy | **200** | Energy narrative |
| IMF News | macroeconomics | **403** best-effort | EM / sovereign |
| US State Department Press | geopolitics | **200** | Diplomacy / sanctions |
| US DoD News | military | **200** | Defense posture |
| White House News | geopolitics | **200** | Fiscal / executive policy |
| SEC Press Releases | capital_flows | **200** | Markets / crypto regulation |

### Dropped after probe (do not re-add without re-verify)

| Name | URL issue | Probe |
|------|-----------|-------|
| BIS Press Releases (old URL) | `rss_all_pressrel.rss` | **404** → replaced by all_categories |
| US Treasury Press | `treasury-press-releases.xml` | timeout / fail |
| EIA What's New | `press_release.xml` | **404** |
| World Bank News | `news/all.rss` | **404** |
| USTR Press | ustr.gov rss.xml | **404** |

## Deferred (next density pass)

| Source class | Examples | Why deferred |
|--------------|----------|--------------|
| BoJ / PBoC / RBA / SNB | Official press | Need stable RSS/Atom endpoints |
| IEA | iea.org feeds | 403 without cookies in probe |
| US Treasury | alternate feed or HTML scraper | prior URL dead |
| USTR / trade | new endpoint | prior RSS 404 |
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
4. **Only ship feeds that probe 200** (or documented best-effort like IMF 403).  
5. Re-probe on Pi before treating live density as production SoT.
