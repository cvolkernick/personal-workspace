#!/usr/bin/env node
/* Plan 2026-10-26 AC13: steps chart model — bars, rolling line, target only when non-null. */
"use strict";
const steps = require("../static/trends-steps.js");
function assert(c, m) { if (!c) { console.error("FAIL:", m); process.exit(1); } }

const days = [
  { date: "2026-10-19", steps: 5000, partial: false, rolling_7d: null },
  { date: "2026-10-20", steps: null, partial: false, rolling_7d: null },
  { date: "2026-10-25", steps: 2770, partial: false, rolling_7d: 5779 },
  { date: "2026-10-26", steps: 6315, partial: true, rolling_7d: 5600 },
];
const weeks = [
  { week_start: "2026-10-19", week_end: "2026-10-25", avg_steps: 5779, step_days: 7, steps_used: true, complete: true },
  { week_start: "2026-10-26", week_end: "2026-11-01", avg_steps: null, step_days: 0, steps_used: false, complete: false },
];
const nullTarget = steps.buildModel({ days, weeks, step_target: { value: null } });
assert(nullTarget.bars[1] === null, "missing day is no bar (null, not 0)");
assert(nullTarget.bars[0] === 5000, "bar value");
assert(nullTarget.rolling[0] === null && nullTarget.rolling[2] === 5779, "rolling line keeps gaps");
assert(nullTarget.barColors[3] === steps.COLORS.barToday && nullTarget.partial[3], "today lighter + partial");
assert(nullTarget.barColors[0] === steps.COLORS.bar, "past bar color");
assert(!nullTarget.hasTarget && nullTarget.targetLine === null, "no target line when null");
assert(nullTarget.targetChip === steps.NO_TARGET, "null target shows chip");
let ds = steps.datasets(nullTarget);
assert(ds.length === 2 && ds[0].type === "bar" && ds[1].type === "line" && ds[1].spanGaps === false, "bars + rolling only");
assert(nullTarget.weekChip.value === "5,779" && /7\/7/.test(nullTarget.weekChip.sub), "chip = last completed week avg + days");
assert(JSON.stringify(nullTarget.mondays) === "[0,3]", "Monday ticks");

const withTarget = steps.buildModel({ days, weeks, step_target: { value: 7279 } });
ds = steps.datasets(withTarget);
assert(ds.length === 3 && ds[2].borderDash && ds[2].label === "Coach target 7,279", "dashed target line labeled");
assert(withTarget.targetLine.every((v) => v === 7279), "flat target");
assert(withTarget.targetChip === null, "no chip when target set");
assert(JSON.stringify(steps.RANGES) === "[4,8,13]" && steps.DEFAULT_WEEKS === 8, "4/8/13 toggle, default 8");
console.log("trends_steps.js OK");
