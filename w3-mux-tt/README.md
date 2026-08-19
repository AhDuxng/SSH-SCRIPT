# W3 – Multiplexed Interactive Editing

Benchmark đo độ trễ từng phím khi 1, 2 hoặc 4 phiên Vim/Nano hoạt động đồng
thời trong cùng một connection. Thiết kế bám các mục 3, 5, 6 và 7 của
`Thiết kế thí nghiệm.pdf`.

## Kiến trúc được kiểm chứng

```text
SSH: 1 TCP connection (ControlMaster)
 ├─ interactive_0 → SSH PTY channel
 ├─ interactive_1 → SSH PTY channel
 ├─ interactive_2 → SSH PTY channel
 └─ interactive_3 → SSH PTY channel

SSH3: 1 QUIC connection + 1 conversation
 ├─ interactive_0 → QUIC bidirectional PTY stream
 ├─ interactive_1 → QUIC bidirectional PTY stream
 ├─ interactive_2 → QUIC bidirectional PTY stream
 └─ interactive_3 → QUIC bidirectional PTY stream

Mosh: 1 UDP terminal session
 ├─ I1: interactive_0 → editor process trực tiếp
 └─ I2/I4: tmux panes được chọn độc lập theo round-robin
     ├─ interactive_0 → editor process
     ├─ interactive_1 → editor process
     ├─ interactive_2 → editor process
     └─ interactive_3 → editor process
```

Mosh không được mô tả như có channel/stream transport. I1 mở editor trực tiếp
giống baseline `w3_minimal`; I2/I4 là nhiều editor
process thật cùng cạnh tranh trong một terminal session; `transport_semantics`
ghi `tmux_pane_in_terminal`. `synchronize-panes` bị tắt. Runner lần lượt chọn
từng pane qua chính terminal Mosh, chờ pane được chọn và repaint ổn định, rồi
mới bắt đầu đồng hồ và gửi phím cho riêng pane đó. Hình học pane được phát thành
marker ngay trong terminal Mosh trước warm-up; runner không mở SSH phụ khi
workload đang chạy. Trial dùng một tmux server/socket riêng và các phím chọn
pane F5–F8 chỉ được bind trong server riêng đó, không sửa tmux của người dùng.

## Workload

- Editor: Vim và Nano.
- Kịch bản: `W3-I1`, `W3-I2`, `W3-I4`.
- Mỗi editor bắt đầu với file rỗng và soạn cùng chương trình C 100 ký tự,
  gồm cả 6 phím Enter.
- SSH/SSH3 đồng bộ các stream trước từng ký tự. Mosh I2/I4 đo các pane theo
  round-robin cho từng ký tự rồi xoay pane bắt đầu ở ký tự kế tiếp để tránh
  thiên lệch thứ tự; không gõ hết file ở một pane trước pane khác.
- Sau warm-up 5 giây, editor được repaint, lịch sử parser được xóa và workload
  mới bắt đầu.
- Trước từng phím, reader chờ terminal hết repaint cũ rồi xóa event history,
  tương đương `drain_pending_output()` của `w3_minimal`; bước này nằm ngoài
  khoảng `t_send → t_render`.

Với mỗi ký tự:

```text
keystroke_latency = t_render - t_send
```

`t_render` chỉ được chốt khi parser VT100/xterm thấy đúng ký tự được ghi tại ô
con trỏ đã chụp. Với Mosh I2/I4, mỗi pane được chọn làm active trước phép đo;
chi phí chọn pane và repaint nằm ngoài `t_send → t_render`. Vì vậy cả ký tự
thường lẫn Enter đều có `send_ns` và `render_ns` độc lập cho từng pane. Thời
điểm được lấy ngay khi chunk terminal đến client, không lấy lúc tiến trình
Python xử lý CSV.

- Latency `> 1 s` và `< 2 s`: stall nhưng vẫn hoàn thành.
- Không render trước `2 s`: timeout.
- Nghỉ `0,2 s` trước ký tự tiếp theo.
- Mosh mặc định `--predict=always`; CSV ghi `measurement_mode=local_prediction`
  cho I1 và `local_prediction_selected_pane` cho I2/I4 để phân biệt dữ liệu mới
  đo từng pane, đồng thời không diễn giải latency dự đoán cục bộ như remote echo.
- Runner bắt buộc `MOSH_PREDICT=always` và in giá trị hiệu lực trong `[PLAN]`;
  nếu config hoặc biến môi trường đặt `adaptive/never`, thí nghiệm dừng ngay.
  Với I2/I4, mỗi lần chỉ pane đang được chọn nhận phím; local prediction vì thế
  được đánh giá trên đúng pane người dùng đang tương tác.

Sau ký tự thứ 100, trial kết thúc và đóng editor/session mà không lưu hay kiểm
tra file đầu ra. W3 chỉ đánh giá phản hồi tương tác giống `w3_minimal`; tính đầy
đủ output không tham gia status hoặc thống kê.

## Dữ liệu đầu ra

```text
experiment_order.csv  thứ tự randomized complete blocks
keystrokes.csv        một dòng cho từng ký tự của từng interactive role
streams.csv           kết quả từng stream trong từng trial
trials.csv            tổng hợp và connection audit từng trial
stream_audit.csv      socket, StreamID, conversation ID và semantics
scenario_summary.csv  tổng hợp protocol × editor × scenario
stream_summary.csv    tổng hợp riêng interactive_0…interactive_3
ssh3_vs_ssh.csv       tỷ số median SSH3/SSH và cảnh báo >5%
metadata.json         probe, SHA, ngưỡng và cấu hình phương pháp
```

`stream_complete=1` khi đủ 100 phím đã render trước timeout. Stall vẫn là
keystroke hoàn thành nhưng được báo riêng.
Mean/Median/P95/P99 chỉ dùng các phím đã render (gồm cả stall); timeout không
được gán latency giả mà được thể hiện bằng completion/timeout rate.

`tools/verify_mux.py` còn bắt buộc mỗi mẫu Mosh I2/I4 có một `send_ns` riêng
cho từng pane, `measurement_mode=local_prediction_selected_pane` và xác nhận
render tại cursor của pane đang được chọn. Dữ liệu kiểu broadcast cũ sẽ không
đạt verifier này.

Khi `LIVE_PROGRESS=1`, terminal in ngay một dòng `[LIVE]` cho từng ký tự của
từng interactive role, gồm vị trí trong source, token, trạng thái, latency,
stall và cursor. `LIVE_PROGRESS_EVERY=1` in đủ 100 dòng/stream; có thể đặt 10
để giảm log, nhưng mẫu đầu/cuối và mọi lỗi vẫn luôn được in.

## Chạy nhanh

```bash
cp config.example.env config.env
# sửa SERVER_HOST và key/cổng nếu cần

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

TRIALS_PER_COMBINATION=1 \
WARMUP_SECONDS=0.5 \
INTER_TRIAL_DELAY_SECONDS=0 \
RESULT_DIR=artifacts/smoke-all \
LOG_DIR=artifacts/smoke-all/logs \
bash run_w3.sh config.env 2>&1 | tee artifacts/smoke-all.log
```

Smoke đầy đủ có `3 protocol × 2 editor × 3 scenario = 18 trial`.

## Vẽ hình

```bash
.venv/bin/python tools/plot_w3.py \
  artifacts/smoke-all artifacts/smoke-all/figures --network low
```

Các hình `figure_1_{vim,nano}_per_stream_latency_{mean,median,p95,p99}` dùng
cùng bố cục ba panel I1/I2/I4, màu và hatch như W1. Mỗi role có cột SSH channel,
SSH3 stream và Mosh pane tương ứng. Nhãn hình vẫn ghi rõ các pane Mosh cùng nằm
trong một terminal, không mô tả pane như transport stream độc lập.
Ngoài ra có latency tổng hợp theo scenario, reliability và setup latency.

## Chạy chính thức

Mặc định là 10 trial cho mỗi tổ hợp, tương đương 180 trial. Mỗi stream gõ một
lượt toàn bộ probe C 100 ký tự; mỗi ký tự là một sample độc lập. Payload chuẩn:

```text
bytes=100
characters=100
lines=6
sha256=13a17464f650cd3d831c1433a226d4895555f56ce8cd52a13f8f3841a0bbd430
```

```bash
bash run_w3.sh config.env 2>&1 | tee artifacts/full_run.log
```
