# W2 minimal — Large Output

Benchmark khả năng truyền và hiển thị output lớn qua SSH, SSH3 và Mosh trong
các điều kiện mạng do người chạy gắn nhãn bằng `NETWORK_PROFILE`.

Các workload mặc định:

```text
find /usr
docker logs $(docker ps -q | head -n 1)
journalctl --no-pager
cat /tmp/w2_large_file.txt
```

`large_file.txt` mặc định được tạo trước thí nghiệm với kích thước chính xác
16 MiB. Có thể đổi đường dẫn và kích thước trong `config.env`.

## Thiết kế

- Mỗi tổ hợp `protocol × workload` chạy số connection độc lập do
  `TRIALS_PER_COMBINATION` quy định.
- Mỗi complete block chứa đủ mọi tổ hợp rồi được xáo trộn bằng `RANDOM_SEED`.
- Mỗi trial mở một session mới và đo session setup đến prompt shell đầu tiên.
- Sau warm-up, runner gửi một workload và đọc stream tăng dần đến marker duy
  nhất chứa exit code của lệnh; toàn bộ output không bị giữ trong bộ nhớ.
- Input echo được tắt nên marker không thể match từ dòng lệnh gửi đi.
- Exit code khác 0 được ghi `command_error` và không đưa vào metric thành công.
- `MAX_OUTPUT_LINES=0` là chế độ chuẩn, nhận toàn bộ output không cắt ngắn.

Metric chính gồm completion latency, output bytes, số dòng, throughput MiB/s,
success rate, min/mean/median/P50/P90/P95/P99/max, standard deviation, CI95 và
từng loại lỗi. Biểu đồ luôn ghi success rate ngay trên cột.

## Cài và chạy

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w2_minimal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
# Sửa SERVER_USER, SERVER_HOST, endpoint SSH3 và lệnh Docker nếu cần.
bash run_w2.sh config.env
```

Máy đích cần có `find`, `journalctl`, `docker` nếu đo Docker, và `yes`, `head`,
`wc` để chuẩn bị file. User từ xa phải có quyền đọc journal và Docker logs.

Các lệnh vận hành chi tiết nằm trong [`run.md`](run.md).

## Cấu trúc

```text
w2_minimal/
├── run_w2.sh                 # entry point và chuẩn bị target
├── config.example.env        # cấu hình mẫu
├── scripts/
│   └── prepare_large_file.sh # tạo file text đúng kích thước trên target
├── src/
│   ├── run_w2.py             # scheduler và raw CSV
│   ├── trial.py              # một phép đo output lớn
│   ├── workloads.py          # ánh xạ và bọc command
│   ├── protocol_runner.py    # session SSH, SSH3 và Mosh
│   ├── terminal_io.py        # incremental stream reader
│   ├── config.py             # đọc cấu hình
│   └── constants.py          # workload và schema CSV
├── tools/
│   ├── analyze_w2.py         # thống kê workload và setup
│   └── plot_w2.py            # hình PNG/PDF
├── tests/                    # kiểm thử scheduler, marker và analyzer
└── artifacts/
    ├── results/              # CSV và metadata
    └── figures/              # PNG/PDF
```

## Kết quả

```text
artifacts/results/experiment_order.csv
artifacts/results/samples.csv
artifacts/results/setup_samples.csv
artifacts/results/summary.csv
artifacts/results/setup_summary.csv
artifacts/results/metadata.json
artifacts/figures/*.png
artifacts/figures/*.pdf
```

