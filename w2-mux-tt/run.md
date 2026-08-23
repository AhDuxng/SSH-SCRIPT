# Hướng dẫn vận hành W2 trực tiếp

## Đẩy mã lên Pi1

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT
ssh trungnt@100.76.167.75 'mkdir -p ~/SSH-SCRIPT'
rsync -av \
  --exclude '.build/' \
  --exclude 'bin/' \
  --exclude '.venv/' \
  --exclude 'artifacts/' \
  --exclude 'pi_runs/' \
  --exclude '__pycache__/' \
  stream_mux w2-mux-tt \
  trungnt@100.76.167.75:~/SSH-SCRIPT/
```

Không cần chép payload từ macOS. Pi1 tự tạo đúng payload bằng script đã ghim rồi
triển khai sang Pi2 trước thí nghiệm.

## Tạo venv trên Pi1

```bash
ssh trungnt@100.76.167.75
cd ~/SSH-SCRIPT/w2-mux-tt

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## Smoke test

```bash
cd ~/SSH-SCRIPT/w2-mux-tt

TRIALS_PER_COMBINATION=1 \
SAMPLES_PER_STREAM_PER_TRIAL=5 \
WARMUP_SECONDS=0 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=/tmp/w2-smoke \
bash run_w2.sh config.env
```

Kiểm tra:

```bash
column -s, -t < /tmp/w2-smoke/trials.csv
column -s, -t < /tmp/w2-smoke/stream_audit.csv
column -s, -t < /tmp/w2-smoke/scenario_summary.csv
```

SSH và SSH3 phải đạt 100% byte, dòng và SHA-256. Mosh dùng
`terminal_content_reconstruction` và cũng chỉ hoàn tất khi đủ 102.400 byte cùng
SHA-256 canonical. SSH3 W2-S4 phải có bốn StreamID khác nhau, một
ConversationStreamID và một UDP socket. Mosh vẫn có thể là `partial` nếu
screen-state không cung cấp đủ dòng; phần byte đã xác thực vẫn phải được ghi.
Đọc `verified_bytes` và `content_coverage_pct` trong
`transfers.csv` để xem độ bao phủ nội dung của từng phép truyền và
`mean_content_coverage_pct` trong `scenario_summary.csv` để so sánh độ bao phủ
nội dung theo kịch bản. `raw_byte_ratio_pct` chỉ dùng phát hiện Mosh vẽ lại hoặc
lặp output; `verified_byte_ratio_pct`, SHA-256 và `output_complete` mới là xác
thực toàn vẹn chính xác.

Mosh vẫn chỉ có một terminal. Runner dựng lại viewport theo ANSI, chờ mọi role
báo `DONE` và màn hình ổn định rồi mới xác thực nội dung. Xem thêm
`command_visible_*` trong `scenario_summary.csv`: đây là độ trễ người dùng thấy
lệnh kết thúc, được báo cáo tách biệt với lossless-output completion.

## Chạy chính thức

`config.env` đã đặt 10 trial và 100 mẫu trên mỗi stream trong từng trial:

```bash
cd ~/SSH-SCRIPT/w2-mux-tt
bash run_w2.sh config.env
```

Runner tự ghi `artifacts/full_run.log`; không nối thêm `| tee`.

Vẽ hình sau khi chạy:

```bash
.venv/bin/python tools/plot_w2.py \
  artifacts/results artifacts/results/figures --network medium
```
