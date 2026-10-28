/**
 * FitDash Trends: daily steps (the cut coach's lever). Plan 2026-10-26.
 *
 * Reads GET /api/agent/steps_history (fitdash.steps_history.v1). Bars = daily
 * steps (null = not logged, no bar, never 0). Line = 7-day rolling average
 * (null = gap). Dashed line = coach step target only when non-null; else a
 * chip says the coach hasn't used the steps lever. Today is partial: lighter
 * bar, "so far" tooltip. Mon-Sun weeks; ticks on Mondays. Read-only.
 */
(function (root) {
  "use strict";

  var RANGES = [4, 8, 13];
  var DEFAULT_WEEKS = 8;
  var COLORS = { bar: "#3d9cf0", barToday: "rgba(61,156,240,0.35)", roll: "#f0b429", target: "#5ce1a8" };
  var NO_TARGET = "No step target yet (coach hasn't used the steps lever)";
  var chart = null;
  var currentWeeks = DEFAULT_WEEKS;

  function isMonday(iso) {
    var p = String(iso).split("-");
    return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay() === 1;
  }

  function fmt(n) {
    return n == null ? "—" : Math.round(n).toLocaleString("en-US");
  }

  function lastCompletedWeek(payload) {
    var weeks = (payload && payload.weeks) || [];
    for (var i = weeks.length - 1; i >= 0; i--) if (weeks[i].complete) return weeks[i];
    return null;
  }

  function buildModel(payload) {
    var days = (payload && payload.days) || [];
    var target = payload && payload.step_target ? payload.step_target.value : null;
    var model = {
      labels: days.map(function (d) { return d.date; }),
      bars: days.map(function (d) { return d.steps == null ? null : d.steps; }),
      barColors: days.map(function (d) { return d.partial ? COLORS.barToday : COLORS.bar; }),
      partial: days.map(function (d) { return !!d.partial; }),
      rolling: days.map(function (d) { return d.rolling_7d == null ? null : d.rolling_7d; }),
      mondays: days.map(function (d, i) { return isMonday(d.date) ? i : -1; }).filter(function (i) { return i >= 0; }),
      hasTarget: target != null,
      target: target,
      targetLine: target != null ? days.map(function () { return target; }) : null,
      targetLabel: target != null ? "Coach target " + fmt(target) : null,
      targetChip: target != null ? null : NO_TARGET,
      weeks: Math.max(1, Math.round(days.length / 7)),
    };
    var wk = lastCompletedWeek(payload);
    model.weekChip = wk
      ? { value: fmt(wk.avg_steps), sub: wk.step_days + "/7 days · week of " + wk.week_start + (wk.steps_used ? "" : " (not used: <5 days)") }
      : { value: "—", sub: "no completed week" };
    return model;
  }

  function datasets(model) {
    var ds = [
      { type: "bar", label: "Daily steps", data: model.bars, backgroundColor: model.barColors, borderWidth: 0, order: 3 },
      { type: "line", label: "7d rolling avg", data: model.rolling, borderColor: COLORS.roll, borderWidth: 2,
        pointRadius: 0, spanGaps: false, tension: 0.2, fill: false, order: 1 },
    ];
    if (model.hasTarget) {
      ds.push({ type: "line", label: model.targetLabel, data: model.targetLine, borderColor: COLORS.target,
        borderDash: [6, 4], borderWidth: 1.5, pointRadius: 0, fill: false, order: 2 });
    }
    return ds;
  }

  function $(id) { return root.document ? root.document.getElementById(id) : null; }

  function paint(payload) {
    var model = buildModel(payload);
    var title = $("steps-title");
    if (title) title.textContent = "Daily steps · " + currentWeeks + " wk";
    if ($("steps-week-value")) $("steps-week-value").textContent = model.weekChip.value;
    if ($("steps-week-sub")) $("steps-week-sub").textContent = model.weekChip.sub;
    var tchip = $("steps-target-chip");
    if (tchip) {
      $("steps-target-value").textContent = model.hasTarget ? fmt(model.target) : "—";
      $("steps-target-sub").textContent = model.hasTarget ? "coach target" : model.targetChip;
    }
    var note = $("steps-trend-note");
    if (note) {
      var cov = (payload && payload.coverage) || {};
      note.textContent = cov.days_missing ? cov.days_missing + " day(s) with no steps logged (shown as gaps)." : "";
    }
    var canvas = $("chart-steps");
    if (!canvas || typeof root.Chart === "undefined") return model;
    if (chart) { chart.destroy(); chart = null; }
    var monday = {};
    model.mondays.forEach(function (i) { monday[i] = true; });
    chart = new root.Chart(canvas, {
      type: "bar",
      data: { labels: model.labels, datasets: datasets(model) },
      options: {
        responsive: true, maintainAspectRatio: false, animation: false,
        plugins: {
          legend: { labels: { color: "#c9d1d9", boxWidth: 12 } },
          tooltip: { callbacks: { label: function (ctx) {
            var v = ctx.parsed.y;
            if (v == null) return ctx.dataset.label + ": not logged";
            var s = ctx.dataset.label + ": " + fmt(v);
            if (ctx.datasetIndex === 0 && model.partial[ctx.dataIndex]) s += " (so far)";
            return s;
          } } },
        },
        scales: {
          x: { ticks: { color: "#8b9bb4", autoSkip: false, maxRotation: 0,
                 callback: function (v, i) { return monday[i] ? String(model.labels[i]).slice(5) : ""; } },
               grid: { color: function (c) { return monday[c.index] ? "rgba(139,155,180,0.35)" : "transparent"; } } },
          y: { beginAtZero: true, ticks: { color: "#8b9bb4" }, grid: { color: "rgba(139,155,180,0.12)" } },
        },
      },
    });
    return model;
  }

  function load(weeks) {
    if (weeks) currentWeeks = weeks;
    if (!root.fetch) return Promise.resolve(null);
    var btns = root.document ? root.document.querySelectorAll("[data-steps-weeks]") : [];
    Array.prototype.forEach.call(btns, function (b) {
      b.setAttribute("aria-pressed", String(+b.getAttribute("data-steps-weeks") === currentWeeks));
      b.classList.toggle("primary", +b.getAttribute("data-steps-weeks") === currentWeeks); // active range = highlighted button
    });
    return root.fetch("/api/agent/steps_history?weeks=" + currentWeeks, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (p) {
        if (p && p.ok) return paint(p);
        var note = $("steps-trend-note");
        if (note) note.textContent = "Steps history unavailable.";
        return null;
      })
      .catch(function () { return null; });
  }

  function init() {
    if (!$("steps-trend-card")) return;
    var btns = root.document ? root.document.querySelectorAll("[data-steps-weeks]") : [];
    Array.prototype.forEach.call(btns, function (b) {
      b.addEventListener("click", function () { load(+b.getAttribute("data-steps-weeks")); });
    });
    load(DEFAULT_WEEKS);
  }

  var api = {
    RANGES: RANGES, DEFAULT_WEEKS: DEFAULT_WEEKS, COLORS: COLORS, NO_TARGET: NO_TARGET,
    isMonday: isMonday, buildModel: buildModel, datasets: datasets, paint: paint,
    refresh: function () { return $("steps-trend-card") ? load() : Promise.resolve(null); },
  };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) {
    root.FitDashTrendsSteps = api;
    if (root.document) {
      if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", init);
      else init();
    }
  }
})(typeof window !== "undefined" ? window : null);
