import csv
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pexpect

from config import bool_cfg, q, qjoin, split_args
from constants import CHANNEL_COUNTER_FIELDS
from terminal_io import drain_pending_output
from terminal_screen import TerminalTracker


@dataclass
class BackgroundChannel:
    role: str
    process: subprocess.Popen
    ready_marker: bytes
    ready: threading.Event = field(default_factory=threading.Event)
    bytes_received: int = 0
    started_at: float = field(default_factory=time.monotonic)
    ended_at: float = 0.0
    note: str = ""
    thread: threading.Thread = None


# Quản lý việc mở đóng kết nối, channel nền và target tương tác.
class ProtocolRunner:
    # Khởi tạo trạng thái chạy và các tệp audit cho một giao thức.
    def __init__(self, cfg: dict, protocol: str, trial: dict):
        self.cfg = cfg
        self.protocol = protocol
        self.trial = trial
        self.run_id = trial["run_id"]
        self.block_id = trial["block_id"]
        self.trial_order = trial["trial_order"]
        self.trial_id = trial["trial_id"]
        self.trial_tag = trial["trial_tag"]
        self.user = cfg["SERVER_USER"]
        self.host = cfg["SERVER_HOST"]
        self.port = cfg.get("SERVER_PORT", "").strip()
        self.remote = cfg.get("REMOTE_WORKLOAD", "/tmp/w3_remote_workloads.sh")
        self.log_dir = Path(cfg.get("LOG_DIR", "artifacts/logs"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir = Path(cfg.get("RESULT_DIR", "artifacts/results"))
        self.result_dir.mkdir(parents=True, exist_ok=True)
        port_id = self.port or "default"
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]", "_", self.trial_tag)
        self.cm_path = f"/tmp/w3cm_{os.getpid()}_{port_id}_{safe_tag}"[-100:]
        self.audit_path = self.result_dir / "ssh3_audit.csv"
        self.audit_fields = [
            "run_id", "block_id", "trial_order", "trial_id",
            "ts", "protocol", "target", "profile", "role", "channel_name",
            "launcher_pid", "process_pids", "udp_sockets", "cmd", "log_path",
            "multiplex_hint", "note",
        ]
        self.stream_audit_path = self.result_dir / "ssh3_stream_audit.csv"
        self.stream_audit_fields = [
            "run_id", "block_id", "trial_order", "trial_id",
            "ts", "protocol", "target", "profile", "log_path",
            "stream_ids", "stream_roles", "conversation_stream_ids",
            "byte_roles", "duration_roles_ms", "ready_roles", "matched_lines", "note",
        ]
        self.connection_audit_path = self.result_dir / "connection_audit.csv"
        self.connection_audit_fields = [
            "run_id", "block_id", "trial_order", "trial_id", "ts",
            "protocol", "target", "profile", "valid", "connection_pid",
            "socket_count", "sockets", "check_output", "note",
        ]
        self.counter_path = self.result_dir / "channel_counters.csv"
        self.ssh3_stats_path = self.log_dir / f"ssh3_{self.trial_tag}_stream_stats.log"
        self.edit_file = ""
        if not self.audit_path.exists():
            with open(self.audit_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.audit_fields).writeheader()
        if not self.stream_audit_path.exists():
            with open(self.stream_audit_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.stream_audit_fields).writeheader()
        if not self.connection_audit_path.exists():
            with open(self.connection_audit_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=self.connection_audit_fields).writeheader()
        if not self.counter_path.exists():
            with open(self.counter_path, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CHANNEL_COUNTER_FIELDS).writeheader()
        self.tracker = None
        self.debug_mirror = None
        self.ssh_master_pid = 0

    # Tạo tham số cổng cho lệnh SSH.
    def ssh_port_args(self) -> list:
        return ["-p", self.port] if self.port else []

    # Tạo các tham số Mosh từ cấu hình.
    def mosh_args(self) -> list:
        args = []
        predict = self.cfg.get("MOSH_PREDICT", "").strip()
        if predict:
            args.extend(["--predict", predict])
        if self.port:
            args.append(f"--ssh=ssh -p {self.port}")
        args.extend(split_args(self.cfg.get("MOSH_EXTRA_ARGS", "")))
        return args

    # Tạo tham số SSH3 và đường dẫn keylog cho một phiên thử.
    def ssh3_extra_args(self, target: str, profile: str, role: str) -> list:
        args = []
        if bool_cfg(self.cfg, "SSH3_INSECURE", "0"):
            args.append("-insecure")
        privkey = self.cfg.get("SSH3_PRIVKEY", "").strip()
        if privkey:
            args.extend(["-privkey", os.path.expanduser(privkey)])
        if bool_cfg(self.cfg, "SSH3_VERBOSE", "1"):
            args.append("-v")
        keylog_template = self.cfg.get("SSH3_KEYLOG_TEMPLATE", "").strip()
        if keylog_template:
            keylog_path = keylog_template.format(
                protocol=self.protocol,
                target=target,
                profile=profile,
                role=role,
                pid=os.getpid(),
                ts=int(time.time()),
                log_dir=self.log_dir,
                trial_id=self.trial_id,
                trial_tag=self.trial_tag,
            )
            Path(keylog_path).parent.mkdir(parents=True, exist_ok=True)
            args.extend(["-keylog", keylog_path])
        args.extend(split_args(self.cfg.get("SSH3_EXTRA_ARGS", "")))
        return args

    # Điền các giá trị thực tế vào mẫu lệnh SSH3.
    def format_ssh3_template(
        self,
        template: str,
        target: str,
        profile: str,
        role: str,
        mux_args: str = "",
    ) -> str:
        extra_args = shlex.join(self.ssh3_extra_args(target, profile, role))
        return template.format(
            user=self.user,
            host=self.host,
            port=self.port,
            ssh3_bin=self.cfg.get("SSH3_BIN", "ssh3"),
            ssh3_port=self.cfg.get("SSH3_PORT", "443"),
            ssh3_extra_args=extra_args,
            ssh3_mux_args=mux_args,
            ssh3_path=self.cfg.get("SSH3_PATH", ""),
        )

    # Thu thập đệ quy các PID con của một tiến trình.
    def child_pids(self, pid: int) -> list:
        seen = set()
        pending = [pid]
        out = []
        while pending:
            parent = pending.pop()
            try:
                res = subprocess.run(
                    ["pgrep", "-P", str(parent)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                continue
            for raw in res.stdout.split():
                try:
                    child_pid = int(raw)
                except ValueError:
                    continue
                if child_pid in seen:
                    continue
                seen.add(child_pid)
                out.append(child_pid)
                pending.append(child_pid)
        return out

    # Trả về PID launcher cùng toàn bộ PID con.
    def process_pids(self, launcher_pid: int) -> list:
        pids = [launcher_pid]
        pids.extend(self.child_pids(launcher_pid))
        return pids

    # Liệt kê socket UDP thuộc các PID để kiểm chứng một kết nối QUIC.
    def udp_sockets_for_pids(self, pids: list) -> str:
        rows = []
        for pid in pids:
            try:
                res = subprocess.run(
                    ["lsof", "-nP", "-a", "-p", str(pid), "-iUDP"],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                continue
            for line in res.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9:
                    rows.append(f"pid={pid} {' '.join(parts[8:])}")
        if rows:
            return " | ".join(rows)

        try:
            res = subprocess.run(
                ["ss", "-H", "-u", "-p", "-n"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            return ""
        wanted = {f"pid={pid}," for pid in pids}
        for line in res.stdout.splitlines():
            if any(pid_text in line for pid_text in wanted):
                rows.append(line.strip())
        return " | ".join(rows)

    # Liệt kê TCP socket ESTABLISHED của SSH ControlMaster.
    def tcp_sockets_for_pid(self, pid: int) -> list:
        try:
            res = subprocess.run(
                ["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:ESTABLISHED"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception:
            return []
        rows = []
        for line in res.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 9:
                rows.append(" ".join(parts[8:]))
        return rows

    # Ghi kết quả xác minh connection của một trial.
    def write_connection_audit_row(
        self, target: str, profile: str, valid: bool, pid: int = 0,
        sockets=None, check_output: str = "", note: str = "",
    ):
        sockets = sockets or []
        row = {
            "run_id": self.run_id,
            "block_id": self.block_id,
            "trial_order": self.trial_order,
            "trial_id": self.trial_id,
            "ts": time.time(),
            "protocol": self.protocol,
            "target": target,
            "profile": profile,
            "valid": int(bool(valid)),
            "connection_pid": pid or "",
            "socket_count": len(sockets),
            "sockets": " | ".join(sockets),
            "check_output": check_output.strip(),
            "note": note,
        }
        with open(self.connection_audit_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.connection_audit_fields).writerow(row)

    # Ghi bằng chứng tiến trình và socket của launcher SSH3.
    def write_audit(
        self,
        protocol: str,
        target: str,
        profile: str,
        role: str,
        channel_name: str,
        launcher_pid: int,
        cmd,
        log_path: str = "",
        note: str = "",
    ):
        if protocol != "ssh3":
            return
        pids = self.process_pids(launcher_pid)
        sockets = self.udp_sockets_for_pids(pids)
        hint = "unknown"
        if protocol == "ssh3":
            unique_udp = {item.strip() for item in sockets.split("|") if item.strip()}
            if len(unique_udp) > 1:
                hint = "separate_udp_sockets_likely_separate_quic_connections"
            elif len(unique_udp) == 1:
                hint = "one_udp_socket_seen_for_this_launcher"
            else:
                hint = "no_udp_socket_observed"
        row = {
            "run_id": self.run_id,
            "block_id": self.block_id,
            "trial_order": self.trial_order,
            "trial_id": self.trial_id,
            "ts": time.time(),
            "protocol": protocol,
            "target": target,
            "profile": profile,
            "role": role,
            "channel_name": channel_name,
            "launcher_pid": launcher_pid,
            "process_pids": "+".join(str(p) for p in pids),
            "udp_sockets": sockets,
            "cmd": qjoin(cmd),
            "log_path": log_path,
            "multiplex_hint": hint,
            "note": note,
        }
        with open(self.audit_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.audit_fields).writerow(row)

    # Ghi kết luận số socket UDP quan sát được trong một phiên SSH3.
    def write_connection_summary(self, target: str, profile: str, child_pid: int):
        if self.protocol != "ssh3":
            return True
        launcher_pids = [child_pid]
        all_pids = []
        for pid in launcher_pids:
            all_pids.extend(self.process_pids(pid))
        seen_pids = []
        for pid in all_pids:
            if pid not in seen_pids:
                seen_pids.append(pid)
        sockets = self.udp_sockets_for_pids(seen_pids)
        unique_udp = {item.strip() for item in sockets.split("|") if item.strip()}
        if len(unique_udp) > 1:
            hint = "multiple_udp_sockets_observed_likely_not_single_quic_connection"
        elif len(unique_udp) == 1:
            hint = "single_udp_socket_observed_for_all_launchers"
        else:
            hint = "no_udp_socket_observed"
        row = {
            "run_id": self.run_id,
            "block_id": self.block_id,
            "trial_order": self.trial_order,
            "trial_id": self.trial_id,
            "ts": time.time(),
            "protocol": self.protocol,
            "target": target,
            "profile": profile,
            "role": "trial_summary",
            "channel_name": "all",
            "launcher_pid": child_pid,
            "process_pids": "+".join(str(p) for p in seen_pids),
            "udp_sockets": sockets,
            "cmd": "trial_summary",
            "log_path": "",
            "multiplex_hint": hint,
            "note": (
                f"launcher_pids={'+'.join(str(p) for p in launcher_pids)} "
                f"unique_udp_socket_rows={len(unique_udp)}"
            ),
        }
        with open(self.audit_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.audit_fields).writerow(row)
        self.write_connection_audit_row(
            target,
            profile,
            hint == "single_udp_socket_observed_for_all_launchers",
            child_pid,
            sorted(unique_udp),
            check_output=hint,
            note=row["note"],
        )
        return hint == "single_udp_socket_observed_for_all_launchers"

    # Trích role, stream ID và conversation ID từ log debug SSH3.
    def scan_stream_debug(self, log_path: Path) -> dict:
        max_bytes = int(self.cfg.get("SSH3_STREAM_SCAN_BYTES", "200000"))
        stream_ids = set()
        stream_roles = {}
        conversation_stream_ids = set()
        byte_roles = {}
        duration_roles = {}
        ready_roles = set()
        matches = []
        note = ""
        marker_re = re.compile(
            r"W3_SSH3_STREAM role=([^\s]+) stream_id=(\d+) conversation_stream_id=(\d+)"
        )
        ready_re = re.compile(r"W3_SSH3_READY role=([^\s]+) stream_id=(\d+)")
        bytes_re = re.compile(
            r"W3_SSH3_BYTES role=([^\s]+) stream_id=(\d+) bytes=(\d+) "
            r"ready=(?:true|false) duration_ms=(\d+)"
        )
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                try:
                    size = log_path.stat().st_size
                    if size > max_bytes:
                        f.seek(size - max_bytes)
                except Exception:
                    pass
                for line in f:
                    marker = marker_re.search(line)
                    if marker:
                        role, stream_id, conversation_stream_id = marker.groups()
                        stream_ids.add(stream_id)
                        stream_roles[role] = stream_id
                        conversation_stream_ids.add(conversation_stream_id)
                    ready = ready_re.search(line)
                    if ready:
                        ready_roles.add(ready.group(1))
                    byte_marker = bytes_re.search(line)
                    if byte_marker:
                        byte_roles[byte_marker.group(1)] = byte_marker.group(3)
                        duration_roles[byte_marker.group(1)] = byte_marker.group(4)
                    if (marker or ready or byte_marker) and len(matches) < 40:
                        matches.append(line.strip()[:240])
        except Exception as exc:
            note = repr(exc)
        try:
            if self.ssh3_stats_path.exists():
                with open(self.ssh3_stats_path, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        byte_marker = bytes_re.search(line)
                        if not byte_marker:
                            continue
                        role = byte_marker.group(1)
                        byte_roles[role] = byte_marker.group(3)
                        duration_roles[role] = byte_marker.group(4)
                        if len(matches) < 40:
                            matches.append(line.strip()[:240])
        except Exception as exc:
            note = f"{note}; stats={exc!r}" if note else f"stats={exc!r}"
        return {
            "stream_ids": "+".join(sorted(stream_ids, key=lambda x: int(x) if x.isdigit() else x)),
            "stream_roles": "+".join(f"{role}:{stream_roles[role]}" for role in sorted(stream_roles)),
            "conversation_stream_ids": "+".join(sorted(conversation_stream_ids, key=int)),
            "byte_roles": "+".join(f"{role}:{byte_roles[role]}" for role in sorted(byte_roles)),
            "duration_roles_ms": "+".join(
                f"{role}:{duration_roles[role]}" for role in sorted(duration_roles)
            ),
            "ready_roles": "+".join(sorted(ready_roles)),
            "matched_lines": " || ".join(matches),
            "note": note,
        }

    # Ghi các stream tìm thấy trong log vào CSV audit.
    def write_stream_audit(self, target: str, profile: str):
        if self.protocol != "ssh3":
            return
        log_path = self.log_dir / f"ssh3_{self.trial_tag}_interactive_debug.log"
        if not log_path.exists():
            return
        with open(self.stream_audit_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=self.stream_audit_fields)
            parsed = self.scan_stream_debug(log_path)
            writer.writerow({
                "run_id": self.run_id,
                "block_id": self.block_id,
                "trial_order": self.trial_order,
                "trial_id": self.trial_id,
                "ts": time.time(),
                "protocol": self.protocol,
                "target": target,
                "profile": profile,
                "log_path": str(log_path),
                "stream_ids": parsed["stream_ids"],
                "stream_roles": parsed["stream_roles"],
                "conversation_stream_ids": parsed["conversation_stream_ids"],
                "byte_roles": parsed["byte_roles"],
                "duration_roles_ms": parsed["duration_roles_ms"],
                "ready_roles": parsed["ready_roles"],
                "matched_lines": parsed["matched_lines"],
                "note": parsed["note"],
            })
        byte_roles = {}
        for item in parsed["byte_roles"].split("+"):
            if item:
                role, value = item.rsplit(":", 1)
                byte_roles[role] = int(value)
        stream_roles = {}
        for item in parsed["stream_roles"].split("+"):
            if item:
                role, value = item.rsplit(":", 1)
                stream_roles[role] = value
        duration_roles = {}
        for item in parsed["duration_roles_ms"].split("+"):
            if item:
                role, value = item.rsplit(":", 1)
                duration_roles[role] = int(value) / 1000.0
        ready_roles = set(parsed["ready_roles"].split("+")) if parsed["ready_roles"] else set()
        for role, byte_count in sorted(byte_roles.items()):
            self.write_channel_counter(
                target, profile, role, byte_count, ready=role in ready_roles,
                duration=duration_roles.get(role, 0.0), stream_id=stream_roles.get(role, ""),
            )

    # Ghi bộ đếm byte của một background channel/stream.
    def write_channel_counter(
        self, target: str, profile: str, role: str, byte_count: int,
        ready: bool, duration: float = 0.0, stream_id: str = "", note: str = "",
    ):
        average = (byte_count / duration) if duration > 0 else ""
        row = {
            "run_id": self.run_id,
            "block_id": self.block_id,
            "trial_order": self.trial_order,
            "trial_id": self.trial_id,
            "protocol": self.protocol,
            "target": target,
            "profile": profile,
            "role": role,
            "stream_id": stream_id,
            "bytes_received": byte_count,
            "duration_s": f"{duration:.6f}" if duration > 0 else "",
            "average_bytes_per_s": f"{average:.3f}" if average != "" else "",
            "ready": int(bool(ready)),
            "note": note,
        }
        with open(self.counter_path, "a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CHANNEL_COUNTER_FIELDS).writerow(row)

    # Mở một SSH ControlMaster dùng chung cho mọi channel SSH.
    def start_master_if_needed(self, target: str, profile: str):
        if self.protocol != "ssh":
            return True
        ssh = self.cfg.get("SSH_BIN", "ssh")
        # Một kết nối TCP SSHv2, rồi nhiều session channel qua ControlMaster.
        cmd = [
            ssh, "-MNf",
            "-o", "ControlMaster=yes",
            "-o", "ControlPersist=120s",
            "-o", f"ControlPath={self.cm_path}",
            *self.ssh_port_args(),
            f"{self.user}@{self.host}",
        ]
        created = subprocess.run(
            cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
        if created.returncode != 0:
            self.write_connection_audit_row(
                target, profile, False, check_output=created.stdout,
                note="ControlMaster creation failed",
            )
            raise RuntimeError(f"SSH ControlMaster creation failed: {created.stdout.strip()}")
        time.sleep(0.3)
        check_cmd = [
            ssh, "-S", self.cm_path, "-O", "check", *self.ssh_port_args(),
            f"{self.user}@{self.host}",
        ]
        checked = subprocess.run(
            check_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False
        )
        pid_match = re.search(r"pid=(\d+)", checked.stdout)
        pid = int(pid_match.group(1)) if pid_match else 0
        self.ssh_master_pid = pid
        sockets = self.tcp_sockets_for_pid(pid) if pid else []
        valid = checked.returncode == 0 and pid > 0 and len(sockets) == 1
        self.write_connection_audit_row(
            target, profile, valid, pid, sockets, checked.stdout,
            "" if valid else "expected ssh -O check and exactly one established TCP socket",
        )
        if not valid:
            raise RuntimeError(
                f"SSH master verification failed: rc={checked.returncode} pid={pid} "
                f"tcp_sockets={len(sockets)} output={checked.stdout.strip()}"
            )
        return True

    # Đảm bảo các launcher channel không tự mở thêm TCP connection.
    def verify_ssh_multiplex(
        self, channels: list, target: str, profile: str, interactive_pid: int = 0,
    ):
        master_sockets = self.tcp_sockets_for_pid(self.ssh_master_pid)
        extra = []
        launcher_pids = [channel.process.pid for channel in channels]
        if interactive_pid:
            launcher_pids.append(interactive_pid)
        for launcher_pid in launcher_pids:
            for pid in self.process_pids(launcher_pid):
                for socket in self.tcp_sockets_for_pid(pid):
                    extra.append(f"pid={pid} {socket}")
        valid = len(master_sockets) == 1 and not extra
        self.write_connection_audit_row(
            target,
            profile,
            valid,
            self.ssh_master_pid,
            [f"master={item}" for item in master_sockets] + extra,
            check_output="post-channel socket audit",
            note="" if valid else "background launcher opened an extra TCP socket",
        )
        if not valid:
            raise RuntimeError(
                f"SSH multiplex audit failed: master_sockets={len(master_sockets)} "
                f"extra_channel_sockets={len(extra)}"
            )
        return True

    # Đóng SSH ControlMaster sau khi hoàn tất profile.
    def stop_master_if_needed(self):
        if self.protocol != "ssh":
            return
        ssh = self.cfg.get("SSH_BIN", "ssh")
        cmd = [
            ssh,
            "-S",
            self.cm_path,
            "-O",
            "exit",
            *self.ssh_port_args(),
            f"{self.user}@{self.host}",
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)

    # Tạo lệnh mở terminal tương tác cho giao thức đã chọn.
    def workload_command(self, bg_name: str, target: str, profile: str, ready_file: str = ""):
        mode = "output" if bg_name in ("output", "output_heavy") else bg_name
        if bg_name == "output_heavy":
            rate = int(self.cfg.get("HEAVY_OUTPUT_RATE_BPS", "1048576"))
        elif bg_name == "output":
            rate = int(self.cfg.get("NORMAL_OUTPUT_RATE_BPS", "102400"))
        else:
            rate = 0
        return (
            f"W3_TARGET={q(target)} W3_PROTOCOL={q(self.protocol)} W3_ROLE={q(bg_name)} "
            f"W3_PROFILE={q(profile)} bash {q(self.remote)} {q(mode)} {rate} "
            f"{q(self.trial_tag)} {q(ready_file)}"
        )

    # Tạo lệnh mở terminal tương tác cho giao thức đã chọn.
    def interactive_cmd(self, target: str = "session", profile: str = "interactive", bgs: list = None):
        if self.protocol == "ssh":
            ssh = self.cfg.get("SSH_BIN", "ssh")
            return [
                ssh, "-tt",
                "-o", f"ControlPath={self.cm_path}",
                *self.ssh_port_args(),
                f"{self.user}@{self.host}",
                "bash --noprofile --norc -i",
            ], False

        if self.protocol == "ssh3":
            tmpl = self.cfg.get("SSH3_INTERACTIVE_TEMPLATE", "ssh3 {ssh3_extra_args} {user}@{host}{ssh3_path}")
            mux_args = []
            for bg in bgs or []:
                remote_cmd = self.workload_command(bg, target, profile)
                mux_args.extend(["-mux-background", f"{bg}={remote_cmd}"])
            cmd = self.format_ssh3_template(
                tmpl, target, profile, "interactive", mux_args=shlex.join(mux_args)
            )
            return cmd, True

        if self.protocol == "mosh":
            mosh = self.cfg.get("MOSH_BIN", "mosh")
            return [
                mosh,
                *self.mosh_args(),
                f"{self.user}@{self.host}",
                "--",
                "bash",
                "--noprofile",
                "--norc",
                "-i",
            ], False

        raise ValueError(f"unknown protocol: {self.protocol}")

    # Tạo lệnh mở một background channel qua SSH ControlMaster.
    def bg_cmd(self, bg_name: str, target: str, profile: str):
        remote_cmd = self.workload_command(bg_name, target, profile)

        if self.protocol == "ssh":
            ssh = self.cfg.get("SSH_BIN", "ssh")
            return [
                ssh, "-T",
                "-o", f"ControlPath={self.cm_path}",
                *self.ssh_port_args(),
                f"{self.user}@{self.host}",
                remote_cmd,
            ], False

        raise ValueError(f"{self.protocol} does not use separate background launcher processes")

    # Khởi chạy terminal tương tác và chờ phiên sẵn sàng.
    def spawn_interactive(self, target: str, profile: str, bgs: list):
        cmd, shell = self.interactive_cmd(target, profile, bgs)
        log_path = ""
        spawn_env = {
            **os.environ,
            "TERM": self.cfg.get("TERMINAL_TYPE", "xterm-256color"),
        }
        if self.protocol == "ssh3":
            self.ssh3_stats_path.unlink(missing_ok=True)
            spawn_env["W3_SSH3_STATS_FILE"] = str(self.ssh3_stats_path.resolve())
        # Vim có thể phát ra byte terminal lẻ không hợp lệ UTF-8.
        # Chỉ thay thế các byte đó giúp probe ASCII vẫn chính xác và đo được.
        spawn_options = {
            "encoding": "utf-8",
            "codec_errors": "replace",
            "timeout": 5,
            # Editor cần mô tả terminal thật để repaint từng phím.
            # Máy chạy automation có thể cung cấp TERM=dumb.
            "env": spawn_env,
        }
        if shell:
            child = pexpect.spawn("/bin/bash", ["-lc", cmd], **spawn_options)
        else:
            child = pexpect.spawn(cmd[0], cmd[1:], **spawn_options)
        child.delaybeforesend = 0
        mirror = None
        close_mirror = False
        if self.protocol == "ssh3" and bool_cfg(self.cfg, "SSH3_CAPTURE_DEBUG", "1"):
            log_path = str(self.log_dir / f"ssh3_{self.trial_tag}_interactive_debug.log")
            mirror = open(log_path, "w", encoding="utf-8", errors="ignore")
            close_mirror = True
        elif self.protocol == "mosh" and bool_cfg(self.cfg, "MOSH_CAPTURE_DEBUG", "1"):
            log_path = str(self.log_dir / f"mosh_{self.trial_tag}_interactive_debug.log")
            mirror = open(log_path, "w", encoding="utf-8", errors="ignore")
            close_mirror = True
        elif self.cfg.get("SHOW_TERMINAL_OUTPUT", "0") == "1":
            mirror = sys.stdout
        self.tracker = TerminalTracker(mirror=mirror, close_mirror=close_mirror)
        child.logfile_read = self.tracker
        if self.protocol == "mosh":
            # Startup được xác minh bằng ready-file có retry ở bước kế tiếp.
            time.sleep(0.25)
            return child
        if self.protocol == "ssh3":
            time.sleep(0.25)
        self.write_audit(self.protocol, target, profile, "interactive", "c0", child.pid, cmd, log_path)
        # Marker này chứng minh interactive shell đã nhận và chạy lệnh.
        marker = f"W3_INTERACTIVE_READY_{self.trial_tag}"
        child.sendline(f"printf '\\n{marker}\\n'")
        child.expect_exact(marker, timeout=float(self.cfg.get("CHANNEL_READY_TIMEOUT", "10")))
        return child

    # Đọc và loại output SSH trong bộ nhớ, đồng thời đếm byte và bắt READY.
    def drain_background_channel(self, channel: BackgroundChannel):
        pending = b""
        try:
            while True:
                chunk = os.read(channel.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                channel.bytes_received += len(chunk)
                pending += chunk
                if channel.ready_marker in pending:
                    channel.ready.set()
                pending = pending[-4096:]
        except Exception as exc:
            channel.note = repr(exc)
        finally:
            channel.ended_at = time.monotonic()

    # Mở các background channel SSH qua ControlMaster và dùng memory sink.
    def start_background_channels(self, target: str, profile: str, bgs: list):
        channels = []
        failures = 0
        for bg in bgs:
            cmd, shell = self.bg_cmd(bg, target, profile)
            p = subprocess.Popen(
                cmd,
                shell=shell,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                preexec_fn=os.setsid,
            )
            marker = f"W3_CHANNEL_READY role={bg} trial={self.trial_tag}".encode()
            channel = BackgroundChannel(bg, p, marker)
            channel.thread = threading.Thread(
                target=self.drain_background_channel,
                args=(channel,),
                name=f"w3-{self.trial_tag}-{bg}",
                daemon=True,
            )
            channel.thread.start()
            time.sleep(0.05)
            if p.poll() is not None:
                failures += 1
            channels.append(channel)
        return channels, failures

    # Chờ tất cả workload SSH phát marker READY.
    def wait_background_ready(self, channels: list, timeout: float):
        deadline = time.monotonic() + timeout
        for channel in channels:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not channel.ready.wait(remaining):
                raise TimeoutError(f"background channel did not become ready: {channel.role}")
        return True

    # Chờ Go SSH3 báo đã nhận READY trên từng QUIC stream.
    def wait_ssh3_background_ready(self, child, bgs: list, timeout: float):
        if not bgs:
            return True
        deadline = time.monotonic() + timeout
        expected = set(bgs)
        ready_re = re.compile(r"W3_SSH3_READY role=([^\s]+)")
        while time.monotonic() < deadline:
            ready = set(ready_re.findall(self.tracker.recent_text()))
            if expected <= ready:
                return True
            try:
                child.read_nonblocking(size=4096, timeout=0.05)
            except pexpect.TIMEOUT:
                pass
            except pexpect.EOF:
                break
        raise TimeoutError(f"SSH3 streams not ready: expected={sorted(expected)}")

    # Tạo tải nền bên trong terminal Mosh vốn không hỗ trợ multi-channel.
    def start_mosh_background_inside_terminal(self, child, target: str, profile: str, bgs: list):
        # Mosh không có multi-channel; phần này chỉ tạo tải nền terminal để làm baseline.
        for bg in bgs:
            ready_file = self.mosh_ready_file(bg)
            remote_cmd = self.workload_command(bg, target, profile, ready_file)
            if bg in ("log", "ping", "sysmon"):
                cmd = f"{remote_cmd} >/tmp/w3_mosh_{self.trial_tag}_{bg}.log 2>&1 &"
            else:
                cmd = f"{remote_cmd} &"
            child.sendline(cmd)
            time.sleep(0.05)

    # Tạo tên ready-file riêng cho từng role Mosh.
    def mosh_ready_file(self, role: str) -> str:
        return f"/tmp/w3_ready_{self.trial_tag}_{role}"

    # Chạy một lệnh SSH ngắn chỉ dùng cho handshake trước warm-up Mosh.
    def mosh_control_command(self, remote_command: str, timeout: float = 5.0):
        ssh = self.cfg.get("SSH_BIN", "ssh")
        try:
            return subprocess.run(
                [
                    ssh,
                    "-o", "ConnectTimeout=3",
                    *self.ssh_port_args(),
                    f"{self.user}@{self.host}",
                    remote_command,
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(exc.cmd, 124, stdout="SSH readiness check timed out")

    # Đọc trạng thái ready-file và trả về role đã sẵn sàng.
    def mosh_ready_status(self, roles: list):
        commands = []
        for role in roles:
            ready_file = self.mosh_ready_file(role)
            commands.append(
                f"if [ -f {q(ready_file)} ]; then printf '{role}=READY\\n'; "
                f"else printf '{role}=MISSING\\n'; fi"
            )
        checked = self.mosh_control_command("; ".join(commands))
        ready = {
            line.split("=", 1)[0]
            for line in checked.stdout.splitlines()
            if line.endswith("=READY")
        }
        return ready, checked

    # Đọc output startup để Mosh không bị đầy PTY và phát hiện EOF sớm.
    def drain_mosh_startup(self, child, duration: float = 0.15):
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            try:
                child.read_nonblocking(size=4096, timeout=0.05)
            except pexpect.TIMEOUT:
                continue
            except pexpect.EOF as exc:
                recent = self.tracker.recent_text()[-1000:] if self.tracker else ""
                raise RuntimeError(f"Mosh exited during startup: {recent!r}") from exc

    # Ghi marker điều khiển cục bộ vào log startup mà không đưa vào terminal parser.
    def write_mosh_control_log(self, message: str):
        if self.tracker is None or self.tracker.mirror is None:
            return
        self.tracker.mirror.write(f"\n[W3_MOSH_CONTROL] {message}\n")
        self.tracker.mirror.flush()

    # Retry handshake đến khi interactive shell Mosh thực sự chạy lệnh.
    def wait_mosh_interactive_ready(self, child, timeout: float):
        role = "interactive"
        ready_file = self.mosh_ready_file(role)
        ready_prefix = f"/tmp/w3_ready_{self.trial_tag}_"
        cleared = self.mosh_control_command(f"rm -f {q(ready_prefix)}*")
        if cleared.returncode != 0:
            raise RuntimeError(f"cannot clear Mosh ready files: {cleared.stdout.strip()}")

        deadline = time.monotonic() + timeout
        attempts = 0
        last_output = ""
        while time.monotonic() < deadline:
            attempts += 1
            child.sendline(f": > {q(ready_file)}")
            self.drain_mosh_startup(child, 0.2)
            ready, checked = self.mosh_ready_status([role])
            last_output = checked.stdout.strip()
            if role in ready:
                self.write_mosh_control_log(
                    f"interactive READY attempts={attempts} status={last_output!r}"
                )
                return True
            time.sleep(0.25)

        log_path = self.log_dir / f"mosh_{self.trial_tag}_interactive_debug.log"
        self.write_mosh_control_log(
            f"interactive FAILED attempts={attempts} status={last_output!r}"
        )
        raise TimeoutError(
            f"Mosh interactive not ready after {attempts} attempts; "
            f"status={last_output!r}; log={log_path}"
        )

    # Chờ các workload Mosh tạo ready-file sau khi interactive đã sẵn sàng.
    def wait_mosh_background_ready(self, child, bgs: list, timeout: float):
        if not bgs:
            return True
        expected = set(bgs)
        deadline = time.monotonic() + timeout
        ready = set()
        last_output = ""
        while time.monotonic() < deadline:
            self.drain_mosh_startup(child, 0.1)
            ready, checked = self.mosh_ready_status(bgs)
            last_output = checked.stdout.strip()
            if expected <= ready:
                self.write_mosh_control_log(
                    f"background READY roles={sorted(ready)!r} status={last_output!r}"
                )
                return True
            time.sleep(0.2)

        missing = sorted(expected - ready)
        log_path = self.log_dir / f"mosh_{self.trial_tag}_interactive_debug.log"
        self.write_mosh_control_log(
            f"background FAILED missing={missing!r} status={last_output!r}"
        )
        raise TimeoutError(
            f"Mosh background roles not ready: missing={missing}; "
            f"status={last_output!r}; log={log_path}"
        )

    # Tạo tên tệp tạm riêng cho editor của mỗi phiên.
    def target_file(self, target: str) -> str:
        return f"/tmp/w3latency_{self.trial_tag}_{target}.c"

    # Kiểm tra chương trình editor tồn tại trên máy đích.
    def require_remote_bin(self, child, bin_name: str, target: str):
        ok = f"W3{target.upper()}BINOK"
        missing = f"W3{target.upper()}BINMISS"
        suffix = f"{target.upper()}BIN"
        child.sendline(
            f"if command -v {q(bin_name)} >/dev/null 2>&1; then "
            f"printf W3; printf {suffix}OK; "
            f"else printf W3; printf {suffix}MISS; fi; printf '\\n'"
        )
        idx = child.expect([ok, missing, pexpect.TIMEOUT, pexpect.EOF], timeout=5)
        if idx == 1:
            raise RuntimeError(f"{target} binary not found: {bin_name}")
        if idx == 2:
            raise TimeoutError(f"timeout while checking {target} binary: {bin_name}")
        if idx == 3:
            raise EOFError(f"connection closed while checking {target} binary: {bin_name}")

    # Đưa shell, Vim hoặc Nano vào trạng thái soạn thảo sẵn sàng.
    def prepare_target(self, child, target: str):
        target = target.lower()
        delay = float(self.cfg.get("EDITOR_START_DELAY", "0.80"))
        self.edit_file = self.target_file(target)

        if target == "shell":
            # cat biến target shell thành nơi nhận text an toàn, để dấu câu C
            # không bị hiểu thành cú pháp shell.
            child.sendline(f"cat > {q(self.edit_file)}")
            time.sleep(0.4)
            drain_pending_output(child)
            return

        if target == "vim":
            vim_bin = self.cfg.get("VIM_BIN", "vim")
            if self.protocol != "mosh":
                self.require_remote_bin(child, vim_bin, target)
            tmpl = self.cfg.get("VIM_COMMAND", "{vim} -Nu NONE -n -i NONE {file}")
            child.sendline(tmpl.format(vim=q(vim_bin), file=q(self.edit_file)))
            time.sleep(delay)
            child.send("i")
            time.sleep(0.3)
            drain_pending_output(child)
            return

        if target == "nano":
            nano_bin = self.cfg.get("NANO_BIN", "nano")
            if self.protocol != "mosh":
                self.require_remote_bin(child, nano_bin, target)
            tmpl = self.cfg.get("NANO_COMMAND", "{nano} -w {file}")
            child.sendline(tmpl.format(nano=q(nano_bin), file=q(self.edit_file)))
            time.sleep(delay)
            drain_pending_output(child)
            return

        raise ValueError(f"unknown target: {target}")

    # Thoát sạch chế độ soạn thảo và xóa tệp tạm sau mỗi profile.
    def cleanup_target(self, child, target: str):
        target = target.lower()
        try:
            if target == "shell":
                # Flush dòng nhập canonical cuối của cat trước khi dừng.
                child.send("\n")
                time.sleep(0.1)
                child.sendcontrol("c")
            elif target == "vim":
                child.sendcontrol("c")
                time.sleep(0.1)
                child.sendline(":qa!")
            elif target == "nano":
                child.sendcontrol("x")
                time.sleep(0.2)
                child.send("n")
            time.sleep(0.3)
            if self.edit_file:
                child.sendline(f"rm -f {q(self.edit_file)}")
                time.sleep(0.1)
            child.sendline(f"rm -f /tmp/w3_ready_{q(self.trial_tag)}_* 2>/dev/null || true")
        except Exception:
            pass

    # Dừng background SSH, đợi drainer và ghi bộ đếm byte.
    def stop_procs(self, channels, target: str, profile: str):
        for channel in channels:
            p = channel.process
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except Exception:
                try:
                    p.terminate()
                except Exception:
                    pass
        for channel in channels:
            try:
                channel.process.wait(timeout=2)
            except Exception:
                pass
            if channel.thread is not None:
                channel.thread.join(timeout=2)
            ended = channel.ended_at or time.monotonic()
            duration = max(0.0, ended - channel.started_at)
            self.write_channel_counter(
                target,
                profile,
                channel.role,
                channel.bytes_received,
                channel.ready.is_set(),
                duration=duration,
                note=channel.note,
            )
