#!/usr/bin/env bash
set -euo pipefail

SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SHARED_DIR"

UPSTREAM_URL="${SSH3_UPSTREAM_URL:-https://github.com/francoismichel/ssh3.git}"
UPSTREAM_COMMIT="${SSH3_UPSTREAM_COMMIT:-5b4b242db02a5cfbb9ebf9dfc5aad2c32e10f245}"
OUTPUT_BIN="${SSH3_SERVER_BIN:-bin/ssh3-server-instrumented}"
PATCH_PATH="$SHARED_DIR/patches/ssh3_mux_stdio.patch"
CC_SOURCE_PATH="$SHARED_DIR/patches/mux_cc.go"
PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$CC_SOURCE_PATH" | shasum -a 256 | awk '{print $1}')"
DEFAULT_BUILD_DIR=".build/ssh3-server-${UPSTREAM_COMMIT:0:12}-${PATCH_HASH:0:12}"
BUILD_DIR="${SSH3_SERVER_BUILD_DIR:-$DEFAULT_BUILD_DIR}"

if [[ "$BUILD_DIR" == /* ]]; then
  BUILD_PATH="$BUILD_DIR"
else
  BUILD_PATH="$SHARED_DIR/$BUILD_DIR"
fi
if [[ "$OUTPUT_BIN" == /* ]]; then
  OUTPUT_PATH="$OUTPUT_BIN"
else
  OUTPUT_PATH="$SHARED_DIR/$OUTPUT_BIN"
fi

if ! command -v go >/dev/null 2>&1; then
  echo "Go >= 1.21 is required to build instrumented ssh3-server" >&2
  exit 2
fi

mkdir -p "$(dirname "$BUILD_PATH")" "$(dirname "$OUTPUT_PATH")"
if [[ ! -d "$BUILD_PATH/.git" ]]; then
  git clone "$UPSTREAM_URL" "$BUILD_PATH"
fi

git -C "$BUILD_PATH" fetch origin "$UPSTREAM_COMMIT"
git -C "$BUILD_PATH" checkout --detach "$UPSTREAM_COMMIT"
if git -C "$BUILD_PATH" apply --check "$PATCH_PATH" 2>/dev/null; then
  git -C "$BUILD_PATH" apply "$PATCH_PATH"
elif git -C "$BUILD_PATH" apply --reverse --check "$PATCH_PATH"; then
  echo "Shared SSH3 instrumentation patch is already applied"
else
  echo "$PATCH_PATH does not apply cleanly to $UPSTREAM_COMMIT" >&2
  exit 3
fi
cp "$CC_SOURCE_PATH" "$BUILD_PATH/cmd/mux_cc.go"

(cd "$BUILD_PATH" && go build -o "$OUTPUT_PATH" cmd/ssh3-server/main.go)
printf '%s\n' "$PATCH_HASH" > "${OUTPUT_PATH}.patch.sha256"
echo "Built $OUTPUT_PATH with server-side QUIC congestion tracing"
