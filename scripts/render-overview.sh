#!/usr/bin/env bash
# Render docs/images/overview.html to docs/images/overview.png (3200x1810)
# with headless Firefox at 2x. Needs the Inter and JetBrains Mono fonts.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
profile=$(mktemp -d)
trap 'rm -rf "$profile"' EXIT
# Firefox ignores devPixelsPerPx for --screenshot; the page doubles itself
# with CSS zoom when loaded with #2x.

firefox --headless --no-remote --profile "$profile" \
  --window-size=3200,1810 \
  --screenshot "$root/docs/images/overview.png" \
  "file://$root/docs/images/overview.html#2x" 2>/dev/null
file "$root/docs/images/overview.png"
