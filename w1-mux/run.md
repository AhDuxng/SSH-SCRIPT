# Hướng dẫn vận hành

## Đẩy mã nguồn từ máy phát triển lên Pi1

Pi1 (`100.76.167.75`) là benchmark driver. Không sao chép `.build`, môi trường
ảo, artifacts hoặc binary SSH3 của macOS. Pi1 phải tự build binary ARM từ patch
đã ghim.

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT
ssh trungnt@100.76.167.75 'mkdir -p ~/SSH-SCRIPT'
rsync -av \
  --exclude '.build/' \
  --exclude 'bin/' \
  --exclude '.venv/' \
  --exclude 'artifacts/' \
  --exclude '__pycache__/' \
  stream_mux w1-mux \
  trungnt@100.76.167.75:~/SSH-SCRIPT/
```

`w1-mux/config.env` giữ Pi2 (`192.168.1.202`) làm `SERVER_HOST`, đúng với topology
và cấu hình W3 hiện tại.

## Chạy thí nghiệm chính thức

`config.env` đã đặt 10 trial và 100 mẫu trên mỗi stream trong mỗi trial. Tổng
cộng mỗi `stream_role` có 1.000 mẫu qua 10 connection độc lập.

```bash
cd ~/SSH-SCRIPT/w1-mux
bash run_w1.sh config.env 2>&1 | tee artifacts/full_run.log
```

Khi bắt đầu, chương trình phải in:

```text
[PLAN] trials_per_combination=10 samples_per_stream_per_trial=100 samples_per_stream_role=1000
```

Sau khi hoàn tất, kiểm tra `stream_summary.csv`: mọi dòng phải có
`expected_samples=1000` và `samples=1000`.

## Chuẩn bị Pi1 và smoke test SSH3 multiplex

Chạy trên Pi1:

```bash
ssh trungnt@100.76.167.75
cd ~/SSH-SCRIPT

sudo apt-get update
sudo apt-get install -y python3-venv python3-pip lsof procps rsync mosh golang-go
go version  # Phải là Go 1.21 trở lên.

cd w1-mux
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Kiểm tra Pi1 kết nối được tới target Pi2 trước khi đo.
ssh -i ~/.ssh/id_ed25519 trungnt@192.168.1.202 \
  'python3 --version; command -v free; command -v ps; command -v uptime'

# Build binary ARM từ Go patch dùng chung đã ghim.
cd ../stream_mux
bash scripts/build_ssh3_mux.sh
bin/ssh3-mux-stdio -h 2>&1 | grep -- '-mux-stream'

# Smoke test SSH3 với đủ 1/2/4 stream và bỏ warm-up năm giây.
cd ../w1-mux
PROTOCOLS=ssh3 \
SCENARIOS=W1-S1,W1-S2,W1-S4 \
TRIALS_PER_COMBINATION=1 \
SAMPLES_PER_STREAM_PER_TRIAL=5 \
WARMUP_SECONDS=0 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-ssh3 \
bash run_w1.sh config.env

column -s, -t < artifacts/smoke-ssh3/trials.csv
column -s, -t < artifacts/smoke-ssh3/stream_audit.csv
```

Smoke test chỉ đạt khi `verify_ssh3_mux.py` in `PASSED`, mọi trial có
`connection_valid=1`, `socket_count=1`, và W1-S4 có bốn transport StreamID khác
nhau, không rỗng nhưng chỉ có một ConversationStreamID.

Sau đó smoke test hai mô hình giao thức còn lại:

```bash
PROTOCOLS=ssh,mosh \
SCENARIOS=W1-S1 \
TRIALS_PER_COMBINATION=1 \
SAMPLES_PER_STREAM_PER_TRIAL=5 \
WARMUP_SECONDS=0 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-ssh-mosh \
bash run_w1.sh config.env
```

Chỉ build SSH3 client dùng chung:

```bash
cd /Volumes/SSD/Project/SSH-SCRIPT
bash stream_mux/scripts/build_ssh3_mux.sh
```

Chạy nhanh một ma trận smoke sau khi cấu hình target:

```bash
cd w1-mux
TRIALS_PER_COMBINATION=1 SAMPLES_PER_STREAM_PER_TRIAL=5 \
  INTER_TRIAL_DELAY_SECONDS=0 \
  bash run_w1.sh config.env
```

Tạo lại bảng tổng hợp mà không chạy lại trial:

```bash
cd w1-mux
.venv/bin/python tools/analyze_w1.py artifacts/results
```

Vẽ toàn bộ hình từ bảng tổng hợp:

```bash
.venv/bin/python tools/plot_w1.py \
  artifacts/results artifacts/figures --network low
```

`scenario_summary.csv` và các hình `figure_1_*` gộp mọi stream trong kịch bản.
`stream_summary.csv` cùng các hình `figure_4_*`, `figure_5_*` giữ riêng kết quả
của từng `command_0` đến `command_3`.

Trước khi chấp nhận một SSH3 trial, kiểm tra `stream_audit.csv`: W1-S4 phải có
bốn `stream_ids` khác nhau, đúng một `conversation_id` và
`connection_valid=true`. Go patch được ghim bằng tệp SHA-256 cạnh binary;
`run_w1.sh` sẽ build lại nếu mã băm đã cũ.
