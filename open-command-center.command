#!/bin/bash
# One-click launcher for the Orchestra top-level command center
# Double-click this file in Finder (macOS) to start everything.

cd "$(dirname "$0")"
echo "Starting Orchestra Command Center..."
echo ""
echo "UI:  http://localhost:8790/"
echo "API: http://localhost:8790/api/orchestra"
echo "     domains · synergies · priorities / action plan"
echo ""
echo "Subordinates (start separately if needed):"
echo "  financial-command  :8000"
echo "  projects-dashboard :8765"
echo "  holistic           :8770"
echo "  resistance-dashboard :8787"
echo ""
echo "Press Ctrl+C to stop."
echo ""
python3 orchestra/server.py --port 8790
