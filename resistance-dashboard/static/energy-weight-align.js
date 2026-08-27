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

  var api = {
    KCAL_PER_LB: KCAL_PER_LB,
    TIGHT_LB: TIGHT_LB,
    CAP_LB: CAP_LB,
    isAligned: isAligned,
    energyWeightAlignment: energyWeightAlignment,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashEnergyWeightAlign = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
