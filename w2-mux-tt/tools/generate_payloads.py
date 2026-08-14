#!/usr/bin/env python3
"""Tạo và xác minh bốn payload W2 xác định trước."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


PAYLOAD_BYTES = 102_400
LINE_BYTES = 128
LINE_COUNT = PAYLOAD_BYTES // LINE_BYTES
PAYLOAD_COUNT = 4


# Tạo một dòng ASCII có độ dài cố định và kết thúc bằng LF.
def build_line(stream_index: int, line_index: int) -> bytes:
    header = f"W2S{stream_index}|{line_index:08d}|".encode("ascii")
    digest = hashlib.sha256(
        f"w2-mux-tt:{stream_index}:{line_index}".encode("ascii")
    ).hexdigest().encode("ascii")
    body_size = LINE_BYTES - len(header) - 1
    body = (digest * ((body_size // len(digest)) + 1))[:body_size]
    line = header + body + b"\n"
    if len(line) != LINE_BYTES:
        raise AssertionError("dòng payload không đúng kích thước")
    return line


# Tạo toàn bộ byte của một payload.
def build_payload(stream_index: int) -> bytes:
    payload = b"".join(
        build_line(stream_index, line_index)
        for line_index in range(LINE_COUNT)
    )
    if len(payload) != PAYLOAD_BYTES:
        raise AssertionError("payload không đúng 100 KiB")
    return payload


# Ghi payload, bảng băm và manifest xác định.
def generate(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    checksum_lines = []
    for stream_index in range(PAYLOAD_COUNT):
        name = f"large_output_s{stream_index}_100KiB.txt"
        path = output_dir / name
        payload = build_payload(stream_index)
        digest = hashlib.sha256(payload).hexdigest()
        if not path.exists() or path.read_bytes() != payload:
            path.write_bytes(payload)
        entries.append({
            "stream_index": stream_index,
            "name": name,
            "line_prefix": f"W2S{stream_index}|",
            "bytes": len(payload),
            "lines": payload.count(b"\n"),
            "sha256": digest,
        })
        checksum_lines.append(f"{digest}  {name}")

    manifest = {
        "generator": "w2-mux-tt-v2-100KiB",
        "payload_bytes": PAYLOAD_BYTES,
        "line_bytes": LINE_BYTES,
        "payloads": entries,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "SHA256SUMS").write_text(
        "\n".join(checksum_lines) + "\n", encoding="ascii"
    )
    return manifest


# Kiểm tra payload hiện có khớp manifest vừa tính.
def verify(output_dir: Path) -> dict:
    expected = generate(output_dir)
    for entry in expected["payloads"]:
        path = output_dir / entry["name"]
        raw = path.read_bytes()
        if len(raw) != entry["bytes"]:
            raise ValueError(f"sai kích thước: {path}")
        if raw.count(b"\n") != entry["lines"]:
            raise ValueError(f"sai số dòng: {path}")
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"sai SHA-256: {path}")
    return expected


# Đọc tham số và chuẩn bị payload cho thí nghiệm.
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", nargs="?", default="payloads", type=Path)
    args = parser.parse_args()
    manifest = verify(args.output_dir)
    for entry in manifest["payloads"]:
        print(
            f"{entry['name']} bytes={entry['bytes']} lines={entry['lines']} "
            f"sha256={entry['sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
