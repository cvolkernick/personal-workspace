#!/usr/bin/env bash
# Build team-directory.pdf from the prism-local directory page with headless
# Chromium. Content is not in git (public repo): both input (index.html) and
# output (team-directory.pdf) live in HORIZON_DIRECTORY_DIR, default
# research/horizon/directory/_local/ (gitignored).
#   HORIZON_DIRECTORY_DIR=~/.local/share/horizon-directory bash research/horizon/directory/make_pdf.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${HORIZON_DIRECTORY_DIR:-$HERE/_local}"
DIR="${DIR/#\~/$HOME}"
if [ ! -f "$DIR/index.html" ]; then
  echo "make_pdf: $DIR/index.html missing (directory content is prism-local, not in git)" >&2
  exit 1
fi
CHROME="${CHROMIUM_BIN:-$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)}"
if [ -z "$CHROME" ]; then
  echo "make_pdf: no chromium found" >&2
  exit 1
fi
tmp="$DIR/.team-directory.pdf.tmp"
"$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$tmp" "file://$DIR/index.html" >/dev/null 2>&1
[ -s "$tmp" ] || { echo "make_pdf: chromium produced no PDF" >&2; exit 1; }
chmod 600 "$tmp"
mv -f "$tmp" "$DIR/team-directory.pdf"
echo "make_pdf: wrote $DIR/team-directory.pdf"
