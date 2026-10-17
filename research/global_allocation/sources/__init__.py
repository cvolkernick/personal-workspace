"""Source adapters. One module per feed named in #1249 / #1250."""

from research.global_allocation.sources.base import SourceRecord
from research.global_allocation.sources.coinbase import fetch_btc, fetch_paxg, parse_spot
from research.global_allocation.sources.coingecko import fetch as fetch_coingecko
from research.global_allocation.sources.ecb_m3 import fetch as fetch_ecb
from research.global_allocation.sources.ecb_m3 import parse as parse_ecb
from research.global_allocation.sources.equity_proxy import fetch as fetch_equity
from research.global_allocation.sources.equity_proxy import parse as parse_equity
from research.global_allocation.sources.fed_h6 import fetch as fetch_fed
from research.global_allocation.sources.fed_h6 import parse as parse_fed
from research.global_allocation.sources.frankfurter import fetch as fetch_fx
from research.global_allocation.sources.frankfurter import parse as parse_fx
from research.global_allocation.sources.gold_spot import fetch as fetch_gold
from research.global_allocation.sources.gold_spot import parse as parse_gold
from research.global_allocation.sources.m2index import fetch as fetch_m2
from research.global_allocation.sources.m2index import parse as parse_m2
from research.global_allocation.sources.mempool import fetch as fetch_mempool
from research.global_allocation.sources.mempool import parse as parse_mempool
from research.global_allocation.sources.treasury_yield import fetch as fetch_yield
from research.global_allocation.sources.treasury_yield import parse as parse_yield
from research.global_allocation.sources.world_bank import fetch as fetch_wb
from research.global_allocation.sources.world_bank import parse as parse_wb

# Offline fixture file per source id. A missing file is a named failure, not a live call.
OFFLINE_FILES = {
    "fed_h6": ("fed_h6.csv", parse_fed),
    "ecb_m3": ("ecb_m3.csv", parse_ecb),
    "world_bank": ("world_bank.json", parse_wb),
    "coinbase_btc": ("coinbase_btc.json", parse_spot),
    "coinbase_paxg": ("coinbase_paxg.json", parse_spot),
    "mempool_height": ("mempool_height.txt", parse_mempool),
    "frankfurter": ("frankfurter.json", parse_fx),
    "treasury_real_yield": ("treasury_real_yield.csv", parse_yield),
    "gold_spot": ("gold_spot.json", parse_gold),
    "equity_proxy": ("equity_proxy.json", parse_equity),
    "m2index": ("m2_broad.csv", parse_m2),
}

LIVE = (
    ("fed_h6", fetch_fed),
    ("ecb_m3", fetch_ecb),
    ("world_bank", fetch_wb),
    ("coingecko", fetch_coingecko),
    ("coinbase_btc", fetch_btc),
    ("coinbase_paxg", fetch_paxg),
    ("mempool_height", fetch_mempool),
    ("frankfurter", fetch_fx),
    ("treasury_real_yield", fetch_yield),
    ("gold_spot", fetch_gold),
    ("equity_proxy", fetch_equity),
    ("m2index", fetch_m2),
)

# Weekly price refresh. No broad-money, FX, yield, or CoinGecko call.
WEEKLY_SOURCE_IDS = (
    "gold_spot",
    "coinbase_paxg",
    "coinbase_btc",
    "mempool_height",
    "equity_proxy",
)


def _offline_record(directory, source_id: str) -> SourceRecord:
    from pathlib import Path

    directory = Path(directory)
    filename, parser = OFFLINE_FILES[source_id]
    path = directory / filename
    if not path.is_file():
        return SourceRecord(
            source_id, source_id, "", None, None, None, f"offline fixture missing: {filename}"
        )
    return parser(path.read_bytes())


def collect(offline_dir=None) -> dict[str, SourceRecord]:
    if offline_dir is not None:
        out: dict[str, SourceRecord] = {}
        for source_id in OFFLINE_FILES:
            out[source_id] = _offline_record(offline_dir, source_id)
        out["coingecko"] = fetch_coingecko()
        return out
    return {source_id: fetch() for source_id, fetch in LIVE}


def collect_weekly(offline_dir=None) -> dict[str, SourceRecord]:
    """Gold, PAXG, bitcoin, height, and the equity proxy. Nothing else."""
    if offline_dir is not None:
        return {source_id: _offline_record(offline_dir, source_id) for source_id in WEEKLY_SOURCE_IDS}
    live = dict(LIVE)
    return {source_id: live[source_id]() for source_id in WEEKLY_SOURCE_IDS}
