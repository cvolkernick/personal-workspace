#!/usr/bin/env bash
# Build team-directory.pdf from index.html with headless Chromium (run on prism
# at deploy). Output is gitignored; Horizon serves it at /directory/team-directory.pdf.
#   bash research/horizon/directory/make_pdf.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CHROME="${CHROMIUM_BIN:-$(command -v chromium || command -v chromium-browser || command -v google-chrome || true)}"
if [ -z "$CHROME" ]; then
  echo "make_pdf: no chromium found" >&2
  exit 1
fi
tmp="$HERE/.team-directory.pdf.tmp"
"$CHROME" --headless --disable-gpu --no-sandbox --no-pdf-header-footer \
  --print-to-pdf="$tmp" "file://$HERE/index.html" >/dev/null 2>&1
[ -s "$tmp" ] || { echo "make_pdf: chromium produced no PDF" >&2; exit 1; }
mv -f "$tmp" "$HERE/team-directory.pdf"
echo "make_pdf: wrote $HERE/team-directory.pdf"
