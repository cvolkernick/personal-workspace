#!/usr/bin/env bash
# Install a macOS LaunchAgent that syncs Grok auth → Pi every 20 minutes
# while this Mac is awake/logged in.
#
#   bash projects-dashboard/install_mac_auth_sync.sh
#   bash projects-dashboard/install_mac_auth_sync.sh uninstall

set -euo pipefail

LABEL="com.personalworkspace.sync-pi-grok-auth"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="${REPO}/projects-dashboard/sync_pi_grok_auth.sh"
LOG_DIR="${HOME}/Library/Logs/personal-workspace"
REMOTE="${1:-prism-agent@192.168.100.98}"

if [[ "${1:-}" == "uninstall" ]]; then
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "uninstalled ${LABEL}"
  exit 0
fi

# if first arg looks like user@host, use it
if [[ "${1:-}" == *"@"* ]]; then
  REMOTE="$1"
fi

chmod +x "$SCRIPT"
mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

cat >"$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${SCRIPT}</string>
    <string>${REMOTE}</string>
  </array>
  <key>StartInterval</key>
  <integer>1200</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/sync-pi-grok-auth.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/sync-pi-grok-auth.err</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${HOME}/.grok/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key>
    <string>${HOME}</string>
  </dict>
</dict>
</plist>
EOF

launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true

echo "installed ${LABEL}"
echo "  remote:  ${REMOTE}"
echo "  script:  ${SCRIPT}"
echo "  interval: 20 minutes"
echo "  logs:    ${LOG_DIR}/sync-pi-grok-auth.{log,err}"
echo "  uninstall: bash projects-dashboard/install_mac_auth_sync.sh uninstall"
