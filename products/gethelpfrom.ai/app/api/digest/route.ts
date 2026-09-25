import { NextResponse } from "next/server"
import { loadCanon } from "../../../lib/canon.js"
import { buildDigestFromSession } from "../../../lib/digest.js"
import { sendOpsSummary } from "../../../lib/email.js"
import { commitLead } from "../../../lib/store.js"

export const runtime = "nodejs"

export async function POST(req: Request) {
  let body: { session?: Record<string, unknown> }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false, error: "Expected JSON." }, { status: 400 })
  }
  if (!body.session) return NextResponse.json({ ok: false, error: "Missing session." }, { status: 400 })
  const built = buildDigestFromSession(loadCanon(), body.session)
  if (!built.ok || !built.digest) return NextResponse.json(built, { status: 400 })
  const digest = built.digest
  const stored = commitLead(digest)
  const email = stored.deduped
    ? { emailed: false, reason: "deduped" }
    : await sendOpsSummary({ summaryMd: digest.summary_md })
  return NextResponse.json({
    ok: true,
    deduped: stored.deduped,
    auto_accept: false,
    email,
    session_id: digest.session_id,
  })
}
