/**
 * FitDash energy vs scale alignment.
 *
 * expectedLb = cumDeltaKcal / 3500 (Wishnofsky). Gap = scale − expected.
 * "Lines up" if |gap| ≤ 1.25 lb, or relative 55% **inside a 5 lb cap**.
 * |gap| > 5 lb is never aligned — 55% of a corrupt huge expected cannot pass.
 */
(function (root) {
  "use strict";

  var KCAL_PER_LB = 3500;
  var TIGHT_LB = 1.25;
  var CAP_LB = 5;

  /** True only for the UI "Lines up" badge. */
  function isAligned(absRes, absExp) {
    if (absRes <= TIGHT_LB) return true;
    if (absRes > CAP_LB) return false;
    return absExp >= 0.75 && absRes / absExp <= 0.55;
  }

  /**
   * Compare logged cumulative intake−burned to observed weight change.
   * Returns null if not enough data.
   */
  function energyWeightAlignment(opts) {
    opts = opts || {};
    var pairDays = opts.pairDays;
    var cumDeltaKcal = opts.cumDeltaKcal;
    if (pairDays == null || pairDays < 5 || cumDeltaKcal == null) return null;
    var inWin = (opts.weights || [])
      .map(function (w) {
        return {
          date: String(w.date || "").slice(0, 10),
          lbs: Number(w.weight_lbs),
        };
      })
      .filter(function (w) {
        return (
          w.date &&
          !Number.isNaN(w.lbs) &&
          w.date >= opts.windowStart &&
          w.date <= opts.windowEnd
        );
      })
      .sort(function (a, b) {
        return a.date.localeCompare(b.date);
      });
    if (inWin.length < 2) return null;
    var first = inWin[0];
    var last = inWin[inWin.length - 1];
    var spanMs =
      new Date(last.date + "T12:00:00").getTime() -
      new Date(first.date + "T12:00:00").getTime();
    if (spanMs < 5 * 86400000) return null;

    var actualLb = last.lbs - first.lbs;
    var expectedLb = cumDeltaKcal / KCAL_PER_LB;
    var residualLb = actualLb - expectedLb;
    var absExp = Math.abs(expectedLb);
    var absAct = Math.abs(actualLb);
    var absRes = Math.abs(residualLb);

    var bothNearFlat = absExp < 0.4 && absAct < 0.4;
    var sameSign =
      bothNearFlat ||
      (expectedLb === 0 && absAct < 0.5) ||
      expectedLb * actualLb > 0;
    var aligned = isAligned(absRes, absExp);

    var status = "mixed";
    if (aligned && (sameSign || bothNearFlat)) status = "aligned";
    else if (!sameSign && absRes >= 1.0) status = "divergent";
    else if (absRes >= 1.5) status = "offset";

    var hint = String(opts.goalHint || "").toLowerCase();
    var goal = "recomp";
    if (/cut|deficit|loss|lean/.test(hint)) goal = "cut";
    else if (/bulk|surplus|gain|mass/.test(hint)) goal = "gain";
    else if (cumDeltaKcal < -1500) goal = "cut";
    else if (cumDeltaKcal > 1500) goal = "gain";

    var advice = [];
    if (status === "aligned") {
      advice.push(
        "Logged energy balance and scale change roughly line up for this window — good calibration of intake/burn tracking."
      );
      if (goal === "cut" && actualLb > -0.3) {
        advice.push(
          "For fat loss, deepen the deficit slightly (or improve adherence) — scale is nearly flat despite a logged deficit."
        );
      } else if (goal === "gain" && actualLb < 0.3) {
        advice.push(
          "For mass gain, add a small surplus — scale is flat despite a logged surplus/near balance."
        );
      }
    } else {
      if (residualLb <= -1.0) {
        advice.push(
          "Scale dropped more (or rose less) than the logged calorie balance suggests."
        );
        advice.push(
          "Check: under-logged food, overestimated burn, or water/glycogen noise. If logging is solid and the goal is a cut, you may not need a deeper deficit."
        );
      } else if (residualLb >= 1.0) {
        advice.push(
          "Scale held or rose more than the logged calorie balance suggests."
        );
        if (absRes > CAP_LB) {
          advice.push(
            "Gap is outside the 5 lb don't-panic band — treat logged burn as suspect (corrupt days or wearable overestimate). Do not deepen the cut on this gap."
          );
        } else {
          advice.push(
            "Common fixes: tighten food logging (oils, drinks, bites), treat wearable burn as an estimate, reduce weekend surplus. If goal is a cut, increase the true deficit (lower intake or more NEAT)."
          );
          if (goal === "gain") {
            advice.push(
              "If bulk is the goal and weight is rising faster than planned, trim surplus slightly."
            );
          }
        }
      } else if (!sameSign) {
        advice.push(
          "Energy balance and weight moved in opposite directions — treat this window as noisy; recheck after more consistent weigh-ins."
        );
      }
    }
    advice.push(
      "Rule of thumb only (~3,500 kcal ≈ 1 lb); short windows and water weight can dominate."
    );

    return {
      status: status,
      actualLb: actualLb,
      expectedLb: expectedLb,
      residualLb: residualLb,
      first: first,
      last: last,
      goal: goal,
      advice: advice,
    };
  }

  /**
   * Line, implied burn, and 4-week review on the existing card (#961).
   *
   * Already true of the card, and kept here: a day with no finite intake
   * or no finite burn adds nothing to predicted change. It is not stored
   * as zero.
   *
   * Not on the card today: 14 logged days, and 80% of the window logged.
   * Those gates hide only the new line, implied burn, and weekly review.
   * From calories / On scale / Gap stay on energyWeightAlignment.
   */
  var MIN_LOGGED_DAYS = 14;
  var MIN_COVERAGE = 0.8;
  var WEEK_DAYS = 28;
  var WEIGHT_AVG_DAYS = 7;

  function finiteNum(v) {
    if (v == null || v === "" || typeof v === "boolean") return null;
    var n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function has(obj, key) {
    return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
  }

  function z2(n) {
    return n < 10 ? "0" + n : String(n);
  }

  function addDays(iso, delta) {
    var parts = String(iso).slice(0, 10).split("-");
    var dt = new Date(Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2])));
    dt.setUTCDate(dt.getUTCDate() + delta);
    return dt.getUTCFullYear() + "-" + z2(dt.getUTCMonth() + 1) + "-" + z2(dt.getUTCDate());
  }

  function mapBy(rows, key) {
    var by = {};
    (rows || []).forEach(function (row) {
      if (!row || row.date == null) return;
      var day = String(row.date).slice(0, 10);
      if (day.length < 10) return;
      var v = finiteNum(row[key]);
      if (v == null) return;
      by[day] = v;
    });
    return by;
  }

  function windowLabels(opts) {
    opts = opts || {};
    if (opts.labels && opts.labels.length) {
      return opts.labels.map(function (d) {
        return String(d).slice(0, 10);
      });
    }
    if (!opts.windowStart || !opts.windowEnd) return [];
    var out = [];
    var cursor = String(opts.windowStart).slice(0, 10);
    var end = String(opts.windowEnd).slice(0, 10);
    var guard = 0;
    while (cursor <= end && guard < 400) {
      out.push(cursor);
      cursor = addDays(cursor, 1);
      guard += 1;
    }
    return out;
  }

  /** Trailing mean. Missing days are skipped, not zero. One sample is enough. */
  function trailingMean(by, day) {
    var vals = [];
    var k;
    for (k = WEIGHT_AVG_DAYS - 1; k >= 0; k--) {
      var d = addDays(day, -k);
      if (has(by, d)) vals.push(by[d]);
    }
    if (!vals.length) return null;
    var sum = 0;
    for (k = 0; k < vals.length; k++) sum += vals[k];
    return sum / vals.length;
  }

  function loggedCount(labels, intakeBy, foodRows) {
    var extra = {};
    (foodRows || []).forEach(function (row) {
      if (!row || row.date == null) return;
      var day = String(row.date).slice(0, 10);
      if (day.length >= 10) extra[day] = true;
    });
    var n = 0;
    labels.forEach(function (day) {
      if (has(intakeBy, day) || extra[day]) n += 1;
    });
    return n;
  }

  function meanIntake(labels, intakeBy) {
    var sum = 0;
    var n = 0;
    labels.forEach(function (day) {
      if (!has(intakeBy, day)) return;
      sum += intakeBy[day];
      n += 1;
    });
    return n ? sum / n : null;
  }

  /**
   * Predicted change uses pair days only. An unlogged day (no finite
   * intake) is absent from the net, even when the device still has burn.
   * pairedNet, when the card passes it, is that same skip.
   */
  function pairState(labels, intakeBy, burnedBy, pairedNet) {
    var net = {};
    var cum = 0;
    var pairs = 0;
    var sumOut = 0;
    var burnN = 0;
    labels.forEach(function (day) {
      var delta = null;
      if (pairedNet) {
        if (!has(pairedNet, day) || !Number.isFinite(pairedNet[day])) return;
        delta = pairedNet[day];
      } else if (has(intakeBy, day) && has(burnedBy, day)) {
        delta = intakeBy[day] - burnedBy[day];
      }
      if (delta == null) return;
      net[day] = delta;
      cum += delta;
      pairs += 1;
      if (has(burnedBy, day)) {
        sumOut += burnedBy[day];
        burnN += 1;
      }
    });
    return {
      net: net,
      cum: cum,
      pairs: pairs,
      meanBurn: burnN ? sumOut / burnN : null,
    };
  }

  function predictedSeries(labels, net) {
    var run = 0;
    var seen = false;
    return labels.map(function (day) {
      if (has(net, day)) {
        run += net[day];
        seen = true;
      }
      return seen ? run / KCAL_PER_LB : null;
    });
  }

  function weightEndpoints(weights, start, end) {
    var rows = [];
    (weights || []).forEach(function (w) {
      if (!w) return;
      var date = String(w.date || "").slice(0, 10);
      var lbs = finiteNum(w.weight_lbs);
      if (!date || lbs == null) return;
      if (start && date < start) return;
      if (end && date > end) return;
      rows.push({ date: date, lbs: lbs });
    });
    rows.sort(function (a, b) {
      return a.date.localeCompare(b.date);
    });
    if (rows.length < 2) return null;
    var first = rows[0];
    var last = rows[rows.length - 1];
    return { first: first, last: last, actualLb: last.lbs - first.lbs };
  }

  /**
   * Path is the trailing 7-day mean minus the first weigh-in.
   * The last point is the card's scale change (last weigh-in − first),
   * so the line ends on the On scale chip.
   */
  function actualSeries(labels, weightBy, firstLbs, actualLb) {
    var out = labels.map(function (day) {
      var mean = trailingMean(weightBy, day);
      if (mean == null || firstLbs == null) return null;
      return mean - firstLbs;
    });
    if (actualLb != null && out.length) out[out.length - 1] = actualLb;
    return out;
  }

  function sevenDelta(labels, weightBy) {
    var first = null;
    var last = null;
    labels.forEach(function (day) {
      var mean = trailingMean(weightBy, day);
      if (mean == null) return;
      if (first == null) first = mean;
      last = mean;
    });
    if (first == null || last == null) return null;
    return last - first;
  }

  function impliedBurn(meanIn, delta7, windowDays) {
    if (meanIn == null || delta7 == null || !windowDays) return null;
    return meanIn - (delta7 * KCAL_PER_LB) / windowDays;
  }

  function valuesOn(by, labels) {
    return labels.map(function (day) {
      return has(by, day) ? by[day] : null;
    });
  }

  function coverageMessage(loggedDays, windowDays, weightsReady) {
    var pct = windowDays ? Math.round((100 * loggedDays) / windowDays) : 0;
    if (loggedDays < MIN_LOGGED_DAYS || !windowDays || loggedDays / windowDays < MIN_COVERAGE) {
      return (
        "Calibrating — coverage is " +
        loggedDays +
        " of " +
        windowDays +
        " days logged (" +
        pct +
        "%). The line, implied burn, and 4-week review wait for 14 logged days and 80% of this window."
      );
    }
    if (!weightsReady) {
      return "Calibrating — coverage is enough. Two weigh-ins at least 5 days apart are still needed for the scale line.";
    }
    return "";
  }

  function weeklyReview(labels, intakeBy, burnedBy, weightBy, weights) {
    var slice = labels.length > WEEK_DAYS ? labels.slice(labels.length - WEEK_DAYS) : labels.slice();
    if (slice.length < 2) return null;
    var state = pairState(slice, intakeBy, burnedBy, null);
    var ends = weightEndpoints(weights, slice[0], slice[slice.length - 1]);
    var gapLb = null;
    if (ends && state.pairs > 0) {
      gapLb = ends.actualLb - state.cum / KCAL_PER_LB;
    }
    var delta7 = sevenDelta(slice, weightBy);
    var burn = impliedBurn(meanIntake(slice, intakeBy), delta7, slice.length);
    return {
      days: slice.length,
      gapLb: gapLb,
      impliedBurn: burn,
      loggedBurn: state.meanBurn,
    };
  }

  function energyScaleExtension(opts) {
    opts = opts || {};
    var labels = windowLabels(opts);
    var intakeBy = mapBy(opts.intakeRows, "calories");
    var burnedBy = mapBy(opts.burnedRows, "calories");
    var weightBy = mapBy(opts.weights, "weight_lbs");
    var windowDays = labels.length;
    var loggedDays = loggedCount(labels, intakeBy, opts.foodLogRows);
    var paired = pairState(labels, intakeBy, burnedBy, opts.pairedNet || null);
    var burnedSeries = valuesOn(burnedBy, labels);
    var start = labels[0] || "";
    var end = labels.length ? labels[labels.length - 1] : "";
    var ends = labels.length ? weightEndpoints(opts.weights, start, end) : null;
    var card = energyWeightAlignment({
      cumDeltaKcal: paired.pairs > 0 ? paired.cum : null,
      pairDays: paired.pairs,
      weights: opts.weights || [],
      windowStart: start,
      windowEnd: end,
      goalHint: opts.goalHint || "",
    });
    var weightsReady = !!card;
    var coverageOk =
      windowDays > 0 &&
      loggedDays >= MIN_LOGGED_DAYS &&
      loggedDays / windowDays >= MIN_COVERAGE;
    var open = !!(coverageOk && weightsReady);
    var predicted = predictedSeries(labels, paired.net);
    var actual = actualSeries(
      labels,
      weightBy,
      ends ? ends.first.lbs : null,
      card ? card.actualLb : ends ? ends.actualLb : null
    );
    if (card && predicted.length) predicted[predicted.length - 1] = card.expectedLb;
    if (card && actual.length) actual[actual.length - 1] = card.actualLb;
    var finalPredicted = card
      ? card.expectedLb
      : predicted.length
        ? predicted[predicted.length - 1]
        : null;
    var finalActual = card ? card.actualLb : actual.length ? actual[actual.length - 1] : null;
    var delta7 = sevenDelta(labels, weightBy);
    var meanIn = meanIntake(labels, intakeBy);
    var burn = impliedBurn(meanIn, delta7, windowDays);
    var implied = null;
    var weekly = null;
    if (open && burn != null && paired.meanBurn != null) {
      implied = {
        burnPerDay: burn,
        meanIntake: meanIn,
        delta7Lb: delta7,
        windowDays: windowDays,
        loggedBurnPerDay: paired.meanBurn,
        diffPerDay: burn - paired.meanBurn,
      };
    }
    if (open) weekly = weeklyReview(labels, intakeBy, burnedBy, weightBy, opts.weights);
    return {
      open: open,
      loggedDays: loggedDays,
      windowDays: windowDays,
      coverage: windowDays ? loggedDays / windowDays : 0,
      cumDeltaKcal: paired.cum,
      pairDays: paired.pairs,
      predicted: predicted,
      actual: actual,
      burnedSeries: burnedSeries,
      finalPredicted: finalPredicted,
      finalActual: finalActual,
      implied: implied,
      weekly: weekly,
      message: open ? "" : coverageMessage(loggedDays, windowDays, weightsReady),
    };
  }

  function grouped(n) {
    var neg = n < 0;
    var s = String(Math.abs(Math.round(n)));
    var out = "";
    var i;
    for (i = 0; i < s.length; i++) {
      if (i > 0 && (s.length - i) % 3 === 0) out += ",";
      out += s.charAt(i);
    }
    return (neg ? "-" : "") + out;
  }

  function fmtLb(n) {
    if (n == null || !Number.isFinite(n)) return "—";
    var sign = n >= 0 ? "+" : "";
    return sign + n.toFixed(1) + " lb";
  }

  function fmtKcal(n) {
    if (n == null || !Number.isFinite(n)) return "—";
    return grouped(n) + " kcal/day";
  }

  function fmtSignedKcal(n) {
    if (n == null || !Number.isFinite(n)) return "—";
    var sign = n > 0 ? "+" : "";
    return sign + grouped(n) + " kcal/day";
  }

  function attrLb(n) {
    return Number.isFinite(n) ? n.toFixed(1) : "";
  }

  function chartSvg(model) {
    var pred = model.predicted || [];
    var act = model.actual || [];
    var n = pred.length;
    if (!n) return "";
    var lo = Infinity;
    var hi = -Infinity;
    function consider(v) {
      if (v == null || !Number.isFinite(v)) return;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
    var i;
    for (i = 0; i < n; i++) {
      if (pred[i] != null) {
        consider(pred[i] - CAP_LB);
        consider(pred[i] + CAP_LB);
      }
      consider(act[i]);
    }
    if (lo === Infinity) return "";
    if (hi - lo < 1) {
      var mid = (hi + lo) / 2;
      lo = mid - 0.5;
      hi = mid + 0.5;
    }
    var plotL = 8;
    var plotR = 312;
    var plotT = 10;
    var plotB = 118;
    function xOf(idx) {
      var denom = Math.max(n - 1, 1);
      return plotL + (idx / denom) * (plotR - plotL);
    }
    function yOf(v) {
      return plotT + ((hi - v) / (hi - lo)) * (plotB - plotT);
    }
    function segments(pick) {
      var paths = [];
      var seg = [];
      function flush() {
        if (seg.length < 2) {
          seg = [];
          return;
        }
        var d = "M" + seg[0];
        var s;
        for (s = 1; s < seg.length; s++) d += " L" + seg[s];
        paths.push(d);
        seg = [];
      }
      for (i = 0; i < n; i++) {
        var v = pick(i);
        if (v == null || !Number.isFinite(v)) {
          flush();
          continue;
        }
        seg.push(xOf(i).toFixed(1) + " " + yOf(v).toFixed(1));
      }
      flush();
      return paths;
    }
    var band = [];
    var gap = [];
    var segTop = [];
    var segBot = [];
    var segGap = [];
    function flushBand() {
      if (segTop.length < 2) {
        segTop = [];
        segBot = [];
        return;
      }
      var d = "M" + segTop[0];
      var s;
      for (s = 1; s < segTop.length; s++) d += " L" + segTop[s];
      for (s = segBot.length - 1; s >= 0; s--) d += " L" + segBot[s];
      d += " Z";
      band.push(d);
      segTop = [];
      segBot = [];
    }
    function flushGap() {
      if (segGap.length < 2) {
        segGap = [];
        return;
      }
      var d = "M" + segGap[0].p;
      var s;
      for (s = 1; s < segGap.length; s++) d += " L" + segGap[s].p;
      for (s = segGap.length - 1; s >= 0; s--) d += " L" + segGap[s].a;
      d += " Z";
      gap.push(d);
      segGap = [];
    }
    for (i = 0; i < n; i++) {
      if (pred[i] == null || !Number.isFinite(pred[i])) {
        flushBand();
        flushGap();
        continue;
      }
      segTop.push(xOf(i).toFixed(1) + " " + yOf(pred[i] + CAP_LB).toFixed(1));
      segBot.push(xOf(i).toFixed(1) + " " + yOf(pred[i] - CAP_LB).toFixed(1));
      if (act[i] == null || !Number.isFinite(act[i])) {
        flushGap();
      } else {
        segGap.push({
          p: xOf(i).toFixed(1) + " " + yOf(pred[i]).toFixed(1),
          a: xOf(i).toFixed(1) + " " + yOf(act[i]).toFixed(1),
        });
      }
    }
    flushBand();
    flushGap();
    var predPath = segments(function (idx) {
      return pred[idx];
    }).join(" ");
    var actPath = segments(function (idx) {
      return act[idx];
    }).join(" ");
    var label =
      "Predicted change ends at " +
      fmtLb(model.finalPredicted) +
      ". Scale change ends at " +
      fmtLb(model.finalActual) +
      ". Shaded gap. 5 lb band around predicted.";
    var html = '<svg class="ewi-chart" viewBox="0 0 320 132" role="img" aria-label="' + label + '"';
    html += ' data-final-predicted="' + attrLb(model.finalPredicted) + '"';
    html += ' data-final-actual="' + attrLb(model.finalActual) + '">';
    band.forEach(function (d) {
      html += '<path d="' + d + '" fill="rgba(92, 225, 168, 0.16)" stroke="none"></path>';
    });
    gap.forEach(function (d) {
      html += '<path d="' + d + '" fill="rgba(240, 180, 41, 0.28)" stroke="none"></path>';
    });
    if (predPath) {
      html += '<path d="' + predPath + '" fill="none" stroke="#5ce1a8" stroke-width="2"></path>';
    }
    if (actPath) {
      html += '<path d="' + actPath + '" fill="none" stroke="#f0c14d" stroke-width="2"></path>';
    }
    html += "</svg>";
    return html;
  }

  function extensionHtml(model) {
    if (!model) return "";
    if (!model.open) {
      return '<p class="ewi-note">' + (model.message || "Calibrating — coverage is short.") + "</p>";
    }
    var html = chartSvg(model);
    html +=
      '<p class="ewi-note">Green is predicted from logged days. Amber is the 7-day average scale. The shaded gap sits between them, and the band is ±5 lb around predicted. The line ends on the chips above.</p>';
    if (model.implied) {
      html +=
        '<p class="ewi-implied">Implied burn <strong>' +
        fmtKcal(model.implied.burnPerDay) +
        "</strong>. Estimated from your weight trend. Logged burn <strong>" +
        fmtKcal(model.implied.loggedBurnPerDay) +
        "</strong> · difference <strong>" +
        fmtSignedKcal(model.implied.diffPerDay) +
        "</strong>.</p>";
    } else {
      html += '<p class="ewi-implied">Calibrating — implied burn needs a weight trend and logged intake.</p>';
    }
    var week = model.weekly;
    if (week && (week.gapLb != null || week.impliedBurn != null || week.loggedBurn != null)) {
      var name = week.days >= WEEK_DAYS ? "4-week gap" : week.days + "-day gap";
      html +=
        '<p class="ewi-weekly">' +
        name +
        " <strong>" +
        fmtLb(week.gapLb) +
        "</strong> · implied burn <strong>" +
        fmtKcal(week.impliedBurn) +
        "</strong> · logged burn <strong>" +
        fmtKcal(week.loggedBurn) +
        "</strong>.</p>";
    } else {
      html += '<p class="ewi-weekly">Calibrating — the 4-week review needs more of this window.</p>';
    }
    return html;
  }

  var api = {
    KCAL_PER_LB: KCAL_PER_LB,
    TIGHT_LB: TIGHT_LB,
    CAP_LB: CAP_LB,
    MIN_LOGGED_DAYS: MIN_LOGGED_DAYS,
    MIN_COVERAGE: MIN_COVERAGE,
    WEEK_DAYS: WEEK_DAYS,
    isAligned: isAligned,
    energyWeightAlignment: energyWeightAlignment,
    addDays: addDays,
    energyScaleExtension: energyScaleExtension,
    extensionHtml: extensionHtml,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashEnergyWeightAlign = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
