#!/usr/bin/env bash
set -euo pipefail

# Tạo một bản sao quic-go đã bật CUBIC và trỏ checkout SSH3 tới bản sao đó.
SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH3_SOURCE="${1:?usage: prepare_quic_cubic.sh SSH3_SOURCE}"
QUIC_MODULE="github.com/quic-go/quic-go"
EXPECTED_VERSION="${SSH3_QUIC_VERSION:-v0.40.1-0.20240102075208-1083d1fb8f98}"
PATCH_PATH="$SHARED_DIR/patches/quic_go_cubic.patch"
PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "${BASH_SOURCE[0]}" | shasum -a 256 | awk '{print $1}')"
QUIC_BUILD_PATH="$SHARED_DIR/.build/quic-go-cubic-1083d1fb8f98-${PATCH_HASH:0:12}"

ACTUAL_VERSION="$(cd "$SSH3_SOURCE" && go list -m -f '{{.Version}}' "$QUIC_MODULE")"
if [[ "$ACTUAL_VERSION" != "$EXPECTED_VERSION" ]]; then
  echo "Unexpected quic-go version: $ACTUAL_VERSION (expected $EXPECTED_VERSION)" >&2
  exit 4
fi

if [[ ! -f "$QUIC_BUILD_PATH/.w3-cubic-ready" ]]; then
  QUIC_SOURCE="$(cd "$SSH3_SOURCE" && go list -m -f '{{.Dir}}' "$QUIC_MODULE")"
  if [[ -z "$QUIC_SOURCE" || ! -d "$QUIC_SOURCE" ]]; then
    (cd "$SSH3_SOURCE" && go mod download "$QUIC_MODULE@$ACTUAL_VERSION")
    QUIC_SOURCE="$(cd "$SSH3_SOURCE" && go list -m -f '{{.Dir}}' "$QUIC_MODULE")"
  fi
  if [[ -e "$QUIC_BUILD_PATH" ]]; then
    echo "Incomplete CUBIC dependency directory exists: $QUIC_BUILD_PATH" >&2
    exit 5
  fi
  mkdir -p "$QUIC_BUILD_PATH"
  cp -R "$QUIC_SOURCE/." "$QUIC_BUILD_PATH/"
  chmod -R u+w "$QUIC_BUILD_PATH"
  # Tạo repository cục bộ để git apply không tự nhận nhầm repository cha.
  git -C "$QUIC_BUILD_PATH" init -q
  git -C "$QUIC_BUILD_PATH" apply --check "$PATCH_PATH"
  git -C "$QUIC_BUILD_PATH" apply "$PATCH_PATH"
  if ! grep -q 'false, // use CUBIC' \
    "$QUIC_BUILD_PATH/internal/ackhandler/sent_packet_handler.go"; then
    echo "CUBIC patch verification failed in $QUIC_BUILD_PATH" >&2
    exit 6
  fi
  printf '%s\n' "$ACTUAL_VERSION" > "$QUIC_BUILD_PATH/.quic-go-version"
  printf '%s\n' "$PATCH_HASH" > "$QUIC_BUILD_PATH/.quic-go-cubic-patch.sha256"
  touch "$QUIC_BUILD_PATH/.w3-cubic-ready"
fi

if ! grep -q 'false, // use CUBIC' \
  "$QUIC_BUILD_PATH/internal/ackhandler/sent_packet_handler.go"; then
  echo "CUBIC patch verification failed in $QUIC_BUILD_PATH" >&2
  exit 6
fi

(cd "$SSH3_SOURCE" && go mod edit -replace "$QUIC_MODULE=$QUIC_BUILD_PATH")
echo "Prepared quic-go CUBIC dependency at $QUIC_BUILD_PATH"
