/**
 * FitDash Trends #392: 90-day Active Zone Minutes + 7d rolling avg + trendline.
 *
 * Overlay only — reads HealthSnapshot.active_zone_minutes already exported
 * by #304/#318. Uses total_minutes only. Missing / empty points → honest "—".
 * Missing civil days stay null (never 0, never invented from other series).
 *
 * Trendline is an OLS fit on present daily total_minutes (civil-day index as
 * x), evaluated across the 90-day axis — not on the rolling-average series,
 * and not treating gaps as 0.
 *
 * Cardio-load context only. Does not change planner or generate buttons.
 */
(function (root) {
  "use strict";

  var SPAN_DAYS = 90;
  var ROLL_DAYS = 7;
  var MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];

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

  /** Trailing mean of present (finite) points in the last `window` civil slots. */
  function rollingAverage(values, window) {
    var out = [];
    var win = window == null ? ROLL_DAYS : window;
    for (var i = 0; i < values.length; i++) {
      var slice = [];
      for (var j = Math.max(0, i - win + 1); j <= i; j++) {
        var v = values[j];
        if (v != null && !Number.isNaN(Number(v))) slice.push(Number(v));
      }
      out.push(
        slice.length ? slice.reduce(function (s, x) { return s + x; }, 0) / slice.length : null
      );
    }
    return out;
  }

  /**
   * OLS y = a + b*x on present points (index as x). Evaluated at every slot.
   * Gaps are omitted from the fit (not 0). Fewer than 2 present points → nulls.
   */
  function linearTrend(values) {
    var pts = [];
    (values || []).forEach(function (v, i) {
      if (v != null && !Number.isNaN(Number(v))) pts.push({ x: i, y: Number(v) });
    });
    if (pts.length < 2) {
      return (values || []).map(function () {
        return null;
      });
    }
    var n = pts.length;
    var meanX = pts.reduce(function (s, p) { return s + p.x; }, 0) / n;
    var meanY = pts.reduce(function (s, p) { return s + p.y; }, 0) / n;
    var num = 0;
    var den = 0;
    pts.forEach(function (p) {
      num += (p.x - meanX) * (p.y - meanY);
      den += (p.x - meanX) * (p.x - meanX);
    });
    if (den === 0) {
      return (values || []).map(function () {
        return null;
      });
    }
    var b = num / den;
    var a = meanY - b * meanX;
    return (values || []).map(function (_, i) {
      return a + b * i;
    });
  }

  function lastFinite(values) {
    for (var i = (values || []).length - 1; i >= 0; i--) {
      if (values[i] != null && !Number.isNaN(Number(values[i]))) return Number(values[i]);
    }
    return null;
  }

  /**
   * 90 civil days ending today. Daily series is real total_minutes only.
   * Missing days stay null (not 0). No present points → lastRolling7 is null.
   */
  function azmSeries(points, now) {
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
    var rolling7 = rollingAverage(daily, ROLL_DAYS);
    var trend = linearTrend(daily);
    return {
      spanDays: SPAN_DAYS,
      rollDays: ROLL_DAYS,
      pointDays: present.length,
      lastRolling7: present.length ? lastFinite(rolling7) : null,
      daily: daily,
      rolling7: rolling7,
      trend: trend,
      labels: labels,
    };
  }

  function formatRolling(avg) {
    if (avg == null) return "—";
    return Math.round(avg).toLocaleString();
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

  function monthLabel(iso) {
    var p = String(iso || "").split("-");
    if (p.length < 2) return "";
    var mo = Number(p[1]);
    if (!mo || mo < 1 || mo > 12) return "";
    return MONTHS[mo - 1];
  }

  /**
   * Month starts in the window. A mid-month first slot is kept only when the
   * next 1st is far enough away — otherwise May 30 sits on Jun 1.
   */
  function monthTickIndexes(labels) {
    var candidates = [];
    var prev = "";
    (labels || []).forEach(function (iso, i) {
      var ym = String(iso || "").slice(0, 7);
      if (!ym || ym === prev) return;
      candidates.push(i);
      prev = ym;
    });
    if (!candidates.length) return [];
    var minGap = 12;
    var ticks = [];
    candidates.forEach(function (i, k) {
      var next = k + 1 < candidates.length ? candidates[k + 1] : (labels || []).length;
      if (k === 0 && next - i < minGap && candidates.length > 1) return;
      var prevKept = ticks.length ? ticks[ticks.length - 1] : -minGap;
      if (ticks.length && i - prevKept < minGap) return;
      ticks.push(i);
    });
    return ticks;
  }

  function finitePairs(series) {
    var out = [];
    (series || []).forEach(function (v, i) {
      if (v == null || Number.isNaN(Number(v))) return;
      out.push({ i: i, v: Number(v) });
    });
    return out;
  }

  /**
   * 90-day spark: daily (thin, gaps break) + 7d rolling avg + OLS trendline.
   * Y domain is 0 → max of present daily / rolling. No weekday letters.
   * Null daily days keep their X slot and get no zero point.
   */
  function sparklineSvg(daily, labels, rolling, trend) {
    var presentDaily = finitePairs(daily);
    if (!presentDaily.length) return "";
    var presentRoll = finitePairs(rolling);
    var presentTrend = finitePairs(trend);
    var w = 400;
    var h = 80;
    var left = 28;
    var right = 8;
    var top = 12;
    var bottom = 18;
    var plotW = w - left - right;
    var plotH = h - top - bottom;
    var plotBottom = h - bottom;
    var n = daily.length;
    var max = 0;
    presentDaily.concat(presentRoll).forEach(function (p) {
      if (p.v > max) max = p.v;
    });
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

    function polyline(series, attrs) {
      var run = [];
      function flush() {
        if (!run.length) return;
        var d = run
          .map(function (p, idx) {
            return (idx === 0 ? "M" : "L") + x(p.i).toFixed(1) + " " + y(p.v).toFixed(1);
          })
          .join(" ");
        parts.push('<path d="' + d + '" fill="none" ' + attrs + "/>");
        run = [];
      }
      for (var i = 0; i < n; i++) {
        var v = series[i];
        if (v == null || Number.isNaN(Number(v))) {
          flush();
          continue;
        }
        run.push({ i: i, v: Number(v) });
      }
      flush();
    }

    polyline(
      daily,
      'class="azm-daily" stroke="#8b9bb4" stroke-opacity="0.9" stroke-width="1.15" stroke-linecap="round" stroke-linejoin="round"'
    );
    if (rolling && rolling.length) {
      polyline(
        rolling,
        'class="azm-roll" stroke="#3d9cf0" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'
      );
    }
    if (trend && trend.length && presentTrend.length >= 2) {
      polyline(
        trend,
        'class="azm-trend" stroke="#f07178" stroke-width="1.6" stroke-dasharray="5 3" stroke-linecap="round" stroke-linejoin="round"'
      );
    }

    monthTickIndexes(labels).forEach(function (xi) {
      var label = monthLabel(labels[xi]);
      if (!label) return;
      parts.push(
        '<text class="azm-x" x="' +
          x(xi).toFixed(1) +
          '" y="' +
          (h - 3).toFixed(1) +
          '" text-anchor="' +
          (xi === 0 ? "start" : "middle") +
          '" font-size="9" fill="#8b9bb4">' +
          label +
          "</text>"
      );
    });
    return (
      '<svg class="azm-spark-svg" viewBox="0 0 ' +
      w +
      " " +
      h +
      '" width="100%" height="' +
      h +
      '" data-y-min="0" data-y-max="' +
      yMaxLabel +
      '" role="img" aria-label="Daily Active Zone Minutes with 7-day rolling average and trendline, 0 to ' +
      yMaxLabel +
      ' minutes, last 90 days">' +
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
    var stats = azmSeries(azmPoints(data), now);
    var key = stats.pointDays + ":" + String(stats.lastRolling7);
    valueEl.textContent = formatRolling(stats.lastRolling7);
    var sub = document.getElementById("azm-week-sub");
    if (sub) {
      sub.textContent =
        stats.pointDays > 0
          ? "90d · " + stats.pointDays + (stats.pointDays === 1 ? " day" : " days")
          : "90d";
    }
    var note = document.getElementById("azm-trend-note");
    if (note) {
      note.textContent =
        stats.pointDays > 0
          ? "90d daily AZM · 7d rolling avg · trendline on daily points · " +
            stats.pointDays +
            (stats.pointDays === 1 ? " day" : " days") +
            " with points"
          : "No Active Zone Minutes in the last 90 days.";
    }
    var spark = document.getElementById("azm-sparkline");
    if (spark) {
      spark.innerHTML = sparklineSvg(stats.daily, stats.labels, stats.rolling7, stats.trend);
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
    ROLL_DAYS: ROLL_DAYS,
    windowLabels: windowLabels,
    azmPoints: azmPoints,
    azmSeries: azmSeries,
    rollingAverage: rollingAverage,
    linearTrend: linearTrend,
    formatRolling: formatRolling,
    weekdayLetter: weekdayLetter,
    monthLabel: monthLabel,
    monthTickIndexes: monthTickIndexes,
    sparklineSvg: sparklineSvg,
    paintAzmCard: paintAzmCard,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashTrendsAzm = api;
  bindLive();
})(typeof globalThis !== "undefined" ? globalThis : this);
