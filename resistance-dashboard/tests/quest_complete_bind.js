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
  return { ok: true, status: 200, json: async () => ({ ok: true }) };
};
global.showAlert = () => {
  throw new Error("showAlert should not fire on success");
};
global.state = { daily_tasks: { summary: { done: 0, total: 2 } } };
global.setTimeout = () => {};
global.document = {
  documentElement: { dataset: {} },
  createElement: (tag) => ({ className: "", textContent: "", tagName: String(tag).toUpperCase() }),
  querySelector: () => null,
  addEventListener() {},
};

global.unlockQuestCard = loadFn("unlockQuestCard");
global.onDailyQuestClick = loadFn("onDailyQuestClick");
const onDailyQuestClick = global.onDailyQuestClick;

function makeBtn(attrs) {
  const classes = new Set(["quest-card"]);
  if (attrs.pending) classes.add("quest-card-pending");
  const attributes = {
    "data-task-id": attrs.taskId || "",
    "data-list-id": attrs.listId || "",
    "data-parent-id": attrs.parentId || "",
  };
  const btn = {
    disabled: !!attrs.disabled,
    removed: false,
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
    closest: (sel) => (sel === ".quest-group" ? { getAttribute: (k) => (k === "data-group" ? "training" : ""), querySelector: () => null, querySelectorAll: () => [], classList: { add: () => {} }, appendChild: () => {} } : sel === ".quest-card" ? btn : null),
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
  const ready = makeBtn({ taskId: "t1", listId: "L1", parentId: "p1" });
  await click(ready);
  assert(fetches.length === 1, "ready leaf should POST once");
  assert(fetches[0].url === "/api/daily-tasks/complete", "POST path is /api/daily-tasks/complete");
  assert(fetches[0].method === "POST", "method is POST");
  const body = JSON.parse(fetches[0].body);
  assert(body.task_id === "t1" && body.list_id === "L1", "body carries leaf ids");
  assert(body.group === "training", "body carries quest group for lift auto-log");

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
