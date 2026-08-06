# W2 minimal — Continuous monitoring

Benchmark độ trễ hiển thị của các sự kiện liên tục qua SSH, SSH3 và Mosh. Thiết
kế lấy mẫu được rút gọn từ `test-w2`: mỗi connection thu nhiều marker có timestamp
phía server thay vì coi toàn bộ một lệnh output lớn là một mẫu.

## Workload

- `top`: màn hình giám sát được làm mới định kỳ; Mosh dùng marker dạng dòng như
  `test-w2` để tránh raw PTY redraw làm hỏng timestamp.
- `tail`: writer thêm dòng timestamp vào file tạm và client chạy `tail -f`.
- `ping`: lấy timestamp `ping -D` ở đầu từng dòng reply.

## Một trial

1. Mở một connection mới và đo session setup đến prompt đầu tiên.
2. Dùng nhiều round-trip probe để ước lượng chênh lệch clock Pi1–Pi2.
3. Khởi động workload liên tục và bỏ `WARMUP_SAMPLES` marker đầu.
4. Ghi đúng `SAMPLES_PER_TRIAL` marker tiếp theo vào `samples.csv`.
5. Tính `latency = client_receive - server_event - clock_offset`.
6. Đóng connection; nghỉ rồi chuyển sang tổ hợp kế tiếp.

Regex marker cho phép ANSI, newline và backspace xen giữa từng ký tự để chịu được
cách Mosh cập nhật trạng thái màn hình. Sequence phải tăng nên cùng một marker
không được tính hai lần.

Nếu trial dừng sớm, mỗi vị trí mẫu còn thiếu vẫn có một hàng timeout/failure. Do
đó success rate luôn dùng đúng mẫu số cấu hình, không chỉ đếm mẫu thành công.

## Cài và chạy

Trên client:

```bash
cd ~/SSH-SCRIPT/w2_minimal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
bash run_w2.sh config.env 2>&1 | tee artifacts/full_run.log
```

Target cần `bash`, GNU `date`, `ping`, `tail`, `sleep` và endpoint của ba giao
thức. Docker, journalctl và file 16 MiB không còn thuộc W2 này.

## Kết quả

```text
artifacts/results/experiment_order.csv
artifacts/results/samples.csv
artifacts/results/setup_samples.csv
artifacts/results/clock_offsets.csv
artifacts/results/trials.csv
artifacts/results/summary.csv
artifacts/results/setup_summary.csv
artifacts/results/metadata.json
artifacts/figures/*.png
artifacts/figures/*.pdf
```

`clock_offsets.csv` là dẫn chứng cho số probe hợp lệ, offset và median RTT của
từng connection. Mỗi hình latency ghi cả success rate trên từng cột.
