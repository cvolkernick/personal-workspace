/**
 * Private lead store. Default directory is outside the repo (os tmp)
 * so PII is not written onto personal-workspace master.
 * auto_accept stays false. Nothing here starts a demo build.
 */
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import os from "node:os"
import path from "node:path"

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const DAY_MS = 24 * 60 * 60 * 1000

export function dataDir(env = process.env) {
  return env.GETHELPFROM_DATA_DIR || path.join(os.tmpdir(), "gethelpfrom-data")
}

function ensure(dir) {
  mkdirSync(dir, { recursive: true })
}

export function saveSession(id, state, env = process.env) {
  if (!UUID.test(id)) throw new Error("bad session id")
  const dir = path.join(dataDir(env), "sessions")
  ensure(dir)
  writeFileSync(path.join(dir, `${id}.json`), JSON.stringify(state))
}

export function loadSession(id, env = process.env) {
  if (!UUID.test(String(id || ""))) return null
  const file = path.join(dataDir(env), "sessions", `${id}.json`)
  if (!existsSync(file)) return null
  return JSON.parse(readFileSync(file, "utf8"))
}

function leadEmail(doc) {
  return String(doc.contact?.email || doc.sessions?.[0]?.contact?.email || "").toLowerCase()
}

function leadTime(doc) {
  const raw = doc.completed_at || doc.sessions?.[0]?.completed_at || 0
  return new Date(raw).getTime()
}

export function commitLead(digest, env = process.env) {
  const dir = path.join(dataDir(env), "leads")
  ensure(dir)
  const email = String(digest.contact.email || "").toLowerCase()
  const now = new Date(digest.completed_at).getTime()
  for (const name of readdirSync(dir)) {
    if (!name.endsWith(".json")) continue
    const full = path.join(dir, name)
    const doc = JSON.parse(readFileSync(full, "utf8"))
    const firstAt = leadTime(doc)
    if (leadEmail(doc) !== email) continue
    if (!(now - firstAt >= 0 && now - firstAt < 14 * DAY_MS)) continue
    const sessions = Array.isArray(doc.sessions) ? doc.sessions.slice() : [doc]
    sessions.push(digest)
    const next = { ...doc, sessions, deduped: true, auto_accept: false }
    writeFileSync(full, JSON.stringify(next, null, 2))
    return { deduped: true, file: name, auto_accept: false }
  }
  const day = String(digest.completed_at).slice(0, 10)
  const file = `${day}-${digest.session_id}.json`
  const doc = { ...digest, auto_accept: false, sessions: [digest] }
  writeFileSync(path.join(dir, file), JSON.stringify(doc, null, 2))
  return { deduped: false, file, auto_accept: false }
}

export function escapeUsed(sessionId, env = process.env) {
  if (!UUID.test(sessionId)) return true
  return existsSync(path.join(dataDir(env), "escape", `${sessionId}.json`))
}

export function markEscapeUsed(sessionId, env = process.env) {
  if (!UUID.test(sessionId)) throw new Error("bad session id")
  const dir = path.join(dataDir(env), "escape")
  ensure(dir)
  writeFileSync(path.join(dir, `${sessionId}.json`), JSON.stringify({ at: new Date().toISOString() }))
}
