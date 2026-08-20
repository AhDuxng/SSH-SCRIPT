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
TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0.5 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=/tmp/w3-smoke \
bash run_w3.sh config.env
```

Kiểm tra:

```bash
column -s, -t < /tmp/w3-smoke/trials.csv
column -s, -t < /tmp/w3-smoke/scenario_summary.csv
column -s, -t < /tmp/w3-smoke/stream_audit.csv
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
RESULT_DIR=/tmp/w3-smoke-mosh-panes \
bash run_w3.sh config.env
```

Cuối smoke test, verifier phải báo `independent Mosh pane timing passed`.

## Vẽ smoke test

```bash
.venv/bin/python tools/plot_w3.py \
  /tmp/w3-smoke /tmp/w3-smoke/figures --network smoke
```

## Chạy chính thức

Giữ `TRIALS_PER_COMBINATION=10`, warm-up 5 giây và interval 0,2 giây:

Mosh I2/I4 lần lượt chọn từng pane rồi đo riêng; thao tác chọn pane hoàn tất
trước `send_ns` và không nằm trong keystroke latency.

```bash
bash run_w3.sh config.env
```

Runner tự ghi `artifacts/full_run.log`; không nối thêm `| tee`.

Vẽ theo network profile thật:

```bash
.venv/bin/python tools/plot_w3.py \
  artifacts/results artifacts/results/figures --network high
```
