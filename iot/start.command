#!/bin/bash
# Local UI on Mac; API/control/schedules proxied to Pi (see iot/backend.json)
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$(pwd)"
exec python3 iot/server.py --port 8780 --host 127.0.0.1
