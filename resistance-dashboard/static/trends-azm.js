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
  var TARGET_LOOKBACK_DAYS = 14;
  var AZM_TARGET_FLOOR = 10;
  var AZM_TARGET_CAP = 45;
  var DEFAULT_AZM_TARGET = 20;
  var SERIES_COLORS = {
    daily: "#8b9bb4",
    roll: "#3d9cf0",
    trend: "#f07178",
    target: "#5ce1a8",
  };
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

  /** Python 3 int(round(x)) for x >= 0 — ties to even. */
  function pyRound(n) {
    var x = Number(n);
    if (Number.isNaN(x)) return 0;
    var f = Math.floor(x);
    var frac = x - f;
    if (frac < 0.5) return f;
    if (frac > 0.5) return f + 1;
    return f % 2 === 0 ? f : f + 1;
  }

  function median(values) {
    var ordered = [];
    (values || []).forEach(function (v) {
      if (v == null || Number.isNaN(Number(v))) return;
      ordered.push(Number(v));
    });
    ordered.sort(function (a, b) {
      return a - b;
    });
    var n = ordered.length;
    if (!n) return null;
    var mid = Math.floor(n / 2);
    if (n % 2) return ordered[mid];
    return (ordered[mid - 1] + ordered[mid]) / 2;
  }

  /**
   * Present AZM in (today − lookback, today). Today is excluded.
   * Missing days stay missing. Logged 0 is a real rest day and stays.
   * Same window as cardio_quest.recent_azm_minutes.
   */
  function recentAzmMinutes(daily, labels, lookback) {
    var n = (labels || []).length;
    var win = lookback == null ? TARGET_LOOKBACK_DAYS : lookback;
    if (!n || win < 1) return [];
    var from = Math.max(0, n - 1 - win);
    var to = n - 1;
    var out = [];
    for (var i = from; i < to; i++) {
      var v = daily ? daily[i] : null;
      if (v == null || Number.isNaN(Number(v))) continue;
      out.push(Number(v));
    }
    return out;
  }

  /**
   * Daily AZM aim. Median of recent present days, floor 10 / cap 45,
   * default 20. Standard (not easy/walk) — Trends is the aiming line,
   * not today's recovery cut. Same formula as cardio_target_minutes(easy=False).
   */
  function azmTargetMinutes(recent) {
    var raw =
      recent && recent.length ? median(recent) : DEFAULT_AZM_TARGET;
    if (raw == null || Number.isNaN(Number(raw))) raw = DEFAULT_AZM_TARGET;
    return pyRound(Math.min(AZM_TARGET_CAP, Math.max(AZM_TARGET_FLOOR, raw)));
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
    var target = azmTargetMinutes(recentAzmMinutes(daily, labels, TARGET_LOOKBACK_DAYS));
    return {
      spanDays: SPAN_DAYS,
      rollDays: ROLL_DAYS,
      pointDays: present.length,
      lastRolling7: present.length ? lastFinite(rolling7) : null,
      target: target,
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
   * 90-day chart: daily (thin, gaps break) + 7d rolling avg + OLS trendline
   * + horizontal target (14d median, floor 10 / cap 45).
   * Sized to fill the card's chart-box (not an 80px spark).
   * Y domain is 0 → max of present daily / rolling / target. No weekday letters.
   * Null daily days keep their X slot and get no zero point.
   */
  function sparklineSvg(daily, labels, rolling, trend, target) {
    var presentDaily = finitePairs(daily);
    if (!presentDaily.length) return "";
    var presentRoll = finitePairs(rolling);
    var presentTrend = finitePairs(trend);
    var tgt =
      target == null || Number.isNaN(Number(target)) ? null : Number(target);
    var w = 720;
    var h = 220;
    var left = 36;
    var right = 12;
    var top = 16;
    var bottom = 28;
    var plotW = w - left - right;
    var plotH = h - top - bottom;
    var plotBottom = h - bottom;
    var n = daily.length;
    var max = 0;
    presentDaily.concat(presentRoll).forEach(function (p) {
      if (p.v > max) max = p.v;
    });
    if (tgt != null && tgt > max) max = tgt;
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
        '" stroke="' +
        SERIES_COLORS.daily +
        '" stroke-opacity="0.35" stroke-width="1"/>'
    );
    parts.push(
      '<text class="azm-y" x="' +
        (left - 4).toFixed(1) +
        '" y="' +
        (top + 4).toFixed(1) +
        '" text-anchor="end" font-size="11" fill="' +
        SERIES_COLORS.daily +
        '">' +
        yMaxLabel +
        "</text>"
    );
    parts.push(
      '<text class="azm-y" x="' +
        (left - 4).toFixed(1) +
        '" y="' +
        plotBottom.toFixed(1) +
        '" text-anchor="end" font-size="11" fill="' +
        SERIES_COLORS.daily +
        '" dominant-baseline="middle">0</text>'
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
      'class="azm-daily" stroke="' +
        SERIES_COLORS.daily +
        '" stroke-opacity="0.9" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    );
    if (rolling && rolling.length) {
      polyline(
        rolling,
        'class="azm-roll" stroke="' +
          SERIES_COLORS.roll +
          '" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"'
      );
    }
    if (trend && trend.length && presentTrend.length >= 2) {
      polyline(
        trend,
        'class="azm-trend" stroke="' +
          SERIES_COLORS.trend +
          '" stroke-width="2" stroke-dasharray="6 4" stroke-linecap="round" stroke-linejoin="round"'
      );
    }
    if (tgt != null) {
      var ty = y(tgt);
      parts.push(
        '<line class="azm-target" x1="' +
          left.toFixed(1) +
          '" y1="' +
          ty.toFixed(1) +
          '" x2="' +
          (w - right).toFixed(1) +
          '" y2="' +
          ty.toFixed(1) +
          '" stroke="' +
          SERIES_COLORS.target +
          '" stroke-width="1.75" stroke-dasharray="3 5" stroke-linecap="round"/>'
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
          '" font-size="11" fill="' +
          SERIES_COLORS.daily +
          '">' +
          label +
          "</text>"
      );
    });
    return (
      '<svg class="azm-spark-svg" viewBox="0 0 ' +
      w +
      " " +
      h +
      '" width="100%" height="100%" preserveAspectRatio="none" data-y-min="0" data-y-max="' +
      yMaxLabel +
      '" role="img" aria-label="Daily Active Zone Minutes with 7-day rolling average, trendline, and ' +
      (tgt != null ? Math.round(tgt) + "-minute target, " : "") +
      "0 to " +
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
    var key = stats.pointDays + ":" + String(stats.lastRolling7) + ":" + String(stats.target);
    valueEl.textContent = formatRolling(stats.lastRolling7);
    var sub = document.getElementById("azm-week-sub");
    if (sub) {
      sub.textContent =
        stats.pointDays > 0
          ? "90d · " + stats.pointDays + (stats.pointDays === 1 ? " day" : " days")
          : "90d";
    }
    var targetEl = document.getElementById("azm-target-value");
    if (targetEl) {
      targetEl.textContent =
        stats.target == null ? "—" : String(stats.target);
    }
    var targetSub = document.getElementById("azm-target-sub");
    if (targetSub) {
      targetSub.textContent = "14d median";
    }
    var note = document.getElementById("azm-trend-note");
    if (note) {
      note.textContent =
        stats.pointDays > 0
          ? "90d daily AZM · 7d rolling avg · trendline · target " +
            stats.target +
            " (14d median, floor 10 / cap 45) · " +
            stats.pointDays +
            (stats.pointDays === 1 ? " day" : " days") +
            " with points"
          : "No Active Zone Minutes in the last 90 days. Target " +
            stats.target +
            " is the default until days land.";
    }
    var spark = document.getElementById("azm-sparkline");
    if (spark) {
      spark.innerHTML = sparklineSvg(
        stats.daily,
        stats.labels,
        stats.rolling7,
        stats.trend,
        stats.target
      );
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
    TARGET_LOOKBACK_DAYS: TARGET_LOOKBACK_DAYS,
    AZM_TARGET_FLOOR: AZM_TARGET_FLOOR,
    AZM_TARGET_CAP: AZM_TARGET_CAP,
    DEFAULT_AZM_TARGET: DEFAULT_AZM_TARGET,
    SERIES_COLORS: SERIES_COLORS,
    windowLabels: windowLabels,
    azmPoints: azmPoints,
    azmSeries: azmSeries,
    rollingAverage: rollingAverage,
    linearTrend: linearTrend,
    median: median,
    pyRound: pyRound,
    recentAzmMinutes: recentAzmMinutes,
    azmTargetMinutes: azmTargetMinutes,
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
