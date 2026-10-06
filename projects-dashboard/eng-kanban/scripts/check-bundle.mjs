import { execFileSync } from "node:child_process"
import { readFileSync, readdirSync, statSync } from "node:fs"
import { join } from "node:path"

execFileSync("npx", ["vite", "build"], { stdio: "inherit" })

const forbidden = [
  "GITHUB_TOKEN",
  "GH_TOKEN",
  "BUZZ_BOARD_GITHUB_TOKEN",
  "api.github.com",
  "ghp_",
]

function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) {
      walk(path)
      continue
    }
    if (!/\.(js|css|html)$/.test(path)) continue
    const text = readFileSync(path, "utf8")
    for (const needle of forbidden) {
      if (text.includes(needle)) {
        console.error(`${path} contains ${needle}`)
        process.exit(1)
      }
    }
  }
}

walk("dist")
console.log("client bundle has no GitHub token")
