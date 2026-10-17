"""Import snapshot 0 from the seed markdown table. Does not edit the seed files."""

from __future__ import annotations

import argparse
import re
from decimal import Decimal
from pathlib import Path

from research.global_allocation.inputs import resolve
from research.global_allocation.model import (
    CAVEAT,
    CLASS_IDS,
    anchor_review,
    class_record,
    share_map,
    stale_days,
)
from research.global_allocation.schema import SchemaError, seal
from research.global_allocation.store import EXISTS, Store

PACKAGE_ROOT = Path(__file__).resolve().parent
SEED_MD = PACKAGE_ROOT / "seed" / "global_asset_allocation.md"
SEED_DATE = "2026-10-17"
SEED_RUN_AT = "2026-10-17T16:00:00Z"
GROSS = Decimal("912")

_LABELS = (
    ("real estate", "real_estate"),
    ("debt securities", "debt_securities"),
    ("listed equities", "listed_equities"),
    ("broad money", "broad_money"),
    ("gold", "gold"),
    ("private markets", "private_markets"),
    ("art", "art_collectibles"),
    ("bitcoin", "bitcoin"),
    ("other crypto", "other_crypto"),
)


def parse_section1(markdown: str) -> tuple[list[dict[str, str]], str]:
    rows: list[dict[str, str]] = []
    total = ""
    in_table = False
    for line in markdown.splitlines():
        if line.startswith("## 1."):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|"):
            continue
        if "---" in line or line.startswith("| Class"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        label = cells[0].replace("**", "").strip()
        value = cells[1].replace("**", "").strip()
        if label.lower().startswith("total"):
            total = value
            continue
        rows.append({"label": label, "value": value, "as_of": cells[3].strip(), "source": cells[4].strip()})
    if not total:
        raise ValueError("seed table has no total")
    return rows, total


def class_id_for(label: str) -> str:
    key = label.lower()
    for prefix, class_id in _LABELS:
        if key.startswith(prefix):
            return class_id
    raise ValueError(f"unmapped seed label: {label}")


def build(root: Path, markdown: str | None = None) -> dict:
    text = markdown if markdown is not None else SEED_MD.read_text(encoding="utf-8")
    parsed, total = parse_section1(text)
    if Decimal(total) != GROSS:
        raise ValueError(f"seed total {total} != 912")
    by_id = {}
    for row in parsed:
        by_id[class_id_for(row["label"])] = row
    if list(by_id) != CLASS_IDS and set(by_id) != set(CLASS_IDS):
        raise ValueError(f"seed classes {sorted(by_id)} != {CLASS_IDS}")
    anchors, quantities = resolve(root)
    fx = _fx_basis(text)
    classes = []
    values = {}
    for class_id in CLASS_IDS:
        row = by_id[class_id]
        entry = anchors[class_id]
        if Decimal(str(entry["value_usd_tn"])) != Decimal(row["value"]):
            raise ValueError(f"{class_id} anchor {entry['value_usd_tn']} != table {row['value']}")
        if entry["as_of_display"] != row["as_of"]:
            raise ValueError(f"{class_id} as_of display does not match the seed table")
        due, days = stale_days(entry["as_of"], SEED_DATE, entry.get("review_after"))
        values[class_id] = Decimal(row["value"])
        classes.append(
            {
                "class_id": class_id,
                "value": values[class_id],
                "as_of": row["as_of"],
                "due": due,
                "days": days,
                "entry": entry,
            }
        )
    shares = share_map(values, GROSS)
    tonnes = quantities["gold_above_ground_tonnes"]
    doc = {
        "anchor_review": anchor_review(anchors, quantities, SEED_DATE),
        "caveats": [
            CAVEAT,
            "Not net wealth. Advisory only.",
            "Seed snapshot: mixed price vintages, equities not price-scaled. stats_eligible false.",
        ],
        "classes": [
            class_record(
                item["class_id"],
                item["value"],
                shares[item["class_id"]],
                item["as_of"],
                item["entry"]["value_kind"],
                item["due"],
                item["days"],
                item["entry"]["source_name"],
                item["entry"]["source_url"],
                item["entry"]["method_note"],
                item["entry"]["method_version"],
                item["entry"].get("methodology_break"),
                item["entry"]["confidence"],
                bool(item["entry"].get("verified", False)),
            )
            for item in classes
        ],
        "fx_basis": fx,
        "inputs": {
            "btc_height": None,
            "btc_supply": 20_100_000,
            "equity_proxy_price_at_anchor": None,
            "equity_proxy_price_now": None,
            "equity_proxy_symbol": "VT",
            "gold_tonnes": tonnes["value"],
            "gold_tonnes_as_of": tonnes["as_of"],
            "real_yield_10y": None,
            "real_yield_as_of": None,
        },
        "kind": "seed",
        "revision_reason": None,
        "run_at_utc": SEED_RUN_AT,
        "run_date_et": SEED_DATE,
        "schema_version": 1,
        "snapshot_id": f"{SEED_DATE}_seed",
        "stats_eligible": False,
        "supersedes": None,
        "totals": {"gross_usd_tn": 912, "shares_pct": shares},
        "unit": "usd_tn",
    }
    return seal(doc)


def _fx_basis(markdown: str) -> dict:
    match = re.search(r"CNY ([0-9.]+), EUR ([0-9.]+), JPY ([0-9.]+), GBP ([0-9.]+)", markdown)
    if match is None or "FX 16 Oct 2026" not in markdown:
        raise ValueError("seed FX line missing")
    cny, eur, jpy, gbp = match.groups()
    return {
        "as_of": "2026-10-16",
        "rates": {"CNY": Decimal(cny), "EUR": Decimal(eur), "GBP": Decimal(gbp), "JPY": Decimal(jpy)},
        "source_name": "Frankfurter (ECB reference)",
        "source_url": "https://api.frankfurter.dev/v1/latest",
        "unavailable": None,
    }


def run(root: Path) -> int:
    store = Store(root)
    path = store.path_for(SEED_DATE, "seed")
    if path.is_file():
        print(f"exists {path.name}")
        return 0
    try:
        doc = build(root)
    except (SchemaError, ValueError) as exc:
        print(f"validation failed: {exc}")
        return 1
    result = store.write_new(path, doc)
    if result == EXISTS:
        print(f"exists {path.name}")
        return 0
    print(f"wrote {path.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import the 2026-10-17 seed snapshot")
    parser.add_argument("--root", default=str(PACKAGE_ROOT), help="package root that receives snapshots/")
    args = parser.parse_args(argv)
    return run(Path(args.root))


if __name__ == "__main__":
    raise SystemExit(main())
