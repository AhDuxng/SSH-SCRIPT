"""Transport SSH với một ControlMaster và nhiều session channel."""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import threading

from .base import ConnectionAudit, MultiplexConnection, RawStream, StreamSpec
from .common import (
    PipeReader,
    process_tree,
    socket_rows,
    ssh_base,
)


class SSHConnection(MultiplexConnection):
    # Khởi tạo trạng thái ControlMaster của một trial.
    def __init__(self, cfg: dict, specs: list[StreamSpec], trial_tag: str):
        super().__init__("ssh", specs)
        self.cfg = cfg
        self.target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", trial_tag)
        self.control_path = f"/tmp/muxcm_{os.getpid()}_{safe}"[-100:]
        self.processes: dict[str, subprocess.Popen] = {}
        self.readers: list[PipeReader] = []
        self.watchers: list[threading.Thread] = []
        self.master_pid = 0

    # Gửi dữ liệu vào một SSH channel.
    def _send(self, role: str, data: bytes):
        process = self.processes[role]
        if process.stdin is None:
            raise RuntimeError(f"{role}: stdin unavailable")
        process.stdin.write(data)
        process.stdin.flush()

    # Đóng stdin của một SSH channel.
    def _close_input(self, role: str) -> None:
        process = self.processes[role]
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()

    # Chờ tiến trình và phát sự kiện thoát sau khi pipe đã cạn.
    def _watch_process(
        self, role: str, process: subprocess.Popen, readers: list[PipeReader]
    ) -> None:
        status = process.wait()
        for reader in readers:
            reader.thread.join(timeout=1)
        self.streams[role].put_exit(status)

    # Mở một ControlMaster và các session channel.
    def open(self, timeout: float) -> dict[str, RawStream]:
        master_command = [
            *ssh_base(self.cfg), "-MNf",
            "-o", "ControlMaster=yes", "-o", "ControlPersist=120s",
            "-o", f"ControlPath={self.control_path}", self.target,
        ]
        created = subprocess.run(
            master_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, check=False,
        )
        if created.returncode:
            raise RuntimeError(f"SSH ControlMaster failed: {created.stdout.strip()}")

        checked = subprocess.run(
            [*ssh_base(self.cfg), "-S", self.control_path, "-O", "check", self.target],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=False,
        )
        match = re.search(r"pid=(\d+)", checked.stdout)
        self.master_pid = int(match.group(1)) if match else 0
        if checked.returncode or not self.master_pid:
            raise RuntimeError(
                f"SSH ControlMaster verification failed: {checked.stdout.strip()}"
            )

        for spec in self.specs:
            role = spec.role
            stream = RawStream(
                role,
                lambda data, current=role: self._send(current, data),
                lambda current=role: self._close_input(current),
            )
            self.streams[role] = stream
            remote_command = spec.remote_command
            if spec.allocate_pty:
                remote_command = (
                    f"stty cols {spec.columns} rows {spec.rows}; "
                    f"export TERM={shlex.quote(spec.terminal_type)}; "
                    f"exec {spec.remote_command}"
                )
            command = [
                *ssh_base(self.cfg), "-tt" if spec.allocate_pty else "-T",
                "-o", f"ControlPath={self.control_path}",
                self.target, remote_command,
            ]
            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, start_new_session=True,
                env={**os.environ, "TERM": spec.terminal_type},
            )
            self.processes[role] = process
            stdout_reader = PipeReader(
                process.stdout,
                lambda data, current=role: self.streams[current].put_data(data, 0),
                f"ssh-{role}-stdout",
            )
            stderr_reader = PipeReader(
                process.stderr,
                lambda data, current=role: self.streams[current].put_data(data, 1),
                f"ssh-{role}-stderr",
            )
            channel_readers = [stdout_reader, stderr_reader]
            for reader in channel_readers:
                reader.start()
                self.readers.append(reader)
            watcher = threading.Thread(
                target=self._watch_process,
                args=(role, process, channel_readers),
                name=f"ssh-{role}-wait",
                daemon=True,
            )
            watcher.start()
            self.watchers.append(watcher)

        master_sockets = socket_rows([self.master_pid], "tcp")
        launcher_sockets = socket_rows(
            [
                pid
                for process in self.processes.values()
                for pid in process_tree(process.pid)
            ],
            "tcp",
        )
        valid = len(master_sockets) == 1 and not launcher_sockets
        self.audit = ConnectionAudit(
            "ssh", valid, self.master_pid, len(master_sockets), len(self.roles),
            {role: "" for role in self.roles}, [],
            {role: "ssh_session_channel" for role in self.roles},
            "OpenSSH ControlMaster checked; "
            f"master_sockets={master_sockets}; launcher_sockets={launcher_sockets}",
        )
        if not valid:
            raise RuntimeError(f"invalid OpenSSH multiplex audit: {self.audit}")
        return self.streams

    # Đóng các channel rồi đóng ControlMaster.
    def close(self) -> None:
        for stream in self.streams.values():
            try:
                stream.close_input()
            except Exception:
                pass
        for process in self.processes.values():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        if self.master_pid:
            subprocess.run(
                [
                    *ssh_base(self.cfg), "-S", self.control_path,
                    "-O", "exit", self.target,
                ],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
            )
            self.master_pid = 0
