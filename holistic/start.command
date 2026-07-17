#!/bin/bash
# Double-click or: bash holistic/start.command
cd "$(dirname "$0")/.." || exit 1
exec python3 holistic/server.py --port 8770
