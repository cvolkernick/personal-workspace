"use strict";
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");

function extractFn(src, name) {
  const re = new RegExp("(?:async\\s+)?function\\s+" + name + "\\s*\\(");
  const m = re.exec(src);
  if (!m) throw new Error("missing " + name);
  const start = src.indexOf("{", m.index);
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}") {
      depth--;
      if (depth === 0) return src.slice(m.index, i + 1);
    }
  }
  throw new Error("unclosed " + name);
}

const startFlag = html.indexOf("let coachRefreshInFlight");
const loadFn = extractFn(html, "loadCoach");
const stampFn = extractFn(html, "stampCoachUpdated");
const bundle = html.slice(startFlag, html.indexOf("async function loadCoach")) + stampFn + "\n" + loadFn;

let fetchImpl = async () => ({ ok: true, json: async () => ({ ok: true }) });
const btn = { disabled: false, textContent: "Refresh" };
const updated = { textContent: "" };
const toasts = [];
const renders = [];
const document = {
  getElementById(id) {
    if (id === "btn-coach-refresh") return btn;
    if (id === "coach-updated") return updated;
    return null;
  },
};
function toast(msg) { toasts.push(msg); }
function renderCoach(plan) { renders.push(plan); }
let fetchCalls = 0;
async function fetch(url) {
  fetchCalls++;
  return fetchImpl(url);
}

const loadCoach = new Function(
  "document",
  "toast",
  "renderCoach",
  "fetch",
  bundle + "\nreturn loadCoach;"
)(document, toast, renderCoach, fetch);

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

(async () => {
  // Non-force must not touch button or toast.
  btn.disabled = false;
  btn.textContent = "Refresh";
  await loadCoach();
  assert(btn.disabled === false, "non-force disabled");
  assert(btn.textContent === "Refresh", "non-force label");
  assert(toasts.length === 0, "non-force toasted");
  assert(updated.textContent === "", "non-force stamped");
  assert(fetchCalls === 1, "non-force fetch");

  // Force success: in-flight label, toast, stamp, restore.
  let inFlightLabel = null;
  let inFlightDisabled = null;
  let stackedFetches = 0;
  fetchImpl = async (url) => {
    inFlightLabel = btn.textContent;
    inFlightDisabled = btn.disabled;
    stackedFetches++;
    const second = loadCoach({ refresh: true });
    await new Promise((r) => setTimeout(r, 5));
    await second;
    assert(String(url).includes("refresh=1"), "missing refresh=1: " + url);
    return { ok: true, json: async () => ({ ok: true }) };
  };
  toasts.length = 0;
  await loadCoach({ refresh: true });
  assert(inFlightDisabled === true, "force not disabled in flight");
  assert(inFlightLabel === "Refreshing…", "force label in flight: " + inFlightLabel);
  assert(btn.disabled === false, "force not restored");
  assert(btn.textContent === "Refresh", "force label not restored");
  assert(toasts.join("|") === "Coach refreshed", "success toast: " + toasts.join("|"));
  assert(updated.textContent.startsWith(" · Updated "), "stamp: " + updated.textContent);
  assert(/ · Updated \d{2}:\d{2}$/.test(updated.textContent), "stamp format: " + updated.textContent);
  assert(stackedFetches === 1, "stacked force fetches: " + stackedFetches);

  // HTTP ok / plan.ok false → failure toast, no extra stamp change besides previous.
  const prevStamp = updated.textContent;
  fetchImpl = async () => ({ ok: true, json: async () => ({ ok: false, error: "nope" }) });
  toasts.length = 0;
  await loadCoach({ refresh: true });
  assert(toasts.join("|") === "Coach refresh failed", "plan.ok false toast");
  assert(updated.textContent === prevStamp, "failure overwrote stamp");
  assert(renders[renders.length - 1].ok === false, "inline error not rendered");

  // Throw → failure toast + renderCoach error.
  fetchImpl = async () => { throw new Error("network"); };
  toasts.length = 0;
  await loadCoach({ refresh: true });
  assert(toasts.join("|") === "Coach refresh failed", "throw toast");
  assert(renders[renders.length - 1].ok === false, "throw render");
  assert(btn.disabled === false, "throw did not restore button");

  console.log("OK");
})().catch((e) => {
  console.error(e.stack || e);
  process.exit(1);
});
