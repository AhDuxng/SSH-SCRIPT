# Lệnh chạy W3 minimal

Chạy từ thư mục dự án:

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT/w3_minimal
```

## Cài lần đầu

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp config.example.env config.env
bash scripts/build_ssh3_mux.sh
```

Giữ `.venv`; `run_w3.sh` đang dùng `.venv/bin/python`. Sửa `SERVER_USER`, `SERVER_HOST` và các tham số SSH3 trong `config.env` cho thiết bị đích. File này được Git bỏ qua nên có thể cấu hình riêng trên Mac và Pi mà không gây xung đột khi pull.

## Chạy thí nghiệm đầy đủ

`config.env` hiện chỉ đo Vim và Nano, chạy 3 trial độc lập cho mỗi tổ hợp; mỗi trial đo đủ 80 ký tự. Tổng cộng: `3 giao thức × 2 editor × 3 mức tải × 3 trial = 54 trial`.

```bash
bash run_w3.sh config.env
```

Chạy số connection khác mà không sửa config:

```bash
TRIALS_PER_COMBINATION=10 bash run_w3.sh config.env
```

Muốn chạy 5 trial cho mỗi tổ hợp (tổng 90 trial):

```bash
TRIALS_PER_COMBINATION=5 bash run_w3.sh config.env
```

Chạy một phần ma trận:

```bash
PROTOCOLS=ssh3 TARGETS=vim PROFILE_NAMES=c0_bg4 \
TRIALS_PER_COMBINATION=1 bash run_w3.sh config.env
```

Lưu ý: mỗi lần chạy sẽ tạo mới các CSV trong `RESULT_DIR`. Muốn smoke test mà không ghi đè dữ liệu chính, dùng thư mục tạm:

```bash
PROTOCOLS=ssh3 TARGETS=vim PROFILE_NAMES=c0_bg4 \
TRIALS_PER_COMBINATION=1 RESULT_DIR=/private/tmp/w3_results \
LOG_DIR=/private/tmp/w3_logs bash run_w3.sh config.env
```

## Chạy lại phân tích

```bash
.venv/bin/python tools/analyze_w3.py \
  artifacts/results/samples.csv \
  artifacts/results/summary.csv
```

Bốn hình có thể tạo từ thống kê gộp mẫu thành công:

```bash
.venv/bin/python tools/plot_w3.py artifacts/results/summary.csv artifacts/figures --metric mean
.venv/bin/python tools/plot_w3.py artifacts/results/summary.csv artifacts/figures --metric median
.venv/bin/python tools/plot_w3.py artifacts/results/summary.csv artifacts/figures --metric p90
.venv/bin/python tools/plot_w3.py artifacts/results/summary.csv artifacts/figures --metric p95
```

Mỗi lệnh tạo một hình PNG và PDF, với SSH, SSH3 và Mosh chung một biểu đồ.

## Xác minh SSH3 nhiều stream

```bash
.venv/bin/python tools/verify_ssh3_mux.py artifacts/results
```

Kết quả hợp lệ phải có dạng:

```text
SSH3 multiplex verification PASSED: ... one UDP socket, one conversation, unique stream per role
```

## Kiểm thử code

```bash
PYTHONPYCACHEPREFIX=/private/tmp/w3_pycache \
  .venv/bin/python -m py_compile src/*.py tools/*.py
PYTHONPYCACHEPREFIX=/private/tmp/w3_pycache \
  .venv/bin/python -m unittest discover -s tests -v
bash -n run_w3.sh scripts/*.sh
```

## File kết quả chính

```text
artifacts/results/experiment_order.csv   # thứ tự trial đã random
artifacts/results/samples.csv            # từng ký tự, có char_index/char_total
artifacts/results/trials.csv             # thống kê từng connection
artifacts/results/trial_analysis.csv     # thống kê từng connection sau analyze
artifacts/results/summary.csv            # mean/median/P90/P95 gộp mẫu thành công
artifacts/results/connection_audit.csv   # bằng chứng TCP/UDP connection
artifacts/results/channel_counters.csv   # byte nhận theo channel/stream
artifacts/results/ssh3_stream_audit.csv  # stream ID, conversation ID và READY
```
