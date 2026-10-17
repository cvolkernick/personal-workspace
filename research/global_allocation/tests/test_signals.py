"""Hand-checked advisory signals. Seed and weekly points stay out of the math."""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from research.global_allocation.model import CLASS_IDS
from research.global_allocation.report import render_report
from research.global_allocation.signals import (
    evaluate,
    load_config,
    m2_yoy,
    percentile_rank,
    q,
    sample_std,
    signal_3,
)

RAMP = [Decimal(10 + i) for i in range(14)]


def _dates(n: int) -> list[str]:
    return [f"{2025 + i // 12:04d}-{i % 12 + 1:02d}-15" for i in range(n)]


def _shares(n: int) -> dict[str, list[Decimal]]:
    shares = {class_id: list(RAMP) + [Decimal("16.5")] for class_id in CLASS_IDS}
    if n < 15:
        return {class_id: values[:n] for class_id, values in shares.items()}
    shares["real_estate"][-1] = Decimal("10.2")
    shares["debt_securities"][-1] = Decimal("5")
    shares["listed_equities"][-1] = Decimal("22.8")
    shares["gold"][-1] = Decimal("30")
    shares["private_markets"] = [Decimal("2.22")] * n
    shares["bitcoin"] = [Decimal("0.18")] * n
    shares["art_collectibles"] = [Decimal("1")] * n
    return shares


def _version(class_id: str, index: int) -> str:
    if class_id == "art_collectibles" and index >= 8:
        return "anchor-v2"
    if class_id == "art_collectibles":
        return "anchor-v1"
    return "v1"


def _doc(index: int, n: int, *, kind: str = "monthly", stats: bool = True, sid: str | None = None) -> dict:
    shares = _shares(n)
    classes = []
    for class_id in CLASS_IDS:
        share = shares[class_id][index]
        if class_id == "broad_money":
            value = Decimal(100 + index)
        elif class_id == "gold":
            value = Decimal(10)
        elif class_id == "bitcoin":
            value = Decimal(5)
        else:
            value = Decimal(1)
        classes.append(
            {
                "as_of": _dates(n)[index],
                "id": class_id,
                "label": class_id,
                "method_version": _version(class_id, index),
                "methodology_break": "planted art break" if class_id == "art_collectibles" and index >= 8 else None,
                "share_pct": format(share, "f"),
                "source_name": "fixture",
                "stale": False,
                "value_usd_tn": format(value, "f"),
            }
        )
    return {
        "anchor_review": [],
        "classes": classes,
        "inputs": {"real_yield_10y": "2.74", "real_yield_as_of": "2026-03-15"},
        "kind": kind,
        "run_date_et": _dates(n)[index],
        "snapshot_id": sid or f"{_dates(n)[index]}_{kind}",
        "stats_eligible": stats,
        "totals": {"gross_usd_tn": "100"},
    }


def _monthly(n: int = 15) -> list[dict]:
    return [_doc(i, n) for i in range(n)]


def _yields(last: str, count: int = 100) -> list[tuple[str, Decimal]]:
    end = date.fromisoformat(last)
    points = []
    for i in range(count):
        day = end - timedelta(days=count - 1 - i)
        points.append((day.isoformat(), Decimal(i) / Decimal(10)))
    return points


def _hand_z(x: str) -> Decimal:
    window = RAMP
    mean = sum(window, Decimal(0)) / Decimal(len(window))
    std = sample_std(window)
    return (Decimal(x) - mean) / std


def test_config_records_each_threshold_and_a_rationale():
    raw = load_config()
    assert raw["window"] == Decimal(36)
    assert raw["min_history"] == Decimal(12)
    assert raw["z_high"] == Decimal("2.0")
    assert raw["percentile_high"] == Decimal(95)
    assert raw["percentile_low"] == Decimal(5)
    assert raw["watch_z"] == Decimal("1.5")
    assert raw["money_gap_high_pp"] == Decimal(10)
    assert raw["money_gap_low_pp"] == Decimal(25)
    assert raw["money_growth_min_pct"] == Decimal(4)
    assert raw["equity_z"] == Decimal("1.5")
    assert raw["real_yield_percentile_high"] == Decimal(75)
    assert raw["real_yield_percentile_low"] == Decimal(25)


def test_percentile_rank_is_inclusive():
    window = [Decimal(i) for i in range(10)]
    assert percentile_rank(Decimal(5), window) == Decimal(60)


def test_fifteen_months_match_hand_z_percentiles_and_flags():
    docs = _monthly(15)
    docs.append(_doc(0, 15, kind="seed", stats=False, sid="seed-noise"))
    docs[-1]["classes"] = [
        {**item, "share_pct": "99"} for item in docs[-1]["classes"]
    ]
    docs.append(_doc(0, 15, kind="weekly", stats=False, sid="weekly-noise"))
    gone = _doc(0, 15, sid="gone")
    gone["classes"] = [{**item, "share_pct": "77"} for item in gone["classes"]]
    docs.append(gone)
    yields = _yields("2026-03-15")
    m2 = [("2025-08", Decimal("22000")), ("2026-08", Decimal("23342.8"))]
    result = evaluate(docs, superseded={"gone"}, yields=yields, m2_points=m2, focus=docs[14])
    by = result["by_class"]

    assert by["gold"]["fired_high"] is True
    assert by["gold"]["fired_low"] is False
    assert q(by["gold"]["z"], "0.0001") == q(_hand_z("30"), "0.0001") == "3.2271"
    assert q(by["gold"]["percentile"], "0.01") == "100.00"
    assert by["gold"]["n"] == 14

    assert by["listed_equities"]["status"] == "watch"
    assert by["listed_equities"]["fired_high"] is False
    assert q(by["listed_equities"]["z"], "0.0001") == "1.5060"
    assert q(by["listed_equities"]["percentile"], "0.01") == "92.86"

    assert by["real_estate"]["status"] == "watch"
    assert q(by["real_estate"]["z"], "0.0001") == "-1.5060"
    assert q(by["real_estate"]["percentile"], "0.01") == "7.14"

    assert by["debt_securities"]["fired_low"] is True
    assert q(by["debt_securities"]["z"], "0.0001") == "-2.7490"
    assert q(by["debt_securities"]["percentile"], "0.01") == "0.00"

    assert by["broad_money"]["status"] == "none"
    assert q(by["broad_money"]["z"], "0.0001") == "0.0000"
    assert q(by["broad_money"]["percentile"], "0.01") == "50.00"
    assert by["other_crypto"]["status"] == "none"

    assert by["private_markets"]["status"] == "flat"
    assert by["bitcoin"]["status"] == "flat"
    assert by["private_markets"]["fired_high"] is False

    art = by["art_collectibles"]
    assert art["status"] == "insufficient"
    assert art["n"] == 6
    assert art["fired_high"] is False
    assert art["fired_low"] is False
    assert "anchor-v1 -> anchor-v2" in art["break_note"]
    assert "planted art break" in art["break_note"]

    signal_2 = result["signal_2"]
    hand_g = Decimal(str(math.log(float(Decimal(114) / Decimal(102))))) * Decimal(100)
    assert signal_2["fired_money"] is True
    assert signal_2["fired_hard"] is False
    assert q(signal_2["g"], "0.01") == q(hand_g, "0.01") == "11.12"
    assert q(signal_2["h"], "0.01") == "0.00"
    assert q(signal_2["gap"], "0.01") == "11.12"
    assert q(signal_2["share_change_pp"], "0.01") == "18.00"

    signal_3 = result["signal_3"]
    assert signal_3["fired_high"] is True
    assert signal_3["fired_low"] is False
    assert q(signal_3["percentile"], "0.01") == "100.00"
    assert signal_3["as_of"] == "2026-03-15"


def test_signal_1_under_12_never_fires_and_seed_is_out():
    seed = _doc(0, 15, kind="seed", stats=False, sid="only-seed")
    result = evaluate([seed], focus=seed)
    for class_id in CLASS_IDS:
        item = result["by_class"][class_id]
        assert item["status"] == "insufficient"
        assert item["n"] == 0
        assert item["fired_high"] is False
        assert item["fired_low"] is False
    twelve = _monthly(12)
    short = evaluate(twelve, focus=twelve[-1])
    assert short["by_class"]["gold"]["n"] == 11
    assert short["by_class"]["gold"]["status"] == "insufficient"
    assert short["by_class"]["gold"]["fired_high"] is False


def test_signal_2_before_13_prints_m2_proxy_and_does_not_fire():
    docs = _monthly(10)
    m2 = [("2025-08", Decimal("22000")), ("2026-08", Decimal("23342.8"))]
    result = evaluate(docs, m2_points=m2, focus=docs[-1])
    signal_2 = result["signal_2"]
    assert signal_2["fired_money"] is False
    assert signal_2["fired_hard"] is False
    assert signal_2["n"] == 10
    assert q(signal_2["proxy"]["percent"], "0.01") == "6.10"
    assert signal_2["proxy"]["as_of"] == "2026-08"
    text = render_report(docs, focus=docs[-1], m2_points=m2)
    assert "US M2 YoY proxy (Fed H.6): 6.10% as of 2026-08" in text
    assert "money outrunning hard assets." not in text
    assert "hard assets outrunning money." not in text


def test_signal_2_names_a_money_version_break():
    docs = _monthly(15)
    for index, doc in enumerate(docs):
        for item in doc["classes"]:
            if item["id"] == "gold" and index >= 10:
                item["method_version"] = "tonnes-x-spot-v2"
                item["methodology_break"] = "gold quantity revised"
    result = evaluate(docs, focus=docs[-1])
    assert result["signal_2"]["fired_money"] is False
    assert result["signal_2"]["n"] == 5
    joined = " ".join(result["signal_2"]["break_notes"])
    assert "gold v1 -> tonnes-x-spot-v2" in joined
    assert "gold quantity revised" in joined


def test_signal_3_day_one_and_both_conditions():
    config = load_config()
    end = "2026-10-17"
    yields = []
    start = date(2026, 10, 17)
    for i in range(100):
        day = start - timedelta(days=99 - i)
        yields.append((day.isoformat(), Decimal(i)))
    high_z = {"z": Decimal("1.6")}
    low_z = {"z": Decimal("-1.6")}
    flat_z = {"z": Decimal("0")}
    mid = [(stamp, Decimal(50)) if stamp == end else (stamp, value) for stamp, value in yields]
    # Rebuild a clean series whose last point is the one under test.
    series = [(stamp, Decimal(i)) for i, (stamp, _value) in enumerate(yields)]
    top = signal_3(high_z, series, end, config)
    assert top["fired_high"] is True
    assert q(top["r"], "0.01") == "99.00"
    assert q(top["percentile"], "0.01") == "100.00"
    mild = [(stamp, Decimal(50) if i == 99 else Decimal(i)) for i, (stamp, _value) in enumerate(series)]
    # 0..98 plus a last value of 50. Count <= 50 includes 0..50 and the extra 50.
    no_pct = signal_3(high_z, mild, end, config)
    assert no_pct["fired_high"] is False
    assert no_pct["r"] == Decimal(50)
    no_z = signal_3(flat_z, series, end, config)
    assert no_z["fired_high"] is False
    assert no_z["percentile"] == Decimal(100)
    low_series = [(stamp, Decimal(i)) for i, (stamp, _value) in enumerate(series)]
    low_series[-1] = (end, Decimal(10))
    low = signal_3(low_z, low_series, end, config)
    assert low["fired_low"] is True
    assert low["fired_high"] is False
    low_miss = signal_3(low_z, series, end, config)
    assert low_miss["fired_low"] is False

    seed = _doc(0, 15, kind="seed", stats=False, sid="seed")
    seed["run_date_et"] = end
    text = render_report([seed], focus=seed, yields=series)
    assert "insufficient history (n=0)" in text
    assert "10Y real yield 99.00% as of 2026-10-17" in text
    assert "36-month percentile 100.00" in text
    assert "equity share z unavailable" in text
    assert "equity share stretched high while real yields are high" not in text
    assert "ADVISORY" in text.splitlines()[0]
    assert "regime context: not wired" in text


def test_report_lines_stay_advisory():
    docs = _monthly(15)
    text = render_report(docs, focus=docs[-1], yields=_yields("2026-03-15"), m2_points=[("2025-08", Decimal("22000")), ("2026-08", Decimal("23342.8"))])
    assert text.startswith("# ADVISORY")
    for line in text.splitlines():
        if "signal " in line:
            assert "ADVISORY" in line
    words = []
    token = []
    for char in text.lower():
        if char.isalpha():
            token.append(char)
        elif token:
            words.append("".join(token))
            token = []
    if token:
        words.append("".join(token))
    for banned in ("buy", "sell", "trade", "recommend"):
        assert banned not in words
    assert "insufficient history (n=6)" in text
    assert "planted art break" in text
    assert "money outrunning hard assets" in text
    assert "regime context: not wired" in text
    assert m2_yoy([]) is None
