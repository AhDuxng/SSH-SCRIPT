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

`config.env` chỉ đo Vim và Nano; số connection độc lập của mỗi tổ hợp do `TRIALS_PER_COMBINATION` quy định. Mỗi connection đo đủ 80 ký tự. Tổng trial là `3 giao thức × 2 editor × 3 mức tải × TRIALS_PER_COMBINATION`.

Runner mặc định nghỉ `INTER_TRIAL_DELAY_SECONDS=3.00` sau khi đóng mỗi connection và trước trial kế tiếp. Cooldown này áp dụng cho cả SSH, SSH3 và Mosh.

Với `RUN_SESSION_SETUP=1`, cuối lượt chạy runner tự mở thêm `SETUP_TRIALS`
phiên độc lập cho mỗi giao thức và lưu `setup_samples.csv` cùng
`setup_summary.csv`. Đặt `RUN_SESSION_SETUP=0` chỉ khi chủ động muốn bỏ phép đo
setup.

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

## Thống kê và vẽ thời gian session setup

Phép đo này độc lập với thí nghiệm Vim/Nano và dùng cùng định nghĩa với
`test-w1`: từ sau khi spawn client đến khi nhận shell prompt đầu tiên. Mỗi mẫu
mở một phiên mới; không dùng ControlMaster, không mở tải nền và không mở editor.
`run_w3.sh` tự chạy bước này khi `RUN_SESSION_SETUP=1`; lệnh dưới đây dùng khi
cần chạy lại riêng phần setup mà không chạy lại 180 trial tương tác.

```bash
.venv/bin/python tools/measure_session_setup.py config.env

.venv/bin/python tools/plot_setup.py \
  artifacts/results/setup_summary.csv \
  artifacts/figures \
  --metric median
```

Lệnh đo tạo `setup_samples.csv` và `setup_summary.csv`; lệnh vẽ tạo
`figure_5_session_setup_median.png` và PDF tương ứng. `SETUP_TRIALS` trong
`config.env` quy định số phiên độc lập của mỗi giao thức.

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
artifacts/results/setup_samples.csv       # từng phép đo mở phiên mới
artifacts/results/setup_summary.csv       # thống kê session setup theo connection
artifacts/results/trial_analysis.csv     # thống kê từng connection sau analyze
artifacts/results/summary.csv            # mean/median/P90/P95 gộp mẫu thành công
artifacts/results/connection_audit.csv   # bằng chứng TCP/UDP connection
artifacts/results/channel_counters.csv   # byte nhận theo channel/stream
artifacts/results/ssh3_stream_audit.csv  # stream ID, conversation ID và READY
```
