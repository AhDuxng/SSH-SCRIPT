# W1 — Vòng lệnh multiplex

Đây là phần triển khai W1 theo `Thiết kế thí nghiệm.pdf`. Mỗi trial tạo một
connection mới, mở toàn bộ stream của kịch bản, chờ tất cả stream báo READY,
warm-up năm giây rồi giải phóng barrier chung.

## Workload

Mỗi stream chạy tuần tự đúng năm lệnh:

```text
ls -1 /usr/bin | head -n 30
df -h /
free -m
ps -eo pid,comm,%cpu,%mem --sort=pid | head -n 30
uptime
```

Lệnh tiếp theo chỉ được gửi sau khi nhận kết quả của lệnh trước. Các stream khác
nhau chạy đồng thời.

Các kịch bản:

- `W1-S1`: một command stream.
- `W1-S2`: hai command stream.
- `W1-S4`: bốn command stream.

Mỗi block chứa đủ mọi tổ hợp `protocol × scenario`. Thứ tự trong block được xáo
trộn có seed để có thể tái lập.

## Multi-stream SSH3 thật

W1 không tạo một tiến trình SSH3 cho từng stream. Bộ chạy sử dụng
[`ssh3_mux_stdio.patch`](../stream_mux/patches/ssh3_mux_stdio.patch), được build
bằng [`build_ssh3_mux.sh`](../stream_mux/scripts/build_ssh3_mux.sh).

Go client gọi `client.Dial` đúng một lần, sau đó gọi
`OpenChannel("session", ...)` cho từng cờ `-mux-stream`. Vì vậy mỗi role có một
QUIC StreamID thật, nhưng tất cả role vẫn thuộc cùng một ConversationStreamID và
cùng một UDP socket.

SSH dùng một ControlMaster và một session channel cho từng role. Mosh được ghi
đúng theo giới hạn giao thức: các role là tiến trình đồng thời trong một terminal
session, không phải transport stream.

## Phép đo

- `latency_ms = thời điểm client nhận kết quả - thời điểm client gửi lệnh`.
- Command hoàn thành khi client nhận đúng result frame trước timeout.
- Phía gửi ghi số byte và SHA-256 chính xác của stdout trong lần chạy đó.
- Client tự tính lại số byte và SHA-256 của output đã nhận để kiểm tra đầy đủ.
- Mẫu timeout/lỗi vẫn đi vào completion rate nhưng không đi vào percentile.
- Kết quả được giữ riêng từng stream và tổng hợp toàn kịch bản.

Các metric gồm Mean, Median, P95, P99, Command Completion Rate, Stream Completion
Rate và Output Completeness.

`setup_ms` được đo từ ngay trước khi mở connection đến khi audit transport hoàn
tất và mọi role của workload báo READY. Giá trị từng trial nằm trong
`trials.csv`; Mean, Median, P95 và P99 nằm trong `scenario_summary.csv`. Khoảng
thời gian này không gồm warm-up hoặc workload.

Mosh sử dụng terminal ảo rộng và cao theo `W1_MOSH_COLUMNS` và `W1_MOSH_ROWS` để
frame Base64 của W1 không bị wrap hoặc scroll khỏi trạng thái terminal trước khi
client nhận được. Adapter loại mã điều khiển ANSI trước khi giải mã frame.

## Cách chạy

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w1-mux
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash run_w1.sh config.env
```

Các tệp kết quả chính:

```text
artifacts/results/experiment_order.csv
artifacts/results/samples.csv
artifacts/results/streams.csv
artifacts/results/trials.csv
artifacts/results/stream_audit.csv
artifacts/results/command_summary.csv
artifacts/results/scenario_summary.csv
artifacts/results/metadata.json
```

## Cấu trúc

```text
w1-mux/
├── config.env
├── config.example.env
├── remote_agent.py
├── run_w1.sh
├── src/
│   ├── config.py
│   ├── constants.py
│   ├── framing.py
│   ├── run_w1.py
│   ├── stream_adapter.py
│   └── trial.py
└── tools/
    ├── analyze_w1.py
    └── verify_ssh3_mux.py
```

Phần mở connection và stream thô được dùng chung từ `../stream_mux/`. Giao thức
`exec/result`, framing Base64 và agent thực thi lệnh chỉ thuộc W1, nằm trong
`src/stream_adapter.py`, `src/framing.py` và `remote_agent.py`.
