"""Gửi lệnh xuất payload trực tiếp qua các stream byte dùng chung."""

from __future__ import annotations

import codecs
import shlex
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from stream_mux import ConnectionAudit, RawStream, StreamSpec
from stream_mux import open_multiplex_connection

from constants import PAYLOAD_BYTES, PAYLOAD_LINE_BYTES, PAYLOAD_LINES
from framing import MarkerDecoder, MarkerEvent, build_direct_line, request_token
from terminal_screen import TerminalScreen


MARKER_ROW_PREFIX = "__W2TT_"


@dataclass
class PendingTransfer:
    """Giữ trạng thái một lần truyền payload đang diễn ra."""

    token: str
    line_prefix: bytes
    expected_lines: tuple[bytes, ...] = ()
    expected_set: frozenset[bytes] = frozenset()
    matched: dict[bytes, int] = field(default_factory=dict)
    event: threading.Event = field(default_factory=threading.Event)
    output: bytearray = field(default_factory=bytearray)
    started: bool = False
    ambiguous: bool = False
    truncated: bool = False
    exit_code: int | None = None
    error: str = ""
    first_byte_wall_ns: int = 0
    first_byte_mono_ns: int = 0
    last_byte_wall_ns: int = 0
    last_byte_mono_ns: int = 0
    marker_wall_ns: int = 0
    marker_mono_ns: int = 0
    content_complete_wall_ns: int = 0
    content_complete_mono_ns: int = 0

    # Đếm số dòng payload deterministic khác nhau đã quan sát được.
    @property
    def unique_matched(self) -> int:
        return len(self.matched)

    # Đếm số lần một dòng đã quan sát bị lặp lại.
    @property
    def duplicate_count(self) -> int:
        return sum(count - 1 for count in self.matched.values())


class DirectCoordinator:
    """Điều phối nhiều lần truyền trên một stream vật lý."""

    # Khởi tạo bộ điều phối và giới hạn vùng giữ output.
    def __init__(
        self, raw_stream: RawStream, background: bool, max_capture_bytes: int,
        screen: TerminalScreen | None = None,
    ):
        self.raw_stream = raw_stream
        self.background = background
        self.max_capture_bytes = max_capture_bytes
        self.decoder = MarkerDecoder()
        self.pending: dict[str, PendingTransfer] = {}
        self.active: dict[str, PendingTransfer] = {}
        self.lock = threading.Lock()
        self.screen = screen
        self.screen_decoder = (
            codecs.getincrementaldecoder("utf-8")("replace") if screen else None
        )

    # Tạo mã dấu mốc ổn định từ mã yêu cầu.
    def _token(self, request_id: str) -> str:
        return request_token(request_id)

    # Đăng ký lần truyền trước khi gửi lệnh xuất payload.
    def _register(
        self, request_id: str, line_prefix: bytes,
        expected_lines: tuple[bytes, ...],
    ) -> PendingTransfer:
        token = self._token(request_id)
        transfer = PendingTransfer(
            token, line_prefix,
            expected_lines=expected_lines,
            expected_set=frozenset(expected_lines),
        )
        with self.lock:
            if token in self.pending:
                raise RuntimeError(f"trùng mã yêu cầu W2: {request_id}")
            self.pending[token] = transfer
        return transfer

    # Gỡ lần truyền khỏi các bảng trạng thái.
    def _discard(self, transfer: PendingTransfer) -> None:
        with self.lock:
            self.pending.pop(transfer.token, None)
            self.active.pop(transfer.token, None)

    # Ghi một dòng payload và thời điểm byte đầu/cuối.
    def _append_output(
        self, transfer: PendingTransfer, data: bytes,
        wall_ns: int, mono_ns: int,
    ) -> None:
        if not transfer.first_byte_mono_ns:
            transfer.first_byte_wall_ns = wall_ns
            transfer.first_byte_mono_ns = mono_ns
        transfer.last_byte_wall_ns = wall_ns
        transfer.last_byte_mono_ns = mono_ns
        remaining = self.max_capture_bytes - len(transfer.output)
        if remaining <= 0:
            transfer.truncated = True
            return
        transfer.output.extend(data[:remaining])
        if len(data) > remaining:
            transfer.truncated = True

    # Ghi nhận một dòng payload deterministic đã quan sát được.
    def _note_line(
        self, transfer: PendingTransfer, line: bytes,
        wall_ns: int, mono_ns: int,
    ) -> None:
        if line not in transfer.expected_set:
            return
        transfer.matched[line] = transfer.matched.get(line, 0) + 1
        if (
            not transfer.content_complete_mono_ns
            and len(transfer.matched) == len(transfer.expected_lines)
        ):
            transfer.content_complete_wall_ns = wall_ns
            transfer.content_complete_mono_ns = mono_ns

    # Định tuyến một dòng theo tiền tố riêng của payload.
    def _route_output(self, data: bytes, wall_ns: int, mono_ns: int) -> None:
        active = list(self.active.values())
        if not active:
            return
        matches = [item for item in active if item.line_prefix in data]
        if len(matches) == 1:
            transfer = matches[0]
            marker = data.find(transfer.line_prefix)
            payload = data[marker:]
            self._append_output(transfer, payload, wall_ns, mono_ns)
            if self.screen is None:
                self._note_line(transfer, payload, wall_ns, mono_ns)
            return
        if len(active) == 1:
            self._append_output(active[0], data, wall_ns, mono_ns)
            if self.screen is None:
                self._note_line(active[0], data, wall_ns, mono_ns)
            return
        for transfer in active:
            transfer.ambiguous = True

    # Phân phối một sự kiện parser tới lần truyền tương ứng.
    def feed(
        self, event: MarkerEvent, wall_ns: int, mono_ns: int
    ) -> None:
        with self.lock:
            if event.kind == "start":
                transfer = self.pending.get(event.token)
                if transfer is not None:
                    transfer.started = True
                    self.active[event.token] = transfer
                return
            if event.kind == "done":
                transfer = self.pending.pop(event.token, None)
                self.active.pop(event.token, None)
                if transfer is not None:
                    transfer.exit_code = event.exit_code
                    transfer.marker_wall_ns = wall_ns
                    transfer.marker_mono_ns = mono_ns
                    transfer.event.set()
                return
            if event.kind == "output":
                self._route_output(event.data, wall_ns, mono_ns)

    # Đọc nội dung payload và dấu mốc từ viewport đã dựng lại.
    def _scan_screen(self, wall_ns: int, mono_ns: int) -> None:
        """Với Mosh, terminal update là nguồn sự thật thay cho byte thô.

        tmux chèn cursor sequence giữa các pane nên luồng byte thô không còn
        giữ nguyên ranh giới dòng. Viewport đã dựng lại thì có, và mỗi dòng
        mang token riêng của trial/role/sample nên không thể lẫn giữa các mẫu.
        """
        if self.screen is None:
            return
        with self.lock:
            waiting = list(self.pending.values())
        if not waiting:
            return
        prefixes = {MARKER_ROW_PREFIX}
        by_prefix: dict[str, PendingTransfer] = {}
        for transfer in waiting:
            prefix = transfer.line_prefix.decode("ascii", errors="replace")
            by_prefix[prefix] = transfer
            prefixes.add(prefix)

        for _row, prefix, line in self.screen.rows_with_prefixes(tuple(prefixes)):
            if prefix == MARKER_ROW_PREFIX:
                for event in MarkerDecoder().feed(line):
                    if event.kind in {"start", "done"}:
                        self.feed(event, wall_ns, mono_ns)
                continue
            transfer = by_prefix.get(prefix)
            if transfer is None:
                continue
            with self.lock:
                self._note_line(transfer, line, wall_ns, mono_ns)

    # Giải mã một khối byte vừa nhận.
    def feed_bytes(
        self, data: bytes, wall_ns: int | None = None, mono_ns: int | None = None
    ) -> None:
        wall_ns = time.time_ns() if wall_ns is None else wall_ns
        mono_ns = time.perf_counter_ns() if mono_ns is None else mono_ns
        if self.screen is not None and self.screen_decoder is not None:
            text = self.screen_decoder.decode(data)
            if text:
                self.screen.feed(text)
        for event in self.decoder.feed(data):
            self.feed(event, wall_ns, mono_ns)
        self._scan_screen(wall_ns, mono_ns)

    # Báo lỗi cho toàn bộ lần truyền đang chờ.
    def fail_all(self, message: str) -> None:
        with self.lock:
            transfers = list(self.pending.values())
            self.pending.clear()
            self.active.clear()
        for transfer in transfers:
            transfer.error = message
            transfer.event.set()

    # Gửi lệnh xuất payload thật và chờ dấu hoàn thành.
    def execute(
        self, request_id: str, command_text: str,
        line_prefix: bytes, timeout: float,
        expected_lines: tuple[bytes, ...] = (),
        wrap: "callable | None" = None,
    ) -> dict:
        transfer = self._register(request_id, line_prefix, expected_lines)
        line = build_direct_line(command_text, transfer.token, self.background)
        if wrap is not None:
            line = wrap(line)
        sent_wall_ns = time.time_ns()
        sent_mono_ns = time.perf_counter_ns()
        try:
            self.raw_stream.send(line)
        except Exception:
            self._discard(transfer)
            raise
        timed_out = not transfer.event.wait(timeout)
        if timed_out:
            # Viewport có thể vẫn đang được vẽ nốt khi dấu hoàn thành chưa tới;
            # quét thêm một lần để không bỏ sót nội dung đã thực sự hiển thị.
            self._scan_screen(time.time_ns(), time.perf_counter_ns())
            self._discard(transfer)
        if transfer.error:
            raise RuntimeError(transfer.error)
        first_latency = (
            (transfer.first_byte_mono_ns - sent_mono_ns) / 1_000_000.0
            if transfer.first_byte_mono_ns else None
        )
        completion_latency = (
            (transfer.last_byte_mono_ns - sent_mono_ns) / 1_000_000.0
            if transfer.last_byte_mono_ns else None
        )
        content_latency = (
            (transfer.content_complete_mono_ns - sent_mono_ns) / 1_000_000.0
            if transfer.content_complete_mono_ns else None
        )
        return {
            "stdout": bytes(transfer.output),
            "exit_code": transfer.exit_code,
            "send_time_ns": sent_wall_ns,
            "first_byte_time_ns": transfer.first_byte_wall_ns or None,
            "last_byte_time_ns": transfer.last_byte_wall_ns or None,
            "marker_time_ns": transfer.marker_wall_ns or None,
            "content_complete_time_ns": transfer.content_complete_wall_ns or None,
            "first_byte_latency_ms": first_latency,
            "completion_latency_ms": completion_latency,
            "content_complete_latency_ms": content_latency,
            "marker_latency_ms": (
                transfer.marker_mono_ns - sent_mono_ns
            ) / 1_000_000.0 if transfer.marker_mono_ns else None,
            "matched_lines": dict(transfer.matched),
            "unique_matched_lines": transfer.unique_matched,
            "duplicate_matched_lines": transfer.duplicate_count,
            "completion_marker_received": not timed_out,
            "timed_out": timed_out,
            "output_ambiguous": transfer.ambiguous,
            "output_truncated": transfer.truncated,
        }

    # Kiểm tra Bash từ xa đã sẵn sàng nhận lệnh.
    def probe(self, request_id: str, timeout: float) -> None:
        self.execute(request_id, ":", b"__W2TT_NO_OUTPUT__", timeout)


class DirectOutputStream:
    """Cung cấp một vai trò truyền output của W2."""

    # Gắn vai trò logic vào bộ điều phối vật lý.
    def __init__(
        self, role: str, coordinator: DirectCoordinator, wrap=None,
    ):
        self.role = role
        self.coordinator = coordinator
        self.raw_stream = coordinator.raw_stream
        self.stream_id = self.raw_stream.stream_id
        self.conversation_id = self.raw_stream.conversation_id
        self.request_lock = threading.Lock()
        self.wrap = wrap

    # Gửi tuần tự một lệnh output trên vai trò này.
    def execute(
        self, request_id: str, command: str,
        line_prefix: bytes, timeout: float,
        expected_lines: tuple[bytes, ...] = (),
    ) -> dict:
        with self.request_lock:
            return self.coordinator.execute(
                request_id, command, line_prefix, timeout,
                expected_lines, self.wrap,
            )


class DirectW2Connection:
    """Ghép workload W2 trực tiếp với transport dùng chung."""

    # Khởi tạo connection và các vai trò output.
    def __init__(self, cfg: dict, protocol: str, roles: list[str], trial_tag: str):
        self.cfg = cfg
        self.protocol = protocol
        self.roles = list(roles)
        self.trial_tag = trial_tag
        self.transport = None
        self.streams: dict[str, DirectOutputStream] = {}
        self.coordinators: list[DirectCoordinator] = []
        self.pumps: list[threading.Thread] = []
        self.closing = False
        self.tmux_session = ""
        self.tmux_socket = ""
        self.fifo_paths: dict[str, str] = {}
        self.audit = ConnectionAudit(protocol, False, 0, 0, 0, {}, [], {}, "chưa mở")

    # Cho biết Mosh có chạy mỗi vai trò trong một tmux pane riêng hay không.
    def _mosh_uses_panes(self) -> bool:
        if self.protocol != "mosh":
            return False
        if self.cfg.get("W2_MOSH_LAYOUT", "tmux").strip().lower() != "tmux":
            return False
        return len(self.roles) > 1

    # Kiểm tra viewport đủ chỗ cho toàn bộ batch của kịch bản.
    def _check_viewport(self, columns: int, rows: int, minimum_rows: int) -> None:
        if columns < PAYLOAD_LINE_BYTES or rows < minimum_rows:
            raise ValueError(
                "viewport Mosh không đủ giữ một batch W2: "
                f"cần columns>={PAYLOAD_LINE_BYTES}, rows>={minimum_rows}; "
                f"nhận columns={columns}, rows={rows}"
            )

    # Tạo lệnh dựng tmux với một pane điều khiển và một pane cho mỗi vai trò.
    def _tmux_remote_command(self, shell: str, columns: int, rows: int) -> str:
        session = f"w2_{self.trial_tag}"
        session = "".join(
            character if character.isalnum() else "_" for character in session
        )[:80]
        socket = f"{session[:40]}_socket"
        self.tmux_socket = socket
        tmux = (
            f"{shlex.quote(self.cfg.get('TMUX_BIN', 'tmux'))} "
            f"-L {shlex.quote(socket)} -f /dev/null"
        )
        self.tmux_session = session

        control = "stty -echo 2>/dev/null; while IFS= read -r __w2cmd; do eval \"$__w2cmd\"; done"
        commands = [
            f"{tmux} kill-server 2>/dev/null || true",
            (
                f"{tmux} new-session -d -x {columns} -y {rows} "
                f"-s {shlex.quote(session)} {shlex.quote(control)}"
            ),
            f"{tmux} set-option -t {shlex.quote(session)} status off",
            f"{tmux} set-option -t {shlex.quote(session)} base-index 0",
            f"{tmux} set-window-option -t {shlex.quote(session)} pane-base-index 0",
            f"{tmux} set-window-option -t {shlex.quote(session)} synchronize-panes off",
        ]
        for index, role in enumerate(self.roles):
            fifo = f"/tmp/w2f_{session}_{index}"
            self.fifo_paths[role] = fifo
            # Mở FIFO ở chế độ đọc-ghi để shell của pane không bao giờ thấy EOF
            # và không cần một tiến trình ghi thường trực.
            worker = (
                f"stty -echo 2>/dev/null; "
                f"exec {shell} <> {shlex.quote(fifo)}"
            )
            commands.append(f"rm -f {shlex.quote(fifo)}; mkfifo {shlex.quote(fifo)}")
            commands.append(
                f"{tmux} split-window -d -t {shlex.quote(session)} "
                f"{shlex.quote('/bin/bash -lc ' + shlex.quote(worker))}"
            )
        commands += [
            # Chia đều theo chiều dọc: mọi pane giữ nguyên chiều rộng terminal
            # nên một dòng payload 4095 ký tự không bao giờ bị xuống dòng, và
            # chiều cao mỗi pane tiên đoán được thay vì phụ thuộc resize.
            f"{tmux} select-layout -t {shlex.quote(session)} even-vertical",
            f"{tmux} select-pane -t {shlex.quote(session + ':0.0')}",
            f"exec {tmux} attach-session -t {shlex.quote(session)}",
        ]
        return "; ".join(commands)

    # Tạo Bash thường trực cho từng mô hình transport.
    def _stream_specs(self) -> list[StreamSpec]:
        shell = self.cfg.get(
            "DIRECT_SHELL_COMMAND", "/bin/bash --noprofile --norc"
        )
        if self.protocol != "mosh":
            return [StreamSpec(role, f"exec {shell}") for role in self.roles]

        columns = int(self.cfg.get("W2_MOSH_COLUMNS", "4096"))
        rows = int(self.cfg.get("W2_MOSH_ROWS", "144"))
        if self._mosh_uses_panes():
            # even-vertical chia đều cho pane điều khiển và các pane payload;
            # mỗi pane cần chỗ cho payload, hai dấu mốc và một dòng dự phòng,
            # cộng một dòng viền cho mỗi lần tách.
            per_pane = PAYLOAD_LINES + 3
            self._check_viewport(
                columns, rows, per_pane * (len(self.roles) + 1) + len(self.roles)
            )
            return [StreamSpec(
                "terminal",
                self._tmux_remote_command(shell, columns, rows),
                allocate_pty=True,
                columns=columns,
                rows=rows,
            )]

        self._check_viewport(columns, rows, len(self.roles) * (PAYLOAD_LINES + 2))
        return [StreamSpec(
            "terminal",
            f"stty -echo; exec {shell}",
            allocate_pty=True,
            columns=columns,
            rows=rows,
        )]

    # Đọc dữ liệu liên tục từ một stream vật lý.
    def _pump(self, coordinator: DirectCoordinator) -> None:
        raw_stream = coordinator.raw_stream
        while True:
            try:
                event = raw_stream.receive()
            except Exception as exc:
                if not self.closing:
                    coordinator.fail_all(repr(exc))
                return
            if event.kind == "data":
                coordinator.feed_bytes(
                    event.data,
                    event.observed_wall_ns or None,
                    event.observed_mono_ns or None,
                )
                continue
            if event.kind in {"error", "exit"}:
                if not self.closing:
                    message = event.message or f"stream thoát với mã {event.exit_status}"
                    coordinator.fail_all(message)
                return

    # Bọc một dòng lệnh để pane điều khiển chuyển tiếp vào FIFO của vai trò.
    def _fifo_wrapper(self, role: str):
        fifo = self.fifo_paths[role]

        def wrap(line: bytes) -> bytes:
            body = line.decode("utf-8").rstrip("\n")
            # Xóa pane ngay trước dấu mốc bắt đầu để nội dung của mẫu trước
            # không còn nằm trên viewport khi mẫu mới được xác thực.
            payload = "printf '\\033[2J\\033[H'; " + body
            forward = (
                f"printf '%s\\n' {shlex.quote(payload)} > {shlex.quote(fifo)}"
            )
            return (forward + "\n").encode("utf-8")

        return wrap

    # Mở transport, Bash và kiểm tra sẵn sàng song song.
    def open(self, timeout: float) -> dict[str, DirectOutputStream]:
        self.transport = open_multiplex_connection(
            self.cfg, self.protocol, self._stream_specs(), self.trial_tag
        )
        try:
            raw_streams = self.transport.open(timeout)
        finally:
            self.audit = self.transport.audit

        max_capture = int(self.cfg.get("MAX_CAPTURE_BYTES", "2097152"))
        if max_capture < PAYLOAD_BYTES:
            raise ValueError(
                f"MAX_CAPTURE_BYTES phải ít nhất {PAYLOAD_BYTES}"
            )
        if self.protocol == "mosh":
            screen = TerminalScreen(
                int(self.cfg.get("W2_MOSH_ROWS", "144")),
                int(self.cfg.get("W2_MOSH_COLUMNS", "4096")),
            )
            uses_panes = self._mosh_uses_panes()
            coordinator = DirectCoordinator(
                raw_streams["terminal"], not uses_panes, max_capture, screen
            )
            self.coordinators.append(coordinator)
            for role in self.roles:
                self.streams[role] = DirectOutputStream(
                    role, coordinator,
                    self._fifo_wrapper(role) if uses_panes else None,
                )
        else:
            for role in self.roles:
                coordinator = DirectCoordinator(raw_streams[role], False, max_capture)
                self.coordinators.append(coordinator)
                self.streams[role] = DirectOutputStream(role, coordinator)

        for index, coordinator in enumerate(self.coordinators):
            pump = threading.Thread(
                target=self._pump,
                args=(coordinator,),
                name=f"w2-tt-{self.protocol}-{index}",
                daemon=True,
            )
            pump.start()
            self.pumps.append(pump)

        with ThreadPoolExecutor(max_workers=len(self.coordinators)) as pool:
            futures = [
                pool.submit(
                    coordinator.probe,
                    f"{self.trial_tag}:ready:{index}",
                    timeout,
                )
                for index, coordinator in enumerate(self.coordinators)
            ]
            for future in futures:
                future.result()
        return self.streams

    # Xóa trạng thái terminal và xác nhận buffer đã sạch sau warm-up.
    def prepare_workload(self, timeout: float) -> None:
        with ThreadPoolExecutor(max_workers=len(self.coordinators)) as pool:
            futures = [
                pool.submit(
                    coordinator.execute,
                    f"{self.trial_tag}:clear:{index}",
                    "printf '\\033[2J\\033[H'",
                    b"__W2TT_NO_OUTPUT__",
                    timeout,
                )
                for index, coordinator in enumerate(self.coordinators)
            ]
            for future in futures:
                future.result()

    # Dọn tmux server và FIFO của trial qua chính pane điều khiển.
    def _teardown_tmux(self, stream) -> None:
        """Không mở connection phụ: mọi thứ đi qua terminal đang đo.

        tmux server chạy detached nên nó sống sót khi Mosh đóng; nếu không dọn,
        mỗi trial để lại một server cùng các FIFO trên máy đích.
        """
        if not self.tmux_session:
            return
        tmux = (
            f"{shlex.quote(self.cfg.get('TMUX_BIN', 'tmux'))} "
            f"-L {shlex.quote(self.tmux_socket)}"
        )
        fifos = " ".join(
            shlex.quote(path) for path in self.fifo_paths.values()
        )
        command = f"rm -f {fifos}; {tmux} kill-server 2>/dev/null || true\n"
        try:
            stream.send(command.encode("utf-8"))
        except Exception:
            pass

    # Đóng Bash và connection sau trial.
    def close(self) -> None:
        self.closing = True
        sent: set[int] = set()
        for stream in self.streams.values():
            identity = id(stream.raw_stream)
            if identity in sent:
                continue
            sent.add(identity)
            self._teardown_tmux(stream.raw_stream)
            try:
                stream.raw_stream.send(b"exit\n")
            except Exception:
                pass
        if self.transport is not None:
            self.transport.close()


# Tạo connection W2 gửi lệnh trực tiếp.
def open_direct_w2_connection(
    cfg: dict, protocol: str, roles: list[str], trial_tag: str
) -> DirectW2Connection:
    return DirectW2Connection(cfg, protocol, roles, trial_tag)
