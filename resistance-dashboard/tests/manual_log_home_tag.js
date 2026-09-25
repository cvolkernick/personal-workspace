#!/usr/bin/env node
/**
 * Manual-log optgroup uses the first push/pull/legs tag in catalog order (#919).
 * Back Extension Machine (legs, pull) groups under Legs. Smith Shrugs stays Pull.
 * The Session type dropdown is the save key; the group label is not posted.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
const PPL = ["push", "pull", "legs"];

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
  if (start >= 6 && SRC.slice(start - 6, start) === "async ") start -= 6;
  const brace = SRC.indexOf("{", start);
  let depth = 0;
  for (let i = brace; i < SRC.length; i++) {
    const ch = SRC[i];
    if (ch === "{") depth += 1;
    else if (ch === "}") {
      depth -= 1;
      if (depth === 0) return SRC.slice(start, i + 1);
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

const labelStart = SRC.indexOf("const LIBRARY_GROUP_LABEL = ");
const labelEnd = SRC.indexOf(";", labelStart);
if (labelStart < 0 || labelEnd < 0) throw new Error("missing LIBRARY_GROUP_LABEL");

class El {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.label = "";
    this.value = "";
    this.textContent = "";
    this.required = false;
    this.disabled = false;
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  set innerHTML(value) {
    if (value === "") this.children = [];
  }
}

const api = new Function(
  "document",
  `const LIBRARY_SESSION_ORDER = ${JSON.stringify(PPL)};
${SRC.slice(labelStart, labelEnd + 1)}
${extractFn("catalogHomeSession")}
${extractFn("libraryLogExercises")}
${extractFn("fillExerciseNameSelect")}
return { catalogHomeSession, libraryLogExercises, fillExerciseNameSelect };`
)({
  createElement(tag) {
    return new El(tag);
  },
});

function groupLabel(sel, name) {
  for (const node of sel.children) {
    if (node.tag !== "optgroup") continue;
    for (const opt of node.children) {
      if (opt.value === name) return node.label;
    }
  }
  return null;
}

assert(api.catalogHomeSession(["legs", "pull"]) === "legs", "legs then pull");
assert(api.catalogHomeSession(["pull", "push"]) === "pull", "pull then push");
assert(api.catalogHomeSession(["push"]) === "push", "push only");
assert(api.catalogHomeSession(["Push"]) === "push", "case");
assert(api.catalogHomeSession(["core", "push"]) === "push", "skip non-ppl");
assert(api.catalogHomeSession(["core"]) === "core", "non-ppl falls through");
assert(api.catalogHomeSession([]) === "other", "empty");
assert(api.catalogHomeSession(null) === "other", "null");

const homeSrc = extractFn("catalogHomeSession");
assert(
  !homeSrc.includes("LIBRARY_SESSION_ORDER.find"),
  "home tag must not scan LIBRARY_SESSION_ORDER"
);
assert(homeSrc.includes("tags.find"), "home tag scans catalog order");

const catalogs = [
  path.join(ROOT, "fitness", "exercises", "catalog.json"),
  path.join(ROOT, "..", "fitness", "exercises", "catalog.json"),
];

for (const catalogPath of catalogs) {
  const catalog = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  global.state = { workout_store: { catalog } };
  const items = api.libraryLogExercises();
  const byName = Object.fromEntries(items.map((it) => [it.name, it.session]));
  assert(byName["Back Extension Machine"] === "legs", catalogPath + " back extension");
  assert(byName["Smith Shrugs"] === "pull", catalogPath + " smith shrugs");
  assert(byName["DB Flat Press"] === "push", catalogPath + " db flat press");

  const multi = (catalog.exercises || []).filter((ex) => {
    if (!ex || !ex.available) return false;
    const tags = (ex.session_types || []).map((t) => String(t).toLowerCase());
    return tags.filter((t) => PPL.includes(t)).length > 1;
  });
  assert(
    multi.map((ex) => ex.id).sort().join(",") === "back-extension,smith-shrugs",
    catalogPath + " unexpected multi-tag lifts: " + multi.map((ex) => ex.id).join(",")
  );

  for (const ex of catalog.exercises || []) {
    if (!ex || !ex.available || !ex.name) continue;
    const home = api.catalogHomeSession(ex.session_types);
    assert(
      byName[ex.name] === home,
      ex.id + " grouped " + byName[ex.name] + " expected " + home
    );
  }

  const sel = new El("select");
  api.fillExerciseNameSelect(sel, "");
  assert(groupLabel(sel, "Back Extension Machine") === "Legs", catalogPath + " Legs optgroup");
  assert(groupLabel(sel, "Smith Shrugs") === "Pull", catalogPath + " Pull optgroup");
  assert(groupLabel(sel, "DB Flat Press") === "Push", catalogPath + " Push optgroup");
  assert(
    sel.children.every((node) => node.tag === "option" || node.tag === "optgroup"),
    "dropdown is options only"
  );
}

const fill = extractFn("fillExerciseNameSelect");
assert(fill.includes("groups.set(it.session"), "optgroup key is the home tag");
assert(fill.includes("og.label = LIBRARY_GROUP_LABEL[key]"), "label is display only");

const submit = extractFn("submitWorkout");
assert(
  submit.includes('session_type: $("session_type").value'),
  "save posts the Session type dropdown"
);
assert(!submit.includes("optgroup"), "save does not read the optgroup");
assert(!submit.includes("LIBRARY_GROUP_LABEL"), "save does not read the group label");
assert(!submit.includes("catalogHomeSession"), "save does not use the home tag");

console.log("ok manual-log-home-tag-919");
