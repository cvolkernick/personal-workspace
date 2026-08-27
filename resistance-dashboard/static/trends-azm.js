/**
 * FitDash Trends #299: weekly Active Zone Minutes (7d trailing sum).
 *
 * Overlay only — reads HealthSnapshot.active_zone_minutes already exported
 * by #304/#318. Uses total_minutes only. Missing / empty points → honest "—".
 *
 * Cardio-load context only. Does not change planner or generate buttons.
 */
(function (root) {
  "use strict";

  var SPAN_DAYS = 7;

  function windowLabels(spanDays, now) {
    var end = now ? new Date(now) : new Date();
    end.setHours(0, 0, 0, 0);
    var z = function (x) {
      return String(x).padStart(2, "0");
    };
    var labels = [];
    var days = spanDays == null ? SPAN_DAYS : spanDays;
    for (var i = days - 1; i >= 0; i--) {
      var d = new Date(end);
      d.setDate(d.getDate() - i);
      labels.push(
        d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate())
      );
    }
    return labels;
  }

  function azmPoints(data) {
    var health = (data && data.health) || {};
    var raw = health.active_zone_minutes;
    if (!Array.isArray(raw) && data && Array.isArray(data.active_zone_minutes)) {
      raw = data.active_zone_minutes;
    }
    if (!Array.isArray(raw) && data && data.today && Array.isArray(data.today.active_zone_minutes)) {
      raw = data.today.active_zone_minutes;
    }
    return Array.isArray(raw) ? raw : [];
  }

  /**
   * 7d trailing window ending today. Sum total_minutes of days that actually
   * have a numeric point. Missing days stay null (not 0). No points → null.
   */
  function weeklyAzm(points, now) {
    var labels = windowLabels(SPAN_DAYS, now);
    var byDate = {};
    (points || []).forEach(function (row) {
      if (!row) return;
      var day = String(row.date || "").slice(0, 10);
      if (!day) return;
      var mins = row.total_minutes;
      if (mins == null || mins === "") return;
      var n = Number(mins);
      if (Number.isNaN(n)) return;
      byDate[day] = n;
    });
    var daily = labels.map(function (day) {
      return Object.prototype.hasOwnProperty.call(byDate, day) ? byDate[day] : null;
    });
    var present = daily.filter(function (v) {
      return v != null;
    });
    var weeklySum = present.length
      ? present.reduce(function (a, b) {
          return a + b;
        }, 0)
      : null;
    return {
      spanDays: SPAN_DAYS,
      pointDays: present.length,
      weeklySum: weeklySum,
      daily: daily,
      labels: labels,
    };
  }

  function formatWeekly(sum) {
    if (sum == null) return "—";
    return Math.round(sum).toLocaleString();
  }

  /** One weekday letter (S M T W T F S) from a civil YYYY-MM-DD. Local date. */
  function weekdayLetter(iso) {
    var p = String(iso || "").split("-");
    if (p.length < 3) return "";
    var y = Number(p[0]);
    var mo = Number(p[1]);
    var d = Number(p[2]);
    if (!y || !mo || !d) return "";
    var dt = new Date(y, mo - 1, d);
    if (Number.isNaN(dt.getTime())) return "";
    return "SMTWTFS".charAt(dt.getDay());
  }

  /**
   * Labeled 7-day spark. Y domain is 0 → max present total_minutes (not min/max).
   * Null days keep their X slot, break the line, and get no dot / no zero point.
   */
  function sparklineSvg(daily, labels) {
    var present = [];
    (daily || []).forEach(function (v, i) {
      if (v == null || Number.isNaN(Number(v))) return;
      present.push({ i: i, v: Number(v) });
    });
    if (!present.length) return "";
    var w = 184;
    var h = 56;
    var left = 26;
    var right = 6;
    var top = 11;
    var bottom = 15;
    var plotW = w - left - right;
    var plotH = h - top - bottom;
    var plotBottom = h - bottom;
    var n = daily.length;
    var max = Math.max.apply(
      null,
      present.map(function (p) {
        return p.v;
      })
    );
    var yMaxLabel = Math.round(max);
    function x(i) {
      return left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
    }
    function y(v) {
      if (max <= 0) return plotBottom;
      return top + (1 - v / max) * plotH;
    }
    var parts = [];
    parts.push(
      '<line class="azm-baseline" x1="' +
        left.toFixed(1) +
        '" y1="' +
        plotBottom.toFixed(1) +
        '" x2="' +
        (w - right).toFixed(1) +
        '" y2="' +
        plotBottom.toFixed(1) +
        '" stroke="#8b9bb4" stroke-opacity="0.35" stroke-width="1"/>'
    );
    parts.push(
      '<text class="azm-y" x="' +
        (left - 3).toFixed(1) +
        '" y="' +
        (top + 3).toFixed(1) +
        '" text-anchor="end" font-size="9" fill="#8b9bb4">' +
        yMaxLabel +
        "</text>"
    );
    parts.push(
      '<text class="azm-y" x="' +
        (left - 3).toFixed(1) +
        '" y="' +
        plotBottom.toFixed(1) +
        '" text-anchor="end" font-size="9" fill="#8b9bb4" dominant-baseline="middle">0</text>'
    );
    var run = [];
    function flush() {
      if (!run.length) return;
      var d = run
        .map(function (p, idx) {
          return (idx === 0 ? "M" : "L") + x(p.i).toFixed(1) + " " + y(p.v).toFixed(1);
        })
        .join(" ");
      parts.push(
        '<path d="' +
          d +
          '" fill="none" stroke="#3d9cf0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
      );
      run.forEach(function (p) {
        parts.push(
          '<circle cx="' +
            x(p.i).toFixed(1) +
            '" cy="' +
            y(p.v).toFixed(1) +
            '" r="2.2" fill="#3d9cf0"/>'
        );
      });
      run = [];
    }
    for (var i = 0; i < n; i++) {
      var v = daily[i];
      if (v == null || Number.isNaN(Number(v))) {
        flush();
        continue;
      }
      run.push({ i: i, v: Number(v) });
    }
    flush();
    for (var xi = 0; xi < n; xi++) {
      var letter =
        labels && labels[xi] != null ? weekdayLetter(labels[xi]) : "";
      if (!letter) continue;
      parts.push(
        '<text class="azm-x" x="' +
          x(xi).toFixed(1) +
          '" y="' +
          (h - 3).toFixed(1) +
          '" text-anchor="middle" font-size="9" fill="#8b9bb4">' +
          letter +
          "</text>"
      );
    }
    return (
      '<svg class="azm-spark-svg" viewBox="0 0 ' +
      w +
      " " +
      h +
      '" width="100%" height="' +
      h +
      '" data-y-min="0" data-y-max="' +
      yMaxLabel +
      '" role="img" aria-label="Daily Active Zone Minutes, 0 to ' +
      yMaxLabel +
      ' minutes, last 7 days">' +
      parts.join("") +
      "</svg>"
    );
  }

  var paintedKey = "";

  function paintAzmCard(data, now) {
    var card =
      typeof document !== "undefined"
        ? document.getElementById("azm-trend-card")
        : null;
    var valueEl =
      typeof document !== "undefined"
        ? document.getElementById("azm-week-value")
        : null;
    if (!card || !valueEl) return null;
    var stats = weeklyAzm(azmPoints(data), now);
    var key = stats.pointDays + ":" + String(stats.weeklySum);
    valueEl.textContent = formatWeekly(stats.weeklySum);
    var sub = document.getElementById("azm-week-sub");
    if (sub) {
      sub.textContent =
        stats.pointDays > 0
          ? "7d trailing sum · " + stats.pointDays + (stats.pointDays === 1 ? " day" : " days")
          : "7d trailing sum";
    }
    var note = document.getElementById("azm-trend-note");
    if (note) {
      note.textContent =
        stats.pointDays > 0
          ? "7d trailing sum of daily Google Health AZM · " +
            stats.pointDays +
            (stats.pointDays === 1 ? " day" : " days") +
            " with points"
          : "No Active Zone Minutes in the last 7 days.";
    }
    var spark = document.getElementById("azm-sparkline");
    if (spark) {
      spark.innerHTML = sparklineSvg(stats.daily, stats.labels);
    }
    paintedKey = key;
    return stats;
  }

  var lastPayload = null;

  function bindLive() {
    if (typeof document === "undefined" || typeof window === "undefined") {
      return;
    }
    if (typeof window.fetch !== "function") return;
    var origFetch = window.fetch;
    window.fetch = function () {
      var args = Array.prototype.slice.call(arguments);
      var req = args[0];
      var url = typeof req === "string" ? req : (req && req.url) || "";
      return origFetch.apply(this, args).then(function (res) {
        if (res && res.ok && String(url).indexOf("/api/dashboard") !== -1) {
          res
            .clone()
            .json()
            .then(function (data) {
              lastPayload = data;
              setTimeout(function () {
                paintAzmCard(data);
              }, 40);
              setTimeout(function () {
                paintAzmCard(data);
              }, 350);
            })
            .catch(function () {});
        }
        return res;
      });
    };
  }

  var api = {
    SPAN_DAYS: SPAN_DAYS,
    windowLabels: windowLabels,
    azmPoints: azmPoints,
    weeklyAzm: weeklyAzm,
    formatWeekly: formatWeekly,
    weekdayLetter: weekdayLetter,
    sparklineSvg: sparklineSvg,
    paintAzmCard: paintAzmCard,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashTrendsAzm = api;
  bindLive();
})(typeof globalThis !== "undefined" ? globalThis : this);
