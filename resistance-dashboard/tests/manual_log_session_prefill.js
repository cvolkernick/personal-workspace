#!/usr/bin/env node
/**
 * Manual log session dropdown follows today's training quest (#895).
 * A Pull quest must not stay on the old hardcoded Push default.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");

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

function loadFn(name) {
  return new Function(`${extractFn(name)}; return ${name};`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

const questSessionLetter = loadFn("questSessionLetter");
global.questSessionLetter = questSessionLetter;
const applyManualLogSessionPrefill = loadFn("applyManualLogSessionPrefill");

function payload(opts) {
  const o = opts || {};
  const items = [];
  if (o.title != null) {
    items.push({ slug: "train-session", title: o.title });
    items.push({ slug: "ex-db-row", title: "DB Row (50 lb)" });
  }
  return {
    daily_tasks: {
      groups: o.title == null && !o.emptyTraining ? [] : [{ group: "training", items }],
    },
    coach: {
      today: {
        workout: {
          session_type: o.plan,
          next_session_type: o.next,
          is_rest_day: !!o.rest,
        },
        actions: o.action
          ? [{ id: "train-session", kind: "training", text: o.action }]
          : [],
      },
    },
    sessions: o.last
      ? [{ date: "2026-09-21", session_type: o.last, exercises: [] }]
      : [],
  };
}

const mismatch = payload({
  title: "Complete today's PULL session (4 lifts as prescribed).",
  plan: "push",
  next: "push",
  last: "push",
});
assert(
  questSessionLetter(mismatch) === "pull",
  "Pull quest wins over a Push plan and the last log"
);

const el = { value: "push", dataset: {} };
global.$ = (id) => (id === "session_type" ? el : null);
applyManualLogSessionPrefill(mismatch);
assert(el.value === "pull", "prefill replaces the hardcoded Push default");

assert(
  questSessionLetter(
    payload({ title: "Complete today's PUSH session", plan: "pull", last: "legs" })
  ) === "push",
  "Push quest prefills Push"
);
assert(
  questSessionLetter(
    payload({ title: "Easy LEGS — keep loads moderate; prioritize form.", plan: "push" })
  ) === "legs",
  "Legs quest prefills Legs"
);
assert(
  questSessionLetter(
    payload({
      title: "Already trained today (PULL). Next session: PUSH tomorrow.",
      plan: "push",
      next: "push",
    })
  ) === "pull",
  "already-trained quest keeps today's letter, not tomorrow"
);

const rest = payload({
  title: "Rest / recover today — skip heavy lifting; optional walk or mobility.",
  plan: "push",
  next: "push",
  last: "push",
  rest: true,
});
assert(questSessionLetter(rest) === "", "rest quest does not invent a session");
el.value = "push";
el.dataset = {};
applyManualLogSessionPrefill(rest);
assert(el.value === "", "rest day clears the Push default");

assert(
  questSessionLetter({
    coach: {
      today: {
        workout: { session_type: "rest", is_rest_day: true, next_session_type: "push" },
      },
    },
    sessions: [{ session_type: "push" }],
  }) === "",
  "rest plan ignores next_session_type and the last log"
);
assert(
  questSessionLetter({
    coach: {
      today: {
        workout: { session_type: "pull", is_rest_day: false, next_session_type: "legs" },
      },
    },
    sessions: [{ session_type: "push" }],
  }) === "pull",
  "plan letter is used when the quest list has not arrived"
);
assert(
  questSessionLetter(
    payload({
      action: "Complete today's PULL session (4 lifts as prescribed).",
      plan: "push",
    })
  ) === "pull",
  "coach train-session action supplies the letter before the plan"
);

el.value = "legs";
el.dataset = { userPick: "1" };
applyManualLogSessionPrefill(mismatch);
assert(el.value === "legs", "explicit dropdown change is not overwritten");
const posted = { session_type: el.value };
assert(posted.session_type === "legs", "save posts the user pick, not the Pull quest");
assert(questSessionLetter(mismatch) === "pull", "quest letter is still Pull");

const selectAt = HTML.indexOf('id="session_type"');
assert(selectAt > 0, "session dropdown exists");
const selectEnd = HTML.indexOf("</select>", selectAt);
const select = HTML.slice(selectAt, selectEnd);
assert(select.indexOf('value=""') !== -1, "placeholder option exists");
assert(
  select.indexOf('value=""') < select.indexOf('value="push"'),
  "Pick session is the unmarked default, not Push"
);
assert(
  SRC.indexOf("applyManualLogSessionPrefill(data)") > 0,
  "dashboard load applies the quest prefill"
);
assert(
  SRC.indexOf('el.dataset.userPick = "1"') > 0,
  "changing the dropdown marks an explicit pick"
);

console.log("manual_log_session_prefill: ok");
