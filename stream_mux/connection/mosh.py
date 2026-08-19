"""Transport Mosh với đúng một terminal session."""

from __future__ import annotations

import errno
import os
import shlex
import threading
import time

from .base import ConnectionAudit, MultiplexConnection, RawStream, StreamSpec
from .common import process_tree, socket_rows, ssh_base


class MoshConnection(MultiplexConnection):
    # Khởi tạo trạng thái một Mosh session.
    def __init__(self, cfg: dict, specs: list[StreamSpec], trial_tag: str):
        if len(specs) != 1:
            raise ValueError("Mosh supports exactly one terminal stream")
        super().__init__("mosh", specs)
        self.cfg = cfg
        self.trial_tag = trial_tag
        self.child = None
        self.reader_thread: threading.Thread | None = None
        self._write_lock = threading.Lock()
        self._closing = threading.Event()

    # Gửi byte vào terminal Mosh.
    def _send(self, data: bytes):
        if self.child is None:
            raise RuntimeError("Mosh session is not running")
        with self._write_lock:
            self.child.send(data)

    # Đọc byte thô từ terminal Mosh.
    def _read(self):
        import pexpect

        role = self.specs[0].role
        child = self.child
        if child is None:
            return
        while not self._closing.is_set():
            try:
                chunk = child.read_nonblocking(size=4096, timeout=0.1)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF:
                break
            except OSError as exc:
                if self._closing.is_set() and exc.errno == errno.EBADF:
                    break
                self.streams[role].put_error(repr(exc))
                return
            self.streams[role].put_data(chunk)
        self.streams[role].put_exit(child.exitstatus)

    # Mở một terminal session Mosh.
    def open(self, timeout: float) -> dict[str, RawStream]:
        import pexpect

        self._closing.clear()
        spec = self.specs[0]
        target = f"{self.cfg['SERVER_USER']}@{self.cfg['SERVER_HOST']}"
        command = [
            self.cfg.get("MOSH_BIN", "mosh"),
            f"--ssh={shlex.join(ssh_base(self.cfg))}",
        ]
        predict = self.cfg.get("MOSH_PREDICT", "always").strip()
        if predict:
            command += ["--predict", predict]
        command += shlex.split(self.cfg.get("MOSH_EXTRA_ARGS", ""))
        command += [target, "--", "/bin/bash", "-lc", spec.remote_command]
        self.child = pexpect.spawn(
            command[0], command[1:], encoding=None,
            timeout=timeout, env={**os.environ, "TERM": spec.terminal_type},
        )
        self.child.delaybeforesend = 0
        self.child.setwinsize(spec.rows, spec.columns)
        stream = RawStream(spec.role, self._send)
        self.streams[spec.role] = stream
        self.reader_thread = threading.Thread(
            target=self._read, name="mosh-mux", daemon=True
        )
        self.reader_thread.start()
        deadline = time.monotonic() + timeout
        udp_sockets = []
        while time.monotonic() < deadline:
            udp_sockets = socket_rows(process_tree(self.child.pid), "udp")
            if len(udp_sockets) == 1:
                break
            if not self.child.isalive():
                break
            time.sleep(0.05)
        valid = len(udp_sockets) == 1
        self.audit = ConnectionAudit(
            "mosh", valid, self.child.pid, len(udp_sockets), 1,
            {spec.role: ""}, [],
            {spec.role: "terminal_session"},
            "Mosh has one terminal session and no independent transport streams; "
            f"udp_sockets={udp_sockets}",
        )
        if not valid:
            raise RuntimeError(f"invalid Mosh connection audit: {self.audit}")
        return self.streams

    # Đóng terminal session Mosh.
    def close(self) -> None:
        if self.child is None:
            return
        child = self.child
        self._closing.set()
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=0.5)
        try:
            child.sendeof()
        except Exception:
            pass
        deadline = time.monotonic() + 3.0
        while child.isalive() and time.monotonic() < deadline:
            time.sleep(0.05)
        if child.isalive():
            child.close(force=True)
        if self.reader_thread is not None:
            self.reader_thread.join(timeout=0.5)
            self.reader_thread = None
        self.child = None
