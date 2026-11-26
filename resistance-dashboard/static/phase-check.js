/**
 * FF-4 Phase check card (Trends → Guardrails, in place of the Progression
 * tile) and the matching More → Coach line. Display only.
 *
 * Every number comes from the server's coach_card.phase_check block, which is
 * a pure read of body_fat_phase.evaluate() (hits, counted, medians) and of
 * maintenance_targets() / ramp_targets() (switch calories). Nothing here
 * computes the rule or a target. A missing value shows "—", never a guess.
 */
(function (root) {
  "use strict";

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function num(value) {
    if (value == null || value === "" || typeof value === "boolean") return null;
    var n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function pct1(value) {
    var n = num(value);
    return n == null ? "—" : n.toFixed(1);
  }

  function bound(value) {
    var n = num(value);
    if (n == null) return "—";
    return n % 1 === 0 ? String(n) : n.toFixed(1);
  }

  function kcal(value) {
    var n = num(value);
    return n == null ? "—" : Math.round(n).toLocaleString("en-US");
  }

  function grams(value) {
    var n = num(value);
    return n == null ? "—" : String(Math.round(n));
  }

  function md(iso) {
    var s = String(iso || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return "";
    return Number(s.slice(5, 7)) + "/" + Number(s.slice(8, 10));
  }

  function mdy(iso) {
    var s = String(iso || "").slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return "—";
    return md(s) + "/" + s.slice(2, 4);
  }

  function titleCase(text) {
    var s = String(text || "");
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : "";
  }

  function phaseName(pc) {
    if (pc && pc.state === "switched" && pc.last_switch && pc.last_switch.phase) {
      return titleCase(pc.last_switch.phase);
    }
    return titleCase(pc && pc.phase) || "Cut";
  }

  function symbol(pc) {
    return pc && pc.direction === ">=" ? "≥" : "≤";
  }

  function weightChip(rate) {
    var r = rate || {};
    var two = num(r.two_week_pct);
    var band = Array.isArray(r.band) && r.band.length === 2 ? r.band : null;
    var bandText = band ? bound2(band[0]) + "–" + bound2(band[1]) + " band" : "band";
    var pos = r.position === "below" || r.position === "above" ? r.position + " " + bandText
      : r.position === "inside" ? "inside " + bandText : "";
    var tone = r.position === "inside" ? "pc-g" : "pc-y";
    var verb = two == null ? "2-wk rate —" : (two >= 0 ? "losing " : "gaining ") +
      Math.abs(two).toFixed(2) + "%/wk (2-wk)";
    return '<span class="pc-chip"><span class="k">Weight</span> <span class="' + tone + '">' +
      esc(verb) + "</span>" + (pos ? " · " + esc(pos) : "") + "</span>";
  }

  function bound2(value) {
    var n = num(value);
    if (n == null) return "—";
    return String(Number(n.toFixed(2)));
  }

  function liftsChip(lifts) {
    var l = lifts || {};
    var status = l.status;
    var reason = String(l.reason || "");
    var text = "not enough data";
    var tone = "";
    if (status === "green") {
      text = /moved up/i.test(reason) ? "moving up" : "holding";
      tone = "pc-g";
    } else if (status === "yellow") {
      text = "down 2 wks";
      tone = "pc-y";
    } else if (status === "red") {
      text = "down 3 wks";
      tone = "pc-r";
    }
    return '<span class="pc-chip"><span class="k">Main lifts</span> <span class="' + tone + '">' +
      esc(text) + "</span></span>";
  }

  function labsChip(labs) {
    var date = labs && labs.date;
    return '<span class="pc-chip"><span class="k">Labs</span> ' + esc(mdy(date)) + "</span>";
  }

  function macrosText(sw) {
    if (!sw) return "";
    return "(" + grams(sw.protein_g) + " P · " + grams(sw.fat_g) + " F · ~" + grams(sw.carbs_g) + " C)";
  }

  function autoBox(pc) {
    var sym = symbol(pc);
    var target = bound(pc.endpoint) + "%";
    var to = pc.switch_to || (pc.phase === "bulk" ? "cut" : "bulk");
    var held = num(pc.hits_needed) == null ? 12 : num(pc.hits_needed);
    var counted = num(pc.counted_needed) == null ? 14 : num(pc.counted_needed);
    var how = pc.applies_automatically ? "<b>Auto-switch to " + esc(to) + "</b>" : "<b>Switch to " + esc(to) + "</b> (auto-switch off)";
    var line1 = how + " when " + esc(held) + " of " + esc(counted) + " days hold.";
    if (pc.state === "switched") {
      var ls = pc.last_switch || {};
      var t = ls.targets || {};
      return '<div class="pc-auto"><b>Switched to ' + esc(phaseName(pc).toLowerCase()) + "</b> on " +
        esc(md(ls.date) || "—") + ". Now <b>" + esc(kcal(t.calories)) + " kcal</b> " +
        esc(macrosText({ protein_g: t.protein_g, fat_g: t.fat_g, carbs_g: t.carbs_g })) + ".</div>";
    }
    var earliest = pc.earliest_switch_label
      ? " Earliest <b>" + esc(pc.earliest_switch_label) + "</b> if every day stays " + esc(sym + target) + "."
      : "";
    var sw = pc.on_switch;
    var line2;
    if (pc.phase === "bulk") {
      line2 = sw
        ? "On switch: calories go back to your stored cut <b>" + esc(kcal(sw.kcal)) + " kcal</b> " + esc(macrosText(sw)) + "."
        : "On switch: no stored cut targets yet, so FitDash holds and writes nothing.";
    } else if (sw) {
      var ramp = pc.ramp || {};
      var rampText = num(ramp.kcal) == null ? ""
        : ", then <b>+" + esc(kcal(ramp.plus_kcal)) + " → " + esc(kcal(ramp.kcal)) + "</b> after " +
          esc(num(ramp.after_days) == null ? 14 : ramp.after_days) + " days";
      line2 = "On switch: calories go to maintenance <b>" + esc(kcal(sw.kcal)) + " kcal</b> " +
        esc(macrosText(sw)) + rampText + ".";
    } else {
      line2 = "On switch: maintenance calories need a TDEE estimate first, so FitDash holds and writes nothing.";
    }
    return '<div class="pc-auto">' + line1 + earliest + "<br>" + line2 + "</div>";
  }

  /** Full-width Guardrails tile. Empty string when phase_check is missing. */
  function cardHtml(pc) {
    if (!pc || typeof pc !== "object") return "";
    var sym = symbol(pc);
    var target = bound(pc.endpoint);
    var state = String(pc.state || "far");
    var stateLabel = pc.state_label || titleCase(state.replace(/_/g, " "));
    var avg = num(pc.bf_avg_14d) == null ? "—" : pct1(pc.bf_avg_14d) + "%";
    var med7 = num(pc.median_7d) == null ? "—" : pct1(pc.median_7d) + "%";
    var big = num(pc.median_14d) == null ? "—" : pct1(pc.median_14d) + "%";
    var under = sym === "≥" ? "at or over" : "at or under";
    var tick = pc.today_past ? " ✓" : "";
    var hits = Math.max(0, Math.floor(num(pc.hits) || 0));
    var need = Math.max(1, Math.floor(num(pc.hits_needed) || 12));
    var counted = Math.max(0, Math.floor(num(pc.counted) || 0));
    var countedNeed = Math.max(1, Math.floor(num(pc.counted_needed) || 14));
    var segs = "";
    for (var i = 0; i < need; i++) {
      segs += '<div class="pc-seg' + (i < hits ? " on" : "") + '"></div>';
    }
    var barLabel = state === "not_enough_data"
      ? "<span>Counted days: <b>" + esc(counted) + " of " + esc(countedNeed) + "</b></span>" +
        "<span>(needs ≥" + esc(pc.min_readings || 4) + " readings in " + esc(pc.window_days || 7) + " days)</span>"
      : "<span>Days held: <b>" + esc(hits) + " of " + esc(need) + "</b></span><span>(last " + esc(countedNeed) + " days)</span>";
    return '<article class="guardrail-tile phase-check-tile" data-guardrail="phase-check" aria-label="Phase check">' +
      '<div class="pc-hd"><p class="pc-title">Phase check</p><div class="pc-pills">' +
      '<span class="pc-pill">' + esc(phaseName(pc)) + "</span>" +
      '<span class="pc-state pc-state-' + esc(state) + '">' + esc(stateLabel) + "</span></div></div>" +
      '<p class="pc-big">' + esc(big) + " <small>14-day body fat median</small></p>" +
      '<p class="pc-sub">7-day median ' + esc(med7) + " · 14-day avg (chart line) " + esc(avg) + "</p>" +
      '<p class="pc-tgt">Target <b>' + esc(target) + "%</b> — " + esc(under) + esc(tick) + "</p>" +
      '<div class="pc-barlbl">' + barLabel + "</div>" +
      '<div class="pc-bar" role="img" aria-label="' + esc(hits + " of " + need + " days held") + '">' + segs + "</div>" +
      autoBox(pc) +
      '<div class="pc-chips">' + weightChip(pc.weight_rate) + liftsChip(pc.lifts) + labsChip(pc.labs) + "</div>" +
      '<p class="pc-cav">' + esc(pc.caveat || "") + "</p>" +
      "</article>";
  }

  /** One More → Coach line, matching the card. */
  function coachLine(pc) {
    if (!pc || typeof pc !== "object") return "";
    var name = phaseName(pc);
    var to = pc.switch_to || (pc.phase === "bulk" ? "cut" : "bulk");
    if (pc.state === "switched") {
      var ls = pc.last_switch || {};
      var t = ls.targets || {};
      return name + " · switched " + (md(ls.date) || "—") + " · " + kcal(t.calories) + " kcal";
    }
    var bf = num(pc.median_14d) == null ? "—" : pct1(pc.median_14d) + "%";
    if (pc.state === "not_enough_data") {
      return name + " · BF " + bf + " · " + (num(pc.counted) || 0) + " of " + (pc.counted_needed || 14) +
        " counted days · needs ≥" + (pc.min_readings || 4) + " readings in " + (pc.window_days || 7) + " days";
    }
    var line = name + " · BF " + bf + " · held " + (num(pc.hits) || 0) + "/" + (pc.hits_needed || 12);
    var when = md(pc.earliest_switch_date);
    if (when) {
      line += " · " + (pc.applies_automatically ? "auto-switch" : "switch") + " to " + to + " ~" + when + " earliest";
    }
    return line;
  }

  var api = { cardHtml: cardHtml, coachLine: coachLine, esc: esc };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.FitDashPhaseCheck = api;
})(typeof window !== "undefined" ? window : globalThis);
