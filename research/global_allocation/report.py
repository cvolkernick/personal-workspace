"""Monthly advisory report. Written once per YYYY-MM. Advisory text only."""

from __future__ import annotations

import argparse
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from research.global_allocation.model import CLASS_IDS, LABELS
from research.global_allocation.signals import D, evaluate, q
from research.global_allocation.store import Store

PACKAGE_ROOT = Path(__file__).resolve().parent
CAVEAT = "Gross, overlapping, mixed dates. Not net wealth. Advisory only."
FORBIDDEN = ("buy", "sell", "trade", "recommend")


def render_report(
    docs: list[dict[str, Any]],
    *,
    focus: dict[str, Any],
    superseded: set[str] | None = None,
    yields: list[tuple[str, Decimal]] | None = None,
    m2_points: list[tuple[str, Decimal]] | None = None,
) -> str:
    result = evaluate(
        docs,
        superseded=superseded,
        yields=yields,
        m2_points=m2_points,
        focus=focus,
    )
    lines = [
        "# ADVISORY",
        "",
        CAVEAT,
        "",
        "## Snapshot summary",
        "",
        f"- snapshot: {focus.get('snapshot_id')}",
        f"- run date: {focus.get('run_date_et')}",
        f"- gross: {_gross(focus)} USD tn",
    ]
    focus_shares = _shares(focus)
    for class_id in CLASS_IDS:
        item = focus_shares.get(class_id)
        if item is None:
            continue
        lines.append(
            f"- {class_id}: {item.get('value_usd_tn')} USD tn, {item.get('share_pct')}% "
            f"as of {item.get('as_of')}"
        )
    lines.extend(["", "## Since last month", ""])
    lines.extend(_pp_lines(_previous_monthly(docs, focus, superseded), focus, "no prior monthly snapshot"))
    lines.extend(["", "## Since seed", ""])
    lines.extend(_pp_lines(_seed(docs, superseded), focus, "no seed snapshot"))
    lines.extend(
        [
            "",
            "Shares sum to 100%, so classes are correlated.",
            "",
            "## Signals fired",
            "",
        ]
    )
    fired, quiet = _signal_lines(result)
    if fired:
        lines.extend(fired)
    else:
        lines.append("- none")
    lines.extend(["", "## Signals not fired", ""])
    lines.extend(quiet)
    lines.extend(["", "## Caveats", "", f"- {CAVEAT}"])
    stale = [LABELS[class_id] for class_id in CLASS_IDS if (focus_shares.get(class_id) or {}).get("stale")]
    if stale:
        lines.append("- stale classes: " + ", ".join(stale))
    else:
        lines.append("- stale classes: none")
    lines.extend(["", "## Anchors due for review", ""])
    review = focus.get("anchor_review") or []
    if not review:
        lines.append("- none past window")
    for item in review:
        lines.append(
            f"- {item.get('class_id')} as of {item.get('as_of')} past {item.get('window')} "
            f"by {item.get('stale_days')} days"
        )
    lines.extend(["", "## Regime", "", "regime context: not wired", ""])
    text = "\n".join(lines)
    _reject_forbidden(text)
    return text


def _signal_lines(result: dict[str, Any]) -> tuple[list[str], list[str]]:
    fired: list[str] = []
    quiet: list[str] = []
    for class_id in CLASS_IDS:
        item = result["by_class"][class_id]
        line = _signal_1_line(item)
        if item["fired_high"] or item["fired_low"]:
            fired.append(line)
        else:
            quiet.append(line)
    signal_2 = result["signal_2"]
    line_2 = _signal_2_line(signal_2)
    if signal_2["fired_money"] or signal_2["fired_hard"]:
        fired.append(line_2)
    else:
        quiet.append(line_2)
    signal_3 = result["signal_3"]
    line_3 = _signal_3_line(signal_3)
    if signal_3["fired_high"] or signal_3["fired_low"]:
        fired.append(line_3)
    else:
        quiet.append(line_3)
    return fired, quiet


def _signal_1_line(item: dict[str, Any]) -> str:
    class_id = item["class_id"]
    note = f" {item['break_note']}." if item.get("break_note") else ""
    if item["status"] == "insufficient":
        return f"- ADVISORY signal 1 {class_id}: insufficient history (n={item['n']}).{note}"
    if item["status"] == "flat":
        return f"- ADVISORY signal 1 {class_id}: flat, not computed.{note}"
    z = q(item["z"], "0.0001")
    pct = q(item["percentile"], "0.01")
    if item["fired_high"] and item["fired_low"]:
        label = "stretched high and stretched low"
    elif item["fired_high"]:
        label = "stretched high"
    elif item["fired_low"]:
        label = "stretched low"
    elif item["watch"]:
        label = "watch"
    else:
        label = "not fired"
    return f"- ADVISORY signal 1 {class_id}: {label}. z {z}, percentile {pct}.{note}"


def _signal_2_line(item: dict[str, Any]) -> str:
    proxy = _proxy_text(item.get("proxy"))
    notes = ""
    if item.get("break_notes"):
        notes = " " + " ".join(item["break_notes"]) + "."
    if item["status"] != "ok":
        return (
            f"- ADVISORY signal 2: not fired. 12-month log growth needs 13 monthly snapshots "
            f"(n={item['n']}). {proxy}.{notes}"
        )
    gap = q(item["gap"], "0.01")
    g = q(item["g"], "0.01")
    h = q(item["h"], "0.01")
    change = q(item["share_change_pp"], "0.01")
    detail = (
        f"gap {gap} pp, g {g}%, h {h}%. "
        f"Gold plus bitcoin share change {change} pp over 12 monthly steps."
    )
    if item["fired_money"] and item["fired_hard"]:
        name = "money outrunning hard assets and hard assets outrunning money"
    elif item["fired_money"]:
        name = "money outrunning hard assets"
    elif item["fired_hard"]:
        name = "hard assets outrunning money"
    else:
        name = "not fired"
    return f"- ADVISORY signal 2: {name}. {detail}{notes}"


def _proxy_text(proxy: dict[str, Any] | None) -> str:
    if not proxy:
        return "US M2 YoY proxy (Fed H.6): unavailable"
    return (
        f"US M2 YoY proxy (Fed H.6): {q(proxy['percent'], '0.01')}% as of {proxy['as_of']}"
    )


def _signal_3_line(item: dict[str, Any]) -> str:
    if item["status"] == "no_yield" or item["r"] is None:
        return "- ADVISORY signal 3: 10Y real yield unavailable. not fired."
    r = q(item["r"], "0.01")
    as_of = item["as_of"]
    if item["percentile"] is None:
        pct = "36-month percentile unavailable"
    else:
        pct = f"36-month percentile {q(item['percentile'], '0.01')}"
    if item["z"] is None:
        z_text = "equity share z unavailable"
    else:
        z_text = f"equity z {q(item['z'], '0.0001')}"
    if item["fired_high"]:
        name = "equity share stretched high while real yields are high"
    elif item["fired_low"]:
        name = "equity share low while real yields are low"
    else:
        name = "not fired"
    return f"- ADVISORY signal 3: {name}. 10Y real yield {r}% as of {as_of}, {pct}. {z_text}."


def _shares(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in doc.get("classes") or []}


def _gross(doc: dict[str, Any]) -> Any:
    totals = doc.get("totals") or {}
    if totals.get("gross_usd_tn") is not None:
        return totals["gross_usd_tn"]
    return "unknown"


def _seed(docs: list[dict[str, Any]], superseded: set[str] | None) -> dict[str, Any] | None:
    skipped = superseded or set()
    seeds = [
        doc
        for doc in docs
        if doc.get("kind") == "seed" and doc.get("snapshot_id") not in skipped
    ]
    if not seeds:
        return None
    seeds.sort(key=lambda doc: (doc.get("run_date_et") or "", doc.get("snapshot_id") or ""))
    return seeds[-1]


def _previous_monthly(
    docs: list[dict[str, Any]],
    focus: dict[str, Any],
    superseded: set[str] | None,
) -> dict[str, Any] | None:
    skipped = superseded or set()
    focus_key = (focus.get("run_date_et") or "", focus.get("snapshot_id") or "")
    monthly = [
        doc
        for doc in docs
        if doc.get("kind") == "monthly" and doc.get("snapshot_id") not in skipped
    ]
    monthly.sort(key=lambda doc: (doc.get("run_date_et") or "", doc.get("snapshot_id") or ""))
    prior = [
        doc
        for doc in monthly
        if (doc.get("run_date_et") or "", doc.get("snapshot_id") or "") < focus_key
    ]
    if not prior:
        return None
    return prior[-1]


def _pp_lines(baseline: dict[str, Any] | None, focus: dict[str, Any], empty: str) -> list[str]:
    if baseline is None:
        return [f"- {empty}"]
    if baseline.get("snapshot_id") == focus.get("snapshot_id"):
        return [f"- {class_id}: 0.00 pp" for class_id in CLASS_IDS]
    before = _shares(baseline)
    after = _shares(focus)
    lines = []
    for class_id in CLASS_IDS:
        if class_id not in before or class_id not in after:
            lines.append(f"- {class_id}: unavailable")
            continue
        delta = D(after[class_id]["share_pct"]) - D(before[class_id]["share_pct"])
        lines.append(f"- {class_id}: {q(delta, '0.01')} pp")
    return lines


def _reject_forbidden(text: str) -> None:
    words = set()
    token = []
    for char in text.lower():
        if char.isalpha():
            token.append(char)
        else:
            if token:
                words.add("".join(token))
                token = []
    if token:
        words.add("".join(token))
    found = sorted(word for word in FORBIDDEN if word in words)
    if found:
        raise ValueError("forbidden wording: " + ", ".join(found))


def write_once(path: Path, text: str) -> str:
    if path.exists():
        return "exists"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return "exists"
    try:
        view = memoryview(payload)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)
    return "written"


def _alive(store: Store) -> tuple[list[dict[str, Any]], set[str]]:
    lines = store.index_lines()
    superseded = {ln.get("supersedes") for ln in lines if ln.get("supersedes")}
    docs = []
    for line in lines:
        if line.get("snapshot_id") in superseded:
            continue
        docs.append(store.read_snapshot(store.snap_dir / line["file"]))
    return docs, {item for item in superseded if item}


def select_focus(docs: list[dict[str, Any]], month: str) -> dict[str, Any] | None:
    in_month = [doc for doc in docs if str(doc.get("run_date_et") or "").startswith(month)]
    if not in_month:
        return None
    rank = {"monthly": 0, "seed": 1, "weekly": 2}

    def sort_key(doc: dict[str, Any]) -> tuple:
        return (
            rank.get(doc.get("kind"), 9),
            doc.get("run_date_et") or "",
            doc.get("snapshot_id") or "",
        )

    in_month.sort(key=sort_key)
    monthly = [doc for doc in in_month if doc.get("kind") == "monthly"]
    if monthly:
        return monthly[-1]
    return in_month[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one monthly advisory report")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--root", default=str(PACKAGE_ROOT))
    parser.add_argument("--out", help="report path (default: <root>/reports/YYYY-MM_advisory.md)")
    parser.add_argument("--yield-csv", help="daily Treasury real-yield CSV")
    parser.add_argument("--m2-csv", help="Fed H.6 CSV used for the US M2 YoY proxy")
    args = parser.parse_args(argv)
    root = Path(args.root)
    store = Store(root)
    docs, superseded = _alive(store)
    focus = select_focus(docs, args.month)
    if focus is None:
        print(f"no snapshot in {args.month}")
        return 1
    yields = _load_yields(Path(args.yield_csv)) if args.yield_csv else []
    m2_points = _load_m2(Path(args.m2_csv)) if args.m2_csv else []
    text = render_report(docs, focus=focus, superseded=superseded, yields=yields, m2_points=m2_points)
    dest = Path(args.out) if args.out else root / "reports" / f"{args.month}_advisory.md"
    result = write_once(dest, text)
    print(f"{result} {dest.name}")
    return 0


def _load_yields(path: Path) -> list[tuple[str, Decimal]]:
    from research.global_allocation.sources.treasury_yield import parse_series

    return parse_series(path.read_bytes())


def _load_m2(path: Path) -> list[tuple[str, Decimal]]:
    from research.global_allocation.sources.fed_h6 import parse_series

    return parse_series(path.read_bytes())


if __name__ == "__main__":
    raise SystemExit(main())
