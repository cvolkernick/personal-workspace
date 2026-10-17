# Sources

Checked 2026-10-17 while implementing #1250. A source whose robots rule or terms forbid automated use is not fetched. "Unchecked" means this run did not retrieve a separate terms page.

The HTTP client sends `User-Agent: personal-workspace-global-allocation/1.0`. It does not send `python-requests`, `curl`, `wget`, or `Scrapy`.

| Adapter | Terms | Robots | Auth | Limit | Fallback |
|---|---|---|---|---|---|
| Fed H.6 CSV | Unchecked. Official Data Download Program, public preformatted package. | `https://www.federalreserve.gov/robots.txt` returned HTTP 404. | None | One small CSV (`lastobs=6`) per run | Cross-check only. Broad money does not depend on it. |
| ECB M3 CSV | Unchecked. Official SDMX API. | Fetched. `User-agent: *` disallows admin, search, and user paths and allows `/service/data/`. `python-requests`, `curl`, `wget`, and `Scrapy` are `Disallow: /`. | None | `lastNObservations=2` | Cross-check only. |
| World Bank equity cap | Unchecked beyond the public API named in #1249. | `https://api.worldbank.org/robots.txt` returned HTTP 404. | None | `mrnev=2` | Cross-check only. Equities use the SIFMA anchor. |
| CoinGecko global | Unchecked. `https://www.coingecko.com/en/terms` returned HTTP 403. | Fetched `https://api.coingecko.com/robots.txt`: `User-agent: *` disallows `/api/v1`, `/api/v2`, and `/api/v3`. | n/a | n/a | **Demoted.** No request is made. Other crypto is the manual anchor. |
| Coinbase spot (BTC, PAXG) | Unchecked. Public price URL, no separate terms page fetched. | `https://api.coinbase.com/robots.txt` returned HTTP 404. | None | Unspecified | BTC carried forward. PAXG miss sets `price_check unchecked` and the primary gold price is kept. |
| mempool.space tip height | Unchecked. | `https://mempool.space/robots.txt` returned the site HTML shell, not a robots file. No disallow found. | None | One integer per run | BTC carried forward. |
| Frankfurter FX | Unchecked. | Fetched. `User-agent: *` / `Allow: /`. | None | One request per run | `fx_basis.unavailable` set. Prior FX carried when a prior snapshot exists. |
| Treasury real yield CSV | Unchecked beyond the official CSV. | Fetched. `User-agent: *` / `Allow: /`. | None | One year file per run | `real_yield_10y` null and a caveat. Not a class value. |
| gold-api XAU | **Unchecked.** `https://api.gold-api.com/llms.txt` returned HTTP 404. Not demoted: robots allow, and #1249 names this feed. | Fetched `https://gold-api.com/robots.txt`: `User-agent: *` / `Allow: /`. | None | One request per run | Gold carried forward. |
| M2 Index broad-money CSV | CC BY 4.0, stated in robots.txt. Attribute "M2 Index, m2index.com" and quote the observation date. | Fetched. `Allow: /`. The file asks agents to use the CSV instead of crawling country pages. | None | One CSV per monthly run | Broad money carried forward. |
| Alpha Vantage VT | Unchecked. The product is a keyed API. `https://www.alphavantage.co/robots.txt` returned HTTP 404. | No robots file. | `ALPHAVANTAGE_API_KEY` in the environment. Missing key returns unavailable and does not send a request. | Vendor documents 25 requests/day on the free tier. Not re-verified here. | Listed equities carried forward. |

Twelve Data is the #1249 equity fallback and is **not an adapter**. `https://api.twelvedata.com/robots.txt` is `User-agent: *` / `Disallow: /`. #1251 does not add it. A weekly run with no Alpha Vantage key, or a failed Alpha Vantage response, carries listed equities forward with `price_stale` true.

Manual anchors (no automated fetch): Savills real estate, SIFMA debt and the equity level, PitchBook private markets (the 2026-10-17 page timed out; confidence `low`), Deloitte art projection, World Gold Council tonnes, and the CoinMarketCap snapshot 0 figures for bitcoin and other crypto. PitchBook, Savills, SIFMA, Deloitte, and WGC pages are not scraped.

Keys are read from the environment only. They are not written into snapshots, fixtures, or logs. Adapter errors drop the query string so a key in a URL is not printed.
