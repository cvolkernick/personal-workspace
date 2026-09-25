import assert from "node:assert/strict"
import { spawnSync } from "node:child_process"
import { mkdtempSync, readFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"
import test from "node:test"
import { canonReport, loadCanon, productRoot } from "../lib/canon.js"
import { buildDigestFromSession } from "../lib/digest.js"
import { sendOpsSummary } from "../lib/email.js"
import { classifyEscape, ESCAPE_LIMITS, screenEscapeText } from "../lib/escape.js"
import { act, createSession, view } from "../lib/funnel.js"
import { coldStartIds, scoreSession } from "../lib/score.js"
import { commitLead, loadSession, saveSession } from "../lib/store.js"

const MAYA = {
  eob_still_cant_tell_what_we_owe: "thats_me",
  hospital_bill_months_later: "thats_me",
  mom_copay_then_second_bill: "sort_of",
  kids_urgent_care_drawer: "thats_me",
  mystery_streaming_on_joint_card: "thats_me",
  spouse_handles_it_until_they_dont: "thats_me",
  p_msg_never_zero_01: "not_me",
}
const LUIS = {
  facebook_lead_sat_6h: "thats_me",
  google_lsa_silenced_on_install: "thats_me",
  condenser_quote_never_chased: "thats_me",
  p_msg_reply_later_01: "not_me",
}
const PRIYA = {
  p_msg_never_zero_01: "thats_me",
  p_msg_reply_later_01: "thats_me",
  p_msg_reply_later_03: "thats_me",
  p_msg_followup_01: "thats_me",
  p_time_junk_meeting_02: "sort_of",
  eob_still_cant_tell_what_we_owe: "not_me",
}

function pyScore(taps, opts = {}) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from score import score_session
data = json.loads(sys.stdin.read())
print(json.dumps(score_session(
    data["taps"],
    hours=data.get("hours", "3-5"),
    urgency=data.get("urgency", "this_quarter"),
    trust=str(data.get("trust", "3")),
    tools=data.get("tools") or [],
)))
`
  const run = spawnSync("python3", ["-c", script], {
    cwd: productRoot(),
    input: JSON.stringify({ taps, ...opts }),
    encoding: "utf8",
  })
  assert.equal(run.status, 0, run.stderr)
  return JSON.parse(run.stdout)
}

function throughContext(canon, session) {
  let guard = 0
  while (session.cursor.startsWith("ctx:") && guard++ < 30) {
    const id = session.cursor.slice("ctx:".length)
    if (id === "hours") session = act(canon, session, { type: "choose", id: "6-10" })
    if (id === "trust") session = act(canon, session, { type: "choose", id: "3" })
    if (id === "urgency") session = act(canon, session, { type: "choose", id: "weekly_pain" })
    if (id === "hours" || id === "trust" || id === "urgency") {
      session = act(canon, session, { type: "continue" })
    } else {
      session = act(canon, session, { type: "skip" })
    }
  }
  return session
}

test("scorer matches Python for Maya, Luis, Priya, and not-me", () => {
  const canon = loadCanon()
  const cases = [
    [MAYA, { hours: "6-10", urgency: "weekly_pain", trust: "3" }],
    [LUIS, { hours: "10+", urgency: "losing_sleep_or_money", trust: "4", tools: ["jobber"] }],
    [PRIYA, { hours: "6-10", urgency: "weekly_pain", trust: "2" }],
    [{ eob_still_cant_tell_what_we_owe: "not_me" }, {}],
  ]
  for (const [taps, opts] of cases) {
    const js = scoreSession(taps, {
      scoring: canon.scoring,
      scenarios: canon.scenarios.scenarios,
      ...opts,
    })
    assert.deepEqual(js, pyScore(taps, opts))
  }
  const maya = scoreSession(MAYA, {
    scoring: canon.scoring,
    scenarios: canon.scenarios.scenarios,
    hours: "6-10",
    urgency: "weekly_pain",
    trust: "3",
  })
  const top = new Set(maya.shortlist.slice(0, 3).map((r) => r.solution_id))
  assert.ok(top.has("eob_explainer") || top.has("bill_pay_agent"))
  assert.ok(maya.shortlist.length <= 5)
  assert.ok(maya.expanded.length <= 8)
})

test("stage 4 score stays under 300ms without a model", () => {
  const canon = loadCanon()
  const taps = Object.fromEntries(canon.scenarios.scenarios.map((s) => [s.id, "sort_of"]))
  for (let i = 0; i < 5; i++) {
    const t0 = performance.now()
    scoreSession(taps, { scoring: canon.scoring, scenarios: canon.scenarios.scenarios })
    assert.ok(performance.now() - t0 < 300)
  }
})

test("funnel is taps until stage 4, then rank, then email", () => {
  const canon = loadCanon()
  let session = createSession()
  assert.equal(view(canon, session).escape, false)
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "choose", id: "personal" })
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "stage1:personal")
  const buckets = canon.questions.stage1.buckets.personal.map((b) => b.id)
  for (const id of buckets.slice(0, 4)) session = act(canon, session, { type: "toggle-bucket", id })
  assert.equal(session.bucketsByTrack.personal.length, 3)
  session = act(canon, session, { type: "continue" })
  const beforeHours = session.cursor
  assert.equal(beforeHours.startsWith("ctx:"), true)
  session = throughContext(canon, session)
  assert.equal(session.cursor.startsWith("stage2:"), true)
  let stage3 = 0
  let guard = 0
  while (!session.cursor.startsWith("stage3:") && session.cursor !== "analyzing" && guard++ < 20) {
    if (session.cursor.startsWith("stage2:")) {
      const bucket = session.cursor.slice("stage2:".length)
      const sub = canon.questions.stage2.subs[bucket][0]
      session = act(canon, session, { type: "toggle-sub", id: sub.id })
      const group = canon.scenarios.scenarios.filter((s) => s.bucket === bucket && s.sub === sub.id)
      assert.equal(group.length, 4)
    }
    session = act(canon, session, { type: "continue" })
  }
  while (session.cursor.startsWith("stage3:") && guard++ < 80) {
    assert.equal(view(canon, session).escape, false)
    const choices = view(canon, session).choices.map((c) => c.id)
    assert.deepEqual(choices, ["thats_me", "sort_of", "not_me"])
    session = act(canon, session, { type: "tap", response: "thats_me" })
    stage3 += 1
  }
  assert.ok(stage3 >= 4)
  assert.equal(stage3 % 4, 0)
  if (session.cursor === "analyzing") session = act(canon, session, { type: "advance" })
  assert.equal(session.cursor, "stage4")
  assert.equal(session.contact.email, "")
  const short = view(canon, session)
  assert.equal(short.escape, true)
  assert.ok(short.cards.length >= 1 && short.cards.length <= 5)
  const expanded = view(canon, act(canon, session, { type: "expand" }))
  assert.ok(expanded.cards.length <= 8)
  assert.ok(expanded.cards.length >= short.cards.length)
  session = act(canon, session, { type: "expand" })
  const kept = view(canon, session).cards.length
  let rejected = session
  for (const card of view(canon, session).cards) {
    rejected = act(canon, rejected, { type: "reject", id: card.id })
  }
  assert.equal(view(canon, rejected).cards.length, 1)
  assert.ok(view(canon, rejected).cards.length <= kept)
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "stage5")
  assert.equal(session.rankings[0].tag, "must_have")
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "contact")
})

test("hours cannot be skipped and both tracks run personal first", () => {
  const canon = loadCanon()
  let session = createSession()
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "choose", id: "both" })
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "stage1:personal")
  session = act(canon, session, { type: "toggle-bucket", id: "messages" })
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "stage1:business")
  session = act(canon, session, { type: "toggle-bucket", id: "leads" })
  session = act(canon, session, { type: "continue" })
  let guard = 0
  while (session.cursor !== "ctx:hours" && guard++ < 15) {
    assert.notEqual(session.cursor, "ctx:hours")
    session = act(canon, session, { type: "skip" })
  }
  assert.equal(session.cursor, "ctx:hours")
  const stuck = act(canon, session, { type: "skip" })
  assert.equal(stuck.cursor, "ctx:hours")
})

test("self-employed can add the business track", () => {
  const canon = loadCanon()
  let session = createSession()
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "choose", id: "personal" })
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "toggle-bucket", id: "messages" })
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "ctx:role")
  session = act(canon, session, { type: "choose", id: "self_employed" })
  session = act(canon, session, { type: "continue" })
  assert.equal(session.cursor, "offer-business")
  session = act(canon, session, { type: "offer", accept: true })
  assert.equal(session.cursor, "stage1:business")
})

test("cold start shows three track defaults", () => {
  const canon = loadCanon()
  let session = createSession()
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "choose", id: "business" })
  session = act(canon, session, { type: "continue" })
  session = act(canon, session, { type: "toggle-bucket", id: "leads" })
  session = act(canon, session, { type: "continue" })
  session = throughContext(canon, session)
  while (session.cursor.startsWith("stage2:")) session = act(canon, session, { type: "skip" })
  if (session.cursor === "analyzing") session = act(canon, session, { type: "advance" })
  assert.equal(session.cursor, "stage4")
  assert.ok(session.flags.includes("thin_session"))
  assert.deepEqual(
    view(canon, session).cards.map((c) => c.id),
    coldStartIds(["business"]),
  )
})

test("escape hatch re-prompts once and does not replace the shortlist", async () => {
  const session = createSession()
  const again = screenEscapeText(session, "too short")
  assert.equal(again.ok, false)
  const kept = screenEscapeText(again.session, "x")
  assert.equal(kept.ok, true)
  const id = session.id
  const env = { XAI_API_KEY: "test", GETHELPFROM_DATA_DIR: mkdtempSync(path.join(os.tmpdir(), "ghf-esc-")) }
  let calls = 0
  const fetchImpl = async (_url, opts) => {
    calls += 1
    const body = JSON.parse(opts.body)
    assert.equal(body.model, "grok-4.5")
    assert.equal(body.max_tokens, ESCAPE_LIMITS.maxTokens)
    assert.equal(ESCAPE_LIMITS.timeoutMs, 8000)
    throw new Error("down")
  }
  const first = await classifyEscape({ text: "None of these fit my shop", sessionId: id, env, fetchImpl })
  assert.equal(first.keptDeterministic, true)
  assert.equal(calls, 1)
  const second = await classifyEscape({ text: "None of these fit my shop", sessionId: id, env, fetchImpl })
  assert.equal(second.reason, "cap")
  assert.equal(calls, 1)
})

test("digest validates and emails summary_md once per 14 days", async () => {
  const canon = loadCanon()
  const scored = scoreSession(MAYA, {
    scoring: canon.scoring,
    scenarios: canon.scenarios.scenarios,
    hours: "6-10",
    urgency: "weekly_pain",
    trust: "3",
  })
  const session = {
    id: "00000000-0000-4000-8000-000000000621",
    trackChoice: "personal",
    offerBusiness: null,
    context: { hours: "6-10", urgency: "weekly_pain", trust: "3" },
    taps: MAYA,
    rankings: scored.shortlist.map((row, i) => ({
      solution_id: row.solution_id,
      tag: i === 0 ? "must_have" : "nice_to_have",
    })),
    rejected: ["subscription_audit"],
    escapeText: "",
    contact: { name: "Maya Chen", email: "maya@example.com", phone: "", consent: true },
    flags: [],
    scored,
  }
  const built = buildDigestFromSession(canon, session, new Date("2026-09-11T03:40:00Z"))
  assert.equal(built.ok, true, JSON.stringify(built.errors || built.error))
  assert.match(built.digest.summary_md, /No auto-accept/)
  assert.equal(/guaranteed roi|set and forget|automate everything/i.test(built.digest.summary_md), false)
  const dir = mkdtempSync(path.join(os.tmpdir(), "ghf-leads-"))
  const env = { GETHELPFROM_DATA_DIR: dir }
  const first = commitLead(built.digest, env)
  const second = commitLead(
    { ...built.digest, session_id: "00000000-0000-4000-8000-000000000622" },
    env,
  )
  assert.equal(first.deduped, false)
  assert.equal(first.auto_accept, false)
  assert.equal(second.deduped, true)
  const calls = []
  const mailed = await sendOpsSummary({
    summaryMd: built.digest.summary_md,
    env: {
      GETHELPFROM_OPS_EMAIL: "ops@example.com",
      GETHELPFROM_FROM_EMAIL: "lists@example.com",
      RESEND_API_KEY: "test-key",
    },
    fetchImpl: async (_url, opts) => {
      calls.push(JSON.parse(opts.body))
      return { ok: true, status: 200 }
    },
  })
  assert.equal(mailed.emailed, true)
  assert.equal(calls[0].text, built.digest.summary_md)
  assert.deepEqual(calls[0].to, ["ops@example.com"])
  const quiet = await sendOpsSummary({ summaryMd: built.digest.summary_md, env: {} })
  assert.equal(quiet.emailed, false)
  const storeSrc = readFileSync(new URL("../lib/store.js", import.meta.url), "utf8")
  assert.equal(storeSrc.includes("auto_accept: true"), false)
})

test("session ids cannot escape the data directory", () => {
  const env = { GETHELPFROM_DATA_DIR: mkdtempSync(path.join(os.tmpdir(), "ghf-sess-")) }
  assert.throws(() => saveSession("../secrets", { id: "../secrets" }, env))
  assert.equal(loadSession("../secrets", env), null)
  const id = crypto.randomUUID()
  saveSession(id, { id, ok: true }, env)
  assert.equal(loadSession(id, env).ok, true)
})

test("canon versions come from the git files", () => {
  const report = canonReport({ GHF_CANON_SHA_QUESTIONS: "deadbeef" })
  assert.equal(report.drift, true)
  const live = canonReport({})
  assert.equal(live.drift, false)
  const root = productRoot()
  for (const name of ["questions.json", "scenarios.json", "scoring.json"]) {
    const git = spawnSync("git", ["hash-object", path.join(root, name)], { encoding: "utf8" })
    assert.equal(git.status, 0, git.stderr)
    assert.equal(live.files[name].git_blob, git.stdout.trim())
  }
  const ui = [
    readFileSync(path.join(root, "components/Funnel.tsx"), "utf8"),
    readFileSync(path.join(root, "app/page.tsx"), "utf8"),
  ].join("\n")
  assert.equal(ui.includes("Money & bills"), false)
  assert.equal(ui.includes("Which areas feel heaviest"), false)
})
