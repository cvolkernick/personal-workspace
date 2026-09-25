import { NextResponse } from "next/server"
import { loadSession, saveSession } from "../../../lib/store.js"

export const runtime = "nodejs"

export async function GET(req: Request) {
  const id = new URL(req.url).searchParams.get("id") || ""
  const session = loadSession(id)
  if (!session) return NextResponse.json({ ok: false }, { status: 404 })
  return NextResponse.json({ ok: true, session })
}

export async function POST(req: Request) {
  let body: { session?: { id?: string } }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 })
  }
  const id = body.session?.id
  if (!id || !body.session) return NextResponse.json({ ok: false }, { status: 400 })
  try {
    saveSession(id, body.session)
  } catch {
    return NextResponse.json({ ok: false }, { status: 400 })
  }
  return NextResponse.json({ ok: true, id })
}
