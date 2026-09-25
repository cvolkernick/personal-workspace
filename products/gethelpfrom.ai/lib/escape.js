/**
 * Stage 4 escape hatch. One SpaceXAI call, 400 tokens, 8s, one call per session.
 * Failure keeps the deterministic shortlist and stores the raw text.
 */
import { escapeUsed, markEscapeUsed } from "./store.js"

export const ESCAPE_LIMITS = {
  maxTokens: 400,
  timeoutMs: 8000,
  maxCallsPerSession: 1,
  maxUsdPerSession: 0.03,
  model: "grok-4.5",
  endpoint: "https://api.x.ai/v1/chat/completions",
}

export function screenEscapeText(session, text) {
  const trimmed = String(text ?? "").trim()
  if (trimmed.length < 12 && !session.escapeReprompted) {
    return {
      ok: false,
      session: {
        ...session,
        escapeReprompted: true,
        escapeNote: "Say a little more, or leave it and keep the list.",
      },
    }
  }
  return {
    ok: true,
    session: {
      ...session,
      escapeText: trimmed,
      escapeNote: "",
    },
  }
}

export async function classifyEscape({
  text,
  sessionId,
  env = process.env,
  fetchImpl = fetch,
}) {
  const kept = { keptDeterministic: true, text }
  if (!env.XAI_API_KEY) return { ...kept, called: false, reason: "unconfigured" }
  if (escapeUsed(sessionId, env)) return { ...kept, called: false, reason: "cap" }
  markEscapeUsed(sessionId, env)
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), ESCAPE_LIMITS.timeoutMs)
  try {
    const res = await fetchImpl(ESCAPE_LIMITS.endpoint, {
      method: "POST",
      signal: ctrl.signal,
      headers: {
        Authorization: `Bearer ${env.XAI_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: ESCAPE_LIMITS.model,
        max_tokens: ESCAPE_LIMITS.maxTokens,
        messages: [
          {
            role: "system",
            content:
              "The visitor said the shortlist missed. Reply in one short paragraph a human can read. Do not accept a build. Do not promise a price.",
          },
          { role: "user", content: String(text).slice(0, 2000) },
        ],
      }),
    })
    if (!res.ok) return { ...kept, called: true, reason: "provider" }
    const data = await res.json()
    const pass = data?.choices?.[0]?.message?.content
    return {
      ...kept,
      called: true,
      reason: "ok",
      classifierPass: typeof pass === "string" ? pass : "",
    }
  } catch {
    return { ...kept, called: true, reason: "failed" }
  } finally {
    clearTimeout(timer)
  }
}
