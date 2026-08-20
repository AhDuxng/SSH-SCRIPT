# Chạy W4 trên Raspberry Pi

## Đồng bộ và chuẩn bị

```bash
rsync -av \
  --exclude '.venv/' --exclude 'artifacts/' --exclude 'pi_runs/' \
  --exclude '__pycache__/' \
  ~/SSH-SCRIPT/ pi@PI1:~/SSH-SCRIPT/

cd ~/SSH-SCRIPT/w4-mux-tt
cp config.example.env config.env
nano config.env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Smoke test đủ 18 tổ hợp

```bash
set -o pipefail

TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0.5 \
KEY_INTERVAL_SECONDS=0.02 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=/tmp/w4-smoke \
bash run_w4.sh config.env

echo "exit_code=$?"
```

Kiểm tra nhanh:

```bash
column -s, -t < /tmp/w4-smoke/trials.csv
column -s, -t < /tmp/w4-smoke/scenario_summary.csv
column -s, -t < /tmp/w4-smoke/background_summary.csv
column -s, -t < /tmp/w4-smoke/stream_audit.csv
```

## Vẽ hình

```bash
.venv/bin/python tools/plot_w4.py \
  /tmp/w4-smoke /tmp/w4-smoke/figures --network smoke
```

## Chạy chính thức

Giữ warm-up 5 giây, interval 0,2 giây và số trial trong `config.env`:

```bash
set -o pipefail
bash run_w4.sh config.env
echo "exit_code=$?"
```

Runner tự ghi `artifacts/full_run.log`; không nối thêm `| tee`.
