# W2 — Truyền output lớn trực tiếp

`w2-mux-tt` triển khai W2 – Multiplexed Large Output trong
`Thiết kế thí nghiệm.pdf`. Workload dùng chung cách mở connection và stream từ
`../stream_mux/`, nhưng gửi trực tiếp lệnh `cat` vào Bash thường trực trên Pi2;
không có chương trình phụ nhận lệnh, JSON hay Base64 ở tầng workload.

## Payload

Trước thí nghiệm, `tools/generate_payloads.py` tạo bốn tệp:

```text
large_output_s0_1MiB.txt
large_output_s1_1MiB.txt
large_output_s2_1MiB.txt
large_output_s3_1MiB.txt
```

Mỗi tệp có đúng:

- 1.048.576 byte;
- 8.192 dòng, mỗi dòng 128 byte kể cả LF;
- một SHA-256 xác định;
- tiền tố dòng riêng `W2S0|` đến `W2S3|` để định tuyến output khi nhiều tác vụ
  dùng chung terminal Mosh.

`run_w2.sh` tạo payload trên Pi1, chép sang `W2_REMOTE_PAYLOAD_DIR` trên Pi2 rồi
chạy `sha256sum -c` trước khi tạo trial. Việc chuẩn bị này không nằm trong
`setup_ms` hoặc độ trễ truyền.

## Kịch bản

- `W2-S1`: một output stream truyền một payload 1 MiB.
- `W2-S2`: hai output stream đồng thời, mỗi stream một payload 1 MiB.
- `W2-S4`: bốn output stream đồng thời, mỗi stream một payload 1 MiB.

Mỗi trial tạo một connection mới, mở đủ stream, chờ READY, warm-up năm giây, xóa
trạng thái terminal/buffer rồi giải phóng hàng rào đồng bộ. Mỗi output stream thực
hiện đúng một lần truyền trong trial. Mặc định mỗi tổ hợp giao thức × kịch bản có
10 trial.

## Gửi trực tiếp và phép đo end-to-end

Với mỗi output stream, Pi1 gửi một dòng Bash chứa trực tiếp:

```text
cat -- /tmp/w2_mux_tt_payloads/large_output_sX_1MiB.txt
```

Hai dấu mốc văn bản ngắn bao quanh lệnh để phân tách payload và lấy mã thoát.
Chúng không mã hóa payload và không thay thế lệnh `cat`.

Các mốc thời gian:

- `send_time_ns`: ngay trước khi Pi1 ghi dòng lệnh vào stream;
- `first_byte_time_ns`: khi Pi1 quan sát byte payload đầu tiên;
- `last_byte_time_ns`: khi Pi1 quan sát byte payload cuối cùng;
- `marker_time_ns`: khi Pi1 nhận dấu hoàn thành và mã thoát.

Metric chính đúng PDF:

```text
completion_latency_ms = last_byte_time - send_time
```

Ngoài ra chương trình ghi `first_byte_latency_ms`, `marker_latency_ms` và thông
lượng MiB/s. Một phép truyền chỉ có `status=completed` khi:

- nhận được dấu hoàn thành;
- lệnh thoát với mã 0;
- byte nhận bằng byte dự kiến;
- số dòng nhận bằng số dòng dự kiến;
- SHA-256 nhận bằng SHA-256 trong manifest.

Nhận dấu hoàn thành nhưng thiếu hoặc sai output được ghi `partial`; không nhận
dấu trước `TRANSFER_TIMEOUT` được ghi `timeout`.

## Ý nghĩa stream theo giao thức

- SSH dùng một ControlMaster và một session channel cho mỗi `output_X`.
- SSH3 gọi kết nối một lần và mở một QUIC bidirectional stream thật cho mỗi
  `output_X`. Các StreamID phải khác nhau nhưng cùng ConversationStreamID và UDP
  socket.
- Mosh chỉ có một terminal session. W2-S2/W2-S4 là các tiến trình `cat` nền đồng
  thời, không phải nhiều transport stream.

Mosh truyền trạng thái màn hình thay vì luồng byte lossless. Với output 1 MiB,
Mosh có thể nhận dấu hoàn thành nhưng không tái tạo đủ mọi byte đã cuộn khỏi màn
hình. Khi đó `stream_completed=1` nhưng phép truyền là `partial` và
`output_complete=0`. Đây là kết quả phản ánh đúng tính chất giao thức, không được
đổi thành hoàn tất giả.

## Chạy

Tạo môi trường trên Pi1:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Smoke test cả ba giao thức:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-all \
bash run_w2.sh config.env 2>&1 | tee artifacts/smoke-all.log
```

Chạy chính thức 10 trial:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
bash run_w2.sh config.env 2>&1 | tee artifacts/full_run.log
```

Các tệp kết quả:

```text
artifacts/results/experiment_order.csv
artifacts/results/transfers.csv
artifacts/results/streams.csv
artifacts/results/trials.csv
artifacts/results/stream_audit.csv
artifacts/results/scenario_summary.csv
artifacts/results/stream_summary.csv
artifacts/results/metadata.json
```

## Cấu trúc

```text
w2-mux-tt/
├── config.env
├── config.example.env
├── payloads/
├── run_w2.sh
├── src/
│   ├── config.py
│   ├── constants.py
│   ├── framing.py
│   ├── run_w2.py
│   ├── stream_adapter.py
│   └── trial.py
└── tools/
    ├── analyze_w2.py
    ├── generate_payloads.py
    ├── plot_w2.py
    └── verify_ssh3_mux.py
```
