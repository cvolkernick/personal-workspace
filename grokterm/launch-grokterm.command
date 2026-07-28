#!/bin/zsh
# Double-click or: open this file. Keeps the window open if GrokTerm exits.
set -u
export PATH="$HOME/.cargo/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"
ROOT="/Users/cvolkernick/personal-workspace/grokterm"
BIN="$ROOT/target/release/grokterm"
DEBUG="$ROOT/target/debug/grokterm"
LOG="$ROOT/launch-debug.log"

cd "$ROOT" || {
  echo "ERROR: cannot cd to $ROOT"
  echo "Press Enter to close..."
  read
  exit 1
}

clear
echo "========================================"
echo "  GrokTerm launcher"
echo "========================================"
echo "Time: $(date)"
echo "Dir:  $ROOT"
echo ""

if [[ ! -x "$BIN" ]]; then
  if [[ -x "$DEBUG" ]]; then
    BIN="$DEBUG"
    echo "Using debug binary (no release build)."
  else
    echo "Binary missing. Building release..."
    if [[ -f "$HOME/.cargo/env" ]]; then
      # shellcheck disable=SC1091
      source "$HOME/.cargo/env"
    fi
    if ! command -v cargo >/dev/null 2>&1; then
      echo "ERROR: cargo not found. Install Rust: https://rustup.rs"
      echo "Press Enter to close..."
      read
      exit 1
    fi
    cargo build --release 2>&1 | tee -a "$LOG"
    BIN="$ROOT/target/release/grokterm"
  fi
fi

if [[ ! -x "$BIN" ]]; then
  echo "ERROR: still no binary at $BIN"
  echo "Press Enter to close..."
  read
  exit 1
fi

echo "Binary: $BIN"
echo "Keys: Ctrl+T shell · Ctrl+B grok · Ctrl+G manager · Ctrl+V voice · Ctrl+Q quit"
echo "Starting in 1s..."
echo "$(date) starting $BIN" >>"$LOG"
sleep 1

# Run in the foreground on this real TTY (no process substitution — that breaks raw mode).
"$BIN"
code=$?
echo ""
echo "$(date) exited code=$code" >>"$LOG"
echo "========================================"
echo "  GrokTerm exited with code $code"
echo "========================================"
if [[ $code -ne 0 ]]; then
  echo "If this was unexpected, check: $LOG"
fi
echo "Press Enter to close this window..."
read
exit "$code"
