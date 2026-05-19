# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Phạm vi

Repo này benchmark độ trễ và hiệu năng của **SSH, SSH3 và Mosh** dưới các điều kiện mạng mô phỏng. Có 4 workload (W1–W4), mỗi workload nằm trong thư mục riêng.

## Chạy benchmark

```bash
# Mô phỏng mạng (phải chạy trên CẢ client VÀ server để RTT = 2 × OWD):
./set_network.sh <iface> low|medium|high|clear|show

# W1 — Command completion latency:
cd test-w1
./run_all_scenarios.sh              # low, medium, high
./run_w1_benchmark.sh <scenario>    # một scenario đơn lẻ

# W2 — Continuous monitoring (top/tail/ping):
cd test-w2
./run_all_scenarios.sh
./run_w2_benchmark.sh               # quick test (ít trial)

# W3 — Interactive keystroke latency (vim/nano/shell):
cd test-w3-1pane
./run_w3_benchmark.sh <scenario> [run_tag]

# W4 — Large output delivery latency:
cd test-w4
./run_all_scenarios.sh
./run_w4_benchmark.sh <scenario>

# Biểu đồ trend (chạy sau benchmark, trong mỗi thư mục test-wN):
python plot_trend.py --output-dir <results_dir> --prefix <wN> --group-fields protocol workload

# Biểu đồ cross-scenario (W1, W4 — sau khi có kết quả tất cả scenario):
python plot_cross_scenario.py
```

## Mô phỏng mạng

`set_network.sh` bọc `tc netem` (tbf + netem). **Phải chạy trên cả client và server** để RTT = 2 × OWD.

| Scenario | BW | OWD | Jitter | Loss | RTT (cả 2 đầu) |
|----------|-----|------|--------|------|-----------------|
| low | 100Mbps | 10ms | 0 | 0% | ~20ms |
| medium | 40Mbps | 50ms | 4ms | 1.5% | ~100ms ± 8ms |
| high | 10Mbps | 100ms | 16ms | 3% | ~200ms ± 32ms |

## Kiến trúc

```
set_network.sh                      # wrapper tc netem (client + server)

test-w1/                            # W1: command completion latency
  w1_command_loop_benchmark.py      # pexpect, đo latency từ sendline → marker match
  run_w1_benchmark.sh / run_all_scenarios.sh
  plot_trend.py / plot_cross_scenario.py
  w1_results/<scenario>/

test-w2/                            # W2: continuous monitoring (top/tail/ping)
  w2_continuous_monitoring_benchmark.py  # đo latency output liên tục, clock offset estimation
  run_w2_benchmark.sh / run_all_scenarios.sh
  plot_trend.py
  w2_results/<scenario>/

test-w3-1pane/                      # W3: interactive keystroke latency
  w3_interactive_benchmark.py       # đo echo latency cho vim/nano/shell
  run_w3_benchmark.sh
  plot_trend.py
  w3_results/<scenario>/<run_tag>/

test-w4/                            # W4: large output delivery (Time-to-Interactive)
  w4_large_output_benchmark.py      # đo thời gian nhận toàn bộ output lớn (512K–10M)
  setup_w4_fixtures.sh              # tạo fixture files trên server (chạy 1 lần trước benchmark)
  run_w4_benchmark.sh / run_all_scenarios.sh
  plot_trend.py / plot_cross_scenario.py
  w4_results/<scenario>/
```

### Phương pháp đo lường chung

Tất cả benchmark dùng **pexpect** để mở phiên terminal thực:
1. Spawn phiên (`ssh` / `mosh --ssh=...` / `ssh3 -privkey ...`)
2. Đặt PS1 marker duy nhất để nhận diện prompt qua ANSI sequences
3. Đo thời gian từ `sendline` đến khi khớp marker/pattern
4. Ghi `SampleRecord` (latency) và `FailureRecord` (timeout/EOF) ra CSV

Session setup time = spawn → shell prompt đầu tiên.

### Khác biệt giữa các workload

| Workload | Đo gì | Metric chính |
|----------|--------|--------------|
| W1 | Latency hoàn thành lệnh đơn giản (echo, ls, date) | `latency_ms` |
| W2 | Latency output liên tục (top refresh, tail -f, ping) | `latency_ms` với clock offset correction |
| W3 | Echo latency từng keystroke trong editor/shell | `latency_ms` per character |
| W4 | Thời gian nhận output lớn (cat file 512K–10M) | `latency_ms` (Time-to-Interactive), `ttfb_ms`, `throughput_kib_s` |

### Tham số benchmark phổ biến

| Biến | W1 | W2 | W3 | W4 |
|------|----|----|----|----|
| ITERATIONS | 50 | 10 | 100 | 10 |
| WARMUP | 3 | — | 10 | 2 |
| TRIALS | 10 | 5 | 3 | 5 |
| MOSH_PREDICT | never | never | never | never |
| SHUFFLE_PAIRS | true | true | false | true |

### Cấu hình kết nối (hardcoded trong scripts)

- **VPN (Tailscale):** HOST=`100.66.79.93`, SOURCE_IP=`100.70.166.91`, iface=`tailscale0`
- **LAN (USB):** HOST=`10.42.0.206`, SOURCE_IP=`10.42.0.1`, iface=`enp43s0`/`eth0`
- **User:** `pi`, key `~/.ssh/id_ed25519`
- **SSH3:** port 4433, endpoint `:4433/ssh3-term`, flag `-insecure`

### Output format

Kết quả nằm trong `<wN>_results/<scenario>/`:
- `<wN>_line_log.csv` — mỗi dòng = một sample
- `<wN>_session_setup.csv` — thời gian thiết lập phiên
- `<wN>_meta.json` — metadata + summary thống kê
- `baseline.txt` — snapshot mạng trước khi đo
- `*.png` — biểu đồ trend

## Phụ thuộc

```bash
pip install pexpect matplotlib numpy
```

Yêu cầu `ssh`, `ssh3`, `mosh` và `tc` (iproute2) trên cả client và server.

## Quy ước code

- Python scripts dùng `argparse`, output CSV + JSON meta, dataclass cho records.
- Shell scripts dùng `set -euo pipefail`, SSH ControlMaster cho multiplexing khi cần nhiều lệnh remote.
- `run_all_scenarios.sh` tự prime sudo + keepalive background, áp tc cả 2 đầu, chờ settle (`SETTLE_SEC=30`), rồi benchmark.
- W4 cần chạy `setup_w4_fixtures.sh` trên server một lần để tạo `/tmp/w4_paths_{small,medium,large}.txt`.
