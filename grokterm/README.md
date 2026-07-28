# GrokTerm

Clean-room multi-tab terminal host for **Grok Build**.

Real PTY sessions (shell + Grok CLI), a shared manager control plane, and
two-way voice tool dispatch onto the same host actions.

Public product description: [grokterm.com](https://grokterm.com/) · announcement
by [@Daniel_Farinax](https://x.com/Daniel_Farinax).

## Features (MVP)

- **Multi-tab PTYs** — independent interactive shell sessions per tab
- **Grok tabs** — embed the real local Grok CLI (`~/.grok/bin/grok` or `PATH`)
- **Manager control plane** — `help`, open shell/Grok, close/list/switch tabs
- **Voice dispatch** — intents/tools map to the same manager actions
- **Host keys** — Ctrl+T new shell · Ctrl+B new Grok · Ctrl+G manager · Ctrl+V voice · Ctrl+Q quit

## Requirements

- macOS 13+ (or Linux with a working PTY)
- Rust 1.70+ to build
- Optional: Grok Build CLI at `~/.grok/bin/grok` for Grok tabs

## Build & run

```bash
cargo build --release
./target/release/grokterm --help
./target/release/grokterm              # interactive host
./target/release/grokterm --grok       # start with a Grok tab
./target/release/grokterm --voice      # voice entry path (dispatch + live if available)
```

## Tests

```bash
cargo test
```

## Architecture

| Module        | Role                                      |
|---------------|-------------------------------------------|
| `tab`         | Tab kinds and multi-tab state machine     |
| `pty_session` | Real PTY spawn / read / write             |
| `manager`     | Shared control-plane command parse/dispatch |
| `grok_path`   | Resolve Grok binary; clear missing errors |
| `voice`       | Intent/tool → manager command mapping     |
| `keys`        | Host key bindings                         |
| `host`        | Coordinates tabs + sessions + UI          |

## Non-goals (deferred)

Notarized `.app`/DMG, auto-update, session restore, full VT/mouse parity,
full MCP host, production theme system.

## License

MIT
