#!/bin/bash
# Launch Horizon visual dashboard
cd "$(dirname "$0")/../.." || exit 1
exec python3 research/horizon/server.py --bootstrap
