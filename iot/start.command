#!/bin/bash
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 iot/server.py --port 8780
