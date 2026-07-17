#!/bin/bash
cd "$(dirname "$0")/.."
echo "Starting Personal Command Center server on port 8000..."
echo "The dashboard will open in your browser shortly."
echo "Press Ctrl+C in this window to stop the server."

# Open browser after a short delay (macOS)
(sleep 1.5 && open http://localhost:8000/financial-command/index.html) &

python3 -m http.server 8000