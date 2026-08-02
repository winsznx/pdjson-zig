#!/bin/sh
# Record the exact toolchain the evidence was produced with, and fail early if
# something required is missing. Versions are written to
# artifacts/toolchain.json so every other artifact can be traced to a build
# environment rather than to "a laptop, once".
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

need() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "FAIL: required tool not found: $1" >&2
        exit 1
    }
}

need zig
need cc
need python3

ZIG_VERSION=$(zig version)
CC_VERSION=$(cc --version 2>&1 | head -1)
PY_VERSION=$(python3 --version 2>&1)
UNAME=$(uname -a)

# The port is written against Zig 0.16's std API (std.Io, process.Init). Older
# or much newer toolchains will not compile it, so say so plainly instead of
# failing later with a wall of type errors.
case "$ZIG_VERSION" in
    0.16.*) : ;;
    *)
        echo "WARNING: this project is developed against Zig 0.16.x; found $ZIG_VERSION." >&2
        echo "         The build may fail on std library API differences." >&2
        ;;
esac

mkdir -p "$ROOT/artifacts"
cat > "$ROOT/artifacts/toolchain.json" <<EOF
{
  "schema": "pdjson-zig/toolchain@1",
  "zig": "$ZIG_VERSION",
  "c_compiler": "$CC_VERSION",
  "python": "$PY_VERSION",
  "uname": "$UNAME"
}
EOF

echo "  zig    $ZIG_VERSION"
echo "  cc     $CC_VERSION"
echo "  python $PY_VERSION"
echo "  wrote artifacts/toolchain.json"
