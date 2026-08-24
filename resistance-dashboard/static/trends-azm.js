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

  function sparklineSvg(daily) {
    var present = [];
    (daily || []).forEach(function (v, i) {
      if (v == null || Number.isNaN(Number(v))) return;
      present.push({ i: i, v: Number(v) });
    });
    if (!present.length) return "";
    var w = 160;
    var h = 36;
    var pad = 3;
    var n = daily.length;
    var min = Math.min.apply(
      null,
      present.map(function (p) {
        return p.v;
      })
    );
    var max = Math.max.apply(
      null,
      present.map(function (p) {
        return p.v;
      })
    );
    var span = max - min;
    function x(i) {
      return pad + (n <= 1 ? (w - 2 * pad) / 2 : (i / (n - 1)) * (w - 2 * pad));
    }
    function y(v) {
      if (span <= 0) return h / 2;
      return pad + (1 - (v - min) / span) * (h - 2 * pad);
    }
    var parts = [];
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
    return (
      '<svg class="azm-spark-svg" viewBox="0 0 ' +
      w +
      " " +
      h +
      '" width="100%" height="' +
      h +
      '" role="img" aria-label="Daily Active Zone Minutes">' +
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
      spark.innerHTML = sparklineSvg(stats.daily);
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
    sparklineSvg: sparklineSvg,
    paintAzmCard: paintAzmCard,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashTrendsAzm = api;
  bindLive();
})(typeof globalThis !== "undefined" ? globalThis : this);
