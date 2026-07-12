#!/bin/bash
# Double-click to connect Google Fit (opens browser once).
cd "$(dirname "$0")"
echo "Connecting Google Fit…"
python3 scripts/google_fit_auth.py
echo ""
echo "Press Enter to close this window."
read -r _
