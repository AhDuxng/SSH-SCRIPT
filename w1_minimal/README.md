# W1 minimal — Command Loop

Benchmark độ trễ vòng điều khiển tuần tự qua SSH, SSH3 và Mosh.

Trong mỗi loop, runner gửi đúng năm lệnh theo thứ tự:

```text
ls → df -h → free -m → ps aux → uptime
```

Lệnh sau chỉ được gửi khi client đã nhận prompt của shell sau lệnh trước. Vì
PTY truyền output theo thứ tự, prompt riêng này là mốc xác nhận toàn bộ kết quả
của lệnh đã về client. Input echo được tắt trước khi đo để prompt không thể bị
match từ chính dòng lệnh gửi đi.

Mỗi trial là một session độc lập. Thời gian session setup được đo từ ngay trước
khi spawn client đến prompt shell đầu tiên. Không có background channel,
multiplex, thread, editor hay terminal-screen parser như W3.

## Cài và chạy

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w1_minimal
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
# Sửa SERVER_USER, SERVER_HOST và cấu hình SSH3 trong config.env.
bash run_w1.sh config.env
```

Danh sách lệnh vận hành đầy đủ nằm trong [`run.md`](run.md).

Có thể ghi đè cấu hình mà không sửa file:

```bash
TRIALS_PER_PROTOCOL=1 LOOPS_PER_TRIAL=2 WARMUP_LOOPS=0 \
RESULT_DIR=/private/tmp/w1_results bash run_w1.sh config.env
```

## Cách đo

- `latency_ms`: từ ngay trước khi gửi một lệnh đến khi nhận prompt kế tiếp.
- `loop_latency_ms`: từ lúc gửi `ls` đến khi nhận prompt sau `uptime`.
- `session_setup_ms`: từ trước khi spawn client đến prompt shell đầu tiên;
  phần cài prompt đo lường không nằm trong metric này.
- Warm-up vẫn được ghi vào CSV với `warmup=1` nhưng không đi vào summary.
- Timeout/lỗi được giữ trong CSV và completion rate, không đưa vào latency.
- Mosh mặc định dùng `--predict=always`, nhưng phép đo này chờ output/prompt từ
  server chứ không dừng ở local prediction.

Mỗi summary có số mẫu/phiên, success rate, min, mean, median, P50, P90, P95,
P99, max, standard deviation, CI 95% của mean và số lỗi theo từng loại.

## Kết quả

```text
artifacts/results/experiment_order.csv  # thứ tự trial thực tế
artifacts/results/samples.csv           # từng lệnh trong từng loop
artifacts/results/loops.csv             # kết quả trọn vòng năm lệnh
artifacts/results/setup_samples.csv      # session setup của từng connection
artifacts/results/summary.csv           # metric theo protocol × command
artifacts/results/loop_summary.csv      # metric tổng thời gian vòng
artifacts/results/setup_summary.csv     # metric session setup
artifacts/results/metadata.json         # cấu hình và mô tả lượt chạy
artifacts/figures/*.png                  # hình raster
artifacts/figures/*.pdf                  # hình vector
```

`run_w1.sh` mặc định tự phân tích và vẽ mean, median, P90, P95. Có thể chạy lại:

```bash
.venv/bin/python tools/analyze_w1.py artifacts/results
.venv/bin/python tools/plot_w1.py artifacts/results artifacts/figures --metric median
```

## Cấu trúc

```text
w1_minimal/
├── run_w1.sh                 # entry point
├── config.example.env        # cấu hình mẫu
├── src/
│   ├── run_w1.py             # scheduler và ghi raw CSV
│   ├── trial.py              # vòng năm lệnh tuần tự
│   ├── protocol_runner.py    # session SSH, SSH3 và Mosh
│   ├── terminal_io.py        # prompt gating và buffer PTY
│   ├── config.py             # đọc cấu hình
│   └── constants.py          # command và schema CSV
├── tools/
│   ├── analyze_w1.py         # thống kê command, loop và setup
│   └── plot_w1.py            # hình PNG/PDF
├── tests/                    # kiểm thử scheduler và analyzer
├── artifacts/
│   ├── results/              # CSV và metadata sinh ra
│   └── figures/              # PNG/PDF sinh ra
└── .venv/                    # môi trường Python cục bộ
```
