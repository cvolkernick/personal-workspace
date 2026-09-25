import { NextResponse } from "next/server"
import { resumeUrl, sendEmail } from "../../../lib/email.js"
import { saveSession } from "../../../lib/store.js"

export const runtime = "nodejs"

export async function POST(req: Request) {
  let body: { session?: { id?: string; contact?: { name?: string; email?: string } } }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 })
  }
  const session = body.session
  const email = String(session?.contact?.email ?? "").trim()
  if (!session?.id || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ ok: false, error: "Email is required." }, { status: 400 })
  }
  try {
    saveSession(session.id, session)
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 })
  }
  const origin = new URL(req.url).origin
  const url = resumeUrl(origin, session.id)
  const sent = await sendEmail({
    to: email,
    subject: "Your GetHelpFrom list",
    text: `Pick up where you left off:\n${url}\n`,
  })
  return NextResponse.json({ ok: true, url, emailed: sent.emailed })
}
