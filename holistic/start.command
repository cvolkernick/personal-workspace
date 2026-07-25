#!/bin/bash
# Open always-on Time allocator dashboard on the Pi (no local server).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/deploy/open_dashboard.sh" holistic
