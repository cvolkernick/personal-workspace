import type { NextConfig } from "next"
import { createHash } from "node:crypto"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const appRoot = path.dirname(fileURLToPath(import.meta.url))

function sha(name: string): string {
  return createHash("sha256").update(readFileSync(path.join(appRoot, name))).digest("hex")
}

const nextConfig: NextConfig = {
  outputFileTracingRoot: appRoot,
  env: {
    GHF_CANON_SHA_QUESTIONS: sha("questions.json"),
    GHF_CANON_SHA_SCENARIOS: sha("scenarios.json"),
    GHF_CANON_SHA_SCORING: sha("scoring.json"),
  },
}

export default nextConfig
