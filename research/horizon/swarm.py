"""Horizon /swarm page: node topology drawn from ops/NODES.md.

The diagram is generated at request time from the markdown so it can't drift:
  - "## <host>" sections → nodes; their "### Units → ports" table → app list
  - the top comparison table "Role" row → node role
  - "### Planned and external" bullets  `- id | label | detail`
  - "### Flows" bullets                 `- from -> to | label`
Styled like the Team Directory (paper background, serif heads, cards).
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

SWARM_PATH = "/swarm"


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(s: str) -> str:
    return re.sub(r"[*`]", "", s).strip()


def parse_nodes_md(text: str) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    extras: list[dict[str, str]] = []
    flows: list[dict[str, str]] = []
    hosts: list[str] = []
    roles: dict[str, str] = {}
    section = sub = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section, sub = line[3:].strip(), ""
            if re.fullmatch(r"[a-z0-9-]+", section):
                nodes.setdefault(section, {"id": section, "units": [], "timers": "", "notes": []})
            continue
        if line.startswith("### "):
            sub = line[4:].strip().lower()
            continue
        s = line.strip()
        if s.startswith("|") and not section:
            cells = _cells(s)
            if cells and cells[0] == "" and len(cells) > 1:
                hosts = [_plain(c) for c in cells[1:]]
            elif cells and cells[0].lower() == "role":
                for h, v in zip(hosts, cells[1:]):
                    roles[h] = _plain(v)
            continue
        if section in nodes and sub.startswith("units") and s.startswith("|"):
            cells = _cells(s)
            if len(cells) >= 2 and cells[0] not in ("Unit",) and not set(cells[0]) <= set("-: "):
                nodes[section]["units"].append(
                    {"unit": _plain(cells[0]), "port": _plain(cells[1]), "note": _plain(cells[2]) if len(cells) > 2 else ""}
                )
            continue
        if section in nodes and s.startswith("Timers:"):
            nodes[section]["timers"] = _plain(s[7:])
            continue
        if section in nodes and "backup pull" in sub and s.startswith("- "):
            nodes[section]["notes"].append(_plain(s[2:]))
            continue
        if section == "Topology" and s.startswith("- "):
            body = s[2:]
            if "flows" in sub and "->" in body:
                route, _, label = body.partition("|")
                a, _, b = route.partition("->")
                flows.append({"from": a.strip(), "to": b.strip(), "label": label.strip()})
            elif "planned" in sub or "external" in sub:
                parts = [p.strip() for p in body.split("|")]
                if len(parts) >= 2:
                    extras.append({"id": parts[0], "label": parts[1], "detail": parts[2] if len(parts) > 2 else ""})
    for h, n in nodes.items():
        n["role"] = roles.get(h, "")
        n["backup_status"] = (sub_status := next((x for x in n["notes"] if "Last run" in x), ""))
    return {"nodes": list(nodes.values()), "extras": extras, "flows": flows}


# ------------------------------------------------------------------ svg
W, H = 1200, 820


def _t(x, y, s, cls, anchor="start"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{html.escape(str(s))}</text>'


def _wrap(s: str, n: int) -> list[str]:
    out, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n and cur:
            out.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    return out + ([cur] if cur else [])


def render_svg(topo: dict[str, Any]) -> str:
    boxes: dict[str, tuple[int, int, int, int]] = {}
    parts: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img" '
        'aria-label="Swarm topology" class="eco">',
        '<defs><marker id="sw-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#7d8594"/></marker></defs>',
    ]
    # Tailscale boundary
    parts.append('<rect x="20" y="170" width="1160" height="470" rx="26" fill="#f4f6fa" '
                 'stroke="#8ea0c4" stroke-dasharray="8 6"/>')
    parts.append(_t(40, 196, "TAILSCALE TAILNET (private boundary)", "colh"))
    extras = {e["id"]: e for e in topo["extras"]}
    nodes = topo["nodes"]
    # node columns inside the tailnet
    cols = [(40, 360), (480, 320), (860, 300)]
    for i, n in enumerate(nodes[:2]):
        x, w = cols[i]
        y, h = 215, 400
        boxes[n["id"]] = (x, y, w, h)
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#fff" stroke="#1f2a44" stroke-width="1.5"/>')
        parts.append(_t(x + 16, y + 30, n["id"], "pt"))
        ly = y + 50
        for line in _wrap(n.get("role", ""), 46)[:3]:
            parts.append(_t(x + 16, ly, line, "ps"))
            ly += 17
        ly += 8
        for u in n["units"][:14]:
            port = u["port"].split(" ")[0] if u["port"] else ""
            parts.append(_t(x + 16, ly, u["unit"][:34], "ps"))
            parts.append(_t(x + w - 16, ly, port, "ps", "end"))
            ly += 17
        if len(n["units"]) > 14:
            parts.append(_t(x + 16, ly, f"+{len(n['units']) - 14} more", "lbl"))
        if n.get("backup_status"):
            for line in _wrap(n["backup_status"], 44)[:3]:
                ly += 17
                parts.append(_t(x + 16, ly, line, "lbl"))
    pi3 = extras.get("pi3-relay")
    if pi3:
        x, w = cols[2]
        y, h = 215, 180
        boxes["pi3-relay"] = (x, y, w, h)
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="16" fill="#fff" stroke="#8a919d" stroke-dasharray="6 5"/>')
        parts.append(_t(x + 16, y + 30, pi3["label"], "pt"))
        for j, line in enumerate(_wrap(pi3["detail"], 44)[:4]):
            parts.append(_t(x + 16, y + 52 + 17 * j, line, "ps"))
    # external boxes
    ext_pos = {"vercel": (40, 30, 560, 100), "internet": (640, 30, 300, 100), "r2": (810, 680, 350, 100)}
    for eid, (x, y, w, h) in ext_pos.items():
        e = extras.get(eid)
        if not e:
            continue
        boxes[eid] = (x, y, w, h)
        dashed = ' stroke-dasharray="6 5"' if "planned" in e["label"].lower() else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="#eceef1" stroke="#c9cdd4"{dashed}/>')
        parts.append(_t(x + 16, y + 28, e["label"], "pt"))
        for j, line in enumerate(_wrap(e["detail"], int(w / 7.2))[:3]):
            parts.append(_t(x + 16, y + 50 + 17 * j, line, "ps"))

    def anchor(bid: str, toward: tuple[float, float]) -> tuple[float, float]:
        x, y, w, h = boxes[bid]
        cx, cy = x + w / 2, y + h / 2
        dx, dy = toward[0] - cx, toward[1] - cy
        if dx == dy == 0:
            return cx, cy
        sx = (w / 2) / abs(dx) if dx else float("inf")
        sy = (h / 2) / abs(dy) if dy else float("inf")
        s = min(sx, sy)
        return cx + dx * s, cy + dy * s

    seen: dict[tuple[str, str], int] = {}
    for idx, f in enumerate(topo["flows"], 1):
        a, b = f["from"], f["to"]
        if a not in boxes or b not in boxes:
            continue
        k = seen.get((a, b), 0)
        seen[(a, b)] = k + 1
        bx, by, bw, bh = boxes[b]
        ax_, ay_, aw, ah = boxes[a]
        off = 26 * k
        p1 = anchor(a, (bx + bw / 2, by + bh / 2 + off))
        p2 = anchor(b, (ax_ + aw / 2, ay_ + ah / 2 + off))
        p1 = (p1[0], p1[1] + off)
        p2 = (p2[0], p2[1] + off)
        dash = ' stroke-dasharray="5 5"' if "planned" in f["label"].lower() else ""
        parts.append(f'<line x1="{p1[0]:.0f}" y1="{p1[1]:.0f}" x2="{p2[0]:.0f}" y2="{p2[1]:.0f}" '
                     f'stroke="#7d8594" stroke-width="1.6" marker-end="url(#sw-ar)"{dash}/>')
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        parts.append(f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="10" fill="#1f2a44"/>'
                     f'<text x="{mx:.0f}" y="{my + 4:.0f}" class="fn" text-anchor="middle">{idx}</text>'
                     f'<title>{html.escape(f["label"])}</title>')
    parts.append("</svg>")
    return "\n".join(parts)


CSS = """
:root{--ink:#1c2230;--muted:#5d6676;--line:#e6e8ec;--paper:#fcfbf8;--accent:#1f2a44}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 "Inter","Helvetica Neue",system-ui,-apple-system,"Segoe UI",Arial,sans-serif}
h1,h2,h3{font-family:"Noto Serif Display","Noto Serif","IBM Plex Serif",Georgia,serif;font-weight:600;letter-spacing:-.01em;margin:0}
.mast{max-width:1080px;margin:0 auto;padding:72px 32px 40px;border-bottom:1px solid var(--line)}
.kicker{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
.mast h1{font-size:56px;line-height:1.05;margin:14px 0 14px}
.dek{font-size:20px;color:var(--muted);max-width:720px;margin:0 0 28px}
main{max-width:1080px;margin:0 auto;padding:0 32px 64px}
.sec{padding:64px 0 24px;border-bottom:1px solid var(--line)}
.sechead{display:flex;align-items:baseline;gap:16px;margin-bottom:12px}
.num{font-family:Georgia,serif;font-size:14px;color:#a3a9b4;letter-spacing:.1em}
.sec h2{font-size:34px}
.lede{font-size:18px;color:#3a4252;max-width:760px;margin:0 0 32px}
.fig{margin:0 0 32px;background:#fff;border:1px solid var(--line);border-radius:20px;padding:16px;overflow-x:auto}
.eco{width:100%;min-width:760px;height:auto;display:block}
.eco .pt{font:600 15px "Inter",system-ui,sans-serif;fill:#1c2230}
.eco .ps{font:12.5px "Inter",system-ui,sans-serif;fill:#5d6676}
.eco .colh{font:600 11px "Inter",system-ui,sans-serif;fill:#8a919d;letter-spacing:.16em}
.eco .fn{font:600 11px "Inter",system-ui,sans-serif;fill:#fff}
.eco .lbl{font:italic 12px Georgia,serif;fill:#555b66}
.cards{display:grid;grid-template-columns:repeat(2,1fr);gap:22px}
.card{background:#fff;border:1px solid var(--line);border-radius:18px;padding:26px 26px 20px;border-top:5px solid var(--accent)}
.card.planned{border-top-style:dashed;border-top-color:#8a919d}
.card h3{font-size:26px;margin:4px 0 4px}
.role{margin:0 0 12px;color:#3a4252;font-size:16px}
.card ul,.card ol{margin:0;padding-left:22px}.card li{font-size:14.5px;margin:3px 0}
.src{color:var(--muted);font-size:13px;margin-top:24px}
@media(max-width:760px){.cards{grid-template-columns:1fr}.mast h1{font-size:40px}}
"""


def render_page(topo: dict[str, Any], source: str = "ops/NODES.md") -> str:
    cards = []
    for n in topo["nodes"]:
        units = "".join(
            f"<li>{html.escape(u['unit'])} <span style='color:#8a919d'>{html.escape(u['port'])}</span></li>"
            for u in n["units"]
        )
        status = f"<p class='role'><b>Backup pull:</b> {html.escape(n['backup_status'])}</p>" if n.get("backup_status") else ""
        cards.append(
            f"<div class='card'><h3>{html.escape(n['id'])}</h3><p class='role'>{html.escape(n.get('role',''))}</p>"
            f"{status}<ul>{units}</ul></div>"
        )
    for e in topo["extras"]:
        cls = "card planned" if "planned" in e["label"].lower() else "card"
        cards.append(f"<div class='{cls}'><h3>{html.escape(e['label'])}</h3><p class='role'>{html.escape(e['detail'])}</p></div>")
    flows = "".join(
        f"<li value='{i}'><b>{html.escape(f['from'])}</b> → <b>{html.escape(f['to'])}</b>: {html.escape(f['label'])}</li>"
        for i, f in enumerate(topo["flows"], 1)
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Swarm · Horizon</title>
<style>{CSS}</style></head><body>
<header class="mast"><div class="kicker">Horizon · Infrastructure</div><h1>Swarm</h1>
<p class="dek">Every node, what runs on it, and how data moves between them. Generated from {html.escape(source)} on each request.</p></header>
<main>
<section class="sec" id="map"><div class="sechead"><span class="num">01</span><h2>Topology</h2></div>
<p class="lede">Solid boxes and arrows are live. Dashed ones are planned. Numbered arrows match the Data flows list below. Everything inside the dashed frame is reachable only on the tailnet.</p>
<figure class="fig">{render_svg(topo)}</figure></section>
<section class="sec" id="nodes"><div class="sechead"><span class="num">02</span><h2>Nodes</h2></div>
<div class="cards">{''.join(cards)}</div></section>
<section class="sec" id="flows"><div class="sechead"><span class="num">03</span><h2>Data flows</h2></div>
<div class="card"><ol>{flows}</ol></div>
<p class="src">Source: {html.escape(source)}. Live health is on the Model page's Nodes card.</p></section>
</main></body></html>"""


PRIVATE_NODES_MD = Path("~/.config/horizon/nodes.private.md")


def resolve_nodes_md(public_md: Path, env: dict[str, str] | None = None) -> tuple[Path, str]:
    """Pick the source: HORIZON_NODES_MD, then the untracked private copy, then public.

    ops/NODES.md in git is public and high-level. On prism the detailed version
    lives off-repo (mode 600) and is preferred when present, so the tailnet-only
    page keeps full detail without it ever entering git.
    """
    import os

    env = os.environ if env is None else env
    override = (env.get("HORIZON_NODES_MD") or "").strip()
    if override and Path(override).expanduser().is_file():
        return Path(override).expanduser(), "private overlay"
    private = PRIVATE_NODES_MD.expanduser()
    if private.is_file():
        return private, "private overlay"
    return public_md, "ops/NODES.md"


def swarm_html(nodes_md: Path, env: dict[str, str] | None = None) -> str | None:
    path, label = resolve_nodes_md(nodes_md, env)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return render_page(parse_nodes_md(text), source=label)
