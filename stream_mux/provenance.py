"""Ghi lại bằng chứng về binary thực sự được dùng cho một lần chạy."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from .connection.common import ssh_base


# Đọc thuật toán congestion control đã được nướng vào một binary.
def binary_congestion(path: Path) -> str:
    """Trả về 'cubic', 'reno' hoặc 'unknown'.

    `go mod edit -replace` trỏ quic-go sang cây nguồn đã vá trong `.build/`, và
    đường dẫn đó nằm lại trong khối metadata module của binary. Không cần Go
    trên máy đang kiểm tra.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return "unknown"
    if b"quic-go-cubic" in data:
        return "cubic"
    if b"quic-go/quic-go" in data:
        return "reno"
    return "unknown"


# Tính SHA-256 của một tệp theo từng khối.
def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


# Mô tả binary SSH3 phía client.
def local_ssh3_binary(cfg: dict, project_dir: Path) -> dict:
    raw = cfg.get("SSH3_MUX_BIN", "../stream_mux/bin/ssh3-mux-stdio")
    path = Path(raw) if os.path.isabs(raw) else (project_dir / raw)
    path = path.resolve()
    record = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return record
    stat = path.stat()
    record.update({
        "sha256": file_sha256(path),
        "size_bytes": stat.st_size,
        "modified_time_ns": stat.st_mtime_ns,
        "congestion_control": binary_congestion(path),
    })
    info = path.with_suffix(path.suffix + ".build-info")
    if info.exists():
        record["build_info"] = dict(
            line.split("=", 1)
            for line in info.read_text().splitlines()
            if "=" in line
        )
    return record


# Script dò binary ssh3-server đang phục vụ trên máy đích.
REMOTE_SERVER_PROBE = "\n".join([
    'pid=$(pgrep -x ssh3-server 2>/dev/null | head -1)',
    '[ -z "$pid" ] && pid=$(pgrep -f "[s]sh3-server" 2>/dev/null | head -1)',
    'if [ -z "$pid" ]; then echo "state=stopped"; else',
    '  echo "state=running"',
    # /proc/<pid>/exe chỉ đọc được nếu tiến trình cùng chủ sở hữu; server
    # thường chạy dưới systemd nên phải lần lượt thử nhiều nguồn.
    '  exe=$(readlink -f /proc/$pid/exe 2>/dev/null || true)',
    '  if [ -z "$exe" ] || [ ! -r "$exe" ]; then',
    '    exe=$(ps -p "$pid" -o args= 2>/dev/null | awk "{print \\$1}")',
    '  fi',
    '  if [ -z "$exe" ] || [ ! -r "$exe" ]; then',
    '    exe=$(systemctl show -p ExecStart --value ssh3-server 2>/dev/null '
    '| sed -n "s/.*path=\\([^ ;]*\\).*/\\1/p" | head -1)',
    '  fi',
    '  if [ -z "$exe" ] || [ ! -r "$exe" ]; then',
    '    for candidate in /usr/local/bin/ssh3-server /usr/bin/ssh3-server; do',
    '      [ -r "$candidate" ] && exe="$candidate" && break',
    '    done',
    '  fi',
    '  echo "path=${exe:-}"',
    '  if [ -n "$exe" ] && [ -r "$exe" ]; then',
    '    if grep -a -q quic-go-cubic "$exe" 2>/dev/null; then',
    '      echo "congestion_control=cubic"',
    '    else echo "congestion_control=reno"; fi',
    '    echo "sha256=$(sha256sum "$exe" 2>/dev/null | awk "{print \\$1}")"',
    '    info="${exe}.build-info"',
    '    [ -r "$info" ] && sed "s/^/build_info_/" "$info"',
    '  else echo "congestion_control=unreadable"; fi',
    'fi',
    'exit 0',
])


# Hỏi máy đích xem nó đang phục vụ binary SSH3 nào.
def remote_ssh3_binary(cfg: dict, timeout: float = 20.0) -> dict:
    target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
    try:
        checked = subprocess.run(
            [*ssh_base(cfg), "-o", "ConnectTimeout=10", target, REMOTE_SERVER_PROBE],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": repr(exc)}
    record: dict = {}
    build_info: dict = {}
    for line in checked.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.startswith("build_info_"):
            build_info[key[len("build_info_"):]] = value
        else:
            record[key] = value
    if build_info:
        record["build_info"] = build_info
    if not record:
        record = {
            "state": "unknown",
            "note": "không lấy được thông tin binary từ máy đích",
        }
    return record


# Gom toàn bộ bằng chứng về transport dùng cho một lần chạy.
def collect(cfg: dict, protocols, project_dir: Path) -> dict:
    """Không bao giờ ném lỗi: thiếu bằng chứng không được làm hỏng phép đo."""
    record: dict = {}
    try:
        if "ssh3" in protocols:
            record["ssh3_client_binary"] = local_ssh3_binary(cfg, project_dir)
            record["ssh3_server_binary"] = remote_ssh3_binary(cfg)
    except Exception as exc:
        record["error"] = repr(exc)
    return record
