/**
 * FitDash workout history: weight × sets × reps next to volume.
 * Matches GitHub log triples: ``225 lbs x 3 x 8``.
 */
(function (root) {
  "use strict";

  function fmtWeightLbs(w) {
    var n = Number(w);
    if (!isFinite(n)) return "";
    if (Math.abs(n - Math.round(n)) < 1e-9) return String(Math.round(n));
    return String(n);
  }

  function formatSet(st) {
    if (!st) return "";
    var w = fmtWeightLbs(st.weight_lbs);
    if (!w || st.sets == null || st.reps == null) return "";
    return w + " lbs x " + st.sets + " x " + st.reps;
  }

  function formatSets(ex) {
    if (ex && typeof ex.sets_label === "string" && ex.sets_label.trim()) {
      return ex.sets_label.trim();
    }
    var sets = (ex && ex.sets) || [];
    var parts = [];
    for (var i = 0; i < sets.length; i++) {
      var lab = formatSet(sets[i]);
      if (lab) parts.push(lab);
    }
    return parts.join(", ");
  }

  function formatVolume(vol) {
    var n = Math.round(Number(vol) || 0);
    try {
      return n.toLocaleString();
    } catch (err) {
      return String(n);
    }
  }

  function formatExerciseLine(ex) {
    var name = (ex && ex.name) || "Exercise";
    var pr = ex && ex.is_pr ? " (PR)" : "";
    var vol = formatVolume(ex && ex.volume);
    var setsLabel = formatSets(ex);
    if (setsLabel) return name + pr + " (" + setsLabel + " · " + vol + " vol)";
    return name + pr + " (" + vol + " vol)";
  }

  var api = {
    fmtWeightLbs: fmtWeightLbs,
    formatSet: formatSet,
    formatSets: formatSets,
    formatExerciseLine: formatExerciseLine,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  if (root) root.FitDashHistorySets = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
