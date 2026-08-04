#!/bin/bash
# Open the always-on Orchestra dashboard on the Raspberry Pi (no local server).
# Double-click this file on macOS.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$ROOT/deploy/open_dashboard.sh" orchestra
