/**
 * Reads the git canon next to this app. No copied question catalog.
 */
import { createHash } from "node:crypto"
import { execFileSync } from "node:child_process"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")

const FILES = ["questions.json", "scenarios.json", "scoring.json"]

const ENV_KEYS = {
  "questions.json": "GHF_CANON_SHA_QUESTIONS",
  "scenarios.json": "GHF_CANON_SHA_SCENARIOS",
  "scoring.json": "GHF_CANON_SHA_SCORING",
}

export function productRoot() {
  return ROOT
}

export function readJson(name) {
  return JSON.parse(readFileSync(path.join(ROOT, name), "utf8"))
}

export function loadCanon() {
  return {
    questions: readJson("questions.json"),
    scenarios: readJson("scenarios.json"),
    scoring: readJson("scoring.json"),
    digestSchema: readJson("digest.schema.json"),
  }
}

export function sha256File(name) {
  return createHash("sha256").update(readFileSync(path.join(ROOT, name))).digest("hex")
}

export function gitBlob(name) {
  try {
    return execFileSync("git", ["hash-object", path.join(ROOT, name)], {
      encoding: "utf8",
    }).trim()
  } catch {
    return null
  }
}

export function canonReport(env = process.env) {
  const canon = loadCanon()
  const versionOf = {
    "questions.json": canon.questions.version,
    "scenarios.json": canon.scenarios.version,
    "scoring.json": canon.scoring.version,
  }
  const files = {}
  let drift = false
  for (const name of FILES) {
    const sha256 = sha256File(name)
    const expected = env[ENV_KEYS[name]]
    const mismatched = Boolean(expected) && expected !== sha256
    if (mismatched) drift = true
    files[name] = {
      version: versionOf[name],
      sha256,
      git_blob: gitBlob(name),
      drift: mismatched,
    }
  }
  return {
    versions: {
      questions: canon.questions.version,
      scenarios: canon.scenarios.version,
      scoring: canon.scoring.version,
    },
    files,
    drift,
  }
}
