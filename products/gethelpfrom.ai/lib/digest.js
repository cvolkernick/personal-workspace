/**
 * Builds a lead digest from canon + session. Scores are recomputed here.
 * The client payload is not trusted for ranks' value numbers.
 */
import AjvImport from "ajv/dist/2020.js"
import addFormatsImport from "ajv-formats"
import { answeredTapCount, coldStart, scoreSession } from "./score.js"
import { contactProblems, tracksOf } from "./funnel.js"

const Ajv = AjvImport.default ?? AjvImport
const addFormats = addFormatsImport.default ?? addFormatsImport

const BANNED = ["guaranteed roi", "set and forget", "automate everything"]

let compiled = null
let compiledSchema = null

function validator(schema) {
  if (compiled && compiledSchema === schema) return compiled
  const ajv = new Ajv({ allErrors: true, strict: false })
  addFormats(ajv)
  compiled = ajv.compile(schema)
  compiledSchema = schema
  return compiled
}

function scoreFor(canon, session) {
  const taps = session.taps ?? {}
  const tracks = tracksOf(session)
  const trust = String(session.context?.trust ?? "3")
  const common = {
    hours: session.context?.hours || "3-5",
    urgency: session.context?.urgency || "this_quarter",
    trust,
    tools: Array.isArray(session.context?.tools) ? session.context.tools : [],
  }
  if (answeredTapCount(taps) < 3) {
    return coldStart(canon.scoring, { ...session, tracks, taps, trust, context: session.context })
  }
  return scoreSession(taps, {
    scoring: canon.scoring,
    scenarios: canon.scenarios.scenarios,
    ...common,
  })
}

export function demoPrep(canon, session, topId) {
  const meta = canon.scoring.solutions[topId] ?? {}
  const label = meta.label || topId
  const tools = (Array.isArray(session.context?.tools) ? session.context.tools : []).filter((t) => t && t !== "none")
  const build = tools.length
    ? [`Stub ${label} against ${tools.slice(0, 3).join(", ")}. Nothing sends on its own.`]
    : [`Screenshots of a cousin flow for ${label}.`]
  const trust = Number(session.context?.trust || 3)
  return {
    build_before_call: build,
    show_live: [`One happy-path click for ${label}.`],
    ask_on_call: [
      "What data access does this need?",
      trust <= 2 ? "What must never be sent without approval?" : "Who approves anything that gets sent?",
      "What must never be automated?",
    ],
  }
}

export function summaryMd(digest, canon) {
  const top = digest.ranked[0]
  const meta = canon.scoring.solutions[top.solution_id] ?? {}
  const why = digest.why.find((w) => w.solution_id === top.solution_id)?.lines?.[0] ?? ""
  const kind = top.tag === "must_have" ? "must-have" : "nice-to-have"
  const lines = [
    `## ${digest.contact.name} — ${digest.tracks.join(" + ")}`,
    "",
    `**#1 ${kind}:** ${meta.label || top.solution_id}.`,
    `**Why:** ${why}`,
    "**Build (3–5 days):** a working slice of the #1 pick only.",
    `**Ask:** ${digest.demo_prep.ask_on_call.join(" ")}`,
    "",
    "Human reads this before any demo build. No auto-accept.",
  ]
  if (digest.flags?.includes("thin_session")) {
    lines.push("", "Thin session: fewer than 3 scenario answers.")
  }
  if (digest.escape_hatch?.text) {
    lines.push("", `**In their words:** ${digest.escape_hatch.text}`)
  }
  return lines.join("\n")
}

export function buildDigestFromSession(canon, session, now = new Date()) {
  const problems = contactProblems(session)
  const rankings = session.rankings ?? []
  if (!rankings.length) problems.push("Rank at least one.")
  if (rankings.length && !rankings.some((r) => r.tag === "must_have")) {
    problems.push("Mark at least one must-have.")
  }
  const tracks = tracksOf(session)
  if (!tracks.length) problems.push("Pick a track.")
  if (problems.length) return { ok: false, error: problems[0], problems }

  const scored = session.scored?.shortlist ? session.scored : scoreFor(canon, session)
  const byId = Object.fromEntries((scored.all ?? scored.expanded ?? scored.shortlist).map((r) => [r.solution_id, r]))
  const ranked = rankings.slice(0, 8).map((r, i) => {
    const row = byId[r.solution_id] ?? {
      value: 0,
      feasibility: 0,
      confidence: 0,
      why: ["We suggested this from your other taps in this area."],
    }
    return {
      rank: i + 1,
      solution_id: r.solution_id,
      tag: r.tag,
      value: row.value,
      feasibility: row.feasibility,
      confidence: row.confidence,
      why: row.why,
    }
  })
  const why = ranked.map((r) => ({ solution_id: r.solution_id, lines: (r.why ?? []).slice(0, 2) }))
  const flags = []
  if (scored.thin_session || answeredTapCount(session.taps) < 3) flags.push("thin_session")
  for (const f of session.flags ?? []) {
    if (!flags.includes(f)) flags.push(f)
  }
  if (!flags.includes("hitl_required")) flags.push("hitl_required")

  const phone = String(session.contact.phone ?? "").trim()
  const digest = {
    schema_version: "1",
    scoring_version: canon.scoring.version,
    questions_version: canon.questions.version,
    scenarios_version: canon.scenarios.version,
    session_id: session.id,
    completed_at: now.toISOString(),
    contact: {
      name: String(session.contact.name).trim(),
      email: String(session.contact.email).trim(),
      phone: phone || null,
      consent: true,
    },
    tracks,
    context: session.context ?? {},
    taps: Object.entries(session.taps ?? {}).map(([scenario_id, response]) => ({ scenario_id, response })),
    ranked: ranked.map(({ why: _why, ...rest }) => rest),
    rejected: session.rejected ?? [],
    escape_hatch: session.escapeText
      ? {
          text: session.escapeText,
          ...(session.classifierPass ? { classifier_pass: session.classifierPass } : {}),
        }
      : null,
    why,
    flags,
    demo_prep: demoPrep(canon, session, ranked[0].solution_id),
  }
  const summary_md = summaryMd(digest, canon)
  const full = { ...digest, summary_md }
  const low = summary_md.toLowerCase()
  if (BANNED.some((phrase) => low.includes(phrase))) {
    return { ok: false, error: "Summary copy crossed a banned promise." }
  }
  const validate = validator(canon.digestSchema)
  const ok = validate(full)
  if (!ok) {
    return { ok: false, error: "Digest does not match digest.schema.json.", errors: validate.errors }
  }
  return { ok: true, digest: full }
}
