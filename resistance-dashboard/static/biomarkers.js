/* FitDash · Blood-panel biomarkers tab + conditional Today card.
 * Data: dashboard payload `biomarkers` (rt_dashboard/biomarkers_view.py).
 * Flags say "review", never diagnose. Caveats are annotations, not flags.
 */
(function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v) => {
    if (v == null || v === "" || isNaN(Number(v))) return "—";
    const n = Number(v);
    return Math.abs(n) >= 100 ? n.toFixed(0) : String(Number(n.toFixed(2)));
  };
  let last = null;
  let pendingBody = null;

  function rangeBar(m) {
    const lo = m.latest.ref_low, hi = m.latest.ref_high, v = m.latest.value;
    if (v == null || (lo == null && hi == null)) return `<div class="bm-range bm-range-none">no printed range</div>`;
    const a = lo != null ? Number(lo) : 0;
    const b = hi != null ? Number(hi) : Number(lo) * 2 || 1;
    const span = b - a || 1;
    const min = a - span * 0.35, max = b + span * 0.35;
    const pct = (x) => Math.max(0, Math.min(100, ((x - min) / (max - min)) * 100));
    return `<div class="bm-range" role="img" aria-label="${esc(m.name)} ${esc(m.latest.value_text)} vs range ${fmt(lo)}–${fmt(hi)}">
      <span class="bm-range-ok" style="left:${pct(a)}%;width:${pct(b) - pct(a)}%"></span>
      <span class="bm-range-dot bm-${esc(m.status)}" style="left:${pct(Number(v))}%"></span>
    </div><div class="bm-range-lbl muted">${fmt(lo)}–${fmt(hi)} ${esc(m.unit)}</div>`;
  }

  function spark(series) {
    const pts = (series || []).filter((p) => p.value != null);
    if (pts.length < 2) return `<span class="bm-spark-empty muted" title="needs 2+ panels">·</span>`;
    const w = 72, h = 22, vals = pts.map((p) => Number(p.value));
    const mn = Math.min(...vals), mx = Math.max(...vals), r = mx - mn || 1;
    const xy = vals.map((v, i) => `${((i / (vals.length - 1)) * (w - 4) + 2).toFixed(1)},${(h - 2 - ((v - mn) / r) * (h - 4)).toFixed(1)}`);
    return `<svg class="bm-spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-hidden="true"><polyline fill="none" stroke="currentColor" stroke-width="1.6" points="${xy.join(" ")}"/></svg>`;
  }

  function delta(m) {
    if (m.warning) return `<span class="bm-delta muted">${esc(m.warning)}</span>`;
    if (!m.delta) return `<span class="bm-delta muted">first result</span>`;
    const d = Number(m.delta.abs), sign = d > 0 ? "+" : d < 0 ? "−" : "±";
    const pct = m.delta.pct == null ? "" : ` (${sign}${Math.abs(m.delta.pct).toFixed(0)}%)`;
    return `<span class="bm-delta ${d > 0 ? "bm-up" : d < 0 ? "bm-down" : ""}">${sign}${fmt(Math.abs(d))}${pct} <span class="muted">vs ${esc(m.delta.since)}</span></span>`;
  }

  function markerCard(m) {
    const flags = (m.flags || []).map((f) => `<li class="bm-flag">⚑ ${esc(f)}</li>`).join("");
    const cav = (m.caveats || []).map((c) => `<li class="bm-caveat">ⓘ ${esc(c)}</li>`).join("");
    const lift = m.marker === "ck" && m.days_since_hard_session != null
      ? `<span class="bm-lift muted">${esc(m.days_since_hard_session)}d since last hard lift</span>` : "";
    return `<article class="bm-marker${m.flags && m.flags.length ? " bm-flagged" : ""}" data-marker="${esc(m.marker)}">
      <header class="bm-marker-head">
        <span class="bm-name">${esc(m.name)}${(m.caveats || []).length ? ` <span class="bm-cav-ico" title="${esc(m.caveats.join(" "))}">ⓘ</span>` : ""}</span>
        <span class="bm-val bm-${esc(m.status)}">${esc(m.latest.value_text != null ? m.latest.value_text : fmt(m.latest.value))} <small>${esc(m.unit)}</small></span>
      </header>
      ${rangeBar(m)}
      <div class="bm-meta">${delta(m)} ${spark(m.series)} ${lift}</div>
      ${flags || cav ? `<ul class="bm-notes">${flags}${cav}</ul>` : ""}
    </article>`;
  }

  function ctxLine(p) {
    const c = p.context || {}, auto = c._auto || [];
    const bits = [];
    const add = (k, label, v) => {
      if (v == null || v === "") return;
      bits.push(`${esc(label)}: <strong>${esc(v === true ? "yes" : v === false ? "no" : v)}</strong>${auto.includes(k) ? ' <span class="bm-auto">FitDash</span>' : ""}`);
    };
    add("draw_time", "drawn", c.draw_time);
    add("fasted", "fasted", c.fasted);
    add("fast_hours", "fast h", c.fast_hours);
    add("creatine", "creatine", c.creatine);
    add("biotin", "biotin", c.biotin);
    add("alcohol_48h", "alcohol 48h", c.alcohol_48h);
    add("recent_illness", "illness", c.recent_illness);
    add("days_since_hard_session", "days since hard session", c.days_since_hard_session);
    add("days_since_heavy_legs", "days since heavy legs/DL", c.days_since_heavy_legs);
    add("phase", "phase", c.phase);
    add("bodyweight_lbs", "bodyweight lb", c.bodyweight_lbs);
    return bits.join(" · ");
  }

  function renderTab(bm) {
    const box = $("biomarkers-body");
    if (!box) return;
    if (bm.error) {
      box.innerHTML = `<p class="muted">${esc(bm.error)}</p>`;
    } else if (!bm.has_data) {
      box.innerHTML = `<p class="muted">No blood panels yet. Import a Rythm PDF or enter values below.</p>`;
    } else {
      const panels = bm.panels || [];
      const lp = panels[panels.length - 1] || {};
      box.innerHTML = `
        <p class="bm-sub muted">Latest draw <strong>${esc(bm.latest_draw)}</strong>${lp.lab ? ` · ${esc(lp.lab)}` : ""} · ${panels.length} panel${panels.length === 1 ? "" : "s"} · ${bm.flagged_count || 0} to review</p>
        <p class="bm-ctx muted">${ctxLine(lp)}</p>
        ${(bm.groups || []).map((g) => `<h3 class="bm-group">${esc(g.label)}</h3><div class="bm-grid">${g.markers.map(markerCard).join("")}</div>`).join("")}`;
    }
    const foot = $("biomarkers-footnote");
    if (foot) foot.textContent = bm.footnote || "Thresholds are rule-of-thumb defaults, not clinical rules.";
    const sel = $("bm-ctx-panel");
    if (sel) {
      sel.innerHTML = (bm.panels || []).slice().reverse().map((p) => `<option value="${esc(p.panel_id)}">${esc(p.draw_date)} · ${esc(p.lab || p.panel_id)}</option>`).join("");
    }
  }

  function renderToday(bm) {
    const card = $("biomarkers-today-card");
    if (!card) return;
    const t = (bm && bm.today_card) || {};
    if (!t.show) { card.hidden = true; card.innerHTML = ""; return; }
    card.hidden = false;
    const items = (t.flagged || []).map((f) => `<li><strong>${esc(f.name)}</strong> — ${esc(f.flag)}</li>`).join("");
    card.innerHTML = `<h2>Blood panel · ${esc(t.reason || "")}</h2>
      <p class="muted" style="margin:0.2rem 0 0.4rem;font-size:0.85rem">Latest draw ${esc(t.latest_draw)}</p>
      ${items ? `<ul class="bm-today-list">${items}</ul>` : ""}
      <button type="button" class="btn" id="bm-open-tab">Open Labs tab</button>
      <p class="muted bm-foot">${esc(bm.footnote || "")}</p>`;
    const b = $("bm-open-tab");
    if (b) b.onclick = () => { const tb = document.querySelector('[data-m-tab="biomarkers"]'); if (tb) tb.click(); };
  }

  function render(data) {
    const bm = (data && data.biomarkers) || { has_data: false, today_card: { show: false }, groups: [] };
    last = bm;
    renderTab(bm);
    renderToday(bm);
  }

  // ---- entry: manual / CSV / JSON / PDF with dry-run preview -------------
  function ctxFromForm() {
    const ctx = {};
    document.querySelectorAll("#bm-entry-form [data-ctx]").forEach((el) => {
      const k = el.getAttribute("data-ctx");
      const v = el.type === "checkbox" ? (el.indeterminate ? "" : el.checked) : el.value;
      if (v !== "" && v != null) ctx[k] = el.getAttribute("data-yn") ? v === "yes" : v;
    });
    return ctx;
  }

  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), credentials: "same-origin" });
    const j = await r.json().catch(() => ({ ok: false, error: `HTTP ${r.status}` }));
    if (!r.ok || !j.ok) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  function showPreview(pv) {
    const box = $("bm-preview");
    if (!box) return;
    const rows = pv.rows.map((r) => `<tr class="${r.duplicate ? "bm-dup" : ""}"><td>${esc(r.marker_name)}</td><td>${esc(r.value_text)}</td><td>${esc(r.unit)}</td><td>${fmt(r.ref_low)}–${fmt(r.ref_high)}${r.ref_text ? " " + esc(r.ref_text) : ""}</td><td>${r.duplicate ? "duplicate — skipped" : "new"}</td></tr>`).join("");
    box.innerHTML = `<p><strong>Preview</strong> · ${esc(pv.panel.lab)} · draw ${esc(pv.panel.draw_date)} · ${pv.new_count} new, ${pv.duplicate_count} duplicate</p>
      <div class="bm-table-wrap"><table class="bm-table"><thead><tr><th>Marker</th><th>Value</th><th>Unit</th><th>Range</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>
      ${(pv.warnings || []).map((w) => `<p class="muted">${esc(w)}</p>`).join("")}`;
    const c = $("bm-commit");
    if (c) c.hidden = !pv.new_count;
  }

  async function buildBody() {
    const kind = ($("bm-kind") || {}).value || "manual";
    const ctx = ctxFromForm();
    if (kind === "pdf") {
      const f = $("bm-file") && $("bm-file").files[0];
      if (!f) throw new Error("Choose a PDF first.");
      const buf = new Uint8Array(await f.arrayBuffer());
      let bin = "";
      for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
      return { kind: "pdf", filename: f.name, content_base64: btoa(bin), context: ctx };
    }
    const text = ($("bm-text") || {}).value || "";
    const draw = ($("bm-draw-date") || {}).value || "";
    const lab = ($("bm-lab") || {}).value || "manual";
    if (kind === "json") return { kind: "json", data: text, context: ctx };
    // manual + csv share CSV text: marker,value,unit,ref_low,ref_high
    const csv = /^\s*marker\s*,/i.test(text) ? text : "marker,value,unit,ref_low,ref_high\n" + text;
    return { kind: "csv", csv, draw_date: draw, lab, context: ctx };
  }

  function bindEntry() {
    const form = $("bm-entry-form");
    if (!form || form.dataset.bound) return;
    form.dataset.bound = "1";
    const status = $("bm-status");
    const kindSel = $("bm-kind");
    const sync = () => {
      const k = kindSel.value;
      $("bm-file-row").hidden = k !== "pdf";
      $("bm-text-row").hidden = k === "pdf";
      $("bm-manual-row").hidden = k === "pdf" || k === "json";
    };
    kindSel.addEventListener("change", sync);
    sync();
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      status.textContent = "Previewing…";
      try {
        pendingBody = await buildBody();
        const j = await post("/api/biomarkers/import", Object.assign({}, pendingBody, { commit: false }));
        showPreview(j.preview);
        status.textContent = "Dry run — nothing saved yet.";
      } catch (e) { status.textContent = String(e.message || e); }
    });
    $("bm-commit").addEventListener("click", async () => {
      if (!pendingBody) return;
      status.textContent = "Saving…";
      try {
        const j = await post("/api/biomarkers/import", Object.assign({}, pendingBody, { commit: true }));
        status.textContent = `Saved ${j.result.inserted} marker(s); ${j.result.skipped.length} duplicate(s) skipped.`;
        $("bm-commit").hidden = true;
        pendingBody = null;
        const rb = $("btn-refresh");
        if (rb) rb.click();
      } catch (e) { status.textContent = String(e.message || e); }
    });
    const cbtn = $("bm-ctx-save");
    if (cbtn) cbtn.addEventListener("click", async () => {
      const pid = ($("bm-ctx-panel") || {}).value;
      if (!pid) return;
      try {
        await post("/api/biomarkers/context", { panel_id: pid, context: ctxFromForm() });
        status.textContent = "Draw context saved.";
        const rb = $("btn-refresh");
        if (rb) rb.click();
      } catch (e) { status.textContent = String(e.message || e); }
    });
  }

  window.FitDashBiomarkers = { render, bindEntry, _last: () => last };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", bindEntry);
  else bindEntry();
})();
