# CLAUDE.md

File này cung cấp hướng dẫn cho Claude Code (claude.ai/code) khi làm việc với code trong repository này.

## Phạm vi

Repo này benchmark độ trễ hoàn thành lệnh và thời gian thiết lập phiên cho **SSH, SSH3 và Mosh** dưới các điều kiện mạng được mô phỏng. Công việc đang hoạt động nằm trong `test-w1/` và `set_network.sh`.

## Chạy benchmark

```bash
# Một scenario đơn lẻ (giả sử tc netem đã được áp dụng trên cả hai đầu):
cd test-w1
./run_w1_benchmark.sh low        # hoặc medium / high

# Chạy đầy đủ có điều phối (áp tc trên client + server, rồi benchmark):
cd test-w1
./run_all_scenarios.sh            # chạy low, medium, high
./run_all_scenarios.sh low medium # chạy một tập con

# Biểu đồ so sánh cross-scenario (sau khi tất cả scenario đã có kết quả):
cd test-w1
python plot_cross_scenario.py
```

## Mô phỏng mạng

`set_network.sh` bọc `tc netem` (tbf + netem). **Phải chạy trên cả client và server** để RTT = 2 × OWD.

```bash
# Áp dụng trên client VÀ server với cùng scenario:
./set_network.sh <iface> low      # RTT ≈ 20ms
./set_network.sh <iface> medium   # RTT ≈ 100ms ± 8ms, loss 1.5%
./set_network.sh <iface> high     # RTT ≈ 200ms ± 32ms, loss 3%
./set_network.sh <iface> clear    # xóa toàn bộ quy tắc tc
./set_network.sh <iface> show     # kiểm tra quy tắc hiện tại
```

## Kiến trúc

```
set_network.sh                  # wrapper tc netem (client + server)
test-w1/
  run_all_scenarios.sh          # điều phối: tc → chờ ổn định → benchmark × 3 scenario
  run_w1_benchmark.sh           # chạy một scenario: snapshot baseline → benchmark Python → plot_trend
  w1_command_loop_benchmark.py  # cốt lõi: phiên pexpect, đo độ trễ hoàn thành lệnh
  plot_trend.py                 # biểu đồ xu hướng theo trial (mean + p95) từ w1_line_log.csv
  plot_cross_scenario.py        # biểu đồ bar/box/percentile cross-scenario + CSV tổng hợp
  w1_results/<scenario>/        # output: w1_line_log.csv, w1_session_setup.csv, w1_meta.json, PNGs
  w1_results/_cross_scenario/   # output: PNGs cross-scenario + w1_cross_summary.csv
```

### Phương pháp đo lường

`w1_command_loop_benchmark.py` dùng **pexpect** để mở phiên terminal thực (không mock). Với mỗi trial:
1. Mở phiên (`ssh` / `mosh --ssh=...` / `ssh3 -privkey ...`)
2. Đặt marker `PS1` duy nhất để nhận diện prompt qua các chuỗi ANSI
3. Bọc mỗi lệnh thành `{ cmd; }; echo __MARKER__` và tính thời gian từ `sendline` đến khi khớp marker
4. Ghi `SampleRecord` (độ trễ) và `FailureRecord` (timeout/EOF) ra CSV

Thời gian thiết lập phiên = spawn → prompt shell đầu tiên (không tính PS1 export).

### Tham số chính trong `run_w1_benchmark.sh`

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `ITERATIONS` | 50 | Số mẫu mỗi trial |
| `WARMUP` | 3 | N mẫu đầu bị loại khỏi tổng hợp |
| `TRIALS` | 10 | Số phiên độc lập mỗi cặp protocol/lệnh |
| `MOSH_PREDICT` | `never` | Chế độ dự đoán local echo của Mosh |
| `SHUFFLE_PAIRS` | `true` | Xáo trộn thứ tự thực thi protocol × lệnh |

### Cấu hình kết nối (hardcoded trong script)

- `HOST=100.66.79.93`, `USER_NAME=pi`, key `~/.ssh/id_ed25519`
- SSH3 endpoint: `:4433/ssh3-term` với `-insecure`
- Source IP client: `100.70.166.91`

## Phụ thuộc

```bash
pip install pexpect matplotlib numpy
```

Yêu cầu `ssh`, `ssh3`, `mosh` và `tc` (iproute2) được cài trên cả client và server.
