#!/usr/bin/env node
/**
 * Behavioral check: a leaf with task_id + list_id POSTs /api/daily-tasks/complete.
 * Extracts helpers from static/app.js (no new browser harness).
 */
"use strict";

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "static", "app.js"),
  "utf8"
);

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

const questLeafIds = loadFn("questLeafIds");

const inherited = questLeafIds(
  { task_id: "t1" },
  { list_id: "L1", task_id: "p1" },
  ""
);
assert(inherited.ready === true, "group list_id + item task_id is ready");
assert(inherited.tid === "t1" && inherited.lid === "L1", "ids inherited");

const fromRoot = questLeafIds({ id: "t2" }, {}, "L-root");
assert(fromRoot.ready === true && fromRoot.tid === "t2" && fromRoot.lid === "L-root", "root list_id + id alias");

const pending = questLeafIds({ title: "Eat oats" }, {}, "");
assert(pending.ready === false, "preview leaf without ids is not ready");

const fetches = [];
global.fetch = async (url, opts) => {
  fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
  return {
    ok: true,
    status: 200,
    json: async () => ({
      ok: true,
      workout_log: {
        action: "upsert",
        wrote: true,
        name: "DB Flat Press",
        session_type: "push",
        exercise: {
          name: "DB Flat Press",
          quest_seeded: true,
          sets: [{ weight_lbs: 50, sets: 3, reps: 10 }],
          raw: "quest-seeded:50/3/10",
        },
      },
    }),
  };
};
global.showAlert = () => {
  throw new Error("showAlert should not fire on success");
};
global.state = {
  daily_tasks: { summary: { done: 0, total: 2 } },
  meta: { local_today: "2026-08-23" },
  coach: { today: { date: "2026-08-23", workout: { session_type: "rest", next_session_type: "push" } } },
  sessions: [],
};
global.lastSyncedDailyTasks = null;
global.setTimeout = () => {};
global.document = {
  documentElement: { dataset: {} },
  createElement: (tag) => ({ className: "", textContent: "", tagName: String(tag).toUpperCase() }),
  querySelector: () => null,
  addEventListener() {},
};

global.looksLikeLiftQuest = loadFn("looksLikeLiftQuest");
global.applyQuestUncheckToSessions = loadFn("applyQuestUncheckToSessions");
global.applyQuestUpsertToSessions = loadFn("applyQuestUpsertToSessions");
global.pplSessionTypeFromState = loadFn("pplSessionTypeFromState");
global.loggedExercisesForDay = loadFn("loggedExercisesForDay");
global.patchLocalQuestCompleted = loadFn("patchLocalQuestCompleted");
global.paintQuestMeter = loadFn("paintQuestMeter");
global.renderHistory = () => {};
global.renderTodayLoggedLifts = () => {};
global.applyWorkoutLogToLocalState = loadFn("applyWorkoutLogToLocalState");
global.unlockQuestCard = loadFn("unlockQuestCard");
global.onDailyQuestClick = loadFn("onDailyQuestClick");
const onDailyQuestClick = global.onDailyQuestClick;
const looksLikeLiftQuest = global.looksLikeLiftQuest;
const applyQuestUncheckToSessions = global.applyQuestUncheckToSessions;
const applyQuestUpsertToSessions = global.applyQuestUpsertToSessions;
const loggedExercisesForDay = global.loggedExercisesForDay;

function makeBtn(attrs) {
  const classes = new Set(["quest-card"]);
  if (attrs.pending) classes.add("quest-card-pending");
  if (attrs.done) classes.add("is-done");
  const attributes = {
    "data-task-id": attrs.taskId || "",
    "data-list-id": attrs.listId || "",
    "data-parent-id": attrs.parentId || "",
    "data-group": attrs.group || "",
    "data-title": attrs.title || "",
    "data-slug": attrs.slug || "",
  };
  const groupName = attrs.group || "training";
  const btn = {
    disabled: !!attrs.disabled,
    removed: false,
    textContent: attrs.title || "",
    classList: {
      contains: (c) => classes.has(c),
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
    },
    getAttribute: (k) => attributes[k] || "",
    setAttribute: (k, v) => {
      attributes[k] = String(v);
    },
    removeAttribute: (k) => {
      delete attributes[k];
    },
    closest: (sel) => (sel === ".quest-group" ? { getAttribute: (k) => (k === "data-group" ? groupName : ""), querySelector: () => null, querySelectorAll: () => [], classList: { add: () => {}, remove: () => {} }, appendChild: () => {} } : sel === ".quest-card" ? btn : null),
    remove: () => {
      btn.removed = true;
    },
  };
  return btn;
}

async function click(btn) {
  const ev = {
    target: {
      closest: (sel) => (sel === ".quest-card" ? btn : null),
    },
    preventDefault() {},
  };
  await onDailyQuestClick(ev);
}

(async () => {
  assert(looksLikeLiftQuest("training", "DB Flat Press (50 lb 3×10)", "ex-db-flat-press") === true, "ex-* training leaf is a lift");
  assert(looksLikeLiftQuest("nutrition", "Next meal: Chicken · 210g", "meal-0-chicken-0") === false, "meal is not a lift");
  assert(looksLikeLiftQuest("other", "Drink 3L water", "action-hydration-0") === false, "hydration is not a lift");
  assert(looksLikeLiftQuest("training", "Complete today's PUSH session", "train-session") === false, "session title is not a lift");

  const ready = makeBtn({
    taskId: "t1",
    listId: "L1",
    parentId: "p1",
    group: "training",
    title: "DB Flat Press (50 lb 3×10)",
    slug: "ex-db-flat-press",
  });
  await click(ready);
  assert(fetches.length === 1, "ready leaf should POST once");
  assert(fetches[0].url === "/api/daily-tasks/complete", "POST path is /api/daily-tasks/complete");
  assert(fetches[0].method === "POST", "method is POST");
  const body = JSON.parse(fetches[0].body);
  assert(body.task_id === "t1" && body.list_id === "L1", "body carries leaf ids");
  assert(body.group === "training", "body carries quest group for lift auto-log");
  assert(body.title === "DB Flat Press (50 lb 3×10)", "body carries title");
  assert(body.slug === "ex-db-flat-press", "body carries slug");
  assert(body.completed === true, "first click completes");
  assert(body.date === "2026-08-23", "body carries viewer civil day");
  assert(body.session_type === "push", "rest-gated Today still sends PPL session_type");
  assert(body.next_session_type === "push", "body carries next_session_type");
  assert(ready.classList.contains("is-done"), "lift card stays visible as done");
  assert(ready.disabled === false, "done lift stays clickable for uncheck");
  assert(
    global.state.sessions.length === 1 &&
      global.state.sessions[0].exercises[0].name === "DB Flat Press",
    "complete upserts the lift into today's local sessions"
  );
  const painted = loggedExercisesForDay(global.state.sessions, "2026-08-23");
  assert(painted.length === 1 && painted[0].name === "DB Flat Press", "logged today includes the lift");
  assert(
    applyQuestUpsertToSessions(
      [{ date: "2026-08-23", session_type: "push", exercises: [{ name: "DB Flat Press", quest_seeded: true }] }],
      { action: "upsert", name: "DB Flat Press", session_type: "push", exercise: { name: "DB Flat Press" } },
      "2026-08-23"
    )[0].exercises.length === 1,
    "duplicate complete does not invent a second row"
  );

  fetches.length = 0;
  global.fetch = async (url, opts) => {
    fetches.push({ url, method: (opts && opts.method) || "GET", body: opts && opts.body });
    return {
      ok: true,
      status: 200,
      json: async () => ({ ok: true, workout_log: { action: "uncheck_remove", name: "DB Flat Press" } }),
    };
  };
  global.state = {
    daily_tasks: { summary: { done: 1, total: 2 } },
    meta: { local_today: "2026-08-23" },
    sessions: [
      {
        date: "2026-08-23",
        session_type: "push",
        exercises: [
          { name: "DB Flat Press", quest_seeded: true, raw: "quest-seeded:50/3/10" },
          { name: "Cable Fly", quest_seeded: false },
        ],
      },
    ],
  };
  let historySessions = null;
  global.renderHistory = (sessions) => {
    historySessions = sessions;
  };
  const doneLift = makeBtn({
    taskId: "t1",
    listId: "L1",
    parentId: "p1",
    done: true,
    group: "training",
    title: "DB Flat Press (50 lb 3×10)",
    slug: "ex-db-flat-press",
  });
  await click(doneLift);
  assert(fetches.length === 1, "done lift should POST uncheck");
  const uncheckBody = JSON.parse(fetches[0].body);
  assert(uncheckBody.completed === false, "second click sends completed:false");
  assert(uncheckBody.list_id === "L1" && uncheckBody.task_id === "t1", "uncheck keeps leaf ids");
  assert(uncheckBody.parent_id === "p1", "uncheck keeps parent_id");
  assert(uncheckBody.group === "training", "uncheck keeps group");
  assert(uncheckBody.title === "DB Flat Press (50 lb 3×10)", "uncheck keeps title");
  assert(uncheckBody.slug === "ex-db-flat-press", "uncheck keeps slug");
  assert(!doneLift.classList.contains("is-done"), "uncheck clears done state");
  assert(
    historySessions &&
      historySessions[0].exercises.length === 1 &&
      historySessions[0].exercises[0].name === "Cable Fly",
    "uncheck_remove drops the seeded lift from today's log"
  );

  const kept = applyQuestUncheckToSessions(
    [
      {
        date: "2026-08-23",
        exercises: [
          { name: "DB Flat Press", quest_seeded: false, sets: [{ weight_lbs: 55 }] },
        ],
      },
    ],
    { action: "uncheck_remove", name: "DB Flat Press" },
    "2026-08-23"
  );
  assert(kept[0].exercises.length === 1, "edited row survives local uncheck patch");

  fetches.length = 0;
  const doneMeal = makeBtn({
    taskId: "t2",
    listId: "L1",
    done: true,
    group: "nutrition",
    title: "Next meal: Chicken · 210g",
    slug: "meal-0-chicken-0",
  });
  await click(doneMeal);
  assert(fetches.length === 0, "done meal card does not POST uncheck");

  fetches.length = 0;
  let alerted = "";
  global.showAlert = (msg) => {
    alerted = String(msg || "");
  };
  const stale = makeBtn({
    taskId: "t9",
    listId: "L9",
    pending: true,
    disabled: true,
  });
  await click(stale);
  assert(fetches.length === 1, "leaf with ids still posts even if leftover pending/disabled");
  assert(fetches[0].url === "/api/daily-tasks/complete", "stale-ready still hits complete");

  fetches.length = 0;
  alerted = "";
  const bare = makeBtn({ pending: true });
  await click(bare);
  assert(fetches.length === 0, "leaf without ids must not POST complete");
  assert(alerted.length > 0, "missing ids show an honest error");

  fetches.length = 0;
  alerted = "";
  global.fetch = async () => {
    fetches.push({ url: "/api/daily-tasks/complete", method: "POST" });
    return { ok: false, status: 400, json: async () => ({ ok: false, error: "Google Tasks not configured" }) };
  };
  const failBtn = makeBtn({ taskId: "t1", listId: "L1" });
  await click(failBtn);
  assert(failBtn.disabled === false, "failure re-enables the control");
  assert(!failBtn.classList.contains("is-completing"), "failure clears completing spinner");
  assert(alerted.indexOf("Google Tasks") >= 0, "GT failure is honest");

  const listeners = [];
  global.document = {
    documentElement: { dataset: {} },
    addEventListener(type, fn) {
      listeners.push({ type, fn });
    },
  };
  const bindDailyQuestClicks = loadFn("bindDailyQuestClicks");
  bindDailyQuestClicks();
  bindDailyQuestClicks();
  assert(listeners.length === 2, "quest click + retry bound once each");
  assert(listeners.every((l) => l.type === "click"), "both handlers are click");
  assert(listeners[0].fn === onDailyQuestClick, "bound handler is onDailyQuestClick");

  console.log("ok");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
