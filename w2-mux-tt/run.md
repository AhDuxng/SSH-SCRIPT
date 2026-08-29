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

Mosh vẫn chỉ có một terminal, nhưng từ W2-S2 trở lên mỗi vai trò chạy trong một
tmux pane riêng nên output của chúng không còn trộn byte vào nhau. Runner dựng
lại viewport theo ANSI và đối chiếu từng dòng ngay khi nó xuất hiện.

Ba cột latency trong `scenario_summary.csv` phải được đọc tách bạch:

- `content_complete_*` — nội dung đã đủ; metric so sánh chính cho cả ba giao thức.
- `command_visible_*` — độ trễ người dùng thấy lệnh kết thúc.
- `completion_*` — byte cuối của luồng, chỉ có nghĩa với SSH/SSH3.

Kiểm tra `content_complete_rate_pct` cùng lúc với `content_complete_median_ms`:
latency chỉ được tính trên các mẫu đạt đủ nội dung.

Nếu smoke test Mosh báo lỗi viewport, tăng `W2_MOSH_ROWS` (W2-S4 cần ít nhất
144 dòng) hoặc đặt `W2_MOSH_LAYOUT=single` để quay lại mô hình một shell dùng
chung của bản trước.

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
