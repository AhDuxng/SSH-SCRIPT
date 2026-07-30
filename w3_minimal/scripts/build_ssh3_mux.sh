#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

UPSTREAM_URL="${SSH3_UPSTREAM_URL:-https://github.com/francoismichel/ssh3.git}"
UPSTREAM_COMMIT="${SSH3_UPSTREAM_COMMIT:-5b4b242db02a5cfbb9ebf9dfc5aad2c32e10f245}"
BUILD_DIR="${SSH3_BUILD_DIR:-.build/ssh3}"
OUTPUT_BIN="${SSH3_MUX_BIN:-bin/ssh3-mux}"
if [[ "$OUTPUT_BIN" == /* ]]; then
  OUTPUT_PATH="$OUTPUT_BIN"
else
  OUTPUT_PATH="$(pwd)/$OUTPUT_BIN"
fi

if ! command -v go >/dev/null 2>&1; then
  echo "Go >= 1.21 is required to build ssh3-mux" >&2
  exit 2
fi

mkdir -p "$(dirname "$BUILD_DIR")" "$(dirname "$OUTPUT_BIN")"
if [[ ! -d "$BUILD_DIR/.git" ]]; then
  git clone "$UPSTREAM_URL" "$BUILD_DIR"
fi

git -C "$BUILD_DIR" fetch origin "$UPSTREAM_COMMIT"
git -C "$BUILD_DIR" checkout --detach "$UPSTREAM_COMMIT"
PATCH_PATH="$PROJECT_DIR/patches/ssh3_mux.patch"
if git -C "$BUILD_DIR" apply --check "$PATCH_PATH" 2>/dev/null; then
  git -C "$BUILD_DIR" apply "$PATCH_PATH"
elif git -C "$BUILD_DIR" apply --reverse --check "$PATCH_PATH"; then
  echo "SSH3 multiplex patch is already applied"
else
  echo "$PATCH_PATH does not apply cleanly to $UPSTREAM_COMMIT" >&2
  exit 3
fi

GO_BUILD_ARGS=(build -o "$OUTPUT_PATH")
if [[ "$(uname -s)" == "Darwin" ]]; then
  # External linking adds the Mach-O UUID required by current macOS.
  GO_BUILD_ARGS+=( -ldflags=-linkmode=external )
fi
(cd "$BUILD_DIR" && go "${GO_BUILD_ARGS[@]}" cmd/ssh3/main.go)
if [[ "$(uname -s)" == "Darwin" ]]; then
  codesign --force --sign - "$OUTPUT_PATH"
fi
PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" | awk '{print $1}')"
printf '%s\n' "$PATCH_HASH" > "${OUTPUT_PATH}.patch.sha256"
echo "Built $OUTPUT_BIN from SSH3 commit $UPSTREAM_COMMIT"
