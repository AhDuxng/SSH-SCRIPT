#!/usr/bin/env python3
"""Generate the deterministic 1 MiB W4 background payload."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR / "src"))

from constants import (  # noqa: E402
    PAYLOAD_BYTES, PAYLOAD_LINE_BYTES, PAYLOAD_LINES, PAYLOAD_NAME,
    PAYLOAD_PREFIX, PAYLOAD_SHA256,
)


def build_line(index: int) -> bytes:
    header = f"{PAYLOAD_PREFIX}{index:08d}|".encode("ascii")
    digest = hashlib.sha256(f"w4-mux-tt:0:{index}".encode()).hexdigest().encode()
    size = PAYLOAD_LINE_BYTES - len(header) - 1
    return header + (digest * (size // len(digest) + 1))[:size] + b"\n"


def build_payload() -> bytes:
    payload = b"".join(build_line(index) for index in range(PAYLOAD_LINES))
    if len(payload) != PAYLOAD_BYTES or hashlib.sha256(payload).hexdigest() != PAYLOAD_SHA256:
        raise AssertionError("deterministic W4 payload does not match its fixed specification")
    return payload


def main() -> int:
    output_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "payloads")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_payload()
    path = output_dir / PAYLOAD_NAME
    if not path.exists() or path.read_bytes() != payload:
        path.write_bytes(payload)
    manifest = {
        "name": PAYLOAD_NAME, "bytes": len(payload), "lines": payload.count(b"\n"),
        "line_bytes": PAYLOAD_LINE_BYTES, "line_prefix": PAYLOAD_PREFIX,
        "sha256": PAYLOAD_SHA256,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output_dir / "SHA256SUMS").write_text(f"{PAYLOAD_SHA256}  {PAYLOAD_NAME}\n")
    print(f"{PAYLOAD_NAME} bytes={len(payload)} lines={PAYLOAD_LINES} sha256={PAYLOAD_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
