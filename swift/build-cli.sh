#!/usr/bin/env bash
# Build the orchard-indexstore-reader Swift CLI and install it to repo bin/.
#
# The Python ingest layer (orchard.ingest.indexstore) finds the CLI at
#   <repo-root>/bin/orchard-indexstore-reader   (bundled)
# or on $PATH. This script produces the bundled binary.
#
# Usage: bash swift/build-cli.sh [--debug]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$ROOT/swift/orchard-indexstore-reader"
CONFIG="release"
[[ "${1:-}" == "--debug" ]] && CONFIG="debug"

echo ">> building orchard-indexstore-reader ($CONFIG) in $PKG"
swift build -c "$CONFIG" --package-path "$PKG"

BIN="$PKG/.build/$CONFIG/orchard-indexstore-reader"
[[ -x "$BIN" ]] || { echo "build did not produce $BIN" >&2; exit 1; }

mkdir -p "$ROOT/bin"
cp "$BIN" "$ROOT/bin/orchard-indexstore-reader"
echo ">> installed $ROOT/bin/orchard-indexstore-reader"
"$ROOT/bin/orchard-indexstore-reader" --help >/dev/null 2>&1 && echo ">> OK"
