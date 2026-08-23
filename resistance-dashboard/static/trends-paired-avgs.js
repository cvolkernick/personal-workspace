/**
 * FitDash Trends #254: Avg intake / Avg burned on the same 60d paired-days
 * window as the Σ chips in app.js (days with both nutrition + calories_burned).
 * Overlay only — does not rewrite app.js or add synthetic food rows.
 */
(function (root) {
  "use strict";

  /** Must match CAL_IN_OUT_SPAN_DAYS in app.js. */
  var SPAN_DAYS = 60;

  function windowLabels(spanDays, now) {
    var end = now ? new Date(now) : new Date();
    end.setHours(0, 0, 0, 0);
    var z = function (x) {
      return String(x).padStart(2, "0");
    };
    var labels = [];
    for (var i = spanDays - 1; i >= 0; i--) {
      var d = new Date(end);
      d.setDate(d.getDate() - i);
      labels.push(
        d.getFullYear() + "-" + z(d.getMonth() + 1) + "-" + z(d.getDate())
      );
    }
    return labels;
  }

  /**
   * Same pair set as the Σ chips: 60d civil labels, include a day only when
   * both intake and burned are present and numeric. Avg = Σ / pairDays.
   */
  function pairedCalorieWindow(nutrition, caloriesBurned, spanDays, now) {
    var days = spanDays == null ? SPAN_DAYS : spanDays;
    var labels = windowLabels(days, now);
    var intakeBy = {};
    (nutrition || []).forEach(function (row) {
      if (!row) return;
      intakeBy[String(row.date).slice(0, 10)] = Number(row.calories);
    });
    var burnedBy = {};
    (caloriesBurned || []).forEach(function (row) {
      if (!row) return;
      burnedBy[String(row.date).slice(0, 10)] = Number(row.calories);
    });
    var pairDays = 0;
    var sumIn = 0;
    var sumOut = 0;
    labels.forEach(function (day) {
      var vin = intakeBy[day];
      var vout = burnedBy[day];
      if (
        vin == null ||
        vout == null ||
        Number.isNaN(vin) ||
        Number.isNaN(vout)
      ) {
        return;
      }
      sumIn += vin;
      sumOut += vout;
      pairDays += 1;
    });
    return {
      spanDays: days,
      pairDays: pairDays,
      sumIn: sumIn,
      sumOut: sumOut,
      avgIn: pairDays > 0 ? sumIn / pairDays : null,
      avgOut: pairDays > 0 ? sumOut / pairDays : null,
    };
  }

  function formatAvgKcal(pairDays, sum) {
    if (!pairDays) return "—";
    return Math.round(sum / pairDays).toLocaleString();
  }

  function chipHtml(id, kind, label, value, sub) {
    return (
      '<div class="chart-summary-chip ' +
      kind +
      '" id="' +
      id +
      '">' +
      '<span class="chip-k">' +
      label +
      "</span>" +
      '<span class="chip-v">' +
      value +
      "</span>" +
      '<span class="chip-s">' +
      sub +
      "</span>" +
      "</div>"
    );
  }

  var paintedKey = "";

  function hasSigmaIntakeChip(note) {
    var keys = note.querySelectorAll(".chip-k");
    for (var i = 0; i < keys.length; i++) {
      if ((keys[i].textContent || "").indexOf("Σ intake") !== -1) return true;
    }
    return false;
  }

  function paintAvgChips(data, now) {
    var note =
      typeof document !== "undefined"
        ? document.getElementById("nutrition-note")
        : null;
    if (!note) return null;
    var existing = document.getElementById("trends-avg-row");
    if (!hasSigmaIntakeChip(note)) {
      if (existing) existing.remove();
      paintedKey = "";
      return null;
    }
    var health = (data && data.health) || {};
    var stats = pairedCalorieWindow(
      health.nutrition,
      health.calories_burned,
      SPAN_DAYS,
      now
    );
    var key = stats.pairDays + ":" + stats.sumIn + ":" + stats.sumOut;
    if (existing && note.contains(existing) && paintedKey === key) {
      return stats;
    }
    if (existing) existing.remove();
    var pairedSub =
      stats.pairDays > 0
        ? "kcal/day · " + stats.pairDays + " paired days"
        : "Need paired intake + burned days";
    var row = document.createElement("div");
    row.className = "chart-summary-row";
    row.id = "trends-avg-row";
    row.innerHTML =
      chipHtml(
        "trends-avg-intake",
        "chip-in",
        "Avg intake",
        formatAvgKcal(stats.pairDays, stats.sumIn),
        pairedSub
      ) +
      chipHtml(
        "trends-avg-burned",
        "chip-out",
        "Avg burned",
        formatAvgKcal(stats.pairDays, stats.sumOut),
        pairedSub
      );
    var firstRow = note.querySelector(".chart-summary-row");
    if (firstRow && firstRow.parentNode) {
      if (firstRow.nextSibling) {
        firstRow.parentNode.insertBefore(row, firstRow.nextSibling);
      } else {
        firstRow.parentNode.appendChild(row);
      }
    } else {
      note.insertBefore(row, note.firstChild);
    }
    paintedKey = key;
    return stats;
  }

  var lastPayload = null;

  function bindLive() {
    if (typeof document === "undefined" || typeof window === "undefined") {
      return;
    }
    var note = document.getElementById("nutrition-note");
    if (note && typeof MutationObserver !== "undefined") {
      new MutationObserver(function () {
        if (lastPayload) paintAvgChips(lastPayload);
      }).observe(note, { childList: true, subtree: true });
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
                paintAvgChips(data);
              }, 40);
              setTimeout(function () {
                paintAvgChips(data);
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
    pairedCalorieWindow: pairedCalorieWindow,
    formatAvgKcal: formatAvgKcal,
    paintAvgChips: paintAvgChips,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashTrendsPairedAvgs = api;
  bindLive();
})(typeof globalThis !== "undefined" ? globalThis : this);
