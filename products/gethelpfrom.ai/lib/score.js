/**
 * Deterministic port of score.py. No LLM. Same keys and rounding target.
 * Cold start is product behavior on top of the scorer (SPEC.md), not a Python path.
 */

function round3(n) {
  return Math.round((n + Number.EPSILON) * 1000) / 1000
}

function whyLines(scenarioById, taps, solutionId) {
  const lines = []
  for (const [sid, resp] of Object.entries(taps)) {
    if (resp !== "thats_me" && resp !== "sort_of") continue
    const sc = scenarioById[sid]
    if (!sc || sc.solution !== solutionId) continue
    const verb = resp === "thats_me" ? "that's you" : "sort of you"
    lines.push(`We suggested this because ${verb}: \u201c${sc.text}\u201d`)
    if (lines.length >= 2) break
  }
  if (lines.length === 0) {
    lines.push("We suggested this from your other taps in this area.")
  }
  return lines.slice(0, 2)
}

export function scoreSession(taps, opts = {}) {
  const scoring = opts.scoring
  const scenarios = opts.scenarios
  if (!scoring || !scenarios) {
    throw new Error("scoreSession requires scoring and scenarios")
  }
  const scenarioById = Object.fromEntries(scenarios.map((s) => [s.id, s]))
  const tapScores = scoring.tap_scores
  const tools = opts.tools ?? []
  const hours = opts.hours ?? "3-5"
  const urgency = opts.urgency ?? "this_quarter"
  const trust = String(opts.trust ?? "3")

  const raw = {}
  let answered = 0
  let shown = 0
  for (const sc of scenarios) {
    if (!Object.prototype.hasOwnProperty.call(taps, sc.id)) continue
    shown += 1
    const resp = taps[sc.id]
    if (resp !== "skipped") answered += 1
    const sol = sc.solution
    raw[sol] = (raw[sol] ?? 0) + Number(tapScores[resp] ?? 0)
  }

  const hoursM = Number(scoring.hours_multiplier[hours] ?? 1)
  const urgM = Number(scoring.urgency_multiplier[urgency] ?? 1)
  const trustM = Number(scoring.trust_feasibility[trust] ?? 0.75)
  const conf = shown ? answered / shown : 0

  const ranked = []
  for (const [sol, tapSum] of Object.entries(raw)) {
    if (tapSum < scoring.shortlist.min_value_taps) continue
    const meta = scoring.solutions[sol] ?? {}
    let toolHit = 1
    const wanted = meta.tools ?? []
    if (wanted.length && tools.length) {
      toolHit = wanted.some((t) => tools.includes(t)) ? 1.1 : 0.85
    }
    const value = Math.max(0, tapSum) * hoursM * urgM
    const feasibility = trustM * toolHit
    const w = scoring.weights
    const total = w.value * value + w.feasibility * feasibility * 10 + w.confidence * conf * 10
    ranked.push({
      solution_id: sol,
      label: meta.label ?? sol,
      value: round3(value),
      feasibility: round3(feasibility),
      confidence: round3(conf),
      score: round3(total),
      why: whyLines(scenarioById, taps, sol),
    })
  }
  ranked.sort((a, b) => b.score - a.score)
  const n = Number(scoring.shortlist.default_n)
  const expandN = Number(scoring.shortlist.expand_n)
  return {
    scoring_version: scoring.version,
    shortlist: ranked.slice(0, n),
    expanded: ranked.slice(0, expandN),
    all: ranked,
  }
}

export function coldStartIds(tracks) {
  const list = tracks ?? []
  const businessOnly = list.includes("business") && !list.includes("personal")
  if (businessOnly) return ["inbox_triage", "followup_nudge", "speed_to_lead"]
  return ["inbox_triage", "followup_nudge", "bill_pay_agent"]
}

export function coldStart(scoring, session) {
  const tracks = session.tracks ?? ["personal"]
  const trust = String(session.trust ?? session.context?.trust ?? "3")
  const trustM = Number(scoring.trust_feasibility[trust] ?? 0.75)
  const taps = session.taps ?? {}
  let answered = 0
  let shown = 0
  for (const resp of Object.values(taps)) {
    shown += 1
    if (resp !== "skipped") answered += 1
  }
  const conf = shown ? answered / shown : 0
  const why = [
    "Cold start: fewer than 3 scenario answers, so this is a high-feasibility default for your track.",
  ]
  const shortlist = coldStartIds(tracks).map((sol) => {
    const meta = scoring.solutions[sol] ?? {}
    return {
      solution_id: sol,
      label: meta.label ?? sol,
      value: 1,
      feasibility: round3(trustM),
      confidence: round3(conf),
      score: round3(trustM),
      why,
    }
  })
  return {
    scoring_version: scoring.version,
    thin_session: true,
    shortlist,
    expanded: shortlist,
    all: shortlist,
  }
}

export function answeredTapCount(taps) {
  let n = 0
  for (const resp of Object.values(taps ?? {})) {
    if (resp !== "skipped") n += 1
  }
  return n
}
