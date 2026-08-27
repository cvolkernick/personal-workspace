#!/usr/bin/env node
/**
 * #263: mini battery markup + sleep-band fill. No invented Hidrate rows.
 */
"use strict";

const hbb = require("../static/hidrate-bottle.js");

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

assert(hbb.clampPct(null) === null, "null percent stays unknown");
assert(hbb.clampPct("") === null, "empty percent stays unknown");
assert(hbb.clampPct("nope") === null, "non-numeric stays unknown");
assert(hbb.clampPct(-12) === 0, "clamp floor 0");
assert(hbb.clampPct(140) === 100, "clamp ceiling 100");
assert(hbb.clampPct(68) === 68, "in-range percent unchanged");

assert(hbb.fillLevel(0) === "critical", "0 is critical");
assert(hbb.fillLevel(24) === "critical", "<25 critical (sleep band)");
assert(hbb.fillLevel(25) === "low", "25 is low");
assert(hbb.fillLevel(49) === "low", "<50 low");
assert(hbb.fillLevel(50) === "ok", "50 is ok");
assert(hbb.fillLevel(84) === "ok", "<85 ok");
assert(hbb.fillLevel(85) === "full", "85 is full");
assert(hbb.fillLevel(100) === "full", "100 is full");

const charged = hbb.renderMiniHtml("Spark", 68);
assert(charged.includes('class="sb-shell'), "charged uses sb-shell");
assert(charged.includes("sb-fill-wrap"), "charged uses sb-fill-wrap");
assert(charged.includes('class="sb-fill ok"'), "68% uses sleep ok band");
assert(charged.includes("height:68%"), "fill height matches percent");
assert(charged.includes("68%"), "shows percent label");
assert(charged.includes("Spark"), "keeps bottle name");
assert(!charged.includes("height:100%"), "does not fake a full battery");
assert(!charged.includes("width:68%"), "standing fill is height, not landscape width");

const empty = hbb.renderMiniHtml("Bottle", null);
assert(empty.includes("—"), "unavailable shows em dash");
assert(empty.includes("height:0%"), "unavailable fill is empty");
assert(!empty.includes("sb-fill critical"), "unavailable has no fake level tint");
assert(!empty.includes("height:100%"), "unavailable is not a fake 100%");

const el = { innerHTML: "", title: "", removeAttribute: function () {} };
hbb.paint(
  { hidrate_bottle: { available: true, percent: 12, name: "Puck", field: "batteryLevel" } },
  el
);
assert(el.innerHTML.includes('class="sb-fill critical"'), "12% is critical");
assert(el.innerHTML.includes("height:12%"), "paint fill matches 12");
assert(el.title === "Hidrate batteryLevel", "keeps field tooltip");

const missing = { innerHTML: "", title: "" };
hbb.paint({ hidrate_bottle: { available: false, percent: null, status: "missing_field" } }, missing);
assert(missing.innerHTML.includes("—"), "missing_field is honest dash");
assert(missing.title === "Hidrate Bottle has no charge field", "keeps missing_field tooltip");
assert(!missing.innerHTML.includes("height:100%"), "missing_field is not 100%");

const none = { innerHTML: "", title: "stale", removeAttribute: function (k) { if (k === "title") this.title = ""; } };
hbb.paint({}, none);
assert(none.innerHTML.includes("Bottle"), "no payload still says Bottle");
assert(none.innerHTML.includes("—"), "no payload is dash");
assert(none.title === "", "no payload drops title");

console.log("ok hidrate-bottle-mini");
