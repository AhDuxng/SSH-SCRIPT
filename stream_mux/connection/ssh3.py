"""Transport SSH3 dùng Go client đã patch để mở nhiều QUIC stream."""

from __future__ import annotations

import base64
import json
import os
import queue
import shlex
import signal
import subprocess
import threading
import time

from .base import ConnectionAudit, MultiplexConnection, RawStream, StreamSpec
from .common import JSONPipeReader, PipeReader, cfg_bool, process_tree, socket_rows


class SSH3Connection(MultiplexConnection):
    # Khởi tạo trạng thái bridge SSH3 của một trial.
    def __init__(self, cfg: dict, specs: list[StreamSpec], trial_tag: str):
        super().__init__("ssh3", specs)
        self.cfg = cfg
        self.trial_tag = trial_tag
        self.process: subprocess.Popen | None = None
        self.reader: JSONPipeReader | None = None
        self.stderr_reader: PipeReader | None = None
        self.control_events: queue.Queue[dict] = queue.Queue()
        self._write_lock = threading.Lock()
        self._stderr_lock = threading.Lock()
        self._stderr_buffer = bytearray()

    # Giữ phần cuối stderr để chẩn đoán lỗi khởi động.
    def _capture_stderr(self, data: bytes, wall_ns: int = 0, mono_ns: int = 0) -> None:
        with self._stderr_lock:
            self._stderr_buffer.extend(data)
            if len(self._stderr_buffer) > 131072:
                del self._stderr_buffer[:-131072]

    # Chuyển stderr đã giữ thành văn bản ngắn.
    def _stderr_text(self) -> str:
        with self._stderr_lock:
            return bytes(self._stderr_buffer).decode("utf-8", errors="replace").strip()

    # Gửi một frame điều khiển tới Go bridge.
    def _send_bridge_frame(self, payload: dict):
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("SSH3 mux bridge is not running")
        raw = json.dumps(payload, separators=(",", ":")).encode("ascii") + b"\n"
        with self._write_lock:
            self.process.stdin.write(raw)
            self.process.stdin.flush()

    # Gửi dữ liệu vào đúng QUIC stream theo role.
    def _send(self, role: str, data: bytes):
        self._send_bridge_frame({
            "type": "data",
            "role": role,
            "data_b64": base64.b64encode(data).decode("ascii"),
        })

    # Phân phối sự kiện bridge và dữ liệu stream.
    def _dispatch(self, frame: dict, observed_wall_ns: int = 0, observed_mono_ns: int = 0):
        if frame.get("type") == "data":
            role = str(frame.get("role", ""))
            stream = self.streams.get(role)
            if stream is None:
                return
            try:
                data = base64.b64decode(frame.get("data_b64", ""), validate=True)
            except (ValueError, TypeError):
                stream.put_error("SSH3 bridge returned invalid Base64 data")
                return
            stream.put_data(
                data, int(frame.get("data_type", 0)),
                observed_wall_ns, observed_mono_ns,
            )
            return
        if frame.get("type") == "exit":
            role = str(frame.get("role", ""))
            stream = self.streams.get(role)
            if stream is not None:
                status = frame.get("exit_status")
                stream.put_exit(
                    int(status) if status is not None else None,
                    str(frame.get("message", "")),
                )
            return
        if frame.get("type") == "error" and frame.get("role") in self.streams:
            self.streams[str(frame["role"])].put_error(
                str(frame.get("message", "SSH3 stream error"))
            )
            return
        if frame.get("type") in {"stream_open", "connection_ready", "error"}:
            self.control_events.put(frame)

    # Mở một connection SSH3 và các QUIC stream thật.
    def open(self, timeout: float) -> dict[str, RawStream]:
        binary = self.cfg.get("SSH3_MUX_BIN", "../stream_mux/bin/ssh3-mux-stdio")
        command = [binary]
        if cfg_bool(self.cfg, "SSH3_INSECURE", "0"):
            command.append("-insecure")
        identity = self.cfg.get("SSH3_PRIVKEY", "").strip()
        if identity:
            command += ["-privkey", os.path.expanduser(identity)]
        command += shlex.split(self.cfg.get("SSH3_EXTRA_ARGS", ""))
        pty_specs = [spec for spec in self.specs if spec.allocate_pty]
        if pty_specs:
            pty_shapes = {
                (spec.terminal_type, spec.columns, spec.rows) for spec in pty_specs
            }
            if len(pty_shapes) != 1:
                raise ValueError("all SSH3 PTY streams must use the same terminal shape")
        for spec in self.specs:
            role = spec.role
            command += [
                "-mux-pty-stream" if spec.allocate_pty else "-mux-stream",
                f"{role}={spec.remote_command}",
            ]
            self.streams[role] = RawStream(
                role, lambda data, current=role: self._send(current, data)
            )
        target = f"{self.cfg['SERVER_USER']}@{self.cfg['SERVER_HOST']}"
        port = self.cfg.get("SSH3_PORT", "443").strip()
        path = self.cfg.get("SSH3_PATH", "/ssh3-term").strip()
        command.append(f"{target}:{port}{path}")

        process_env = dict(os.environ)
        if pty_specs:
            shape = pty_specs[0]
            process_env.update({
                "SSH3_MUX_TERM": shape.terminal_type,
                "SSH3_MUX_COLUMNS": str(shape.columns),
                "SSH3_MUX_ROWS": str(shape.rows),
            })
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True, env=process_env,
        )
        self.stderr_reader = PipeReader(
            self.process.stderr, self._capture_stderr, "ssh3-mux-stderr"
        )
        self.stderr_reader.start()
        self.reader = JSONPipeReader(
            self.process.stdout, self._dispatch, "ssh3-mux-stdout"
        )
        self.reader.start()

        deadline = time.monotonic() + timeout
        opened: dict[str, dict] = {}
        connection_ready = False
        while time.monotonic() < deadline and (
            len(opened) < len(self.roles) or not connection_ready
        ):
            try:
                event = self.control_events.get(
                    timeout=min(0.1, max(0.01, deadline - time.monotonic()))
                )
            except queue.Empty:
                if self.process.poll() is not None:
                    break
                continue
            if event.get("type") == "error":
                raise RuntimeError(event.get("message", "SSH3 mux bridge error"))
            if event.get("type") == "stream_open":
                opened[str(event["role"])] = event
            elif event.get("type") == "connection_ready":
                connection_ready = True

        if set(opened) != set(self.roles) or not connection_ready:
            missing = sorted(set(self.roles) - set(opened))
            if self.process.poll() is not None and self.stderr_reader is not None:
                self.stderr_reader.thread.join(timeout=0.5)
            raise RuntimeError(
                f"SSH3 streams not opened: missing={missing}; "
                f"bridge_exit={self.process.poll()}; stderr={self._stderr_text()!r}"
            )
        for role, event in opened.items():
            self.streams[role].stream_id = str(event["stream_id"])
            self.streams[role].conversation_id = str(event["conversation_stream_id"])
        conversation_ids = sorted({
            stream.conversation_id for stream in self.streams.values()
        })
        stream_ids = {
            role: self.streams[role].stream_id for role in self.roles
        }
        unique_ids = len(set(stream_ids.values())) == len(self.roles)
        udp_sockets = socket_rows(process_tree(self.process.pid), "udp")
        valid = unique_ids and len(conversation_ids) == 1 and len(udp_sockets) == 1
        self.audit = ConnectionAudit(
            "ssh3", valid, self.process.pid, len(udp_sockets), len(self.roles),
            stream_ids, conversation_ids,
            {role: "quic_bidirectional_stream" for role in self.roles},
            "one patched process called Dial once; "
            f"udp_sockets={udp_sockets}; IDs reported by OpenChannel",
        )
        if not valid:
            raise RuntimeError(
                f"invalid SSH3 stream audit: {self.audit}; "
                f"stderr={self._stderr_text()!r}"
            )
        return self.streams

    # Đóng các QUIC stream và tiến trình bridge.
    def close(self) -> None:
        if self.process is None:
            return
        process = self.process
        try:
            self._send_bridge_frame({"type": "shutdown"})
            process.wait(timeout=3)
        except Exception:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        if process.stdin is not None:
            try:
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        self.process = None
