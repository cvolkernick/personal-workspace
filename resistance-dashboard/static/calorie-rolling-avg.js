/**
 * FitDash #946: trailing 7-day means for the calories in vs out chart.
 * A missing day is left out of the mean. It is not stored as 0.
 * Fewer than MIN_LOGGED finite days in the window suppresses the point.
 * Days before the visible labels count when they are already in the map.
 */
(function (root) {
  "use strict";

  var WINDOW_DAYS = 7;
  var MIN_LOGGED = 4;

  function finiteKcal(v) {
    if (v == null || v === "") return null;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  /** Last finite calories value wins. Non-finite rows are omitted, not zeroed. */
  function kcalByDate(rows) {
    var by = {};
    (rows || []).forEach(function (row) {
      if (!row || row.date == null) return;
      var v = finiteKcal(row.calories);
      if (v == null) return;
      by[String(row.date).slice(0, 10)] = v;
    });
    return by;
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

  /**
   * One mean per label. Window is that day and the (windowDays - 1) days
   * before it. Absent keys are skipped. Null when fewer than minLogged
   * finite values fall in the window.
   */
  function trailingMeans(byDate, labels, windowDays, minLogged) {
    var win = windowDays == null ? WINDOW_DAYS : windowDays;
    var min = minLogged == null ? MIN_LOGGED : minLogged;
    return (labels || []).map(function (day) {
      var vals = [];
      var k;
      for (k = win - 1; k >= 0; k--) {
        var v = byDate ? finiteKcal(byDate[addDays(day, -k)]) : null;
        if (v == null) continue;
        vals.push(v);
      }
      if (vals.length < min) return null;
      var sum = 0;
      for (k = 0; k < vals.length; k++) sum += vals[k];
      return sum / vals.length;
    });
  }

  /** Visible-axis raw values. A logged 0 stays 0. A missing day stays null. */
  function valuesOnLabels(byDate, labels) {
    return (labels || []).map(function (day) {
      if (!byDate || !Object.prototype.hasOwnProperty.call(byDate, day)) return null;
      return finiteKcal(byDate[day]);
    });
  }

  function tooltipLines(point) {
    point = point || {};
    function fmt(v) {
      var n = finiteKcal(v);
      if (n == null) return "—";
      return Math.round(n).toLocaleString("en-US") + " kcal";
    }
    var avgIn = finiteKcal(point.avgIn);
    var avgOut = finiteKcal(point.avgOut);
    var bal = avgIn != null && avgOut != null ? avgOut - avgIn : null;
    var balText = "—";
    if (bal != null) {
      var rounded = Math.round(bal);
      var word = rounded > 0 ? "deficit" : rounded < 0 ? "surplus" : "even";
      var sign = rounded > 0 ? "+" : "";
      balText = sign + rounded.toLocaleString("en-US") + " kcal (" + word + ")";
    }
    return [
      "7-day avg in: " + fmt(avgIn),
      "7-day avg out: " + fmt(avgOut),
      "daily in: " + fmt(point.rawIn),
      "daily out: " + fmt(point.rawOut),
      "7-day avg out − in: " + balText,
    ];
  }

  var api = {
    WINDOW_DAYS: WINDOW_DAYS,
    MIN_LOGGED: MIN_LOGGED,
    finiteKcal: finiteKcal,
    kcalByDate: kcalByDate,
    addDays: addDays,
    trailingMeans: trailingMeans,
    valuesOnLabels: valuesOnLabels,
    tooltipLines: tooltipLines,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashCalorieRollingAvg = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
