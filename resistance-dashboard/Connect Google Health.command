#!/bin/bash
# Double-click after creating the Web OAuth client for project Grok-code.
cd "$(dirname "$0")"
echo "=========================================="
echo " Connect Google Health API"
echo "=========================================="
echo ""
echo "Scopes requested: weight/metrics, sleep, nutrition/macros, activity calories."
echo "If Google shows an unverified app warning: Advanced → Continue (Testing mode)."
echo "Ensure your Google account is a Test user on project Grok-code."
echo ""
python3 scripts/google_fit_auth.py
echo ""
echo "Press Enter to close."
read -r _
