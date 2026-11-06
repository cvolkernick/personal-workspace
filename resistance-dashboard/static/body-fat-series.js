/**
 * FitDash Trends: align Google Health body-fat % onto the weight chart dates.
 *
 * Last recorded_at that civil day wins. Missing days stay null (never 0).
 * Readings outside 0..100 are dropped. Does not invent a percentage from weight.
 */
(function (root) {
  "use strict";

  function civilDay(value) {
    var day = String(value || "").slice(0, 10);
    return /^\d{4}-\d{2}-\d{2}$/.test(day) ? day : "";
  }

  function percentageOf(row) {
    if (!row) return null;
    var raw = row.percentage != null ? row.percentage : row.body_fat_pct;
    var pct = Number(raw);
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) return null;
    return Math.round(pct * 100) / 100;
  }

  /** {date: {date, percentage, recorded_at}} — later timestamp wins. */
  function lastByDate(samples) {
    var by = {};
    (samples || []).forEach(function (row) {
      var date = civilDay(row && row.date);
      var pct = percentageOf(row);
      if (!date || pct == null) return;
      var ts = String((row && row.recorded_at) || "");
      var prev = by[date];
      if (!prev || ts >= prev.recorded_at) {
        by[date] = { date: date, percentage: pct, recorded_at: ts };
      }
    });
    return by;
  }

  function alignToLabels(labels, samples) {
    var by = lastByDate(samples);
    return (labels || []).map(function (label) {
      var row = by[civilDay(label)];
      return row ? row.percentage : null;
    });
  }

  function latest(samples) {
    var by = lastByDate(samples);
    var rows = Object.keys(by)
      .map(function (day) {
        return by[day];
      })
      .sort(function (a, b) {
        var cmp = a.date < b.date ? -1 : a.date > b.date ? 1 : 0;
        if (cmp) return cmp;
        return a.recorded_at < b.recorded_at ? -1 : a.recorded_at > b.recorded_at ? 1 : 0;
      });
    return rows.length ? rows[rows.length - 1] : null;
  }

  var api = {
    lastByDate: lastByDate,
    alignToLabels: alignToLabels,
    latest: latest,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashBodyFat = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
