#!/usr/bin/env node
/**
 * #955: More-tab toggle + localStorage persistence.
 * Extracts the pure helpers from static/app.js (no browser harness).
 * On macOS, Quick Look screenshots the More tab with Ask collapsed.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "static", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");

function extractFn(name) {
  let start = SRC.indexOf(`function ${name}`);
  if (start < 0) throw new Error("missing function " + name);
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

function loadFns() {
  const names = [
    "moreCollapseKeys",
    "readMoreCollapse",
    "applyMoreSection",
    "toggleMoreSection",
    "commitMoreToggle",
  ];
  const src = names.map(extractFn).join("\n");
  return new Function(`${src}; return { ${names.join(", ")} };`)();
}

function assert(cond, msg) {
  if (!cond) {
    console.error("FAIL:", msg);
    process.exit(1);
  }
}

function fakeHead(expanded) {
  const attributes = {
    "aria-expanded": expanded ? "true" : "false",
    "data-collapse": "more-targets",
    "aria-controls": "more-targets-body",
  };
  const classes = new Set();
  return {
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attributes, name) ? attributes[name] : null;
    },
    setAttribute(name, value) {
      attributes[name] = String(value);
    },
    classList: {
      toggle(name, force) {
        const on = force === undefined ? !classes.has(name) : !!force;
        if (on) classes.add(name);
        else classes.delete(name);
        return on;
      },
      contains(name) {
        return classes.has(name);
      },
    },
  };
}

function fakeBody(value) {
  return { hidden: false, field: { value: value } };
}

const api = loadFns();
const keys = api.moreCollapseKeys();
assert(keys.length === 9, "nine More sections");
assert(keys.indexOf("more-ask") === 0, "ask is a More key");
assert(keys.indexOf("more-connections") === keys.length - 1, "connections is a More key");

const fresh = api.readMoreCollapse(null);
keys.forEach((key) => assert(fresh[key] === true, "default open " + key));
assert(api.readMoreCollapse("")["more-hsa"] === true, "empty storage stays open");
assert(api.readMoreCollapse("{")["more-labs"] === true, "bad JSON stays open");
assert(api.readMoreCollapse("null")["more-ask"] === true, "null JSON stays open");

const saved = api.readMoreCollapse(
  JSON.stringify({
    "more-hsa": false,
    "more-ask": "nope",
    "not-a-section": false,
    "more-targets": true,
  })
);
assert(saved["more-hsa"] === false, "saved closed sticks");
assert(saved["more-ask"] === true, "non-boolean does not close");
assert(saved["more-targets"] === true, "explicit open sticks");
assert(saved["more-labs"] === true, "missing key stays open");
assert(!Object.prototype.hasOwnProperty.call(saved, "not-a-section"), "unknown key dropped");

const head = fakeHead(true);
const body = fakeBody("220");
const closed = api.toggleMoreSection(head, body);
assert(closed === false, "toggle closes an open section");
assert(head.getAttribute("aria-expanded") === "false", "aria-expanded false");
assert(head.classList.contains("is-collapsed") === true, "chevron class on");
assert(body.hidden === true, "body hidden");
assert(body.field.value === "220", "hide does not clear the field");

const reopened = api.toggleMoreSection(head, body);
assert(reopened === true, "second toggle opens");
assert(head.getAttribute("aria-expanded") === "true", "aria-expanded true");
assert(head.classList.contains("is-collapsed") === false, "chevron class off");
assert(body.hidden === false, "body shown again");
assert(body.field.value === "220", "reopen keeps the typed value");

const committed = api.commitMoreToggle(head, body, fresh);
assert(committed.open === false, "commit closes");
assert(committed.state["more-targets"] === false, "only the toggled key closes");
assert(committed.state["more-ask"] === true, "other sections stay open");
const roundTrip = api.readMoreCollapse(JSON.stringify(committed.state));
assert(roundTrip["more-targets"] === false, "persisted JSON reloads closed");
assert(roundTrip["more-hsa"] === true, "persisted JSON keeps the rest open");

const store = {};
const storage = {
  getItem(key) {
    return Object.prototype.hasOwnProperty.call(store, key) ? store[key] : null;
  },
  setItem(key, value) {
    store[key] = String(value);
  },
};
storage.setItem("fitdash-more-collapse-v1", JSON.stringify(committed.state));
const fromDevice = api.readMoreCollapse(storage.getItem("fitdash-more-collapse-v1"));
assert(fromDevice["more-targets"] === false, "device store reloads the closed section");
assert(storage.getItem("missing") === null, "missing key is empty storage");

function sectionSpan(doc, secId) {
  const marker = `id="${secId}"`;
  const i = doc.indexOf(marker);
  if (i < 0) throw new Error("missing " + secId);
  const start = doc.lastIndexOf("<section", i);
  let depth = 0;
  let j = start;
  while (j < doc.length) {
    const nextOpen = doc.indexOf("<section", j);
    const nextClose = doc.indexOf("</section>", j);
    if (nextClose < 0) throw new Error("no close " + secId);
    if (nextOpen !== -1 && nextOpen < nextClose) {
      depth += 1;
      j = nextOpen + 8;
    } else {
      depth -= 1;
      j = nextClose + "</section>".length;
      if (depth === 0) return doc.slice(start, j);
    }
  }
  throw new Error("unbalanced " + secId);
}

function screenshotMoreTab() {
  if (!fs.existsSync("/usr/bin/qlmanage")) {
    console.log("screenshot skipped: qlmanage not installed");
    return;
  }
  const ids = [
    "ask-card",
    "targets-config-section",
    "hsa-section",
    "labs-section",
    "training-settings-section",
    "equipment-inventory-section",
    "exercise-catalog-section",
    "connections-card",
  ];
  let nav = HTML.slice(
    HTML.indexOf('<nav class="mobile-tabbar"'),
    HTML.indexOf("</nav>") + "</nav>".length
  );
  nav = nav.replace(" hidden>", ">");
  nav = nav.replace('class="tab-btn active" data-m-tab="today"', 'class="tab-btn" data-m-tab="today"');
  nav = nav.replace(' aria-current="page"', "");
  nav = nav.replace(
    'class="tab-btn" data-m-tab="more"',
    'class="tab-btn active" data-m-tab="more" aria-current="page"'
  );
  let ask = sectionSpan(HTML, "ask-card");
  ask = ask.replace(
    'class="collapsible-head more-collapse-head" data-collapse="more-ask" aria-expanded="true"',
    'class="collapsible-head more-collapse-head is-collapsed" data-collapse="more-ask" aria-expanded="false"'
  );
  ask = ask.replace(
    'id="more-ask-body" data-collapse-body="more-ask">',
    'id="more-ask-body" data-collapse-body="more-ask" hidden>'
  );
  assert(ask.indexOf('aria-expanded="false"') !== -1, "screenshot source closes Ask");
  assert(ask.indexOf("hidden>") !== -1, "screenshot source hides the Ask body");
  assert(ask.indexOf('id="ask-question"') !== -1, "Ask field stays in the hidden body");
  const rest = ids
    .slice(1)
    .map((id) => sectionSpan(HTML, id))
    .join("\n");
  assert(rest.indexOf('id="tgt-p"') !== -1, "targets field stays mounted");
  assert(rest.indexOf('id="more-targets-body" hidden') === -1, "targets stay open");
  const page = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>More collapse</title>
  <link rel="stylesheet" href="${path.join(ROOT, "static", "styles.css")}" />
</head>
<body class="m-shell" data-m-active="more">
  <div class="wrap">
    ${nav}
    ${ask}
    ${rest}
  </div>
</body>
</html>
`;
  const dir = fs.mkdtempSync(path.join("/tmp", "more-collapse-955-"));
  const file = path.join(dir, "more.html");
  fs.writeFileSync(file, page);
  const shotRun = spawnSync("qlmanage", ["-t", "-s", "900", "-o", dir, file], {
    encoding: "utf8",
    timeout: 20000,
  });
  if (shotRun.status !== 0) {
    console.error(shotRun.stderr || shotRun.stdout);
    assert(false, "Quick Look screenshot failed");
  }
  const shot = path.join(dir, "more.html.png");
  const size = fs.statSync(shot).size;
  assert(size > 20000, "screenshot too small " + size);
  const dims = spawnSync("sips", ["-g", "pixelWidth", "-g", "pixelHeight", shot], {
    encoding: "utf8",
  });
  assert(dims.status === 0, "sips failed");
  assert(dims.stdout.indexOf("pixelWidth") !== -1, "screenshot has a width");
  console.log("screenshot", shot, size);
  console.log(dims.stdout.trim());
}

screenshotMoreTab();
console.log("ok");
