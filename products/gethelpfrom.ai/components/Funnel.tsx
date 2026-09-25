"use client"

import { useEffect, useState } from "react"
import { act, contactProblems, createSession, view } from "../lib/funnel.js"

type Canon = {
  questions: Record<string, unknown>
  scenarios: { scenarios: { id: string; text: string }[] }
  scoring: Record<string, unknown>
}

type Choice = { id: string; label: string; selected: boolean; action: Record<string, unknown> }
type Card = { id: string; label: string; pattern: string; track: string; why: string }
type Rank = Card & { tag: string; index: number; count: number }
type Model = {
  kind: string
  kicker: string
  title: string
  prompt: string
  example: string | null
  layout: string
  choices: Choice[]
  cards: Card[]
  rank: Rank[]
  escape: boolean
  escapeText: string
  escapeNote: string
  contact: { name: string; email: string; phone: string; consent: boolean }
  formError: string
  canBack: boolean
  canSkip: boolean
  primary: string | null
  canPrimary: boolean
  autoAdvanceMs: number
  footnote: string
}

const KEY = "gethelpfrom.session.v1"

export default function Funnel({ canon, resumeId }: { canon: Canon; resumeId?: string }) {
  const [session, setSession] = useState<Record<string, unknown> | null>(null)
  const [escapeDraft, setEscapeDraft] = useState("")
  const [sendError, setSendError] = useState("")
  const [sending, setSending] = useState(false)
  const [resumeNote, setResumeNote] = useState("")
  const [resumeLoaded, setResumeLoaded] = useState(false)

  useEffect(() => {
    const raw = localStorage.getItem(KEY)
    if (raw) {
      try {
        setSession(JSON.parse(raw) as Record<string, unknown>)
        return
      } catch {
        /* fresh session */
      }
    }
    setSession(createSession() as Record<string, unknown>)
  }, [])

  useEffect(() => {
    if (!session) return
    localStorage.setItem(KEY, JSON.stringify(session))
    const id = String(session.id ?? "")
    if (id) document.cookie = `ghf_sid=${id}; Path=/; Max-Age=7776000; SameSite=Lax`
    const timer = setTimeout(() => {
      void fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session }),
      })
    }, 500)
    return () => clearTimeout(timer)
  }, [session])

  useEffect(() => {
    if (!resumeId || !session || resumeLoaded) return
    setResumeLoaded(true)
    if (session.id === resumeId) return
    let cancel = false
    void (async () => {
      const res = await fetch(`/api/session?id=${encodeURIComponent(resumeId)}`)
      if (!res.ok) return
      const data = (await res.json()) as { session?: Record<string, unknown> }
      if (!cancel && data.session) setSession(data.session)
    })()
    return () => {
      cancel = true
    }
  }, [resumeId, session, resumeLoaded])

  const model = session ? (view(canon, session) as Model) : null

  useEffect(() => {
    if (!session || !model?.autoAdvanceMs) return
    const timer = setTimeout(() => {
      setSession((current) => (current ? (act(canon, current, { type: "advance" }) as Record<string, unknown>) : current))
    }, model.autoAdvanceMs)
    return () => clearTimeout(timer)
  }, [session, model?.autoAdvanceMs, model?.kind, canon])

  if (!session || !model) {
    return (
      <main className="shell">
        <p className="kicker">GetHelpFrom.ai</p>
        <h1>Tap what&apos;s true. We&apos;ll suggest what to build.</h1>
      </main>
    )
  }

  const live = session

  function dispatch(action: Record<string, unknown>) {
    setSession((current) => (current ? (act(canon, current, action) as Record<string, unknown>) : current))
  }

  async function sendList() {
    const problems = contactProblems(live) as string[]
    if (problems.length) {
      setSendError(problems[0])
      return
    }
    setSending(true)
    setSendError("")
    try {
      const res = await fetch("/api/digest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ session: live }),
      })
      const data = (await res.json()) as { error?: string }
      if (!res.ok) {
        setSendError(data.error || "Could not send.")
        return
      }
      setSession((current) => (current ? (act(canon, current, { type: "sent" }) as Record<string, unknown>) : current))
    } catch {
      setSendError("Could not send. Your answers are still on this device.")
    } finally {
      setSending(false)
    }
  }

  async function emailResume() {
    const contact = (live.contact ?? {}) as { name?: string; email?: string; phone?: string }
    const problems = contactProblems({ ...live, contact: { ...contact, consent: true } }) as string[]
    const emailProblem = problems.find((p) => p.startsWith("Email") || p.startsWith("Name"))
    if (emailProblem) {
      setResumeNote(emailProblem)
      return
    }
    const res = await fetch("/api/resume", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session: live }),
    })
    const data = (await res.json()) as { emailed?: boolean; url?: string }
    setResumeNote(data.emailed ? "Resume link sent." : `Resume link: ${data.url ?? ""}`)
  }

  return (
    <main className="shell">
      <p className="kicker">{model.kicker}</p>
      <h1>{model.title}</h1>
      {model.prompt && model.kind !== "contact" ? <p className="prompt">{model.prompt}</p> : null}
      {model.example ? <blockquote className="example">{model.example}</blockquote> : null}
      {model.choices.length && model.layout !== "sticky" ? (
        <div className={`choices layout-${model.layout}`}>
          {model.choices.map((choice) => (
            <button
              key={choice.id}
              type="button"
              className="choice"
              aria-pressed={choice.selected}
              onClick={() => dispatch(choice.action)}
            >
              {choice.label}
            </button>
          ))}
        </div>
      ) : null}
      {model.cards.length ? (
        <div className="cards">
          {model.cards.map((card) => (
            <article key={card.id} className="card">
              {card.track ? <span className="track">{card.track}</span> : null}
              <h2>{card.label}</h2>
              {card.pattern ? <p>{card.pattern}</p> : null}
              {card.why ? <p className="why">{card.why}</p> : null}
              <button type="button" className="textish" onClick={() => dispatch({ type: "reject", id: card.id })}>
                Not relevant
              </button>
            </article>
          ))}
        </div>
      ) : null}
      {model.rank.length ? (
        <ol className="rank">
          {model.rank.map((row) => (
            <li key={row.id} className="rank-row">
              <span className="track">{row.track}</span>
              <h2>
                {row.index + 1}. {row.label}
              </h2>
              {row.why ? <p className="why">{row.why}</p> : null}
              <div className="rank-actions">
                <button type="button" className="mini" onClick={() => dispatch({ type: "move", id: row.id, dir: "up" })} disabled={row.index === 0}>
                  Up
                </button>
                <button type="button" className="mini" onClick={() => dispatch({ type: "move", id: row.id, dir: "down" })} disabled={row.index === row.count - 1}>
                  Down
                </button>
                <button
                  type="button"
                  className="tag"
                  aria-pressed={row.tag === "must_have"}
                  onClick={() => dispatch({ type: "tag", id: row.id, tag: row.tag === "must_have" ? "nice_to_have" : "must_have" })}
                >
                  {row.tag === "must_have" ? "Must-have" : "Nice-to-have"}
                </button>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
      {model.escape ? (
        <div className="escape">
          <label htmlFor="escape">None of these — tell us</label>
          <textarea
            id="escape"
            rows={3}
            value={escapeDraft}
            onChange={(event) => setEscapeDraft(event.target.value)}
          />
          {model.escapeNote ? <p className="error">{model.escapeNote}</p> : null}
          <button
            type="button"
            className="textish"
            onClick={() => {
              dispatch({ type: "escape", text: escapeDraft })
              void fetch("/api/escape", {
                method: "POST",
                headers: { "content-type": "application/json" },
                body: JSON.stringify({ sessionId: live.id, text: escapeDraft }),
              })
                .then((res) => res.json())
                .then((data: { classifierPass?: string }) => {
                  if (data.classifierPass) {
                    setSession((current) => (current ? { ...current, classifierPass: data.classifierPass } : current))
                  }
                })
                .catch(() => {
                  /* shortlist stays */
                })
            }}
          >
            Save note
          </button>
        </div>
      ) : null}
      {model.kind === "contact" ? (
        <form
          className="cards"
          onSubmit={(event) => {
            event.preventDefault()
            void sendList()
          }}
        >
          <label>
            Name
            <input
              type="text"
              name="name"
              autoComplete="name"
              value={model.contact.name}
              onChange={(event) => dispatch({ type: "contact", patch: { name: event.target.value } })}
            />
          </label>
          <label>
            Email
            <input
              type="email"
              name="email"
              autoComplete="email"
              value={model.contact.email}
              onChange={(event) => dispatch({ type: "contact", patch: { email: event.target.value } })}
            />
          </label>
          <label>
            Phone, if you want
            <input
              type="tel"
              name="phone"
              autoComplete="tel"
              value={model.contact.phone}
              onChange={(event) => dispatch({ type: "contact", patch: { phone: event.target.value } })}
            />
          </label>
          <label className="consent">
            <input
              type="checkbox"
              checked={model.contact.consent}
              onChange={(event) => dispatch({ type: "contact", patch: { consent: event.target.checked } })}
            />
            <span>{model.prompt}</span>
          </label>
          <button type="button" className="textish" onClick={() => void emailResume()}>
            Email me a resume link
          </button>
          {resumeNote ? <p>{resumeNote}</p> : null}
        </form>
      ) : null}
      {model.formError ? <p className="error">{model.formError}</p> : null}
      {sendError ? <p className="error">{sendError}</p> : null}
      {model.footnote === "Show more" ? (
        <button type="button" className="textish" onClick={() => dispatch({ type: "expand" })}>
          Show more
        </button>
      ) : null}
      {model.footnote && model.footnote !== "Show more" ? <p className="footnote">{model.footnote}</p> : null}
      <div className={model.layout === "sticky" ? "bar sticky" : "bar"}>
        {model.layout === "sticky"
          ? model.choices.map((choice) => (
              <button key={choice.id} type="button" className="choice" onClick={() => dispatch(choice.action)}>
                {choice.label}
              </button>
            ))
          : null}
        {model.canBack ? (
          <button type="button" className="textish" onClick={() => dispatch({ type: "back" })}>
            Back
          </button>
        ) : null}
        {model.canSkip ? (
          <button type="button" className="textish" onClick={() => dispatch({ type: "skip" })}>
            Skip
          </button>
        ) : null}
        {model.primary && model.kind !== "contact" ? (
          <button type="button" className="primary" disabled={!model.canPrimary} onClick={() => dispatch({ type: "continue" })}>
            {model.primary}
          </button>
        ) : null}
        {model.kind === "contact" ? (
          <button type="button" className="primary" disabled={sending} onClick={() => void sendList()}>
            {sending ? "Sending" : "Send my list"}
          </button>
        ) : null}
      </div>
    </main>
  )
}
