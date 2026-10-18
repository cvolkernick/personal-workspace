/**
 * FitDash weekly proper-set tile (#1193).
 * Renders ``muscle_sets_week`` from /api/dashboard. Pure string output so it
 * can be tested in node; app.js only drops the HTML into the card.
 */
(function (root) {
  "use strict";

  var TIER_CLASS = { red: "red", green: "green", blue: "blue" };
  var ROLE_LABEL = { priority: "Priority", middle: "Growth", deprioritized: "Floor" };

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmt(n) {
    var v = Number(n);
    if (!isFinite(v)) return "0";
    return Math.abs(v - Math.round(v)) < 1e-9 ? String(Math.round(v)) : v.toFixed(1);
  }

  function targetLabel(target) {
    if (!target) return "";
    var lo = Number(target.low);
    var hi = Number(target.high);
    var range = lo === hi ? String(lo) : lo + "-" + hi + (target.stretch ? "+" : "");
    return (ROLE_LABEL[target.role] || "") + " " + range;
  }

  function trendLabel(trend) {
    if (!trend || trend.avg == null) return "no history";
    var arrow = trend.direction === "up" ? " ↑" : trend.direction === "down" ? " ↓" : "";
    return "4w avg " + fmt(trend.avg) + arrow;
  }

  function barPct(sets, target) {
    var top = Math.max(Number(target && target.high) || 0, Number(sets) || 0, 1);
    return Math.max(0, Math.min(100, ((Number(sets) || 0) / top) * 100));
  }

  function groupRow(key, row) {
    var tier = TIER_CLASS[row.tier] || "red";
    return (
      '<div class="muscle-set-row" data-group="' + esc(key) + '" data-tier="' + tier + '">' +
      '<span class="muscle-set-name">' + esc(row.label || key) + "</span>" +
      '<span class="muscle-set-bar"><span class="muscle-set-fill ' + tier +
      '" style="width:' + barPct(row.proper_sets, row.target).toFixed(0) + '%"></span></span>' +
      '<span class="muscle-set-value">' + fmt(row.proper_sets) + "</span>" +
      '<span class="muscle-set-meta">' + esc(targetLabel(row.target)) + " · " +
      esc(trendLabel(row.trend_4w)) + "</span>" +
      "</div>"
    );
  }

  function budgetHtml(budget) {
    if (!budget) return "";
    var cap = budget.cap == null ? "no cap yet (not enough history)" : "cap " + fmt(budget.cap) + " proper sets";
    var src = budget.cap_source === "manual_override" ? " (manual)" : budget.cap_held ? " (held)" : "";
    var html = '<p class="muscle-set-budget">Weekly ' + esc(cap + src) + " · targets need " +
      fmt(budget.planned_total) + "</p>";
    if (budget.warning && budget.warning.message) {
      html += '<p class="muscle-set-warn" role="alert">' + esc(budget.warning.message) + "</p>";
    }
    return html;
  }

  function coachHtml(coach) {
    if (!coach || !coach.active || !(coach.flags || []).length) return "";
    var items = coach.flags
      .map(function (f) {
        return "<li>" + esc(f.message) + "</li>";
      })
      .join("");
    return '<div class="muscle-set-coach"><p class="muscle-set-budget">Thursday check (suggestions only)</p><ul>' +
      items + "</ul></div>";
  }

  function tileHtml(week) {
    if (!week || !week.muscle_sets) {
      return '<p class="guardrail-note">Not enough data.</p>';
    }
    var order = Object.keys(week.muscle_sets);
    var rows = order.map(function (k) {
      return groupRow(k, week.muscle_sets[k]);
    });
    var meta = week.muscle_sets_meta || {};
    var head = meta.week_start
      ? '<p class="muscle-set-budget">Week ' + esc(meta.week_start) + " to " + esc(meta.week_end) +
        " · " + fmt(meta.proper_set_total) + " credited proper sets</p>"
      : "";
    return head + '<div class="muscle-set-grid">' + rows.join("") + "</div>" +
      budgetHtml(week.muscle_budget) + coachHtml(week.muscle_coach);
  }

  var api = { tileHtml: tileHtml, groupRow: groupRow, targetLabel: targetLabel, trendLabel: trendLabel, fmt: fmt };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.FitDashMuscleSets = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
