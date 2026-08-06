# Lệnh chạy W2 minimal

## Cài client Pi1

```bash
cd ~/SSH-SCRIPT/w2_minimal
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

## Smoke test

Mỗi giao thức mở một connection cho `docker logs`; mỗi connection bỏ hai marker
warm-up và ghi năm mẫu:

```bash
PROTOCOLS=ssh,ssh3,mosh \
WORKLOADS=docker_logs \
TRIALS_PER_COMBINATION=1 \
SAMPLES_PER_TRIAL=5 \
WARMUP_SAMPLES=2 \
AUTO_PLOT=0 \
RESULT_DIR=/tmp/w2_smoke \
bash run_w2.sh config.env 2>&1 | tee /tmp/w2_smoke.log
```

Kết quả đúng: ba trial đạt `sample=005/005` và `samples.csv` có 15 hàng dữ liệu.

## Chạy đầy đủ

```bash
mkdir -p artifacts
bash run_w2.sh config.env 2>&1 | tee artifacts/full_run.log
```

Cấu hình mặc định tạo `3 protocol × 4 workload × 10 connection = 120
connection`; mỗi connection ghi 100 mẫu, tổng cộng 12.000 mẫu.

## Phân tích và vẽ lại

```bash
.venv/bin/python tools/analyze_w2.py artifacts/results

for metric in mean median p90 p95; do
  .venv/bin/python tools/plot_w2.py \
    artifacts/results artifacts/figures --metric "$metric"
done
```

## Kiểm tra source

```bash
PYTHONPYCACHEPREFIX=/tmp/w2_pycache \
  .venv/bin/python -m py_compile src/*.py tools/*.py
PYTHONPYCACHEPREFIX=/tmp/w2_pycache \
  .venv/bin/python -m unittest discover -s tests -v
bash -n run_w2.sh scripts/*.sh
```
