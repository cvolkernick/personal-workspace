#!/bin/bash
# Open always-on Financial Command Center on the Pi (no local server).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/deploy/open_dashboard.sh" financial-command
