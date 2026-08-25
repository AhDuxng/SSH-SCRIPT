# W2 — Truyền output lớn trực tiếp

`w2-mux-tt` triển khai W2 – Multiplexed Large Output trong
`Thiết kế thí nghiệm.pdf`. Workload dùng chung cách mở connection và stream từ
`../stream_mux/`, nhưng gửi trực tiếp lệnh xuất payload vào Bash thường trực trên Pi2;
không có chương trình phụ nhận lệnh, JSON hay Base64 ở tầng workload.

## Payload

Trước thí nghiệm, `tools/generate_payloads.py` tạo bốn tệp:

```text
large_output_s0_100KB.txt
large_output_s1_100KB.txt
large_output_s2_100KB.txt
large_output_s3_100KB.txt
```

Mỗi tệp có đúng:

- 102.400 byte (100 KB theo đặc tả W2, tức 100 KiB nhị phân);
- 25 dòng, mỗi dòng 4.096 byte kể cả LF;
- một SHA-256 xác định;
- tiền tố dòng riêng `W2S0|` đến `W2S3|` để định tuyến output khi nhiều tác vụ
  dùng chung terminal Mosh.

`run_w2.sh` tạo payload trên Pi1, chép sang `W2_REMOTE_PAYLOAD_DIR` trên Pi2 rồi
chạy `sha256sum -c` trước khi tạo trial. Việc chuẩn bị này không nằm trong
`setup_ms` hoặc độ trễ truyền.

SHA-256 cố định lần lượt cho `s0..s3` là:

```text
574e67a5726d23330a7ce60061b23e43a756ea8c9192df910f332e023eb74d85
08d0af71368751ba7dcc8c715661790c9aec9aba3d437104512fd20710ba7b48
bb894c3f54c1fcf0c98c5b9bb6f9da2ca4b1e190c23ffe7fa4cfd93260bbe798
a8992235b2c46fc84f8c2387c6d9d74be87190d67563a87cdf53ae35e5309f11
```

## Kịch bản

- `W2-S1`: một output stream truyền một payload 100 KB.
- `W2-S2`: hai output stream đồng thời, mỗi stream một payload 100 KB.
- `W2-S4`: bốn output stream đồng thời, mỗi stream một payload 100 KB.

Mỗi trial tạo một connection mới, mở đủ stream, chờ READY và warm-up năm giây.
Tất cả vai trò đi qua một barrier ở đầu từng sample. Với Mosh, driver xóa
viewport và phải nhận marker hậu-xóa riêng trước khi barrier cho phép chạy batch
kế tiếp. Mỗi output stream truyền tuần tự
payload 100 lần trong cùng connection. Mặc định mỗi tổ hợp giao
thức × kịch bản có 10 trial, tức mỗi vai trò thu được 1.000 mẫu.

Số mẫu được đặt bằng:

```env
SAMPLES_PER_STREAM_PER_TRIAL=100
```

## Gửi trực tiếp và phép đo end-to-end

Với mỗi output stream, Pi1 gửi một dòng Bash dùng `sed` để thay 29 byte đầu của
mỗi dòng bằng prefix chứa token riêng của `trial/role/sample`, rồi xuất toàn bộ
file:

```text
sed 's/^............................./W2SX|<sample-token>/' \
  /tmp/w2_mux_tt_payloads/large_output_sX_100KB.txt
```

Hai dấu mốc văn bản ngắn bao quanh lệnh để phân tách payload và lấy mã thoát.
Phép thay thế giữ nguyên đúng 102.400 byte và 25 dòng. Nhờ token riêng, redraw
của sample cũ không thể khớp nội dung kỳ vọng của sample mới. Cách tạo output
này được dùng giống nhau cho SSH, SSH3 và Mosh.

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
- đủ 102.400 byte payload hợp lệ;
- đủ 25 dòng payload duy nhất;
- SHA-256 của payload đã xác thực bằng giá trị cố định trong mã và manifest.

Mỗi dòng payload dài 4.096 byte và có chỉ số duy nhất. Vì vậy độ bao phủ nội dung
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

`verified_byte_ratio_pct` đếm byte thuộc các dòng deterministic chính xác, duy
nhất. `raw_byte_ratio_pct = received_bytes / expected_bytes * 100` chỉ là chỉ số
chẩn đoán và có thể vượt 100% khi Mosh vẽ lại dữ liệu. Với SSH/SSH3, capture byte
thô còn phải khớp nguyên bản; với Mosh, chương trình dựng lại payload canonical
từ 25 dòng đã xác thực rồi kiểm tra byte và SHA-256.

Bộ phân tích báo cáo hai đại lượng không được đánh đồng:

- `fully_verified_output_rate_pct`: phần trăm phép truyền quan sát đủ cả 25 dòng,
  đủ 102.400 byte nội dung hợp lệ và khớp SHA-256. Thiếu dù một dòng thì phép
  truyền không được tính vào tỷ lệ này. Chỉ số này đánh giá độ đầy đủ nội dung;
  `transfer_completion_rate_pct` nghiêm ngặt hơn vì còn yêu cầu marker và mã
  thoát 0.
- `verified_output_ratio_pct`: tổng byte nội dung deterministic đã xác thực chia
  cho tổng byte output theo kế hoạch. Chỉ số này cho biết lượng nội dung thực sự
  quan sát được ngay cả khi nhiều phép truyền chỉ nhận được một phần.

Riêng Mosh, hai chỉ số trên mô tả nội dung đã quan sát và xác thực trên terminal,
không phải tỷ lệ byte thô được truyền lossless. `raw_capture_exact_rate_pct` vì
thế được để `N/A` cho Mosh. Bảng rút gọn dành riêng cho Mosh được lưu tại
`mosh_output_completeness.csv`.

Nhận dấu hoàn thành nhưng thiếu hoặc sai output được ghi `partial`; không nhận
dấu trước `TRANSFER_TIMEOUT` được ghi `timeout`. Với timeout, phần output đã
quan sát vẫn được giữ lại để tính độ bao phủ nội dung.

Với `MOSH_CONTINUE_AFTER_TIMEOUT=1`, timeout Mosh chỉ loại phép truyền hiện tại;
runner giữ barrier hoạt động và tiếp tục batch kế tiếp. `MOSH_BARRIER_GRACE_SECONDS`
cho stream đã hoàn thành sớm chờ stream timeout quay lại barrier mà không làm
hỏng toàn bộ trial. Thống kê tách riêng planned, attempted, partial, timeout và
skipped.

## Ý nghĩa stream theo giao thức

- SSH dùng một ControlMaster và một session channel cho mỗi `output_X`.
- SSH3 gọi kết nối một lần và mở một QUIC bidirectional stream thật cho mỗi
  `output_X`. Các StreamID phải khác nhau nhưng cùng ConversationStreamID và UDP
  socket.
- Mosh chỉ có một terminal session. W2-S2/W2-S4 là các tiến trình `cat` nền đồng
  thời, không phải nhiều transport stream.

Terminal Mosh dùng kích thước `4096 cột × 128 dòng`, giống W1: một dòng payload
không bị wrap và viewport giữ được 4 × 25 dòng cùng marker của W2-S4. Giá trị này được
cấu hình bằng `W2_MOSH_COLUMNS` và `W2_MOSH_ROWS`; chương trình từ chối chạy nếu
viewport nhỏ hơn batch của kịch bản.

Mosh truyền trạng thái màn hình thay vì luồng byte lossless. Vì vậy raw terminal
bytes không được so trực tiếp với file. Trước mỗi sample, viewport được xóa và
client phải nhận marker xác nhận sau lệnh xóa; tất cả vai trò sau đó đi qua cùng
một barrier. Chương trình lọc redraw/control
bytes, gom đủ 25 dòng duy nhất theo prefix và dựng lại đúng thứ tự canonical để
kiểm tra 102.400 byte cùng SHA-256. Nếu thiếu dù một dòng, sample vẫn là
`partial`/`timeout`, không được đổi thành hoàn tất giả.

## Chạy

Tạo môi trường trên Pi1:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
sudo apt-get update
sudo apt-get install -y iproute2 mosh python3-venv
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
RESULT_DIR=/tmp/w2-smoke \
bash run_w2.sh config.env
```

Chạy chính thức 10 trial:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
bash run_w2.sh config.env
```

Runner tự ghi `artifacts/full_run.log`; không nối thêm `| tee`.

Các tệp kết quả:

```text
artifacts/results/experiment_order.csv
artifacts/results/transfers.csv
artifacts/results/streams.csv
artifacts/results/trials.csv
artifacts/results/stream_audit.csv
artifacts/results/scenario_summary.csv
artifacts/results/stream_summary.csv
artifacts/results/ssh3_vs_ssh.csv
artifacts/results/metadata.json
```

Bộ hình dùng cùng bố cục, màu, hatch, nhãn số và cách nhóm như W1:

```text
figure_0_command_visible_*.png/pdf         marker-visible Mean/Median/P95/P99
figure_1_completion_*.png/pdf             latency Mean/Median/P95/P99
figure_2_first_byte_median.png/pdf         latency byte đầu
figure_3_throughput_mean.png/pdf           thông lượng byte đã xác thực
figure_4_setup_*.png/pdf                  setup Mean/Median/P95/P99
figure_5_output_integrity.png/pdf          complete/byte/SHA-256
figure_6_per_stream_completion_*.png/pdf  latency từng stream
figure_7_per_stream_integrity.png/pdf     integrity từng stream
```

Hai hình integrity dùng trục 0–100%. `Transfer complete` chỉ đạt 100% khi nhận
đủ marker, byte, dòng và SHA-256. Bảng CSV vẫn giữ `content_coverage_pct` để xem
phần nội dung hợp lệ của mẫu `partial`/`timeout` mà không tính redraw hay dòng lặp.

`figure_0_command_visible_*.png/pdf` đo từ lúc gửi lệnh đến khi dấu `DONE` hiện
trên terminal. Chỉ số này vẫn được báo cáo cho Mosh khi output không còn đủ
100 KiB, vì nó phản ánh lúc người dùng thấy lệnh đã kết thúc. Các hình
`figure_1_completion_*` vẫn là phép đo lossless-output và có thể ghi `N/A` cho
Mosh. Mosh luôn dùng một terminal vật lý; sau khi mọi tiến trình của sample báo
`DONE`, runner áp dụng ANSI/cursor update vào một viewport chung, chờ màn hình
ổn định rồi mới tính content coverage.

`ssh3_vs_ssh.csv` ghi median latency, mean throughput, tỷ số SSH3/SSH và verdict
cho từng kịch bản. Nếu SSH3 chậm hơn quá 5%, analyzer in `[CHECK]`; đây là cảnh
báo kiểm tra kết quả thực đo, không tự sửa hay loại mẫu.

## Cấu trúc

```text
w2-mux-tt/
├── config.env
├── config.example.env
├── LOG_ANALYSIS.md
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
