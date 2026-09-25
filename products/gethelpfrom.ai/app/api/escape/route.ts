import { NextResponse } from "next/server"
import { classifyEscape } from "../../../lib/escape.js"

export const runtime = "nodejs"

export async function POST(req: Request) {
  let body: { sessionId?: string; text?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 })
  }
  const text = String(body.text ?? "").trim()
  if (text.length < 12) {
    return NextResponse.json({ ok: true, called: false, keptDeterministic: true, reason: "short" })
  }
  const result = await classifyEscape({ text, sessionId: String(body.sessionId ?? "") })
  return NextResponse.json({ ok: true, ...result })
}
