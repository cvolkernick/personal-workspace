#!/bin/sh
# Box fallback for the global-allocation refresh.
# Install by copying this file to ~/.local/lib/global-allocation/refresh.sh.
# The Alpha Vantage key, when there is one, comes from the process environment.
# This file has no secrets, hostnames, or ports.
set -eu
kind="${1:-weekly}"
case "$kind" in
  weekly|monthly) ;;
  *)
    echo "kind must be weekly or monthly" >&2
    exit 1
    ;;
esac
root="${GLOBAL_ALLOCATION_ROOT:-$HOME/personal-workspace}"
cd "$root"
/usr/bin/python3 -m research.global_allocation.refresh --kind "$kind"
status=$?
if [ "$status" -ne 0 ]; then
  exit "$status"
fi
if [ "$kind" != monthly ]; then
  exit 0
fi
/usr/bin/python3 -m research.global_allocation.charts
month=$(/usr/bin/python3 -c 'from research.global_allocation.refresh import run_date_default; print(run_date_default()[:7])')
exec /usr/bin/python3 -m research.global_allocation.report --month "$month"
