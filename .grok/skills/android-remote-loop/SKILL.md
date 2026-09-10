---
name: android-remote-loop
description: Drive the Grok Bot remote Android loop on this Mac — emulator screenshot, debug APK assemble, install/launch/screenshot, instrumented tests with stack traces. Use when the user says those AC phrases, mentions Android emulator/adb/gradle from the phone, or runs /android-remote-loop.
---

# Android remote loop

Always execute on the **registered local Mac**, never the Grok Bot cloud computer.

CLI (JSON on stdout):

```bash
python3 android-loop/android_loop.py <doctor|screenshot|assemble|run|test>
# or, from a worktree:
python3 "$HOME/personal-workspace-worktrees/android-remote-loop-617/android-loop/android_loop.py" …
```

If `personal-workspace/android-loop/android_loop.py` exists relative to cwd, use that.

## Phrase map

| User says | Verb |
|-----------|------|
| Open the Android emulator on the registered machine and take a screenshot. | `screenshot` |
| Build the debug APK and report whether it compiled. | `assemble` |
| Install the latest debug build on the emulator, launch the app, and send back a screenshot. | `run` |
| Run instrumented tests and paste the failing stack traces. | `test` |

Unknown sentences: `python3 android-loop/android_loop.py phrase "<sentence>"`.

## Reply rules

- Attach the PNG at JSON `screenshot` when present.
- For `assemble` / `run`, report `compiled` true/false in the first line.
- For `test`, paste every `failures[].stack` in a fenced block. Do not summarize away the stack.
- If `doctor.ok` is false, stop and report the failed checks. JDK 17 is required; do not point `JAVA_HOME` at a missing Android Studio JRE.
- Do not collect keystore passwords, Google passwords, or 2FA. Hand those to the human.
- Default `--project` is `android-loop/fixture` (loop canary). Native FitDash is out of scope unless the user names it.

Full operator doc: `android-loop/README.md`.
