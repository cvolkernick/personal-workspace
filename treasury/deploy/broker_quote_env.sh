#!/usr/bin/env bash
# Source the shared read-only quote env (treasury/deploy/fm-broker-quote.env).
# Sets FM_BROKER_ADAPTER, FM_BROKER_CLIENT, and FM_BROKER_GROK_BIN only when
# all three are unset, so the bp-poll drop-in keeps its own values.
# Does not place, queue, or cancel an order.
_quote_env_file="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fm-broker-quote.env"
if [[ -z "${FM_BROKER_ADAPTER:-}${FM_BROKER_CLIENT:-}${FM_BROKER_GROK_BIN:-}" && -f "${_quote_env_file}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${_quote_env_file}"
  set +a
fi
unset _quote_env_file
