#!/bin/bash
# One-click launcher for the Personal Command Center
# Double-click this file in Finder (macOS) to start everything.

cd "$(dirname "$0")"
echo "🚀 Starting Personal Command Center..."
echo ""
echo "Server will run on http://localhost:8000"
echo "The dashboard will open in your browser automatically."
echo ""
echo "Press Ctrl+C in this Terminal window when you're done."
echo ""

# Give the server a moment to start, then open the browser (macOS)
(sleep 1.2 && open "http://localhost:8000/dashboard/index.html") &

# Start the simple local web server from the repo root
# This makes the live MD loading and all features work perfectly.
python3 -m http.server 8000