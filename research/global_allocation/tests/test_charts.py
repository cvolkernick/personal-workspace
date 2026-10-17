"""Chart bytes, footer text, hatches, and write-once folders."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from PIL import Image

from research.global_allocation.charts import (
    alive_docs,
    draw_allocation,
    draw_history,
    draw_money,
    footer_lines,
    money_rows,
    png_bytes,
    publish,
)
from research.global_allocation.model import CLASS_IDS, LABELS
from research.global_allocation.report import CAVEAT, write_once

PACKAGE = Path(__file__).resolve().parents[1]
SEED = PACKAGE / "snapshots" / "2026-10-17_seed.json"


def _store(tmp: Path, docs: list[dict]) -> Path:
    snap = tmp / "snapshots"
    snap.mkdir(parents=True)
    lines = []
    for doc in docs:
        name = doc["snapshot_id"] + ".json"
        (snap / name).write_text(json.dumps(doc), encoding="utf-8")
        lines.append(
            json.dumps(
                {
                    "body_sha256": "x",
                    "file": name,
                    "file_sha256": "x",
                    "kind": doc["kind"],
                    "run_date_et": doc["run_date_et"],
                    "snapshot_id": doc["snapshot_id"],
                    "supersedes": doc.get("supersedes"),
                },
                sort_keys=True,
            )
        )
    (snap / "index.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp


def _texts(fig):
    fig.canvas.draw()
    found = []
    sizes = []
    colors = []

    def take(text):
        if text is None:
            return
        value = text.get_text()
        found.append(value)
        if not value.strip():
            return
        sizes.append(text.get_fontsize())
        colors.append(text.get_color())

    for ax in fig.axes:
        take(ax.title)
        for text in ax.texts:
            take(text)
        for tick in list(ax.get_xticklabels()) + list(ax.get_yticklabels()):
            take(tick)
        legend = ax.get_legend()
        if legend is not None:
            for text in legend.get_texts():
                take(text)
        for table in ax.tables:
            for cell in table.get_celld().values():
                take(cell.get_text())
    return "\n".join(found), sizes, colors


def _luminance(rgb):
    def channel(value):
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(color):
    named = {"black": (0, 0, 0), "white": (255, 255, 255)}
    if isinstance(color, str) and color in named:
        return named[color]
    if isinstance(color, str):
        text = color.lstrip("#")
        if len(text) == 6:
            return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))
    if isinstance(color, tuple) and len(color) >= 3:
        vals = color[:3]
        if max(vals) <= 1:
            return tuple(int(round(v * 255)) for v in vals)
        return tuple(int(v) for v in vals)
    raise AssertionError(color)


def test_seed_only_footer_labels_hatches_and_identical_bytes(tmp_path):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    root = _store(tmp_path / "root", [seed])
    out = tmp_path / "charts"
    assert publish(root, out) == 0
    month = seed["run_date_et"][:7]
    for name in (
        "allocation_latest.png",
        "share_history.png",
        "money_supply.png",
        "allocation_latest.csv",
        "share_history.csv",
        "money_supply.csv",
        "ALT.md",
    ):
        assert (out / "latest" / name).is_file()
        assert (out / month / name).is_file()
    alloc = draw_allocation(seed)
    history = draw_history([seed])
    rows = money_rows(seed, json.loads((PACKAGE / "inputs" / "money_components.json").read_text(encoding="utf-8")))
    money = draw_money(rows, seed)
    blob, sizes, colors = _texts(alloc)
    hist_blob, hist_sizes, hist_colors = _texts(history)
    money_blob, money_sizes, money_colors = _texts(money)
    for text in (blob, hist_blob, money_blob):
        for line in footer_lines(seed):
            assert line in text
    footer = "\n".join(footer_lines(seed))
    assert CAVEAT in footer
    for class_id in CLASS_IDS:
        assert class_id in footer
    assert "real_estate " in footer and "(stale)" in footer
    assert "art_collectibles " in footer
    assert f"snapshot {seed['snapshot_id']}" in footer
    assert f"history begins {seed['run_date_et']}" in hist_blob
    assert "seed" in hist_blob
    for label in LABELS.values():
        assert label in blob
        assert label in hist_blob
    hatches = [patch.get_hatch() for patch in alloc.axes[0].patches]
    assert len(hatches) == 9
    assert len(set(hatches)) == 9
    assert all(hatches)
    for size in sizes + hist_sizes + money_sizes:
        assert size >= 9
    for color in colors + hist_colors + money_colors:
        dark = _luminance(_rgb(color))
        white = _luminance((255, 255, 255))
        ratio = (white + 0.05) / (dark + 0.05)
        assert ratio >= 4.5
    first = png_bytes(draw_allocation(seed))
    second = png_bytes(draw_allocation(seed))
    assert first == second
    with Image.open(io_bytes(first)) as image:
        assert image.size[0] == 1600
    alt = (out / "latest" / "ALT.md").read_text(encoding="utf-8")
    assert "allocation_latest.png" in alt
    assert "share_history.png" in alt
    assert "money_supply.png" in alt
    assert "anchor" in money_blob
    dated_png = out / month / "allocation_latest.png"
    before = dated_png.stat().st_mtime_ns
    before_bytes = dated_png.read_bytes()
    assert publish(root, out) == 0
    assert dated_png.read_bytes() == before_bytes
    assert dated_png.stat().st_mtime_ns == before
    assert (out / "latest" / "allocation_latest.png").read_bytes() == before_bytes


def test_money_primary_feeds_and_skip_unsourced_balance_sheet():
    components = {
        "blocs": [
            {
                "id": "us",
                "label": "US",
                "value_usd_tn": 23.34,
                "as_of": "2026-08",
                "source_name": "FRED M2SL",
                "primary": "fed_h6",
            },
            {
                "id": "euro_area",
                "label": "euro area",
                "value_usd_tn": 19.9,
                "as_of": "2026-08",
                "source_name": "ECB",
                "primary": "ecb_m3",
            },
            {
                "id": "china",
                "label": "China",
                "value_usd_tn": 53.8,
                "as_of": "2026-09",
                "source_name": "PBoC",
            },
            {"id": "blank", "label": "Blank", "value_usd_tn": 1, "source_name": "nowhere"},
        ],
        "central_bank_assets": [
            {
                "id": "fed",
                "label": "Fed",
                "value_usd_tn": 6.75,
                "as_of": "2026-10-07",
                "source_name": "FinObservatory",
            },
            {"id": "invented", "label": "Invented", "value_usd_tn": 9, "source_name": "nobody"},
        ],
    }
    doc = {
        "classes": [
            {
                "id": "broad_money",
                "as_of": "2026-08",
                "source_name": "M2 Index",
                "value_usd_tn": 145.1,
                "method_note": (
                    "Fed H.6 M2 24000 billion USD as of 2026-08 "
                    "ECB M3 17646463 UNIT_MULT 6 EUR as of 2026-08"
                ),
            }
        ],
        "fx_basis": {"rates": {"EUR": "0.88865"}},
        "snapshot_id": "demo",
        "run_date_et": "2026-10-17",
        "kind": "monthly",
    }
    rows = money_rows(doc, components)
    by_id = {row["id"]: row for row in rows}
    assert "blank" not in by_id
    assert "invented" not in by_id
    assert by_id["us"]["kind"] == "Fed H.6"
    assert format(by_id["us"]["value_usd_tn"].quantize(Decimal("0.01")), "f") == "24.00"
    assert by_id["euro_area"]["kind"] == "ECB M3"
    assert format(by_id["euro_area"]["value_usd_tn"].quantize(Decimal("0.01")), "f") == "19.86"
    assert by_id["china"]["kind"] == "anchor"
    assert by_id["global"]["value_usd_tn"] == Decimal("145.1")
    assert by_id["fed"]["as_of"] == "2026-10-07"
    fig = draw_money(rows, _seedish(doc))
    text, _, _ = _texts(fig)
    assert "Fed H.6" in text
    assert "anchor" in text
    assert "Invented" not in text


def test_superseded_snapshot_is_not_plotted(tmp_path):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    older = json.loads(json.dumps(seed))
    older["snapshot_id"] = "2026-10-01_monthly"
    older["kind"] = "monthly"
    older["run_date_et"] = "2026-10-01"
    older["stats_eligible"] = True
    for item in older["classes"]:
        item["share_pct"] = 1
    newer = json.loads(json.dumps(seed))
    newer["snapshot_id"] = "2026-10-01_monthly_r1"
    newer["kind"] = "monthly"
    newer["run_date_et"] = "2026-10-01"
    newer["supersedes"] = older["snapshot_id"]
    root = _store(tmp_path, [seed, older, newer])
    docs = alive_docs_from(root)
    ids = [doc["snapshot_id"] for doc in docs]
    assert older["snapshot_id"] not in ids
    csv = (tmp_path / "out")
    assert publish(root, csv) == 0
    history = (csv / "latest" / "share_history.csv").read_text(encoding="utf-8")
    assert f",{older['snapshot_id']}," not in history
    assert f",{newer['snapshot_id']}," in history


def test_report_file_is_not_overwritten(tmp_path):
    path = tmp_path / "2026-10_advisory.md"
    assert write_once(path, "ADVISORY\n") == "written"
    assert write_once(path, "ADVISORY changed\n") == "exists"
    assert path.read_text(encoding="utf-8") == "ADVISORY\n"


def _seedish(doc):
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    seed["classes"] = [item for item in seed["classes"] if item["id"] != "broad_money"]
    seed["classes"].append(doc["classes"][0])
    return seed


def alive_docs_from(root: Path):
    return alive_docs(__import__("research.global_allocation.store", fromlist=["Store"]).Store(root))


def io_bytes(payload: bytes):
    import io

    return io.BytesIO(payload)
