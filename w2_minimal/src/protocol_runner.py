import os
import shlex
import time

import pexpect

from config import bool_cfg
from terminal_io import INITIAL_PROMPT_RE, drain_pending_output, prompt_pattern


# Quản lý lệnh client và vòng đời một session SSH, SSH3 hoặc Mosh.
class ProtocolRunner:
    # Lưu cấu hình, giao thức và prompt riêng của trial.
    def __init__(self, cfg, protocol, prompt_marker):
        self.cfg = cfg
        self.protocol = protocol
        self.prompt_marker = prompt_marker

    # Tạo các tham số SSH dùng cho SSH trực tiếp và bootstrap của Mosh.
    def ssh_common(self, tty=False):
        command = [self.cfg.get("SSH_BIN", "ssh")]
        if tty:
            command.append("-tt")
        if bool_cfg(self.cfg, "SSH_STRICT_HOST_KEY_CHECKING", "0"):
            command.extend(["-o", "StrictHostKeyChecking=yes"])
        else:
            command.extend([
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
            ])
        if bool_cfg(self.cfg, "SSH_BATCH_MODE", "1"):
            command.extend(["-o", "BatchMode=yes"])
        identity = self.cfg.get("SSH_IDENTITY_FILE", "").strip()
        if identity:
            command.extend(["-i", os.path.expanduser(identity)])
        port = self.cfg.get("SERVER_PORT", "").strip()
        if port:
            command.extend(["-p", port])
        return command

    # Tạo lệnh mở session cho giao thức hiện tại.
    def session_command(self):
        target = f"{self.cfg['SERVER_USER']}@{self.cfg['SERVER_HOST']}"
        if self.protocol == "ssh":
            return [*self.ssh_common(tty=True), target]
        if self.protocol == "ssh3":
            command = [self.cfg.get("SSH3_BIN", "ssh3")]
            if bool_cfg(self.cfg, "SSH3_INSECURE", "0"):
                command.append("-insecure")
            identity = self.cfg.get("SSH3_PRIVKEY", "").strip()
            if identity:
                command.extend(["-privkey", os.path.expanduser(identity)])
            command.extend(shlex.split(self.cfg.get("SSH3_EXTRA_ARGS", "")))
            port = self.cfg.get("SSH3_PORT", "443").strip()
            path = self.cfg.get("SSH3_PATH", "/ssh3-term").strip()
            command.append(f"{target}:{port}{path}")
            return command
        if self.protocol == "mosh":
            bootstrap = shlex.join(self.ssh_common(tty=False))
            command = [self.cfg.get("MOSH_BIN", "mosh"), f"--ssh={bootstrap}"]
            predict = self.cfg.get("MOSH_PREDICT", "").strip()
            if predict:
                command.extend(["--predict", predict])
            command.extend(shlex.split(self.cfg.get("MOSH_EXTRA_ARGS", "")))
            command.extend([target, "--", "bash", "--noprofile", "--norc", "-i"])
            return command
        raise ValueError(f"unsupported protocol: {self.protocol}")

    # Chờ prompt riêng của trial, chịu được ANSI/redraw xen giữa ký tự.
    def expect_prompt(self, child, timeout=None):
        child.expect(
            prompt_pattern(self.prompt_marker),
            timeout=timeout if timeout is not None else float(self.cfg.get("SESSION_TIMEOUT", "20")),
        )

    # Mở session, đo đến prompt đầu tiên rồi cài prompt riêng và tắt input echo.
    def open(self):
        command = self.session_command()
        timeout = float(self.cfg.get("SESSION_TIMEOUT", "20"))
        retries = int(self.cfg.get("SESSION_RETRIES", "3"))
        last_error = None
        for attempt in range(1, retries + 1):
            child = None
            try:
                started = time.perf_counter_ns()
                child = pexpect.spawn(
                    command[0], command[1:], encoding="utf-8", codec_errors="replace",
                    timeout=timeout,
                    env={**os.environ, "TERM": self.cfg.get("TERMINAL_TYPE", "xterm-256color")},
                )
                child.delaybeforesend = 0
                child.maxread = int(self.cfg.get("MAX_READ_BYTES", "65536"))
                child.setwinsize(50, 200)
                child.expect(INITIAL_PROMPT_RE, timeout=timeout * attempt)
                setup_ms = (time.perf_counter_ns() - started) / 1_000_000.0
                child.sendline("stty -echo")
                child.expect(INITIAL_PROMPT_RE, timeout=timeout)
                child.sendline(f"export PS1={shlex.quote(self.prompt_marker)}; export COLUMNS=200")
                self.expect_prompt(child, timeout)
                drain_pending_output(child)
                return child, setup_ms
            except (pexpect.TIMEOUT, pexpect.EOF) as exc:
                last_error = exc
                if child is not None:
                    child.close(force=True)
                if attempt < retries:
                    time.sleep(2 * attempt)
            except Exception:
                if child is not None:
                    child.close(force=True)
                raise
        raise last_error or RuntimeError("session open failed")

    # Đóng session nhẹ nhàng và buộc đóng nếu client không thoát đúng hạn.
    def close(self, child):
        try:
            child.sendcontrol("c")
            child.sendline("exit")
            child.expect(pexpect.EOF, timeout=3)
        except Exception:
            child.close(force=True)
