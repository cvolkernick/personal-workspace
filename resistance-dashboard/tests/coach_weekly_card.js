#!/usr/bin/env node
/** Weekly coach card lines: building gate, protein gap, TDEE, steps-first advisory. */
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");

function extractFn(name) {
  const start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    if (SRC[i] === "{") depth += 1;
    else if (SRC[i] === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}
const { coachWeeklyLines } = new Function(`${extractFn("coachWeeklyLines")}; return { coachWeeklyLines };`)();
function assert(c, m) { if (!c) { console.error("FAIL:", m); process.exit(1); } }

assert(coachWeeklyLines(null).length === 0, "null -> no lines");
const building = coachWeeklyLines({
  status: "building", text: "Weekly data building (1/2 counted weeks)",
  week: { start: "2026-10-26" }, averages: {}, days_logged: { weigh_ins: "6/7", food: "7/7" }, counted: true,
  advisory: { detail: "Weekly data building (1/2 counted weeks)" },
});
assert(building[0] === "Weekly data building (1/2 counted weeks)", "building text first");
assert(building.filter((l) => l.includes("building")).length === 1, "building shown once");
assert(!building.some((l) => /null|NaN|undefined/.test(l)), "no null/NaN in building card: " + building.join(" | "));

const ready = coachWeeklyLines({
  status: "ready", week: { start: "2026-10-26" },
  averages: { calories: { avg: 2010, wow: -20 }, steps: { avg: 7400, wow: 300 }, bodyweight: { avg: 199.8, wow: -0.1 } },
  days_logged: { weigh_ins: "7/7", food: "7/7" }, counted: true, noise: [],
  tdee: { tdee: 2050, weeks_used: 2 },
  protein_chip: { show: true, days_under_170g: 3 },
  advisory: { action: "steps_pull_earlier", detail: "Below the band: steps are the lever." },
});
assert(ready.some((l) => l.startsWith("TDEE ~2,050 ± 150")), "tdee line");
assert(ready.some((l) => l.startsWith("Protein gap: 3 days")), "protein gap line");
assert(ready.some((l) => l.includes("steps are the lever")), "advisory line");
assert(!ready.some((l) => l.includes("building")), "no building when ready");
console.log("ok coach_weekly_card");
