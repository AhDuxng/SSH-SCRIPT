# W1 gửi lệnh trực tiếp

`w1-mux-tt` là biến thể gửi lệnh trực tiếp của `w1-mux`. Bản này vẫn dùng
`../stream_mux/` để mở connection và stream giống hệt các workload khác, nhưng
không cài hay chạy chương trình phụ trên Pi2.

## Cách thực thi

Mỗi trial tạo một connection mới. Sau đó chương trình mở đủ số stream của kịch
bản, giữ một Bash thường trực trên từng stream và gửi thẳng năm lệnh sau:

```text
ls -1 /usr/bin | head -n 30
df -h /
free -m
ps -eo pid,comm,%cpu,%mem --sort=pid | head -n 30
uptime
```

Tầng workload không có JSON, Base64 hoặc `remote_agent.py`. Mỗi dòng gửi xuống
gồm lệnh thật và hai dấu mốc văn bản ngắn. Dấu bắt đầu phân tách output; dấu hoàn
thành mang mã thoát của lệnh. Đây chỉ là biên đo, không phải một chương trình mô
phỏng tác nhân. Riêng cầu nối cục bộ của SSH3 trong `stream_mux` vẫn đóng gói byte
giữa Python và tiến trình Go; đó là chi tiết transport dùng chung, không có
chương trình nhận lệnh tương ứng trên Pi2.

Với SSH và SSH3, mỗi vai trò có một Bash và một stream riêng. Với Mosh, giao thức
chỉ có một terminal vật lý nên các vai trò là tác vụ nền chạy đồng thời trong
cùng Bash. Chúng không được ghi là nhiều stream vận chuyển.

## Số mẫu

Mặc định một trial chạy 20 vòng năm lệnh, tức 100 mẫu trên mỗi vai trò. Mười
trial tạo 1.000 mẫu cho cùng một `stream_role` của từng tổ hợp
`giao thức × kịch bản`:

```env
TRIALS_PER_COMBINATION=10
SAMPLES_PER_STREAM_PER_TRIAL=100
MOSH_CONTINUE_AFTER_TIMEOUT=1
```

`SAMPLES_PER_STREAM_PER_TRIAL` phải là bội số dương của 5. Trong mỗi vai trò,
lệnh sau chỉ được gửi khi đã nhận dấu hoàn thành của lệnh trước. Các vai trò
trong cùng trial bắt đầu qua một hàng rào đồng bộ và chạy đồng thời.

Với `MOSH_CONTINUE_AFTER_TIMEOUT=1`, một marker Mosh bị timeout chỉ làm hỏng
mẫu tương ứng; runner tiếp tục gửi mẫu kế tiếp thay vì tự động bỏ qua toàn bộ
phần còn lại của vai trò. Thống kê ghi riêng số mẫu dự kiến, mẫu thực sự đã gửi,
timeout và skipped.

Các kịch bản được cấu hình trong workload này:

- `W1-S1`: một vai trò lệnh.
- `W1-S2`: hai vai trò lệnh.
- `W1-S4`: bốn vai trò lệnh.

`src/constants.py` ánh xạ kịch bản sang 1, 2 hoặc 4. `SCENARIOS` trong
`config.env` chọn kịch bản cần chạy; phần `stream_mux` không chứa quy tắc riêng
của W1.

## Phép đo

`latency_ms` bắt đầu ngay trước khi Pi1 ghi dòng lệnh thật vào stream và kết thúc
khi Pi1 đọc được đúng dấu hoàn thành của lệnh đó. Khoảng này gồm thời gian truyền
lệnh, thực thi trên Pi2 và truyền output/dấu hoàn thành về Pi1.

`setup_ms` bắt đầu ngay trước khi mở connection, gồm thời gian mở đủ stream và
kết thúc sau khi mỗi Bash vật lý phản hồi một lệnh kiểm tra rỗng. Warm-up và các
mẫu workload không nằm trong `setup_ms`.

Các trường output cần được hiểu như sau:

- SSH/SSH3 là luồng byte có thứ tự. Khi nhận đủ dấu bắt đầu và hoàn thành,
  `output_verifiable=1` và `output_complete=1`.
- Mosh truyền trạng thái màn hình, không truyền một luồng byte nguyên bản. Với
  W1-S2/W1-S4, output của nhiều tác vụ còn có thể xen nhau. Vì vậy bản trực tiếp
  vẫn đo chính xác thời điểm hoàn thành bằng dấu riêng, nhưng đặt
  `output_verifiable=0` và không dùng băm output để khẳng định tính toàn vẹn.
- `received_sha256` chỉ là băm output Pi1 đã quan sát. `expected_sha256` để trống
  vì Pi2 không chạy chương trình phụ để tính băm độc lập.

Điểm này rất quan trọng khi so sánh với `w1-mux`: bản cũ mô phỏng tác nhân và có
thể xác minh byte/băm ở hai đầu; bản `w1-mux-tt` giảm lớp xử lý đó để đo đường
gửi lệnh trực tiếp.

## Multi-stream của ba giao thức

- SSH: một ControlMaster, mỗi vai trò là một session channel.
- SSH3: một lần kết nối, mỗi vai trò là một QUIC stream hai chiều thật; mọi vai
  trò dùng chung một ConversationStreamID và một UDP socket.
- Mosh: một terminal session và một UDP socket; 1/2/4 chỉ là số tác vụ logic
  chạy đồng thời trong terminal đó.

Quy tắc mở connection/stream nằm ở `../stream_mux/connection/`. Mọi thay đổi
chuyên biệt cho W1 trực tiếp nằm trong `w1-mux-tt/src/`.

## Chạy

Trên Pi1:

```bash
cd ~/SSH-SCRIPT/w1-mux-tt
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p artifacts
bash run_w1.sh config.env 2>&1 | tee artifacts/full_run.log
```

Smoke test cả ba giao thức, mỗi tổ hợp một trial và năm mẫu mỗi vai trò:

```bash
cd ~/SSH-SCRIPT/w1-mux-tt
TRIALS_PER_COMBINATION=1 \
SAMPLES_PER_STREAM_PER_TRIAL=5 \
WARMUP_SECONDS=0 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=/tmp/w1-smoke-all \
bash run_w1.sh config.env
```

Kết quả chính nằm trong:

```text
artifacts/results/samples.csv
artifacts/results/streams.csv
artifacts/results/trials.csv
artifacts/results/stream_audit.csv
artifacts/results/command_summary.csv
artifacts/results/scenario_summary.csv
artifacts/results/stream_summary.csv
artifacts/results/metadata.json
artifacts/results/congestion/summary.csv
```

Raw congestion nằm trong `artifacts/results/congestion/client|server`; không
tạo thư mục terminal log riêng.

## Cấu trúc

```text
w1-mux-tt/
├── config.env
├── config.example.env
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
    ├── plot_w1.py
    └── verify_ssh3_mux.py
```
