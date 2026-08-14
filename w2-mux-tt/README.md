# W2 — Truyền output lớn trực tiếp

`w2-mux-tt` triển khai W2 – Multiplexed Large Output trong
`Thiết kế thí nghiệm.pdf`. Workload dùng chung cách mở connection và stream từ
`../stream_mux/`, nhưng gửi trực tiếp lệnh `cat` vào Bash thường trực trên Pi2;
không có chương trình phụ nhận lệnh, JSON hay Base64 ở tầng workload.

## Payload

Trước thí nghiệm, `tools/generate_payloads.py` tạo bốn tệp:

```text
large_output_s0_100KiB.txt
large_output_s1_100KiB.txt
large_output_s2_100KiB.txt
large_output_s3_100KiB.txt
```

Mỗi tệp có đúng:

- 102.400 byte, tương đương 100 KiB;
- 800 dòng, mỗi dòng 128 byte kể cả LF;
- một SHA-256 xác định;
- tiền tố dòng riêng `W2S0|` đến `W2S3|` để định tuyến output khi nhiều tác vụ
  dùng chung terminal Mosh.

`run_w2.sh` tạo payload trên Pi1, chép sang `W2_REMOTE_PAYLOAD_DIR` trên Pi2 rồi
chạy `sha256sum -c` trước khi tạo trial. Việc chuẩn bị này không nằm trong
`setup_ms` hoặc độ trễ truyền.

## Kịch bản

- `W2-S1`: một output stream truyền một payload 100 KiB.
- `W2-S2`: hai output stream đồng thời, mỗi stream một payload 100 KiB.
- `W2-S4`: bốn output stream đồng thời, mỗi stream một payload 100 KiB.

Mỗi trial tạo một connection mới, mở đủ stream, chờ READY, warm-up năm giây, xóa
trạng thái terminal/buffer rồi giải phóng hàng rào đồng bộ. Mỗi output stream
truyền tuần tự payload 100 lần trong cùng connection. Mặc định mỗi tổ hợp giao
thức × kịch bản có 10 trial, tức mỗi vai trò thu được 1.000 mẫu.

Số mẫu được đặt bằng:

```env
SAMPLES_PER_STREAM_PER_TRIAL=100
```

## Gửi trực tiếp và phép đo end-to-end

Với mỗi output stream, Pi1 gửi một dòng Bash chứa trực tiếp:

```text
cat -- /tmp/w2_mux_tt_payloads/large_output_sX_100KiB.txt
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

Mỗi dòng payload dài 128 byte và có chỉ số duy nhất. Vì vậy độ bao phủ nội dung
được tính bằng:

```text
content_coverage_pct =
    valid_unique_lines / expected_lines * 100
```

Chỉ dòng khớp chính xác payload gốc và chưa xuất hiện trước đó mới được tính.
`duplicate_lines`, `invalid_lines` và `missing_lines` lần lượt ghi số dòng lặp,
sai và thiếu. `mean_content_coverage_pct` trong `trials.csv`,
`scenario_summary.csv` và `stream_summary.csv` là trung bình độ bao phủ của các
phép truyền thuộc nhóm tương ứng.

`raw_byte_ratio_pct = received_bytes / expected_bytes * 100` được giữ làm chỉ
số chẩn đoán và có thể vượt 100% khi Mosh vẽ lại dữ liệu. Chỉ số thô này không
được dùng để kết luận output đầy đủ. SHA-256 vẫn là điều kiện xác thực toàn vẹn
tuyệt đối.

Nhận dấu hoàn thành nhưng thiếu hoặc sai output được ghi `partial`; không nhận
dấu trước `TRANSFER_TIMEOUT` được ghi `timeout`. Với timeout, phần output đã
quan sát vẫn được giữ lại để tính độ bao phủ nội dung.

## Ý nghĩa stream theo giao thức

- SSH dùng một ControlMaster và một session channel cho mỗi `output_X`.
- SSH3 gọi kết nối một lần và mở một QUIC bidirectional stream thật cho mỗi
  `output_X`. Các StreamID phải khác nhau nhưng cùng ConversationStreamID và UDP
  socket.
- Mosh chỉ có một terminal session. W2-S2/W2-S4 là các tiến trình `cat` nền đồng
  thời, không phải nhiều transport stream.

Terminal Mosh dùng kích thước `4096x128`, thống nhất với W1. Giá trị này được
cấu hình bằng `W2_MOSH_COLUMNS` và `W2_MOSH_ROWS`.

Mosh truyền trạng thái màn hình thay vì luồng byte lossless. Với output 100 KiB,
Mosh có thể nhận dấu hoàn thành nhưng không tái tạo đủ mọi byte đã cuộn khỏi màn
hình. Khi đó `stream_completed=1` nhưng phép truyền là `partial` và
`output_complete=0`; `content_coverage_pct` cho biết Mosh đã tái tạo được bao
`content_coverage_pct` cho biết Mosh đã tái tạo được bao nhiêu phần trăm dòng
payload duy nhất và chính xác. Đây là kết quả phản ánh đúng tính chất giao thức,
không được đổi thành hoàn tất giả.

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
SAMPLES_PER_STREAM_PER_TRIAL=5 \
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

Bộ hình chuẩn giữ nguyên các metric trước và bổ sung độ bao phủ output:

```text
completion_median.png/pdf       độ trễ byte cuối trung vị
completion_p95.png/pdf          P95 độ trễ byte cuối
first_byte_median.png/pdf       độ trễ byte đầu trung vị
throughput_mean.png/pdf         thông lượng trung bình
transfer_completion.png/pdf     tỷ lệ phép truyền hoàn tất
content_coverage.png/pdf        tỷ lệ output hợp lệ và duy nhất
setup_median.png/pdf            thời gian thiết lập trung vị
```

Hai hình tỷ lệ dùng trục 0–100%. `transfer_completion` chỉ đạt 100% khi phép
truyền nhận đủ dấu hoàn thành, byte, dòng và SHA-256;
`content_coverage` vẫn thể hiện phần nội dung hợp lệ đã nhận đối với mẫu
`partial` hoặc `timeout`, đồng thời không tính byte vẽ lại hay dòng lặp.

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
