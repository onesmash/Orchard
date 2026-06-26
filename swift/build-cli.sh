#!/usr/bin/env bash
# Build the orchard-indexstore-reader Swift CLI and install it to repo bin/.
#
# The Python ingest layer prefers the SwiftPM build output and still supports
# the historical bundled path at <repo-root>/bin/orchard-indexstore-reader.
# This script keeps bin/ as a stable entrypoint by installing a tiny wrapper
# that execs the real SwiftPM build artifact.
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
WRAPPER="$ROOT/bin/orchard-indexstore-reader"
cat >"$WRAPPER" <<EOF
#!/usr/bin/env bash
set -euo pipefail
ROOT="\$(cd "\$(dirname "\$0")/.." && pwd)"
BIN="\$ROOT/swift/orchard-indexstore-reader/.build/$CONFIG/orchard-indexstore-reader"
exec "\$BIN" "\$@"
EOF
chmod +x "$WRAPPER"
echo ">> installed wrapper $WRAPPER -> $BIN"
"$BIN" --help >/dev/null 2>&1
"$WRAPPER" --help >/dev/null 2>&1
echo ">> OK"
