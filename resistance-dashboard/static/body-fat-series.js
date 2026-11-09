/**
 * FitDash Trends: body-fat % on the weight chart.
 *
 * Raw points are every valid logged measurement (0..100), at its own civil
 * date. Same-day readings stay separate. Readings outside 0..100 are dropped.
 *
 * The overlay is a trailing 7-calendar-day arithmetic mean: the window
 * ending on day D is [D-6, D] inclusive. Only valid logged measurements in
 * that window count. Missing days are left out — they are not stored as 0,
 * and no percentage is invented. An empty window is null (do not plot 0).
 * One measurement in the window is that measurement. This is not the mean
 * of the last seven readings.
 *
 * Civil dates are the YYYY-MM-DD already stored on each sample (America/New_York
 * at ingest). Day math is UTC calendar arithmetic so a DST boundary cannot
 * skip or repeat a civil day.
 */
(function (root) {
  "use strict";

  var WINDOW_DAYS = 7;

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

  /** Civil-date shift. UTC calendar math so a DST boundary cannot skip a day. */
  function addDays(iso, delta) {
    var parts = String(iso).slice(0, 10).split("-");
    var dt = new Date(
      Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
    );
    dt.setUTCDate(dt.getUTCDate() + delta);
    var z = function (n) {
      return String(n).padStart(2, "0");
    };
    return (
      dt.getUTCFullYear() + "-" + z(dt.getUTCMonth() + 1) + "-" + z(dt.getUTCDate())
    );
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

  /**
   * Every valid logged measurement, original date and percentage.
   * Invalid rows are omitted. Same-day readings are not collapsed.
   */
  function measurements(samples) {
    var out = [];
    (samples || []).forEach(function (row) {
      var date = civilDay(row && row.date);
      var pct = percentageOf(row);
      if (!date || pct == null) return;
      out.push({
        date: date,
        percentage: pct,
        recorded_at: String((row && row.recorded_at) || ""),
      });
    });
    out.sort(function (a, b) {
      if (a.date !== b.date) return a.date < b.date ? -1 : 1;
      if (a.recorded_at !== b.recorded_at) return a.recorded_at < b.recorded_at ? -1 : 1;
      return 0;
    });
    return out;
  }

  /**
   * One mean per label. Window is that civil day and the 6 calendar days
   * before it. Each valid measurement in the window counts once. Null when
   * the window has none (not 0).
   */
  function trailingAverage(samples, labels) {
    var rows = measurements(samples);
    var win = WINDOW_DAYS;
    return (labels || []).map(function (label) {
      var day = civilDay(label);
      if (!day) return null;
      var start = addDays(day, -(win - 1));
      var vals = [];
      var i;
      for (i = 0; i < rows.length; i++) {
        if (rows[i].date < start) continue;
        if (rows[i].date > day) break;
        vals.push(rows[i].percentage);
      }
      if (!vals.length) return null;
      var sum = 0;
      for (i = 0; i < vals.length; i++) sum += vals[i];
      return sum / vals.length;
    });
  }

  /**
   * Category axis for the shared weight chart: every weigh-in date plus
   * every valid body-fat measurement date. Invalid readings do not add a day.
   */
  function axisDates(weightDates, samples) {
    var seen = {};
    var out = [];
    function add(raw) {
      var d = civilDay(raw);
      if (!d || seen[d]) return;
      seen[d] = true;
      out.push(d);
    }
    (weightDates || []).forEach(add);
    measurements(samples).forEach(function (row) {
      add(row.date);
    });
    out.sort();
    return out;
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
    WINDOW_DAYS: WINDOW_DAYS,
    addDays: addDays,
    lastByDate: lastByDate,
    alignToLabels: alignToLabels,
    measurements: measurements,
    trailingAverage: trailingAverage,
    axisDates: axisDates,
    latest: latest,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashBodyFat = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
