#!/bin/bash
# One-click launcher for the Personal Command Center
# Double-click this file in Finder (macOS) to start everything.

cd "$(dirname "$0")"
echo "Starting Financial Command Center..."
echo ""
echo "Server: http://localhost:8000/financial-command/index.html"
echo "API: /api/treasury /api/config /api/refresh"
echo "Press Ctrl+C to stop."
echo ""
python3 financial-command/server.py --port 8000