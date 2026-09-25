/**
 * Funnel state machine. Question text, buckets, subs, and scenarios come from canon JSON.
 * Stage chrome strings are the SPEC.md UI lines, not a second question catalog.
 */
import { answeredTapCount, coldStart, scoreSession } from "./score.js"

const STAGE3_TITLE = "Does this sound like you?"
const STAGE4_TITLE = "Here's what we think fits — do these sound right?"
const STAGE5_TITLE = "Pick what you want. Rank by how useful it sounds. That's what we build first."
const CONSENT = "We'll send your list and may follow up about building the top picks. No newsletter."

export function contactProblems(session) {
  const c = session.contact ?? {}
  const problems = []
  if (!String(c.name ?? "").trim()) problems.push("Name is required.")
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(c.email ?? ""))) problems.push("Email is required.")
  if (c.consent !== true) problems.push("Consent is required.")
  return problems
}

export function tracksOf(session) {
  const choice = session.trackChoice
  if (!choice) return []
  const tracks = choice === "both" ? ["personal", "business"] : [choice]
  if (session.offerBusiness === true && !tracks.includes("business")) tracks.push("business")
  return tracks
}

export function createSession() {
  return {
    id: crypto.randomUUID(),
    cursor: "landing",
    trackChoice: null,
    offerBusiness: null,
    bucketsByTrack: { personal: [], business: [] },
    context: {},
    subsByBucket: {},
    taps: {},
    rejected: [],
    expanded: false,
    rankings: null,
    escapeText: "",
    escapeReprompted: false,
    escapeNote: "",
    notMeHold: false,
    notMeReprompted: false,
    flags: [],
    scored: null,
    contact: { name: "", email: "", phone: "", consent: false },
    formError: "",
    classifierPass: "",
  }
}

function scenarios(canon) {
  return canon.scenarios.scenarios
}

function contextItemsFor(canon, tracks) {
  return canon.questions.context.items.filter((item) => item.tracks.some((t) => tracks.includes(t)))
}

function selectedBuckets(session) {
  const out = []
  for (const track of tracksOf(session)) {
    for (const id of session.bucketsByTrack[track] ?? []) out.push({ id, track })
  }
  return out
}

export function sequence(canon, session) {
  const seq = ["landing", "q0"]
  const choice = session.trackChoice
  if (!choice) return seq
  const personal = choice === "personal" || choice === "both"
  const businessFromChoice = choice === "business" || choice === "both"
  if (personal) seq.push("stage1:personal")
  if (businessFromChoice) seq.push("stage1:business")

  const earlyTracks = []
  if (personal) earlyTracks.push("personal")
  if (businessFromChoice) earlyTracks.push("business")
  const earlyItems = contextItemsFor(canon, earlyTracks)
  for (const item of earlyItems) {
    seq.push(`ctx:${item.id}`)
    if (
      item.id === "role" &&
      session.context.role === "self_employed" &&
      !businessFromChoice &&
      session.offerBusiness == null
    ) {
      seq.push("offer-business")
    }
  }
  if (session.offerBusiness === true) {
    seq.push("stage1:business")
    const late = canon.questions.context.items.filter(
      (item) => item.tracks.includes("business") && !item.tracks.includes("personal") && !earlyItems.some((e) => e.id === item.id),
    )
    for (const item of late) seq.push(`ctx:${item.id}`)
  }
  for (const bucket of selectedBuckets(session)) seq.push(`stage2:${bucket.id}`)
  for (const bucket of selectedBuckets(session)) {
    for (const sub of session.subsByBucket[bucket.id] ?? []) {
      for (const sc of scenarios(canon)) {
        if (sc.bucket === bucket.id && sc.sub === sub) seq.push(`stage3:${sc.id}`)
      }
    }
  }
  seq.push("analyzing", "stage4", "stage5", "contact", "confirm")
  return seq
}

function bucketLabel(canon, id) {
  for (const list of Object.values(canon.questions.stage1.buckets)) {
    const hit = list.find((b) => b.id === id)
    if (hit) return hit.label
  }
  return id
}

function clearScore(session, patch) {
  return { ...session, ...patch, scored: null, rankings: null }
}

function trailingNotMe(canon, session) {
  let streak = 0
  for (const cursor of sequence(canon, session)) {
    if (!cursor.startsWith("stage3:")) continue
    const resp = session.taps[cursor.slice("stage3:".length)]
    if (!resp) break
    if (resp === "not_me") streak += 1
    else streak = 0
  }
  return streak
}

function attachScore(canon, session) {
  const taps = session.taps ?? {}
  const tracks = tracksOf(session)
  const trust = String(session.context?.trust ?? "3")
  const flags = (session.flags ?? []).filter((f) => f !== "thin_session" && f !== "low_effort_all_yes")
  const answered = answeredTapCount(taps)
  if (answered < 3) flags.push("thin_session")
  const shownIds = sequence(canon, session)
    .filter((c) => c.startsWith("stage3:"))
    .map((c) => c.slice("stage3:".length))
  if (shownIds.length > 10 && shownIds.every((id) => taps[id] === "thats_me")) {
    flags.push("low_effort_all_yes")
  }
  const scored =
    answered < 3
      ? coldStart(canon.scoring, { ...session, tracks, taps, trust })
      : scoreSession(taps, {
          scoring: canon.scoring,
          scenarios: canon.scenarios.scenarios,
          hours: session.context?.hours || "3-5",
          urgency: session.context?.urgency || "this_quarter",
          trust,
          tools: Array.isArray(session.context?.tools) ? session.context.tools : [],
        })
  return { ...session, scored, flags }
}

export function visibleCards(canon, session) {
  const scored = session.scored
  if (!scored) return []
  const list = session.expanded ? scored.expanded : scored.shortlist
  return list.filter((row) => !(session.rejected ?? []).includes(row.solution_id))
}

function initRankings(canon, session) {
  if (session.rankings) return session
  const cards = visibleCards(canon, session)
  return {
    ...session,
    rankings: cards.map((c, i) => ({
      solution_id: c.solution_id,
      tag: i === 0 ? "must_have" : "nice_to_have",
    })),
  }
}

function go(canon, session, cursor) {
  let next = { ...session, cursor, notMeHold: false, formError: "" }
  if (cursor === "analyzing" || cursor === "stage4" || cursor === "stage5") {
    if (!next.scored) next = attachScore(canon, next)
  }
  if (cursor === "stage5") next = initRankings(canon, next)
  return next
}

function advance(canon, session) {
  if (session.cursor === "contact") {
    return { ...session, formError: "Send the list from the form." }
  }
  const seq = sequence(canon, session)
  const i = seq.indexOf(session.cursor)
  if (i < 0 || i >= seq.length - 1) return session
  return go(canon, session, seq[i + 1])
}

function choiceList(options) {
  return options.map((opt) => (typeof opt === "string" ? { id: opt, label: opt.replaceAll("_", " ") } : opt))
}

function decorate(canon, row) {
  const meta = canon.scoring.solutions[row.solution_id] ?? {}
  return {
    id: row.solution_id,
    label: meta.label || row.label || row.solution_id,
    pattern: meta.pattern || "",
    track: meta.track || "",
    why: (row.why ?? [])[0] || "",
  }
}

export function view(canon, session) {
  const cursor = session.cursor
  const base = {
    kind: cursor.split(":")[0],
    kicker: "GetHelpFrom.ai",
    title: "",
    prompt: "",
    example: null,
    layout: "stack",
    choices: [],
    cards: [],
    rank: [],
    escape: false,
    escapeText: session.escapeText ?? "",
    escapeNote: session.escapeNote ?? "",
    hold: null,
    contact: session.contact,
    formError: session.formError ?? "",
    canBack: cursor !== "landing" && cursor !== "confirm",
    canSkip: false,
    primary: null,
    canPrimary: true,
    autoAdvanceMs: 0,
    footnote: "",
  }
  if (cursor === "landing") {
    const example = scenarios(canon).find((s) => s.id === "facebook_lead_sat_6h") ?? scenarios(canon)[0]
    return {
      ...base,
      kind: "landing",
      title: "Tap what's true. We'll suggest what to build.",
      prompt: "About three minutes. No essay.",
      example: example?.text ?? "",
      primary: "Start",
    }
  }
  if (cursor === "q0") {
    const q = canon.questions.track_select
    return {
      ...base,
      kind: "q0",
      kicker: "Track",
      title: q.prompt,
      layout: "stack",
      choices: q.options.map((opt) => ({
        id: opt.id,
        label: opt.label,
        selected: session.trackChoice === opt.id,
        action: { type: "choose", id: opt.id },
      })),
      primary: "Continue",
      canPrimary: Boolean(session.trackChoice),
    }
  }
  if (cursor.startsWith("stage1:")) {
    const track = cursor.slice("stage1:".length)
    const selected = new Set(session.bucketsByTrack[track] ?? [])
    return {
      ...base,
      kind: "stage1",
      kicker: track === "business" ? "Business" : "Personal",
      title: canon.questions.stage1.prompt,
      layout: "chips",
      choices: (canon.questions.stage1.buckets[track] ?? []).map((b) => ({
        id: b.id,
        label: b.label,
        selected: selected.has(b.id),
        action: { type: "toggle-bucket", id: b.id },
      })),
      primary: "Continue",
      canPrimary: selected.size >= 1,
      footnote: `Up to ${canon.questions.stage1.max}.`,
    }
  }
  if (cursor.startsWith("ctx:")) {
    const id = cursor.slice("ctx:".length)
    const item = canon.questions.context.items.find((it) => it.id === id)
    const current = session.context[id]
    const choices =
      item?.type === "likert"
        ? (item.scale ?? []).map((n) => ({
            id: String(n),
            label: String(n),
            selected: String(current) === String(n),
            action: { type: "choose", id: String(n) },
          }))
        : choiceList(item?.options ?? []).map((opt) => ({
            id: opt.id,
            label: opt.label,
            selected: item?.type === "multi" ? (current ?? []).includes(opt.id) : current === opt.id,
            action: item?.type === "multi" ? { type: "toggle-multi", id: opt.id } : { type: "choose", id: opt.id },
          }))
    const answered = item?.type === "multi" ? Array.isArray(current) : current != null && current !== ""
    return {
      ...base,
      kind: "context",
      kicker: "Context",
      title: item?.prompt ?? "",
      prompt: item?.type === "likert" ? `${item.low} · ${item.high}` : "",
      layout: item?.type === "likert" ? "likert" : item?.type === "multi" ? "chips" : "stack",
      choices,
      primary: "Continue",
      canPrimary: id === "hours" ? answered : true,
      canSkip: id !== "hours",
    }
  }
  if (cursor === "offer-business") {
    return {
      ...base,
      kind: "offer",
      kicker: "Self-employed",
      title: "Also look at the business side?",
      prompt: "Optional. Personal answers stay.",
      choices: [
        { id: "yes", label: "Yes, add business", selected: false, action: { type: "offer", accept: true } },
        { id: "no", label: "Personal only", selected: false, action: { type: "offer", accept: false } },
      ],
      primary: null,
    }
  }
  if (cursor.startsWith("stage2:")) {
    const bucket = cursor.slice("stage2:".length)
    const selected = new Set(session.subsByBucket[bucket] ?? [])
    return {
      ...base,
      kind: "stage2",
      kicker: bucketLabel(canon, bucket),
      title: canon.questions.stage2.prompt,
      layout: "cards",
      choices: (canon.questions.stage2.subs[bucket] ?? []).map((sub) => ({
        id: sub.id,
        label: sub.label,
        selected: selected.has(sub.id),
        action: { type: "toggle-sub", id: sub.id },
      })),
      primary: "Continue",
      canSkip: true,
    }
  }
  if (cursor.startsWith("stage3:")) {
    const id = cursor.slice("stage3:".length)
    const sc = scenarios(canon).find((s) => s.id === id)
    if (session.notMeHold) {
      return {
        ...base,
        kind: "stage3",
        kicker: "Check",
        title: canon.questions.anti_gaming.straight_line_not_me,
        prompt: sc?.text ?? "",
        primary: "Continue",
        canPrimary: true,
      }
    }
    return {
      ...base,
      kind: "stage3",
      kicker: "Sound like you?",
      title: STAGE3_TITLE,
      prompt: sc?.text ?? "",
      layout: "sticky",
      choices: [
        { id: "thats_me", label: "That's me", selected: false, action: { type: "tap", response: "thats_me" } },
        { id: "sort_of", label: "Sort of", selected: false, action: { type: "tap", response: "sort_of" } },
        { id: "not_me", label: "Not me", selected: false, action: { type: "tap", response: "not_me" } },
      ],
      canSkip: true,
      primary: null,
    }
  }
  if (cursor === "analyzing") {
    return {
      ...base,
      kind: "analyzing",
      kicker: "Scoring",
      title: "Scoring your taps.",
      prompt: "A count of what you tapped. This step does not call a model.",
      primary: null,
      canPrimary: false,
      autoAdvanceMs: 1200,
    }
  }
  if (cursor === "stage4") {
    const cards = visibleCards(canon, session).map((row) => decorate(canon, row))
    const more = (session.scored?.expanded ?? []).filter((row) => !(session.rejected ?? []).includes(row.solution_id))
    return {
      ...base,
      kind: "stage4",
      kicker: "Shortlist",
      title: STAGE4_TITLE,
      layout: "cards",
      cards,
      escape: true,
      primary: "These look right",
      canPrimary: cards.length >= 1,
      footnote: !session.expanded && more.length > cards.length ? "Show more" : "",
    }
  }
  if (cursor === "stage5") {
    const rank = (session.rankings ?? []).map((r, index, arr) => {
      const row = (session.scored?.all ?? []).find((s) => s.solution_id === r.solution_id) ?? {
        solution_id: r.solution_id,
        why: [],
      }
      return { ...decorate(canon, row), tag: r.tag, index, count: arr.length }
    })
    return {
      ...base,
      kind: "stage5",
      kicker: "Rank",
      title: STAGE5_TITLE,
      layout: "rank",
      rank,
      primary: "Continue",
      canPrimary: rank.some((r) => r.tag === "must_have"),
      formError: session.formError,
    }
  }
  if (cursor === "contact") {
    return {
      ...base,
      kind: "contact",
      kicker: "Contact",
      title: "Where should we send the reply?",
      prompt: CONSENT,
      layout: "form",
      primary: "Send my list",
      canPrimary: true,
    }
  }
  return {
    ...base,
    kind: "confirm",
    kicker: "Next",
    title: "We'll read this and reply in 2 business days.",
    prompt: "We'll show you a working slice of your #1 pick.",
    canBack: false,
    primary: null,
  }
}

function toggleIn(list, id, max) {
  const cur = list.slice()
  const i = cur.indexOf(id)
  if (i >= 0) cur.splice(i, 1)
  else if (cur.length < max) cur.push(id)
  return cur
}

export function act(canon, session, action) {
  const type = action?.type
  if (type === "back") {
    const seq = sequence(canon, session)
    const i = seq.indexOf(session.cursor)
    if (i <= 0) return { ...session, notMeHold: false }
    return { ...session, cursor: seq[i - 1], notMeHold: false, formError: "" }
  }
  if (type === "sent" && session.cursor === "contact") {
    return { ...session, cursor: "confirm", formError: "" }
  }
  if (session.notMeHold && type !== "continue" && type !== "advance" && type !== "dismiss-notme") {
    return session
  }
  if (type === "dismiss-notme" || (session.notMeHold && (type === "continue" || type === "advance"))) {
    return advance(canon, { ...session, notMeHold: false, notMeReprompted: true })
  }
  if (type === "choose") {
    if (session.cursor === "q0") {
      return clearScore(session, { trackChoice: action.id, offerBusiness: null, bucketsByTrack: { personal: [], business: [] } })
    }
    if (session.cursor.startsWith("ctx:")) {
      const id = session.cursor.slice("ctx:".length)
      const context = { ...session.context, [id]: action.id }
      let next = clearScore(session, { context })
      if (id === "role" && action.id !== "self_employed") {
        next = { ...next, offerBusiness: null, bucketsByTrack: { ...next.bucketsByTrack, business: session.trackChoice === "business" || session.trackChoice === "both" ? next.bucketsByTrack.business : [] } }
      }
      return next
    }
    return session
  }
  if (type === "toggle-bucket" && session.cursor.startsWith("stage1:")) {
    const track = session.cursor.slice("stage1:".length)
    const max = canon.questions.stage1.max
    const list = toggleIn(session.bucketsByTrack[track] ?? [], action.id, max)
    return clearScore(session, { bucketsByTrack: { ...session.bucketsByTrack, [track]: list } })
  }
  if (type === "toggle-multi" && session.cursor.startsWith("ctx:")) {
    const id = session.cursor.slice("ctx:".length)
    const cur = Array.isArray(session.context[id]) ? session.context[id] : []
    const list = toggleIn(cur, action.id, 99)
    return clearScore(session, { context: { ...session.context, [id]: list } })
  }
  if (type === "toggle-sub" && session.cursor.startsWith("stage2:")) {
    const bucket = session.cursor.slice("stage2:".length)
    const max = canon.questions.stage2.max_per_bucket
    const list = toggleIn(session.subsByBucket[bucket] ?? [], action.id, max)
    return clearScore(session, { subsByBucket: { ...session.subsByBucket, [bucket]: list } })
  }
  if (type === "offer") {
    const next = clearScore(session, { offerBusiness: action.accept === true })
    if (action.accept) return { ...next, cursor: "stage1:business" }
    const seq = sequence(canon, next)
    const i = seq.indexOf("ctx:role")
    return { ...next, cursor: seq[i + 1] ?? "analyzing" }
  }
  if (type === "tap" && session.cursor.startsWith("stage3:")) {
    const id = session.cursor.slice("stage3:".length)
    const tapped = clearScore(session, { taps: { ...session.taps, [id]: action.response } })
    if (action.response === "not_me") {
      const streak = trailingNotMe(canon, tapped)
      if (streak >= 8 && !session.notMeReprompted) return { ...tapped, notMeHold: true }
    }
    return advance(canon, tapped)
  }
  if (type === "skip") {
    if (session.cursor.startsWith("ctx:")) {
      const id = session.cursor.slice("ctx:".length)
      if (id === "hours") return session
      const value = canon.questions.context.items.find((it) => it.id === id)?.type === "multi" ? [] : null
      return advance(canon, clearScore(session, { context: { ...session.context, [id]: value } }))
    }
    if (session.cursor.startsWith("stage2:")) {
      const bucket = session.cursor.slice("stage2:".length)
      return advance(canon, clearScore(session, { subsByBucket: { ...session.subsByBucket, [bucket]: [] } }))
    }
    if (session.cursor.startsWith("stage3:")) {
      const id = session.cursor.slice("stage3:".length)
      return advance(canon, clearScore(session, { taps: { ...session.taps, [id]: "skipped" } }))
    }
    return session
  }
  if (type === "reject" && session.cursor === "stage4") {
    const visible = visibleCards(canon, session)
    if (visible.length <= 1) return session
    if (!visible.some((c) => c.solution_id === action.id)) return session
    return { ...session, rejected: [...session.rejected, action.id], rankings: null }
  }
  if (type === "expand" && session.cursor === "stage4" && !session.expanded) {
    return { ...session, expanded: true, rankings: null }
  }
  if (type === "escape") {
    const trimmed = String(action.text ?? "").trim()
    if (trimmed.length < 12 && !session.escapeReprompted) {
      return {
        ...session,
        escapeReprompted: true,
        escapeNote: "Say a little more, or leave it and keep the list.",
      }
    }
    return { ...session, escapeText: trimmed, escapeNote: "" }
  }
  if (type === "move" && session.cursor === "stage5" && session.rankings) {
    const rankings = session.rankings.slice()
    const i = rankings.findIndex((r) => r.solution_id === action.id)
    const j = i + (action.dir === "up" ? -1 : 1)
    if (i < 0 || j < 0 || j >= rankings.length) return session
    const [row] = rankings.splice(i, 1)
    rankings.splice(j, 0, row)
    return { ...session, rankings }
  }
  if (type === "tag" && session.cursor === "stage5" && session.rankings) {
    return {
      ...session,
      rankings: session.rankings.map((r) => (r.solution_id === action.id ? { ...r, tag: action.tag } : r)),
      formError: "",
    }
  }
  if (type === "contact") {
    return { ...session, contact: { ...session.contact, ...action.patch }, formError: "" }
  }
  if (type === "continue" || type === "advance") {
    if (session.cursor.startsWith("stage1:")) {
      const track = session.cursor.slice("stage1:".length)
      if ((session.bucketsByTrack[track] ?? []).length < 1) return session
    }
    if (session.cursor.startsWith("ctx:")) {
      const id = session.cursor.slice("ctx:".length)
      if (id === "hours" && !session.context.hours) return session
    }
    if (session.cursor === "q0" && !session.trackChoice) return session
    if (session.cursor === "stage5") {
      const rankings = session.rankings ?? []
      if (!rankings.some((r) => r.tag === "must_have")) {
        return { ...session, formError: "Mark at least one as a must-have." }
      }
    }
    return advance(canon, session)
  }
  return session
}
