#!/usr/bin/env bash
set -euo pipefail

SHARED_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SHARED_DIR"

UPSTREAM_URL="${SSH3_UPSTREAM_URL:-https://github.com/francoismichel/ssh3.git}"
UPSTREAM_COMMIT="${SSH3_UPSTREAM_COMMIT:-5b4b242db02a5cfbb9ebf9dfc5aad2c32e10f245}"
OUTPUT_BIN="${SSH3_MUX_BIN:-bin/ssh3-mux-stdio}"
PATCH_PATH="$SHARED_DIR/patches/ssh3_mux_stdio.patch"
QUIC_PATCH_PATH="$SHARED_DIR/patches/quic_go_cubic.patch"
QUIC_PREPARE_SCRIPT="$SHARED_DIR/scripts/prepare_quic_cubic.sh"
PATCH_HASH="$(shasum -a 256 "$PATCH_PATH" "$QUIC_PATCH_PATH" "$QUIC_PREPARE_SCRIPT" | shasum -a 256 | awk '{print $1}')"
DEFAULT_BUILD_DIR=".build/ssh3-${UPSTREAM_COMMIT:0:12}-${PATCH_HASH:0:12}"
BUILD_DIR="${SSH3_BUILD_DIR:-$DEFAULT_BUILD_DIR}"

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
  echo "Go >= 1.21 is required to build ssh3-mux-stdio" >&2
  exit 2
fi

# Tìm một checkout SSH3 đã có commit cần dùng để build được khi mất Internet.
find_cached_source() {
  local git_dir candidate
  while IFS= read -r git_dir; do
    candidate="${git_dir%/.git}"
    [[ "$candidate" == "$BUILD_PATH" ]] && continue
    if git -C "$candidate" cat-file -e "${UPSTREAM_COMMIT}^{commit}" 2>/dev/null; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(
    find "$SHARED_DIR/.build" -mindepth 2 -maxdepth 2 \
      -type d -name .git -print 2>/dev/null
  )
  return 1
}

mkdir -p "$(dirname "$BUILD_PATH")" "$(dirname "$OUTPUT_PATH")"
if [[ ! -d "$BUILD_PATH/.git" ]]; then
  CACHED_SOURCE="$(find_cached_source || true)"
  if [[ -n "$CACHED_SOURCE" ]]; then
    rmdir "$BUILD_PATH" 2>/dev/null || true
    echo "Reusing cached SSH3 source at $CACHED_SOURCE"
    git clone --no-hardlinks "$CACHED_SOURCE" "$BUILD_PATH"
    git -C "$BUILD_PATH" remote set-url origin "$UPSTREAM_URL"
  else
    git clone "$UPSTREAM_URL" "$BUILD_PATH"
  fi
fi

if ! git -C "$BUILD_PATH" cat-file -e "${UPSTREAM_COMMIT}^{commit}" 2>/dev/null; then
  CACHED_SOURCE="$(find_cached_source || true)"
  if [[ -n "$CACHED_SOURCE" ]]; then
    echo "Fetching required commit from local cache $CACHED_SOURCE"
    git -C "$BUILD_PATH" fetch "$CACHED_SOURCE" "$UPSTREAM_COMMIT"
  else
    git -C "$BUILD_PATH" fetch origin "$UPSTREAM_COMMIT"
  fi
fi
git -C "$BUILD_PATH" checkout --detach "$UPSTREAM_COMMIT"
if git -C "$BUILD_PATH" apply --check "$PATCH_PATH" 2>/dev/null; then
  git -C "$BUILD_PATH" apply "$PATCH_PATH"
elif git -C "$BUILD_PATH" apply --reverse --check "$PATCH_PATH"; then
  echo "Shared SSH3 multiplex patch is already applied"
else
  echo "$PATCH_PATH does not apply cleanly to $UPSTREAM_COMMIT" >&2
  exit 3
fi
bash "$QUIC_PREPARE_SCRIPT" "$BUILD_PATH"

GO_BUILD_ARGS=(build -o "$OUTPUT_PATH")
if [[ "$(uname -s)" == "Darwin" ]]; then
  GO_BUILD_ARGS+=( -ldflags=-linkmode=external )
fi
(cd "$BUILD_PATH" && go "${GO_BUILD_ARGS[@]}" cmd/ssh3/main.go)
if [[ "$(uname -s)" == "Darwin" ]]; then
  codesign --force --sign - "$OUTPUT_PATH"
fi
printf '%s\n' "$PATCH_HASH" > "${OUTPUT_PATH}.patch.sha256"
printf 'ssh3_commit=%s\nquic_go_version=%s\ncc_algorithm=cubic\npatch_hash=%s\n' \
  "$UPSTREAM_COMMIT" \
  "${SSH3_QUIC_VERSION:-v0.40.1-0.20240102075208-1083d1fb8f98}" \
  "$PATCH_HASH" > "${OUTPUT_PATH}.build-info"
echo "Built $OUTPUT_PATH from SSH3 commit $UPSTREAM_COMMIT"
