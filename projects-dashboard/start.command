#!/bin/bash
# One-click launcher for the Projects Dashboard (macOS double-click).
cd "$(dirname "$0")"
echo "Starting Projects Dashboard..."
echo ""
echo "Server: http://127.0.0.1:8765/"
echo "API:    GET /api/projects"
echo "Press Ctrl+C to stop."
echo ""
python3 server.py --port 8765
