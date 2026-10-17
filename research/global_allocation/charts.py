"""Deterministic gross-share charts. Weekly runs do not call this module.

PNG bytes drop ancillary chunks so two runs on the same snapshots match.
Font is DejaVu Sans. Figure width is 1600 px. Text is #111111 on white.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from PIL import Image

from research.global_allocation.model import CLASS_IDS, LABELS
from research.global_allocation.report import CAVEAT, _reject_forbidden
from research.global_allocation.store import Store

PACKAGE_ROOT = Path(__file__).resolve().parent
DPI = 100
FIGSIZE = (16, 10)
INK = "#111111"
PALETTE = [
    "#4C78A8",
    "#F58518",
    "#54A24B",
    "#E45756",
    "#72B7B2",
    "#EECA3B",
    "#B279A2",
    "#FF9DA6",
    "#9D755D",
]
HATCHES = ["////", "\\\\\\\\", "xxxx", "....", "++++", "oooo", "****", "||||", "----"]
_FED = re.compile(
    r"Fed H\.6 M2 ([0-9]+(?:\.[0-9]+)?) billion USD as of ([0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)"
)
_ECB = re.compile(
    r"ECB M3 ([0-9]+(?:\.[0-9]+)?) UNIT_MULT ([0-9]+) ([A-Z]+) as of ([0-9]{4}-[0-9]{2}(?:-[0-9]{2})?)"
)


def num(value: Decimal) -> str:
    text = format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def footer_lines(doc: dict[str, Any]) -> list[str]:
    by_id = {item["id"]: item for item in doc.get("classes") or []}
    parts = []
    sources: list[str] = []
    for class_id in CLASS_IDS:
        item = by_id[class_id]
        stale = " (stale)" if item.get("stale") else ""
        parts.append(f"{class_id} {item.get('as_of')}{stale}")
        source = item.get("source_name")
        if source and source not in sources:
            sources.append(source)
    return [
        CAVEAT,
        " · ".join(parts),
        f"snapshot {doc.get('snapshot_id')}",
        "sources: " + ", ".join(sources),
    ]


def load_components(root: Path) -> dict[str, Any]:
    local = Path(root) / "inputs" / "money_components.json"
    path = local if local.is_file() else PACKAGE_ROOT / "inputs" / "money_components.json"
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def money_rows(doc: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in doc.get("classes") or []}
    broad = by_id.get("broad_money") or {}
    note = broad.get("method_note") or ""
    fed = _fed_primary(note)
    ecb = _ecb_primary(note, doc)
    rows: list[dict[str, Any]] = []
    for bloc in components.get("blocs") or []:
        if not bloc.get("as_of") or not bloc.get("source_name"):
            continue
        row = {
            "as_of": str(bloc["as_of"]),
            "id": bloc["id"],
            "kind": "anchor",
            "label": bloc["label"],
            "panel": "broad_money",
            "source_name": bloc["source_name"],
            "value_usd_tn": Decimal(str(bloc["value_usd_tn"])),
        }
        if bloc.get("primary") == "fed_h6" and fed is not None:
            row.update(fed)
        elif bloc.get("primary") == "ecb_m3" and ecb is not None:
            row.update(ecb)
        rows.append(row)
    if broad.get("value_usd_tn") is not None and broad.get("as_of") and broad.get("source_name"):
        rows.append(
            {
                "as_of": str(broad["as_of"]),
                "id": "global",
                "kind": str(broad["source_name"]),
                "label": "global",
                "panel": "broad_money",
                "source_name": str(broad["source_name"]),
                "value_usd_tn": Decimal(str(broad["value_usd_tn"])),
            }
        )
    for entry in components.get("central_bank_assets") or []:
        if not entry.get("as_of") or not entry.get("source_name") or entry.get("value_usd_tn") is None:
            continue
        rows.append(
            {
                "as_of": str(entry["as_of"]),
                "id": entry["id"],
                "kind": str(entry["source_name"]),
                "label": entry["label"],
                "panel": "central_bank",
                "source_name": str(entry["source_name"]),
                "value_usd_tn": Decimal(str(entry["value_usd_tn"])),
            }
        )
    return rows


def _fed_primary(note: str) -> dict[str, Any] | None:
    match = _FED.search(note)
    if not match:
        return None
    billions = Decimal(match.group(1))
    return {
        "as_of": match.group(2),
        "kind": "Fed H.6",
        "source_name": "Fed H.6",
        "value_usd_tn": billions / Decimal(1000),
    }


def _ecb_primary(note: str, doc: dict[str, Any]) -> dict[str, Any] | None:
    match = _ECB.search(note)
    if not match or match.group(3) != "EUR":
        return None
    rates = ((doc.get("fx_basis") or {}).get("rates")) or {}
    if rates.get("EUR") in (None, 0, "0"):
        return None
    eur_tn = Decimal(match.group(1)) * (Decimal(10) ** int(match.group(2))) / Decimal("1000000000000")
    usd_tn = eur_tn / Decimal(str(rates["EUR"]))
    return {
        "as_of": match.group(4),
        "kind": "ECB M3",
        "source_name": "ECB M3",
        "value_usd_tn": usd_tn,
    }


def ordered_docs(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {"seed": 0, "monthly": 1, "weekly": 2}
    return sorted(
        docs,
        key=lambda doc: (
            doc.get("run_date_et") or "",
            rank.get(doc.get("kind"), 9),
            doc.get("snapshot_id") or "",
        ),
    )


def _new_fig() -> Figure:
    fig = Figure(figsize=FIGSIZE, dpi=DPI, facecolor="white")
    FigureCanvasAgg(fig)
    fig.suptitle("")
    return fig


def _style_ax(ax) -> None:
    ax.tick_params(labelsize=9, colors=INK)
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_fontsize(9)
        label.set_color(INK)
    ax.title.set_fontsize(12)
    ax.title.set_color(INK)


def _add_footer(fig: Figure, doc: dict[str, Any]) -> None:
    ax = fig.add_axes([0.04, 0.01, 0.92, 0.16])
    ax.axis("off")
    y = 0.92
    for line in footer_lines(doc):
        ax.text(0.0, y, line, fontsize=9, color=INK, ha="left", va="top", wrap=True, fontfamily="DejaVu Sans")
        y -= 0.23


def draw_allocation(doc: dict[str, Any]) -> Figure:
    fig = _new_fig()
    ax = fig.add_axes([0.02, 0.30, 0.46, 0.62])
    side = fig.add_axes([0.50, 0.30, 0.48, 0.62])
    side.axis("off")
    by_id = {item["id"]: item for item in doc["classes"]}
    sizes = [float(by_id[class_id]["share_pct"]) for class_id in CLASS_IDS]
    wedges, _ = ax.pie(
        sizes,
        startangle=90,
        counterclock=False,
        colors=PALETTE,
        wedgeprops={"width": 0.42, "edgecolor": "white", "linewidth": 0.6},
    )
    for wedge, hatch in zip(wedges, HATCHES):
        wedge.set_hatch(hatch)
    ax.set_title("Latest gross shares", fontsize=12, color=INK, fontfamily="DejaVu Sans")
    callouts = []
    table = []
    for class_id in CLASS_IDS:
        item = by_id[class_id]
        callouts.append(f"{LABELS[class_id]}  {item['value_usd_tn']} ({item['share_pct']}%)")
        table.append([LABELS[class_id], str(item["value_usd_tn"]), str(item["share_pct"])])
    for index, line in enumerate(callouts):
        side.text(0.0, 0.98 - index * 0.055, line, fontsize=9, color=INK, va="top", fontfamily="DejaVu Sans")
    tbl = side.table(
        cellText=table,
        colLabels=["class", "USD tn", "share %"],
        bbox=[0.0, 0.0, 1.0, 0.42],
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    for cell in tbl.get_celld().values():
        cell.get_text().set_color(INK)
        cell.get_text().set_fontsize(9)
    _add_footer(fig, doc)
    return fig


def draw_history(docs: list[dict[str, Any]]) -> Figure:
    fig = _new_fig()
    ax = fig.add_axes([0.07, 0.30, 0.68, 0.58])
    ordered = ordered_docs(docs)
    latest = ordered[-1]
    by_docs = [{item["id"]: item for item in doc["classes"]} for doc in ordered]
    xs = list(range(len(ordered)))
    if len(ordered) == 1:
        bottom = 0.0
        for index, class_id in enumerate(CLASS_IDS):
            share = float(by_docs[0][class_id]["share_pct"])
            ax.bar(
                xs,
                [share],
                bottom=bottom,
                width=0.5,
                color=PALETTE[index],
                hatch=HATCHES[index],
                edgecolor=INK,
                linewidth=0.3,
                label=LABELS[class_id],
            )
            bottom += share
    else:
        series = [[float(mapping[class_id]["share_pct"]) for mapping in by_docs] for class_id in CLASS_IDS]
        polys = ax.stackplot(xs, series, colors=PALETTE, labels=[LABELS[class_id] for class_id in CLASS_IDS])
        for poly, hatch in zip(polys, HATCHES):
            poly.set_hatch(hatch)
            poly.set_edgecolor(INK)
            poly.set_linewidth(0.3)
    ax.set_title("Share history", fontsize=12, color=INK, fontfamily="DejaVu Sans")
    ax.set_ylabel("share %", fontsize=10, color=INK, fontfamily="DejaVu Sans")
    ax.set_xticks(xs)
    ax.set_xticklabels(
        [f"{doc.get('run_date_et')} {doc.get('kind')}" for doc in ordered],
        rotation=30,
        ha="right",
        fontsize=9,
        fontfamily="DejaVu Sans",
    )
    ax.set_ylim(0, 100)
    legend = ax.legend(loc="center left", bbox_to_anchor=(1.02, 0.5), frameon=False, fontsize=9)
    for text in legend.get_texts():
        text.set_color(INK)
        text.set_fontsize(9)
    began = ordered[0].get("run_date_et")
    ax.text(
        0.0,
        1.04,
        f"history begins {began}",
        transform=ax.transAxes,
        fontsize=9,
        color=INK,
        fontfamily="DejaVu Sans",
        clip_on=False,
    )
    for index, doc in enumerate(ordered):
        if doc.get("kind") == "seed":
            ax.text(
                index,
                8,
                "seed",
                fontsize=9,
                color=INK,
                ha="center",
                fontfamily="DejaVu Sans",
                bbox={"boxstyle": "round,pad=0.2", "fc": "white", "ec": "none"},
            )
    _style_ax(ax)
    _add_footer(fig, latest)
    return fig


def draw_money(rows: list[dict[str, Any]], doc: dict[str, Any]) -> Figure:
    fig = _new_fig()
    blocs = [row for row in rows if row["panel"] == "broad_money"]
    banks = [row for row in rows if row["panel"] == "central_bank"]
    if banks:
        top = fig.add_axes([0.40, 0.62, 0.56, 0.28])
        bottom = fig.add_axes([0.40, 0.24, 0.56, 0.28])
        _bars(top, blocs, "Broad money by bloc", xlabel=False)
        _bars(bottom, banks, "Central bank assets", xlabel=True)
    else:
        top = fig.add_axes([0.40, 0.28, 0.56, 0.60])
        _bars(top, blocs, "Broad money by bloc", xlabel=True)
    _add_footer(fig, doc)
    return fig


def _bars(ax, rows: list[dict[str, Any]], title: str, *, xlabel: bool) -> None:
    import textwrap

    values = [float(row["value_usd_tn"]) for row in rows]
    hatches = [HATCHES[index % len(HATCHES)] for index in range(len(rows))]
    colors = [PALETTE[index % len(PALETTE)] for index in range(len(rows))]
    ax.barh(
        range(len(rows)),
        values,
        color=colors,
        hatch=hatches,
        edgecolor=INK,
        linewidth=0.4,
    )
    labels = []
    for row in rows:
        as_of = "\n".join(textwrap.wrap(str(row["as_of"]), 36)) or str(row["as_of"])
        labels.append(f"{row['label']}  {num(row['value_usd_tn'])}  {row['kind']}\n{as_of}")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=9, fontfamily="DejaVu Sans", color=INK)
    ax.invert_yaxis()
    ax.set_title(title, fontsize=12, color=INK, fontfamily="DejaVu Sans")
    if xlabel:
        ax.set_xlabel("USD tn", fontsize=10, color=INK, fontfamily="DejaVu Sans")
    _style_ax(ax)


def png_bytes(fig: Figure) -> bytes:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor="white")
    buffer.seek(0)
    image = Image.open(buffer)
    image.load()
    clean = Image.new(image.mode, image.size)
    if image.mode == "RGBA":
        flat = Image.new("RGB", image.size, "white")
        flat.paste(image, mask=image.split()[-1])
        clean = flat
    else:
        clean.putdata(list(image.getdata()))
    out = io.BytesIO()
    clean.save(out, format="PNG")
    return out.getvalue()


def allocation_csv(doc: dict[str, Any]) -> bytes:
    by_id = {item["id"]: item for item in doc["classes"]}
    rows = []
    for class_id in CLASS_IDS:
        item = by_id[class_id]
        rows.append(
            [
                class_id,
                LABELS[class_id],
                item["value_usd_tn"],
                item["share_pct"],
                item.get("as_of"),
                "true" if item.get("stale") else "false",
                item.get("source_name"),
            ]
        )
    return _csv(
        ["class_id", "label", "value_usd_tn", "share_pct", "as_of", "stale", "source_name"],
        rows,
    )


def history_csv(docs: list[dict[str, Any]]) -> bytes:
    rows = []
    for doc in ordered_docs(docs):
        by_id = {item["id"]: item for item in doc["classes"]}
        for class_id in CLASS_IDS:
            rows.append(
                [
                    doc.get("run_date_et"),
                    doc.get("kind"),
                    doc.get("snapshot_id"),
                    class_id,
                    by_id[class_id]["share_pct"],
                ]
            )
    return _csv(["run_date_et", "kind", "snapshot_id", "class_id", "share_pct"], rows)


def money_csv(rows: list[dict[str, Any]]) -> bytes:
    body = [
        [row["panel"], row["id"], row["label"], num(row["value_usd_tn"]), row["as_of"], row["source_name"], row["kind"]]
        for row in rows
    ]
    return _csv(["panel", "id", "label", "value_usd_tn", "as_of", "source_name", "kind"], body)


def alt_md(doc: dict[str, Any], docs: list[dict[str, Any]], rows: list[dict[str, Any]]) -> str:
    by_id = {item["id"]: item for item in doc["classes"]}
    began = ordered_docs(docs)[0].get("run_date_et")
    alloc_table = ["| class | USD tn | share % | as of |", "|---|---:|---:|---|"]
    for class_id in CLASS_IDS:
        item = by_id[class_id]
        alloc_table.append(
            f"| {LABELS[class_id]} | {item['value_usd_tn']} | {item['share_pct']} | {item.get('as_of')} |"
        )
    hist_table = ["| date | kind | snapshot | class | share % |", "|---|---|---|---|---:|"]
    for doc_i in ordered_docs(docs):
        mapping = {item["id"]: item for item in doc_i["classes"]}
        for class_id in CLASS_IDS:
            hist_table.append(
                f"| {doc_i.get('run_date_et')} | {doc_i.get('kind')} | {doc_i.get('snapshot_id')} | "
                f"{class_id} | {mapping[class_id]['share_pct']} |"
            )
    money_table = ["| panel | label | USD tn | as of | kind |", "|---|---|---:|---|---|"]
    for row in rows:
        money_table.append(
            f"| {row['panel']} | {row['label']} | {num(row['value_usd_tn'])} | {row['as_of']} | {row['kind']} |"
        )
    parts = [
        "# Chart descriptions",
        "",
        "## allocation_latest.png",
        "",
        (
            f"Donut of gross overlapping shares for snapshot {doc.get('snapshot_id')}. "
            "Every class has a text callout with its value and percent, a hatch, and a row in the table. "
            f"{CAVEAT}"
        ),
        "",
        *alloc_table,
        "",
        "## share_history.png",
        "",
        (
            f"Stacked share history from every non-superseded snapshot. Seed points are marked seed. "
            f"history begins {began}. {CAVEAT}"
        ),
        "",
        *hist_table,
        "",
        "## money_supply.png",
        "",
        (
            "Broad money by bloc and the global total. US uses Fed H.6 when that print is on the snapshot, "
            "and the euro area uses ECB M3 when the FX rate is on the snapshot. Other blocs are dated anchors. "
            "Central-bank bars are only entries that carry a source and an as-of. "
            f"{CAVEAT}"
        ),
        "",
        *money_table,
        "",
    ]
    text = "\n".join(parts)
    _reject_forbidden(text)
    return text


def bundle(docs: list[dict[str, Any]], components: dict[str, Any]) -> dict[str, bytes]:
    ordered = ordered_docs(docs)
    latest = ordered[-1]
    rows = money_rows(latest, components)
    figures = {
        "allocation": draw_allocation(latest),
        "history": draw_history(ordered),
        "money": draw_money(rows, latest),
    }
    files = {
        "allocation_latest.png": png_bytes(figures["allocation"]),
        "allocation_latest.csv": allocation_csv(latest),
        "share_history.png": png_bytes(figures["history"]),
        "share_history.csv": history_csv(ordered),
        "money_supply.png": png_bytes(figures["money"]),
        "money_supply.csv": money_csv(rows),
        "ALT.md": alt_md(latest, ordered, rows).encode("utf-8"),
    }
    for fig in figures.values():
        fig.clf()
    return files


def _csv(header: list[str], rows: list[list[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue().encode("utf-8")


def _place(path: Path, payload: bytes, *, overwrite: bool) -> str:
    if path.is_file():
        if path.read_bytes() == payload:
            return "unchanged"
        if not overwrite:
            return "exists"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)
        return "wrote"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    try:
        view = memoryview(payload)
        while view:
            n = os.write(fd, view)
            view = view[n:]
    finally:
        os.close(fd)
    return "wrote"


def alive_docs(store: Store) -> list[dict[str, Any]]:
    lines = store.index_lines()
    superseded = {ln.get("supersedes") for ln in lines if ln.get("supersedes")}
    docs = []
    for line in lines:
        if line.get("snapshot_id") in superseded:
            continue
        docs.append(store.read_snapshot(store.snap_dir / line["file"]))
    return ordered_docs(docs)


def publish(root: Path, out_dir: Path | None = None) -> int:
    store = Store(root)
    docs = alive_docs(store)
    if not docs:
        print("no snapshots")
        return 1
    files = bundle(docs, load_components(root))
    dest_root = out_dir or (root / "charts")
    month = str(docs[-1].get("run_date_et") or "")[:7]
    dated = dest_root / month
    latest = dest_root / "latest"
    if dated.exists():
        print(f"exists {month}")
    else:
        dated.mkdir(parents=True)
        for name, payload in files.items():
            _place(dated / name, payload, overwrite=False)
        print(f"wrote {month}")
    latest.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        _place(latest / name, payload, overwrite=True)
    print("wrote latest")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write global-allocation charts")
    parser.add_argument("--root", default=str(PACKAGE_ROOT))
    parser.add_argument("--out", help="chart root (default: <root>/charts)")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else None
    return publish(Path(args.root), out)


if __name__ == "__main__":
    raise SystemExit(main())
