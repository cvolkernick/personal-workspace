/**
 * Ops mail is config, never a hardcoded alias.
 * Unconfigured preview still stores the digest; it does not pretend mail went out.
 */

const RESEND = "https://api.resend.com/emails"

export async function sendEmail({ to, subject, text, env = process.env, fetchImpl = fetch }) {
  const from = env.GETHELPFROM_FROM_EMAIL
  const key = env.RESEND_API_KEY
  if (!to || !from || !key) return { emailed: false, reason: "unconfigured" }
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), 15000)
  try {
    const res = await fetchImpl(RESEND, {
      method: "POST",
      signal: ctrl.signal,
      headers: {
        Authorization: `Bearer ${key}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from,
        to: [to],
        subject,
        text,
      }),
    })
    return { emailed: res.ok, status: res.status, reason: res.ok ? "sent" : "provider" }
  } catch {
    return { emailed: false, reason: "failed" }
  } finally {
    clearTimeout(timer)
  }
}

export function sendOpsSummary({ summaryMd, env = process.env, fetchImpl = fetch }) {
  const to = env.GETHELPFROM_OPS_EMAIL
  if (!to) return Promise.resolve({ emailed: false, reason: "unconfigured" })
  return sendEmail({
    to,
    subject: "GetHelpFrom lead",
    text: summaryMd,
    env,
    fetchImpl,
  })
}

export function resumeUrl(origin, sessionId) {
  const url = new URL("/", origin)
  url.searchParams.set("s", sessionId)
  return url.toString()
}
