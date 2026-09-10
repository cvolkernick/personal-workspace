# Grok Bot remote Android loop

Drive Android emulator builds, screenshots, and instrumented tests from the
**Grok Bot phone app** the same way a Bot drives the iPhone Simulator on this
Mac. Implementation for [issue #617](https://github.com/cvolkernick/personal-workspace/issues/617).

Native Android FitDash is **not** this package. It is the later validation
app once this loop is green.

## Two computers — do not mix them

Verified against current xAI docs (2026-09-10):

| Computer | What it is | Use it for |
|----------|------------|------------|
| **Grok Bot cloud computer** | Shared Firecracker VM for every Bot on the account. Survives laptop sleep. Docs: [computer-and-apps](https://docs.x.ai/grok-bot/computer-and-apps) | Planning, file work, Gradle *without* an emulator |
| **This registered Mac** | Bacon-style local device via **Grok Bot.app** + local-exec daemon | Emulator, screenshots, `adb`, GPU UI, connected tests |

A Bot only runs commands here when **Settings → General → Agent → Execution on Local Computer** allows it ([approvals](https://docs.x.ai/grok-bot/approvals-security-and-privacy)). Default: **Ask every time**. Mobile approve controls are **Approve once** / **Deny**. Do not switch to **Always allowed** unless Chris says so.

Official downloads: https://x.ai/bot  
Start: https://docs.x.ai/grok-bot/get-started  
Mobile: https://docs.x.ai/grok-bot/mobile

Eligible plans (docs, not guessed): SuperGrok Plus, SuperGrok Heavy, Cursor Pro+, Cursor Ultra, Cursor Teams Standard or Premium.

## Invoke from the phone

Keep **Grok Bot.app** open and signed in on this Mac. Plug in power; disable sleep for the session. Then send **one** of these sentences to any Bot (they map 1:1 onto the CLI):

1. `Open the Android emulator on the registered machine and take a screenshot.`
2. `Build the debug APK and report whether it compiled.`
3. `Install the latest debug build on the emulator, launch the app, and send back a screenshot.`
4. `Run instrumented tests and paste the failing stack traces.`

Pin this as the Bot's standing instruction (or paste it as the first message):

```
Use my registered local Mac, not the Grok Bot cloud computer, for every Android/emulator/adb/gradle command.

Harness:
  python3 ~/personal-workspace/android-loop/android_loop.py

If that path is missing (unmerged branch), use:
  python3 ~/personal-workspace-worktrees/android-remote-loop-617/android-loop/android_loop.py

Map my sentences with `phrase`, or call the verbs directly. JSON is on stdout. When the JSON has a "screenshot" path, attach that PNG in the reply. When "failures" is non-empty, paste every stack trace. Never store keystore passwords, Google passwords, or 2FA codes — hand those steps back to me.
```

Direct verbs (same JSON):

```bash
python3 android-loop/android_loop.py doctor
python3 android-loop/android_loop.py screenshot
python3 android-loop/android_loop.py assemble
python3 android-loop/android_loop.py run          # assemble + install + launch + screenshot
python3 android-loop/android_loop.py test         # connectedDebugAndroidTest
python3 android-loop/android_loop.py phrase "Build the debug APK and report whether it compiled."
```

`--project` points at any Gradle app (default: `android-loop/fixture`, the loop canary — not FitDash).

## Host preflight (this Mac)

`doctor` checks these. Expected on Chris's M1:

- `Grok Bot.app` at `/Applications/Grok Bot.app` with local-exec daemon
- `ANDROID_HOME=$HOME/Library/Android/sdk`
- AVD `Pixel_3a_API_29` (arm64, API 29)
- **JDK 17+** via `brew install openjdk@17` (not the Temurin cask — that pkg needs sudo)
- Android Studio is **not** required

`~/.zshrc` currently exports `JAVA_HOME` at a missing Android Studio JRE. The harness ignores that path and prefers `ANDROID_LOOP_JAVA_HOME` or Homebrew `openjdk@17`. Do not put secrets in this tree.

## What `test` returns

`failures` is a list of `{test, stack}`. That is the AC "paste the failing stack traces" payload. `ok` is false when Gradle fails or any instrumented test fails.

Screenshot / install / launch / connected tests target **emulator serials only**. A USB phone that is authorized for `adb` is never selected; if no AVD is listed, the harness starts `Pixel_3a_API_29`. `test` pins `ANDROID_SERIAL` to that emulator so `connectedDebugAndroidTest` cannot fan out to every device.
