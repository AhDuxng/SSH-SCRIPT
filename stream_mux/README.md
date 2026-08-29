# Lõi connection và stream dùng chung

`stream_mux` chỉ chịu trách nhiệm mở, kiểm chứng và đóng transport. Thư mục này
không chứa lệnh, marker, framing, parser hoặc phép đo riêng của W1–W4.

Mỗi workload tạo danh sách `StreamSpec` rồi truyền vào
`open_multiplex_connection`. Kết quả là các `RawStream` hai chiều chỉ làm việc
với byte và sự kiện `data`, `exit`, `error`.

## Hợp đồng dùng chung

```python
from stream_mux import StreamSpec, open_multiplex_connection

specs = [
    StreamSpec("control", "python3 -u /tmp/workload_agent.py"),
    StreamSpec(
        "interactive",
        "bash --noprofile --norc -i",
        allocate_pty=True,
        terminal_type="xterm-256color",
        columns=80,
        rows=24,
    ),
]

connection = open_multiplex_connection(config, protocol, specs, trial_tag)
streams = connection.open(timeout=20.0)
streams["control"].send(b"payload")
event = streams["control"].receive(timeout=5.0)
connection.close()
```

`connection.open()` chỉ xác nhận connection và các transport stream đã được mở.
READY, warm-up, barrier, timeout tác vụ và cách xác định completion thuộc về
workload, nên phải được cài trong thư mục `w1-mux`, `w2-mux`, `w3-mux` hoặc
`w4-mux`.

## Bất biến theo giao thức

| Giao thức | Connection trong một trial | Ánh xạ `StreamSpec` |
|---|---|---|
| SSH | Một TCP connection bằng `ControlMaster` | Mỗi spec là một SSH session channel |
| SSH3 | Một QUIC connection và một conversation | Mỗi spec là một QUIC bidirectional stream |
| Mosh | Một terminal session | Chỉ nhận một spec; nhiều tác vụ logic phải do workload quản lý trong terminal |

SSH và SSH3 cho phép trộn stream có PTY với stream không PTY trong cùng
connection. Mosh không được báo cáo là multi-stream vì giao thức không có mô hình
channel tương đương.

## Phân chia trách nhiệm

```text
stream_mux/
├── connection/
│   ├── base.py       # StreamSpec, RawStream và audit
│   ├── common.py     # Pipe, tiến trình và kiểm tra socket
│   ├── ssh.py        # ControlMaster và session channel
│   ├── ssh3.py       # QUIC multi-stream qua Go bridge
│   └── mosh.py       # Một terminal session Mosh
├── patches/
│   ├── quic_go_cubic.patch
│   ├── ssh3_jwt_clock_skew.patch
│   └── ssh3_mux_stdio.patch
└── scripts/
    ├── build_ssh3_mux.sh
    ├── build_ssh3_server.sh
    └── prepare_quic_cubic.sh
```

Ví dụ phần chuyên biệt của W1 nằm trong:

```text
w1-mux/
├── remote_agent.py
└── src/
    ├── framing.py
    └── stream_adapter.py
```

W2–W4 phải tạo adapter riêng theo phép đo của mình, nhưng đều dùng cùng
`StreamSpec`, `RawStream`, factory và `ConnectionAudit`.

## Hướng dẫn triển khai workload mới

Khi triển khai W2, W3 hoặc W4, giữ nguyên `stream_mux` và tạo toàn bộ logic đặc
thù trong thư mục của workload:

```text
wN-mux/
├── config.env             # Cấu hình transport và workload
├── config.example.env
├── remote_agent.py        # Chỉ tạo khi workload cần agent từ xa
├── run_wN.sh
└── src/
    ├── stream_adapter.py  # Chuyển RawStream thành giao diện của workload
    ├── trial.py           # READY, warm-up, barrier và phép đo
    └── run_wN.py
```

Quy trình bắt buộc của một trial:

1. Workload đọc cấu hình của chính nó và tạo `StreamSpec` cho từng role.
2. Gọi `open_multiplex_connection(config, protocol, specs, trial_tag)` đúng một
   lần.
3. Gọi `connection.open(timeout)` để mở toàn bộ transport stream.
4. Adapter của workload chờ READY và xóa dữ liệu khởi động còn chờ.
5. Workload thực hiện warm-up, barrier và tác vụ đo theo tài liệu thí nghiệm.
6. Ghi kết quả cùng `connection.audit` trước khi đóng connection.
7. Luôn gọi `connection.close()` trong `finally`.

Mẫu khung triển khai:

```python
from stream_mux import StreamSpec, open_multiplex_connection


# Tạo đặc tả stream theo cấu hình workload.
def build_specs(config):
    return [
        StreamSpec("role_0", config["REMOTE_COMMAND"]),
    ]


# Chạy một trial bằng transport dùng chung.
def run_trial(config, protocol, trial_tag):
    connection = open_multiplex_connection(
        config, protocol, build_specs(config), trial_tag
    )
    try:
        raw_streams = connection.open(
            float(config.get("STREAM_OPEN_TIMEOUT", "20"))
        )
        # Adapter của workload xử lý READY và phép đo tại đây.
        return raw_streams, connection.audit
    finally:
        connection.close()
```

### Phần được đặt trong `stream_mux`

- Tạo và đóng một connection vật lý cho mỗi trial.
- Mở stream/channel theo `StreamSpec`.
- Gửi và nhận byte thô.
- Cấp PTY theo tham số workload truyền vào.
- Kiểm tra socket, StreamID, ConversationStreamID và semantics transport.

### Phần phải đặt trong thư mục workload

- Lệnh và agent từ xa.
- Framing, marker READY, request/result và parser terminal.
- Warm-up, barrier, timeout tác vụ và điều kiện completion.
- Bộ đếm byte, checksum, throughput và công thức latency.
- Schema CSV, thống kê, biểu đồ và verifier riêng.

Không thêm tên lệnh, marker, định dạng frame hoặc metric của W1–W4 vào
`stream_mux`. Nếu một tính năng chỉ phục vụ một phép đo, tính năng đó thuộc
adapter của workload.

## Ánh xạ dự kiến cho W1–W4

| Workload | Adapter riêng | Cách dùng `RawStream` |
|---|---|---|
| W1 | `exec/result` và Base64 | Gửi lệnh, chờ result frame và kiểm tra SHA-256 |
| W2 | Lệnh có output liên tục | Đọc từng khối output đến marker hoàn thành |
| W3 | PTY tương tác và tải nền | Gửi phím trên stream PTY, đồng thời drain các stream nền |
| W4 | Output lớn theo từng chunk | Đọc liên tục, đếm byte và không gom toàn bộ output vào RAM |

Với Mosh, workload chỉ truyền một `StreamSpec` đại diện cho terminal. Nếu cần
nhiều role logic, adapter hoặc agent của workload phải quản lý các role trong
terminal đó và ghi `process_in_terminal`; không được báo cáo chúng là transport
stream.

## Checklist nhất quán

Trước khi chấp nhận một workload mới, xác nhận:

- SSH có đúng một TCP socket của `ControlMaster` và mọi role dùng session channel
  của master đó.
- SSH3 có đúng một UDP socket, một ConversationStreamID và StreamID khác nhau cho
  các role.
- Mosh có đúng một terminal session và không có StreamID giả.
- Mỗi trial tạo connection mới, mở đủ stream trước warm-up và luôn đóng sạch.
- Lõi `stream_mux` không chứa import hoặc hằng số từ một workload cụ thể.
- Cấu hình lệnh, PTY, role và timeout được truyền từ thư mục workload.
- Adapter xử lý byte theo đúng phép đo; không dùng adapter W1 cho output liên tục,
  tương tác ký tự hoặc output lớn.

## SSH3 multi-stream

Patch cung cấp hai cờ lặp:

```text
-mux-stream role=command
-mux-pty-stream role=command
```

Sau đúng một lần `client.Dial`, Go bridge gọi `OpenChannel("session", ...)` cho
từng spec. Mỗi channel có `ChannelID()` riêng nhưng cùng
`ConversationStreamID()`. Với `-mux-pty-stream`, bridge gửi yêu cầu PTY trước
yêu cầu thực thi lệnh.

Build binary dùng chung:

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT
bash stream_mux/scripts/build_ssh3_mux.sh
```

Binary và mã băm patch được tạo tại:

```text
stream_mux/bin/ssh3-mux-stdio
stream_mux/bin/ssh3-mux-stdio.patch.sha256
```

Source build nằm trong cache `.build/` có tên theo commit và checksum patch. Khi
patch thay đổi, script tự dùng cache mới nên không tái sử dụng source đã áp dụng
patch cũ.

## Kiểm tra trước khi đo

`scripts/preflight.py` kiểm tra một cặp client/server trong một lượt: công cụ
hai đầu, module Python, cờ `-mux-stream` của binary, mã băm patch, và **thuật
toán congestion control đã nướng vào binary SSH3 của cả client lẫn server**.
Sau đó nó mở thật một connection cho từng giao thức trong `PROTOCOLS` và xác
nhận lệnh từ xa chạy được.

```bash
cd ~/SSH-SCRIPT
python3 stream_mux/scripts/preflight.py w2-mux-tt/config.env
```

Mã thoát khác 0 nghĩa là còn mục chưa đạt; mỗi mục hỏng in kèm lệnh sửa. Dùng
`--skip-connect` khi chỉ muốn kiểm tra tĩnh, và `--workload` khi chạy trên một
tệp env chưa được chép vào thư mục workload.

Điểm dễ bỏ sót: `run_wN.sh` chỉ tự build lại **client**. Trong W2 phía gửi
payload là server, nên congestion control của luồng được đo là của server;
preflight đọc trực tiếp binary đang phục vụ qua `/proc/<pid>/exe` để báo đúng
thuật toán.

## Thuật toán congestion control của QUIC

`patches/quic_go_cubic.patch` chuyển sender của quic-go từ Reno sang CUBIC.
Script build áp dụng bản vá này cho cả SSH3 client và server. Phần này chỉ chọn
thuật toán truyền tải; source không còn tracer hay collector ghi congestion log.

`patches/ssh3_jwt_clock_skew.patch` cho verifier JWT phía server dung sai lệch
đồng hồ 2 giây, tránh lỗi xác thực ngắt quãng `token used before issued` giữa
hai máy đã bật NTP nhưng vẫn có sai lệch thời gian nhỏ.
