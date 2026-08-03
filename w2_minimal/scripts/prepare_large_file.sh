#!/usr/bin/env bash
set -eu

output_path="${1:?missing output path}"
size_bytes="${2:?missing size in bytes}"

if ! [[ "$size_bytes" =~ ^[0-9]+$ ]] || [[ "$size_bytes" -le 0 ]]; then
  echo "size must be a positive integer" >&2
  exit 2
fi

mkdir -p "$(dirname "$output_path")"
# yes tạo nội dung text lặp lại; head giữ kích thước file chính xác.
yes 'W2 large output payload 0123456789 abcdefghijklmnopqrstuvwxyz' \
  | head -c "$size_bytes" > "$output_path" || true

actual_size="$(wc -c < "$output_path" | tr -d ' ')"
if [[ "$actual_size" != "$size_bytes" ]]; then
  echo "large file size mismatch: expected=$size_bytes actual=$actual_size" >&2
  exit 3
fi
printf 'W2_LARGE_FILE_READY path=%s bytes=%s\n' "$output_path" "$actual_size"

