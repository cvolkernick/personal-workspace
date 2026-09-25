#!/usr/bin/env node
/**
 * Manual-log exercise select loads that lift's last performance (#920).
 * Matcher is catalog id, exact name, or alias. Calf Raises does not fill
 * from DB Calf Raises. No prior log clears the weight. An untouched log
 * opens from the plan prescription. A later refresh keeps an edited weight
 * and a changed select.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  if (start >= 6 && SRC.slice(start - 6, start) === "async ") start -= 6;
  let i = SRC.indexOf("(", start);
  let depth = 0;
  for (; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "(") depth += 1;
    else if (ch === ")") {
      depth -= 1;
      if (depth === 0) {
        i += 1;
        break;
      }
    }
  }
  const brace = SRC.indexOf("{", i);
  depth = 0;
  for (let j = brace; j < SRC.length; j++) {
    const ch = SRC[j];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, j + 1);
    }
  }
  throw new Error("unclosed function " + name);
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const aliasStart = SRC.indexOf("const LOG_NAME_ALIASES = ");
const aliasEnd = SRC.indexOf("function normExerciseName", aliasStart);
if (aliasStart < 0 || aliasEnd < 0) throw new Error("missing LOG_NAME_ALIASES");

const orderStart = SRC.indexOf("const LIBRARY_SESSION_ORDER = ");
const labelStart = SRC.indexOf("const LIBRARY_GROUP_LABEL = ");
const labelEnd = SRC.indexOf(";", labelStart);
if (orderStart < 0 || labelEnd < 0) throw new Error("missing library labels");

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.id = "";
    this.value = "";
    this.textContent = "";
    this.required = false;
    this.disabled = false;
    this.dataset = {};
    this.listeners = {};
    this._html = "";
    this.parent = null;
  }
  appendChild(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove() {
    if (!this.parent) return;
    this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  addEventListener(type, fn) {
    (this.listeners[type] || (this.listeners[type] = [])).push(fn);
  }
  set innerHTML(html) {
    this._html = String(html);
    this.children = [];
    const re = /<(select|div|button|input|label|span)\b([^>]*)>/g;
    let m;
    while ((m = re.exec(this._html))) {
      const el = new El(m[1]);
      const attrs = m[2];
      const cls = attrs.match(/class="([^"]*)"/);
      if (cls) el.className = cls[1];
      const val = attrs.match(/\bvalue="([^"]*)"/);
      if (val) el.value = val[1];
      el.parent = this;
      this.children.push(el);
    }
  }
  get innerHTML() {
    return this._html;
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  querySelectorAll(sel) {
    const want = parseSel(sel);
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (matches(child, want)) out.push(child);
        walk(child);
      }
    };
    walk(this);
    if (want.last && out.length) return [out[out.length - 1]];
    return out;
  }
}

function parseSel(sel) {
  const last = sel.endsWith(":last-child");
  const base = last ? sel.slice(0, -":last-child".length) : sel;
  let tag = "";
  let cls = "";
  if (base.startsWith(".")) cls = base.slice(1);
  else if (base.includes(".")) {
    const parts = base.split(".");
    tag = parts[0];
    cls = parts[1];
  } else {
    tag = base;
  }
  return { tag, cls, last };
}

function matches(el, want) {
  if (want.tag && el.tag !== want.tag) return false;
  if (!want.cls) return true;
  return String(el.className || "")
    .split(/\s+/)
    .includes(want.cls);
}

const byId = {};
const document = {
  createElement(tag) {
    return new El(tag);
  },
  getElementById(id) {
    return byId[id] || null;
  },
  querySelectorAll(sel) {
    if (sel === "#exercise-rows select.ex-name") {
      const rows = byId["exercise-rows"];
      return rows ? rows.querySelectorAll("select.ex-name") : [];
    }
    return [];
  },
};

const api = new Function(
  "document",
  `const $ = (id) => document.getElementById(id);
${SRC.slice(aliasStart, aliasEnd)}
${SRC.slice(orderStart, labelEnd + 1)}
${extractFn("normExerciseName")}
${extractFn("slugExerciseName")}
${extractFn("catalogById")}
${extractFn("canonicalExerciseId")}
${extractFn("lastPerformanceForLog")}
${extractFn("catalogForLogMatch")}
${extractFn("performanceSetPrefill")}
${extractFn("fillCardFromLastPerformance")}
${extractFn("markManualLogUserTouched")}
${extractFn("onManualLogExerciseChange")}
${extractFn("eventTargetIsSetWeight")}
${extractFn("addSetRow")}
${extractFn("catalogHomeSession")}
${extractFn("libraryLogExercises")}
${extractFn("fillExerciseNameSelect")}
${extractFn("addExerciseRow")}
${extractFn("manualLogFormTouched")}
${extractFn("manualLogPlanPrefills")}
${extractFn("applyManualLogPlanPrefill")}
${extractFn("prefillsFromWorkoutPlan")}
${extractFn("refreshExerciseNameSelects")}
return {
  lastPerformanceForLog,
  performanceSetPrefill,
  canonicalExerciseId,
  addExerciseRow,
  onManualLogExerciseChange,
  applyManualLogPlanPrefill,
  manualLogPlanPrefills,
  refreshExerciseNameSelects,
  manualLogFormTouched,
};`
)(document);

const catalogPath = path.join(ROOT, "..", "fitness", "exercises", "catalog.json");
const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));

const sessions = [
  {
    date: "2026-09-01",
    session_type: "legs",
    exercises: [
      { name: "Calf Raises", sets: [{ weight_lbs: 105, sets: 3, reps: 10 }] },
    ],
  },
  {
    date: "2026-09-08",
    session_type: "legs",
    exercises: [
      { name: "DB Calf Raises", sets: [{ weight_lbs: 40, sets: 3, reps: 12 }] },
    ],
  },
  {
    date: "2026-09-15",
    session_type: "legs",
    exercises: [
      { name: "Calf Raises", sets: [{ weight_lbs: 110, sets: 4, reps: 8 }] },
    ],
  },
];

global.state = { workout_store: { catalog }, sessions };

assert(
  api.canonicalExerciseId("Calf Extensions", catalog) === "calf-raises",
  "catalog name alias"
);
assert(
  api.canonicalExerciseId("Calf Raises", catalog) === "calf-raises",
  "historic name alias"
);
assert(
  api.canonicalExerciseId("DB Calf Raises", catalog) === "db-calf-raises",
  "db calf id"
);
assert(
  api.canonicalExerciseId("standing calf raises", catalog) === "db-calf-raises",
  "standing calf is the dumbbell id"
);

const machine = api.lastPerformanceForLog(sessions, "Calf Extensions", catalog);
const historic = api.lastPerformanceForLog(sessions, "Calf Raises", catalog);
const db = api.lastPerformanceForLog(sessions, "DB Calf Raises", catalog);
const standing = api.lastPerformanceForLog(sessions, "standing calf raises", catalog);
const none = api.lastPerformanceForLog(sessions, "Lying Leg Curl", catalog);

assert(machine && machine.date === "2026-09-15", "latest calf extensions " + JSON.stringify(machine));
assert(machine.weight_lbs === 110 && machine.sets === 4 && machine.reps === 8, "latest calf load");
assert(historic.weight_lbs === 110 && historic.date === "2026-09-15", "historic name matches machine");
assert(db.date === "2026-09-08" && db.weight_lbs === 40 && db.sets === 3 && db.reps === 12, "db calf own log");
assert(standing.weight_lbs === 40, "standing alias uses db log");
assert(none == null, "lying leg curl has no log");

const onlyMachine = [sessions[0]];
assert(
  api.lastPerformanceForLog(onlyMachine, "DB Calf Raises", catalog) == null,
  "db name must not take Calf Raises"
);
assert(
  api.lastPerformanceForLog(onlyMachine, "standing calf raises", catalog) == null,
  "standing alias must not take Calf Raises"
);
assert(
  api.lastPerformanceForLog(onlyMachine, "Calf Extensions", catalog).weight_lbs === 105,
  "extensions still match Calf Raises"
);

const rows = new El("div");
rows.id = "exercise-rows";
byId["exercise-rows"] = rows;

const plan = {
  is_rest_day: false,
  session_type: "legs",
  exercises: [
    {
      name: "Calf Extensions",
      prescription: { weight_lbs: 80, sets: 2, reps: 8 },
    },
  ],
};
const data = {
  workout_store: { plan, catalog },
  sessions,
};

api.addExerciseRow();
api.applyManualLogPlanPrefill(data);

function card() {
  return rows.querySelector(".exercise-card");
}
function readSet(node) {
  return {
    name: (node.querySelector(".ex-name") || {}).value,
    weight: (node.querySelector(".set-weight") || {}).value,
    sets: (node.querySelector(".set-sets") || {}).value,
    reps: (node.querySelector(".set-reps") || {}).value,
  };
}

let row = readSet(card());
assert(row.name === "Calf Extensions", "plan name " + row.name);
assert(row.weight === "80", "plan prescription weight, not last log " + row.weight);
assert(row.sets === "2" && row.reps === "8", "plan sets/reps " + row.sets + "x" + row.reps);
assert(rows.dataset.planSeeded === "1", "seeded once");

api.applyManualLogPlanPrefill({
  workout_store: {
    plan: {
      is_rest_day: false,
      exercises: [
        { name: "Calf Extensions", prescription: { weight_lbs: 999, sets: 9, reps: 9 } },
      ],
    },
    catalog,
  },
  sessions,
});
row = readSet(card());
assert(row.weight === "80" && row.name === "Calf Extensions", "refresh keeps the open prescription");

function changeTo(name) {
  const sel = card().querySelector(".ex-name");
  sel.value = name;
  (sel.listeners.change || []).forEach((fn) => fn());
}

changeTo("DB Calf Raises");
row = readSet(card());
assert(row.name === "DB Calf Raises", "select swap name");
assert(row.weight === "40" && row.sets === "3" && row.reps === "12", "db last performance " + JSON.stringify(row));
assert(card().dataset.userSelect === "1", "select marked changed");

changeTo("Calf Extensions");
row = readSet(card());
assert(
  row.weight === "110" && row.sets === "4" && row.reps === "8",
  "extensions load Calf Raises, not DB " + JSON.stringify(row)
);

const weightInput = card().querySelector(".set-weight");
weightInput.value = "55";
(card().listeners.input || []).forEach((fn) => fn({ target: weightInput }));
assert(card().dataset.userWeight === "1", "weight edit marked");

api.applyManualLogPlanPrefill(data);
api.refreshExerciseNameSelects();
row = readSet(card());
assert(row.name === "Calf Extensions", "refresh keeps the changed select " + row.name);
assert(row.weight === "55", "refresh keeps the edited weight " + row.weight);

changeTo("Lying Leg Curl");
row = readSet(card());
assert(row.name === "Lying Leg Curl", "no-log select");
assert(row.weight === "", "no log clears weight, got " + JSON.stringify(row.weight));
assert(row.sets === "1" && row.reps === "10", "cleared row does not keep the previous load " + row.sets + "x" + row.reps);

const refreshSrc = extractFn("refreshExerciseNameSelects");
assert(!refreshSrc.includes("fillCardFromLastPerformance"), "option refresh does not reload last performance");
assert(!refreshSrc.includes("performanceSetPrefill"), "option refresh does not build a prefill");

const applySrc = extractFn("applyManualLogPlanPrefill");
assert(applySrc.indexOf("manualLogFormTouched") < applySrc.indexOf("addExerciseRow"), "touched check before rewrite");
assert(applySrc.indexOf("planSeeded") < applySrc.indexOf("addExerciseRow"), "seeded check before rewrite");
assert(!applySrc.includes("fillCardFromLastPerformance"), "plan open does not apply last performance");
assert(!applySrc.includes("lastPerformanceForLog"), "plan open does not read last performance");

const addSrc = extractFn("addExerciseRow");
assert(addSrc.includes('addEventListener("change"'), "select has a change handler");
assert(addSrc.includes("onManualLogExerciseChange"), "change handler loads last performance");
assert(!addSrc.includes("fillCardFromLastPerformance(card)"), "creating a row does not load last performance");

assert(!SRC.includes("function logPlanToForm"), "log-this-plan button stays gone");

const loadSrc = extractFn("loadDashboard");
assert(loadSrc.includes("applyManualLogPlanPrefill(data)"), "dashboard open seeds the log");
assert(
  loadSrc.indexOf("applyManualLogSessionPrefill(data)") <
    loadSrc.indexOf("applyManualLogPlanPrefill(data)"),
  "session letter still applies"
);

console.log("ok manual-log-last-perf-920");
