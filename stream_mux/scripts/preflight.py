#!/usr/bin/env python3
"""Kiểm tra một cặp client/server đã sẵn sàng chạy W1–W4 hay chưa."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))

from stream_mux import StreamSpec, open_multiplex_connection  # noqa: E402
from stream_mux.connection.common import ssh_base  # noqa: E402


# Công cụ mà máy đích phải có, theo từng workload.
REMOTE_TOOLS = {
    "w1": ("bash", "df", "free", "ps", "uptime"),
    "w2": ("bash", "sed", "sha256sum", "mkfifo"),
    "w3": ("bash", "vim", "nano", "tmux"),
    "w4": ("bash", "vim", "nano", "tmux", "od", "fold", "sha256sum"),
}

# Module Python mà máy chạy benchmark phải có.
LOCAL_MODULES = {
    "w1": ("pexpect",),
    "w2": ("pexpect", "matplotlib", "numpy"),
    "w3": ("pexpect", "matplotlib", "numpy"),
    "w4": ("pexpect", "matplotlib", "numpy"),
}

PATCH_FILES = (
    "patches/ssh3_mux_stdio.patch",
    "patches/ssh3_jwt_clock_skew.patch",
    "patches/ssh3_qlog.patch",
    "patches/quic_go_cubic.patch",
    "scripts/prepare_quic_cc.sh",
)

# Thuật toán chống tắc nghẽn mặc định, phải khớp scripts/patch_hash.sh.
DEFAULT_SSH3_CC = "reno"

# Phiên bản Go tối thiểu để build ssh3; khớp scripts/go_toolchain.sh.
GO_MIN_MINOR = 21


# Thuật toán chống tắc nghẽn mà cấu hình yêu cầu.
def configured_cc(cfg: dict) -> str:
    value = (cfg.get("SSH3_CC") or DEFAULT_SSH3_CC).strip().lower()
    if value not in ("reno", "cubic"):
        raise ValueError(f"SSH3_CC phải là reno hoặc cubic, nhận được: {value!r}")
    return value


class Report:
    """Gom kết quả kiểm tra và quyết định mã thoát."""

    # Khởi tạo báo cáo rỗng.
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []
        self.failed = 0

    # Ghi một dòng kết quả và đếm số mục hỏng.
    def add(self, level: str, label: str, detail: str = "") -> None:
        self.rows.append((level, label, detail))
        if level == "FAIL":
            self.failed += 1

    # Ghi PASS hoặc FAIL theo một điều kiện.
    def check(self, ok: bool, label: str, detail: str = "", soft: bool = False) -> bool:
        self.add("PASS" if ok else ("WARN" if soft else "FAIL"), label, detail)
        return ok

    # Mở một nhóm kiểm tra mới.
    def section(self, title: str) -> None:
        self.rows.append(("SECTION", title, ""))

    # In toàn bộ báo cáo theo thứ tự đã ghi.
    def render(self) -> None:
        icons = {"PASS": "  ok  ", "FAIL": " FAIL ", "WARN": " warn ", "INFO": " info "}
        for level, label, detail in self.rows:
            if level == "SECTION":
                print(f"\n── {label} " + "─" * max(0, 60 - len(label)))
                continue
            line = f"[{icons[level]}] {label}"
            if detail:
                line += f"\n{' ' * 9}{detail}"
            print(line)


# Đọc tệp KEY=VALUE và cho phép môi trường ghi đè.
def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]
    return values


# Chạy một lệnh cục bộ và trả về (mã thoát, output đã gộp).
def run(command: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        checked = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        return 127, f"{command[0]}: not found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    return checked.returncode, checked.stdout.strip()


# Nhận diện workload từ đường dẫn cấu hình.
def detect_workload(config_path: Path) -> str:
    name = config_path.resolve().parent.name
    for key in REMOTE_TOOLS:
        if name.startswith(key):
            return key
    raise ValueError(
        f"không nhận ra workload từ {name!r}. Chép tệp env vào thư mục workload "
        "trước (ví dụ cp .../w2-mux-tt.env w2-mux-tt/config.env), hoặc truyền "
        "--workload w1|w2|w3|w4"
    )


# Tái tạo `shasum -a 256 <patch> | shasum -a 256`. Giá trị gắn với đường dẫn
# tuyệt đối nên chỉ so sánh được trong phạm vi một máy.
def patch_hash(cc: str) -> str:
    import hashlib

    listing = "".join(
        f"{hashlib.sha256((REPO_DIR / 'stream_mux' / relative).read_bytes()).hexdigest()}  "
        f"{REPO_DIR / 'stream_mux' / relative}\n"
        for relative in PATCH_FILES
    )
    # Băm phủ cả lựa chọn thuật toán, đúng như scripts/patch_hash.sh.
    listing += f"cc_algorithm={cc}\n"
    return hashlib.sha256(listing.encode()).hexdigest()


# Đọc thuật toán congestion control từ metadata module của binary.
def binary_congestion(path: Path) -> str:
    if not path.exists():
        return "unknown"
    try:
        data = path.read_bytes()
    except OSError:
        return "unknown"
    if b"quic-go-cubic" in data:
        return "cubic"
    if b"quic-go/quic-go" in data:
        return "reno"
    return "unknown"


# Kiểm tra mọi thứ thuộc về máy chạy benchmark.
def check_client(report: Report, cfg: dict, workload: str, project: Path) -> None:
    report.section("Client")
    report.add("INFO", f"python {sys.version.split()[0]} tại {sys.executable}")

    python_bin = cfg.get("PYTHON_BIN", "python3")
    candidate = project / python_bin if not os.path.isabs(python_bin) else Path(python_bin)
    if candidate.exists():
        report.check(True, f"PYTHON_BIN {python_bin}")
        interpreter = str(candidate)
    else:
        report.check(
            False, f"PYTHON_BIN {python_bin} không tồn tại",
            f"tạo bằng: cd {project} && python3 -m venv .venv && "
            ".venv/bin/pip install -r requirements.txt",
        )
        interpreter = sys.executable

    for module in LOCAL_MODULES[workload]:
        code, _ = run([interpreter, "-c", f"import {module}"])
        # Chỉ pexpect bắt buộc để đo; matplotlib/numpy chỉ cần khi vẽ hình.
        optional = module in {"matplotlib", "numpy"}
        report.check(
            code == 0, f"module {module}" + (" (chỉ cần để vẽ hình)" if optional else ""),
            "" if code == 0 else
            f"cài bằng: {interpreter} -m pip install -r requirements.txt",
            soft=optional,
        )

    tools = [cfg.get("SSH_BIN", "ssh")]
    if "mosh" in cfg.get("PROTOCOLS", ""):
        tools.append(cfg.get("MOSH_BIN", "mosh"))
    for tool in tools:
        found = run(["sh", "-c", f"command -v {shlex.quote(tool)}"])[0] == 0
        report.check(found, f"lệnh {tool}")

    has_lsof = run(["sh", "-c", "command -v lsof"])[0] == 0
    has_ss = run(["sh", "-c", "command -v ss"])[0] == 0
    report.check(
        has_lsof or has_ss, "lsof hoặc ss (dùng để audit socket)",
        "" if (has_lsof or has_ss) else "sudo apt-get install -y lsof iproute2",
    )


# Kiểm tra binary SSH3 phía client: cờ mux, mã băm patch và thuật toán.
def check_ssh3_binary(report: Report, cfg: dict, project: Path) -> None:
    if "ssh3" not in cfg.get("PROTOCOLS", ""):
        return
    report.section("SSH3 client binary")
    wanted_cc = configured_cc(cfg)
    raw = cfg.get("SSH3_MUX_BIN", "../stream_mux/bin/ssh3-mux-stdio")
    path = Path(raw) if os.path.isabs(raw) else (project / raw)
    path = path.resolve()

    if not report.check(path.exists(), f"binary {path}",
                        "" if path.exists() else
                        "build bằng: bash stream_mux/scripts/build_ssh3_mux.sh"):
        return
    report.check(os.access(path, os.X_OK), "binary có quyền thực thi")

    code, output = run([str(path), "-h"], timeout=15)
    report.check("-mux-stream" in output, "binary có cờ -mux-stream")

    expected = patch_hash(wanted_cc)
    stamp = path.with_suffix(path.suffix + ".patch.sha256")
    actual = stamp.read_text().strip() if stamp.exists() else ""
    fresh = actual == expected
    report.check(
        fresh, "patch hash khớp mã nguồn hiện tại",
        "" if fresh else (
            f"đã build={actual[:16] or 'không có'}… cần={expected[:16]}…; "
            "run_wN.sh sẽ tự build lại"
        ),
        soft=True,
    )

    algorithm = binary_congestion(path)
    report.check(
        algorithm == wanted_cc,
        f"congestion control = {algorithm} (cấu hình: {wanted_cc})",
        "" if algorithm == wanted_cc else
        f"build lại: SSH3_CC={wanted_cc} bash stream_mux/scripts/build_ssh3_mux.sh",
    )

    info = path.with_suffix(path.suffix + ".build-info")
    if info.exists():
        report.add("INFO", "build-info", info.read_text().strip().replace("\n", "; "))


# Gom toàn bộ kiểm tra phía máy đích vào đúng một phiên SSH.
def remote_probe_script(cfg: dict, workload: str) -> str:
    tools = list(REMOTE_TOOLS[workload])
    if "mosh" in cfg.get("PROTOCOLS", ""):
        tools.append("mosh-server")
    if workload == "w2" and cfg.get("W2_MOSH_LAYOUT", "tmux") == "tmux":
        tools.append(cfg.get("TMUX_BIN", "tmux"))

    lines = ["set -u"]
    for tool in dict.fromkeys(tools):
        quoted = shlex.quote(tool)
        lines.append(
            f'command -v {quoted} >/dev/null 2>&1 '
            f'&& echo "TOOL {tool} ok" || echo "TOOL {tool} missing"'
        )
    lines += [
        'echo "OS $(uname -sr)"',
        'test -w /tmp && echo "TMP ok" || echo "TMP missing"',
    ]
    if "ssh3" in cfg.get("PROTOCOLS", ""):
        # /proc/<pid>/exe chỉ đọc được nếu cùng chủ sở hữu; server thường chạy
        # dưới systemd nên phải thử lần lượt nhiều nguồn.
        lines += [
            'pid=$(pgrep -x ssh3-server 2>/dev/null | head -1)',
            '[ -z "$pid" ] && pid=$(pgrep -f "[s]sh3-server" 2>/dev/null | head -1)',
            'if [ -z "$pid" ]; then echo "SSH3 stopped"; else',
            '  exe=$(readlink -f /proc/$pid/exe 2>/dev/null || true)',
            '  if [ -z "$exe" ] || [ ! -r "$exe" ]; then',
            r'    exe=$(ps -p "$pid" -o args= 2>/dev/null | awk "{print \$1}")',
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
            '  echo "SSH3 running ${exe:-?}"',
            '  if [ -n "$exe" ] && [ -r "$exe" ]; then',
            '    if grep -a -q quic-go-cubic "$exe" 2>/dev/null; then',
            '      echo "SSH3CC cubic $exe"',
            '    else echo "SSH3CC reno $exe"; fi',
            '    info="${exe}.build-info"',
            '    [ -r "$info" ] && echo "SSH3INFO $(paste -sd ";" "$info")"',
            '  else echo "SSH3CC unreadable ${exe:-khong-xac-dinh}"; fi',
            'fi',
            # Go chỉ cần khi phải build lại trên server; bản mới thường nằm
            # ngoài PATH của phiên SSH không tương tác.
            'best=""; best_minor=-1',
            'for g in $(command -v go) /usr/local/go/bin/go '
            '"$HOME/go/bin/go" /usr/lib/go-1.*/bin/go; do',
            '  [ -x "$g" ] || continue',
            '  m=$("$g" env GOVERSION 2>/dev/null | '
            r'sed -n "s/^go1\.\([0-9]*\).*/\1/p")',
            '  [ -n "$m" ] || continue',
            '  if [ "$m" -gt "$best_minor" ]; then best_minor=$m; best=$g; fi',
            'done',
            'echo "GO ${best_minor} ${best:-khong-co}"',
        ]
    lines.append("exit 0")
    return "\n".join(lines)


# Chạy kiểm tra trên máy đích qua một phiên SSH duy nhất.
def check_server(report: Report, cfg: dict, workload: str) -> None:
    wanted_cc = configured_cc(cfg)
    target = f"{cfg['SERVER_USER']}@{cfg['SERVER_HOST']}"
    report.section(f"Server {target}")
    command = [*ssh_base(cfg), "-o", "ConnectTimeout=10", target,
               remote_probe_script(cfg, workload)]
    code, output = run(command, timeout=45)
    lines = output.splitlines()
    reached = any(
        line.startswith(("TOOL ", "OS ", "TMP ", "SSH3 ")) for line in lines
    )
    if not report.check(
        reached, f"đăng nhập SSH tới {target}",
        "" if reached else output[-300:],
    ):
        return
    if code != 0:
        report.add(
            "INFO", f"lượt kiểm tra từ xa kết thúc với mã {code}",
            "không ảnh hưởng kết quả bên dưới",
        )

    for line in lines:
        parts = line.split(maxsplit=2)
        if not parts:
            continue
        tag = parts[0]
        if tag == "TOOL":
            report.check(
                parts[2] == "ok", f"lệnh {parts[1]} trên máy đích",
                "" if parts[2] == "ok" else f"sudo apt-get install -y {parts[1]}",
            )
        elif tag == "OS":
            report.add("INFO", f"hệ điều hành {parts[1]} {parts[2] if len(parts) > 2 else ''}")
        elif tag == "TMP":
            report.check(parts[1] == "ok", "/tmp ghi được")
        elif tag == "SSH3":
            running = parts[1] == "running"
            report.check(
                running, "ssh3-server đang chạy",
                "" if running else "sudo systemctl start ssh3-server",
            )
            if running and len(parts) > 2:
                report.add("INFO", f"binary đang phục vụ: {parts[2]}")
        elif tag == "SSH3CC":
            algorithm = parts[1]
            binary = parts[2] if len(parts) > 2 else ""
            rebuild = (
                f"trên server: SSH3_CC={wanted_cc} "
                "bash stream_mux/scripts/build_ssh3_server.sh && "
                f"sudo install -m 0755 stream_mux/bin/ssh3-server-{wanted_cc} "
                "/usr/local/bin/ssh3-server && sudo systemctl restart ssh3-server"
            )
            if algorithm == "unreadable":
                report.add(
                    "WARN",
                    "không đọc được binary của ssh3-server để xác định "
                    "congestion control",
                    f"binary={binary or 'không xác định'}; kiểm tra thủ công trên "
                    "server: sudo readlink -f /proc/$(pgrep -x ssh3-server)/exe "
                    "rồi sudo grep -ac quic-go-cubic <đường-dẫn> "
                    "(khác 0 nghĩa là CUBIC, bằng 0 nghĩa là Reno)",
                )
            else:
                report.check(
                    algorithm == wanted_cc,
                    f"congestion control của server = {algorithm} "
                    f"(cấu hình: {wanted_cc})"
                    + (f" ({binary})" if binary else ""),
                    "" if algorithm == wanted_cc else rebuild,
                )
        elif tag == "GO":
            minor = int(parts[1]) if parts[1].lstrip("-").isdigit() else -1
            binary = parts[2] if len(parts) > 2 else ""
            report.check(
                minor >= GO_MIN_MINOR,
                f"Go trên server: go1.{minor} ({binary})" if minor >= 0
                else "Go trên server: không tìm thấy",
                "" if minor >= GO_MIN_MINOR else
                f"cần >= 1.{GO_MIN_MINOR} để build lại ssh3-server; "
                "build_ssh3_server.sh tự chọn bản mới nhất tìm được",
                soft=True,
            )
        elif tag == "SSH3INFO":
            report.add("INFO", "build-info của server", parts[1].strip(";"))


# Mở thật một connection cho từng giao thức và kiểm tra audit.
def check_transports(report: Report, cfg: dict) -> None:
    report.section("Mở connection thật")
    protocols = [
        item.strip() for item in cfg.get("PROTOCOLS", "").split(",") if item.strip()
    ]
    marker = b"__PREFLIGHT_OK__"
    for protocol in protocols:
        # Giữ phiên sống vài giây: Mosh chỉ được coi là hợp lệ khi audit thấy
        # đúng một UDP socket, mà điều đó cần tiến trình còn chạy.
        specs = [StreamSpec(
            "probe", "printf '%s\\n' __PREFLIGHT_OK__; sleep 3",
            allocate_pty=(protocol == "mosh"),
        )]
        connection = None
        try:
            connection = open_multiplex_connection(cfg, protocol, specs, "preflight")
            streams = connection.open(25.0)
            audit = connection.audit
            report.check(
                audit.valid,
                f"{protocol}: connection hợp lệ "
                f"(socket={audit.socket_count}, stream={audit.stream_count})",
                "" if audit.valid else audit.note[:300],
            )
            seen = bytearray()
            deadline = time.monotonic() + 12.0
            while marker not in seen and time.monotonic() < deadline:
                try:
                    event = streams["probe"].receive(timeout=1.0)
                except TimeoutError:
                    continue
                if event.kind == "data":
                    seen.extend(event.data)
                elif event.kind == "error":
                    break
            report.check(
                marker in seen, f"{protocol}: chạy được lệnh từ xa",
                "" if marker in seen else "không nhận được output của lệnh thử",
            )
        except Exception as exc:
            report.check(False, f"{protocol}: không mở được connection", repr(exc)[:300])
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


# Đo RTT tham khảo tới máy đích.
def check_latency(report: Report, cfg: dict) -> None:
    report.section("Mạng")
    host = cfg["SERVER_HOST"]
    code, output = run(["ping", "-c", "5", host], timeout=25)
    summary = [
        line.strip() for line in output.splitlines()
        if "min/avg" in line or "packets transmitted" in line
    ]
    if code != 0:
        report.add(
            "INFO", f"ping {host} không trả lời",
            summary[0] if summary else "ICMP có thể bị chặn; không ảnh hưởng phép đo",
        )
        return
    report.add("INFO", f"RTT tới {host}", summary[-1] if summary else "")


# Đọc tham số và chạy toàn bộ lượt kiểm tra.
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm tra client và server trước khi chạy W1–W4.",
    )
    parser.add_argument("config", type=Path, help="ví dụ w2-mux-tt/config.env")
    parser.add_argument("--workload", choices=sorted(REMOTE_TOOLS))
    parser.add_argument(
        "--skip-connect", action="store_true",
        help="bỏ qua bước mở connection thật cho từng giao thức",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"không có {args.config}", file=sys.stderr)
        return 2
    cfg = load_env(args.config)
    workload = args.workload or detect_workload(args.config)
    project = args.config.resolve().parent

    if not cfg.get("SERVER_HOST") or cfg["SERVER_HOST"] == "CHANGE_ME":
        print("SERVER_HOST chưa được đặt trong config", file=sys.stderr)
        return 2

    report = Report()
    report.add(
        "INFO",
        f"{workload.upper()} — {cfg['SERVER_USER']}@{cfg['SERVER_HOST']} "
        f"— giao thức: {cfg.get('PROTOCOLS', '')}",
    )
    check_client(report, cfg, workload, project)
    check_ssh3_binary(report, cfg, project)
    check_server(report, cfg, workload)
    check_latency(report, cfg)
    if not args.skip_connect:
        os.chdir(project)
        check_transports(report, cfg)

    report.render()
    print()
    if report.failed:
        print(f"{report.failed} mục chưa đạt — xử lý hết trước khi đo.")
        return 1
    print("Mọi mục đã đạt. Chạy smoke test trước khi đo chính thức.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
