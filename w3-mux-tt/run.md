# Hướng dẫn chạy W3 trên Raspberry Pi

## Đồng bộ source lên Pi1

```bash
rsync -av \
  --exclude '.venv/' \
  --exclude 'artifacts/' \
  --exclude 'pi_runs/' \
  --exclude '__pycache__/' \
  ~/SSH-SCRIPT/ pi@PI1:~/SSH-SCRIPT/
```

## Chuẩn bị

Trên Pi2 cần có `vim`, `nano`, `tmux`, SSH server, SSH3 server và mosh-server.

```bash
cd ~/SSH-SCRIPT/w3-mux-tt
cp config.example.env config.env
nano config.env

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## Smoke test

```bash
mkdir -p artifacts

TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0.5 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-all \
LOG_DIR=artifacts/smoke-all/logs \
bash run_w3.sh config.env 2>&1 | tee artifacts/smoke-all.log
```

Kiểm tra:

```bash
column -s, -t < artifacts/smoke-all/trials.csv
column -s, -t < artifacts/smoke-all/scenario_summary.csv
column -s, -t < artifacts/smoke-all/stream_audit.csv
```

Smoke riêng phép đo từng Mosh pane sau khi đổi thiết kế:

```bash
PROTOCOLS=mosh \
EDITORS=vim,nano \
SCENARIOS=W3-I2,W3-I4 \
TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0.5 \
KEY_INTERVAL_SECONDS=0.02 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-mosh-panes \
LOG_DIR=artifacts/smoke-mosh-panes/logs \
bash run_w3.sh config.env 2>&1 | tee artifacts/smoke-mosh-panes.log
```

Cuối smoke test, verifier phải báo `independent Mosh pane timing passed`.

## Vẽ smoke test

```bash
.venv/bin/python tools/plot_w3.py \
  artifacts/smoke-all artifacts/smoke-all/figures --network smoke
```

## Chạy chính thức

Giữ `TRIALS_PER_COMBINATION=10`, warm-up 5 giây và interval 0,2 giây:

Mosh I2/I4 lần lượt chọn từng pane rồi đo riêng; thao tác chọn pane hoàn tất
trước `send_ns` và không nằm trong keystroke latency.

```bash
bash run_w3.sh config.env 2>&1 | tee artifacts/full_run.log
```

Vẽ theo network profile thật:

```bash
.venv/bin/python tools/plot_w3.py \
  artifacts/results artifacts/figures --network high
```
