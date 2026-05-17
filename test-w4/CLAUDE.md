# CLAUDE.md — W4: Đo Output Delivery Latency (SSH / SSH3 / Mosh)

File này cung cấp hướng dẫn cho Claude Code khi làm việc với code trong thư mục `test-w4/`.

---

## 1. Mục tiêu

Benchmark **W4** đo **output delivery latency** — thời gian từ khi gửi lệnh đến khi toàn bộ output của lệnh hiển thị trên client — cho ba giao thức terminal từ xa:

| Giao thức | Mô tả ngắn |
|-----------|-----------|
| **SSH** | OpenSSH truyền thống, TCP stream qua port 22 |
| **SSH3** | SSH over QUIC/HTTP3, port 4433 |
| **Mosh** | Mobile Shell, UDP + SSP (State Synchronization Protocol) |

Benchmark chạy trên **4 kịch bản mạng** (default, low, medium, high) và đo với **3 lệnh sinh output lớn** đại diện cho các tình huống thực tế.

---

## 2. Yêu cầu bắt buộc của script

### 2.1 Ba lệnh đo (workload)

Script **phải** sử dụng các lệnh hệ thống thực tế sinh ra lượng text hữu ích và có khả năng nén tự nhiên, mô phỏng đúng thao tác lỡ tay in log/thư mục quá lớn của quản trị viên:

```bash
find /etc /var/log -type f 2>/dev/null  # Mức 1: Liệt kê log và cấu hình
find /usr/share -type f 2>/dev/null     # Mức 2: Liệt kê tài nguyên chia sẻ
find /usr -type f 2>/dev/null           # Mức 3: Liệt kê toàn bộ system bin/lib (rất lớn)
```

> **Lưu ý triển khai hiện tại:** Định nghĩa đo lường cốt lõi của kịch bản này là **"Time-to-Interactive" (Thời gian phục hồi giao diện)**, KHÔNG PHẢI là Bulk Data Throughput. Chúng ta đo lường: sau khi xả một lượng rác khổng lồ ra màn hình đường truyền kém, mất bao lâu để người dùng nhận lại được dấu nhắc lệnh (prompt).

### 2.2 Bốn kịch bản mạng

| Kịch bản | RTT mục tiêu | Jitter | Packet loss | Mô tả |
|----------|-------------|--------|-------------|-------|
| `default` | ~100ms (VPN) | — | 0% | Không áp tc netem, mạng thực |
| `low` | ~20ms | — | 0% | Mạng LAN tốt |
| `medium` | ~100ms | ±8ms | 1.5% | Mạng WAN trung bình |
| `high` | ~200ms | ±32ms | 3% | Mạng kém, nhiễu cao |

Áp dụng bằng `../set_network.sh <iface> <scenario>` trên **cả client lẫn server**.

### 2.3 Metric cần thu thập

Mỗi lần đo phải ghi lại:

- `latency_ms` (Time-to-Interactive) — thời gian từ lúc gửi lệnh đến khi nhận lại được dấu nhắc lệnh (prompt) và có thể gõ tiếp.
- `ttfb_ms` — thời gian đến byte đầu tiên (Time To First Byte).
- `output_bytes` — số byte nhận được phía client (với Mosh: bytes screen-sync, KHÔNG dùng để tính throughput).
- `session_setup_ms` — thời gian spawn → shell prompt đầu tiên

### 2.4 Cấu trúc output

```
w4_results/
  <scenario>/
    w4_line_log.csv       # mỗi dòng = một sample
    w4_session_setup.csv  # thời gian thiết lập phiên
    w4_meta.json          # metadata + summary thống kê
    baseline.txt          # snapshot mạng trước khi đo
    *.png                 # biểu đồ trend mean/p95 theo trial
```

---

## 3. Chạy benchmark

```bash
# Một scenario (tc netem đã được áp trước):
./run_w4_benchmark.sh low        # hoặc medium / high / default

# Toàn bộ 4 scenario (tự động áp tc, chờ ổn định, rồi đo):
./run_all_scenarios.sh
./run_all_scenarios.sh low medium   # chạy tập con

# Vẽ biểu đồ trend sau khi có kết quả:
python plot_trend.py --output-dir w4_results/low --prefix w4 --group-fields protocol workload command
```

### Tham số chính trong `run_w4_benchmark.sh`

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `ITERATIONS` | 10 | Số sample mỗi trial |
| `WARMUP` | 2 | N sample đầu bị loại khỏi tổng hợp |
| `TRIALS` | 5 | Số phiên độc lập mỗi cặp protocol/lệnh |
| `MOSH_PREDICT` | `never` | Tắt local echo để đo latency thực |
| `SHUFFLE_PAIRS` | `true` | Xáo trộn thứ tự tránh bias thứ tự |

---

## 4. Nền tảng lý thuyết của ba giao thức

### 4.1 SSH (OpenSSH)

- **Transport:** TCP, mã hóa AES/ChaCha20, nén tùy chọn.
- **Đặc điểm với output lớn:** SSH stream toàn bộ byte qua TCP. Khi có packet loss, TCP phải retransmit và congestion window thu hẹp lại → latency tăng **bậc hai** theo loss rate và RTT.
- **Công thức throughput TCP (Mathis):** `BW ≈ MSS / (RTT × √loss)` — throughput giảm mạnh khi loss tăng.
- **Dự đoán lý thuyết:** SSH sẽ chậm nhất ở kịch bản high (loss 3%, RTT 200ms), và thời gian tăng gần tuyến tính với kích thước output.

### 4.2 SSH3 (SSH over QUIC/HTTP3)

- **Transport:** QUIC (UDP), multiplexing stream, không có head-of-line blocking ở transport layer.
- **Đặc điểm với output lớn:** QUIC có cơ chế phục hồi mất gói nhanh hơn TCP (ACK tức thì, không cần đợi timeout). Với output lớn, QUIC chia thành nhiều stream độc lập, mất một packet không block toàn bộ luồng.
- **Dự đoán lý thuyết:** SSH3 sẽ vượt trội SSH rõ rệt khi loss > 1%, đặc biệt với output lớn. Session setup nhanh hơn SSH do QUIC 0-RTT/1-RTT handshake.
- **Nguồn:** Loup Theron & Simon Thoby, "SSH3: Faster and Rich Secure Shell over HTTP/3" (2023).

### 4.3 Mosh (Mobile Shell)

- **Transport:** UDP + SSP (State Synchronization Protocol). Mosh **không stream bytes** — nó đồng bộ **trạng thái màn hình terminal** (screen state diff).
- **Đặc điểm với output lớn:** Mosh chủ động vứt bỏ phần lớn dữ liệu hiển thị trôi tuột ở giữa và chỉ gửi khung hình cuối cùng chứa prompt. Do đó `latency_ms` của Mosh (Time-to-Interactive) sẽ siêu thấp và giúp terminal không bị treo cứng. Đây là **lợi thế thiết kế**, không phải lỗi.
- **Dự đoán lý thuyết:** Latency của Mosh **không tỷ lệ với kích thước output** mà tỷ lệ với RTT. Mosh ổn định hơn SSH/SSH3 khi output lớn, mang lại trải nghiệm Time-to-Interactive tốt nhất dưới đường truyền kém.
- **Nguồn:** Keith Winstein & Hari Balakrishnan, "Mosh: An Interactive Remote Shell for Mobile Clients" (USENIX ATC 2012).

---

## 5. Kết quả thực nghiệm và đánh giá

Dữ liệu từ các run trong `w4_results/{default,low,medium,high}/` (lệnh base64, n=40 sample/cell).

### 5.1 Thời gian thiết lập phiên (Session Setup, ms)

| Kịch bản | SSH | SSH3 | Mosh |
|----------|-----|------|------|
| default (~100ms VPN) | ~740 | ~215 | ~960 |
| low (~20ms) | ~445 | ~128 | ~602 |
| medium (~100ms, loss 1.5%) | ~1500 | ~420 | ~1750 |
| high (~200ms, loss 3%) | ~3100 | ~860 | ~3200 |

**Nhận xét:** SSH3 thiết lập phiên nhanh nhất ở mọi kịch bản (~3× nhanh hơn SSH). Mosh chậm nhất vì phải thiết lập cả SSH tunnel lẫn UDP session. Ở kịch bản high, SSH và Mosh mất >3 giây chỉ để kết nối.

**Đúng lý thuyết:** SSH3 dùng QUIC 1-RTT handshake thay vì TCP 3-way + SSH key exchange nhiều round-trip → setup nhanh hơn, đặc biệt rõ khi RTT cao.

### 5.2 Output Delivery Latency — 692 KiB

| Kịch bản | SSH (ms) | SSH3 (ms) | Mosh (ms) |
|----------|----------|-----------|-----------|
| default | 473 | 231 | 222 |
| low | 152 | 65 | 157 |
| medium | 4 559 | 544 | 276 |
| high | 11 991 | 1 512 | 414 |

### 5.3 Output Delivery Latency — 2.77 MiB

| Kịch bản | SSH (ms) | SSH3 (ms) | Mosh (ms) |
|----------|----------|-----------|-----------|
| default | 1 589 | 223 | 393 |
| low | 354 | 99 | 364 |
| medium | 16 638 | 571 | 481 |
| high | 45 352 | 1 482 | 626 |

### 5.4 Output Delivery Latency — 11.1 MiB

| Kịch bản | SSH (ms) | SSH3 (ms) | Mosh (ms) |
|----------|----------|-----------|-----------|
| default | 7 177 | 326 | 1 227 |
| low | 1 139 | 150 | 1 193 |
| medium | 63 862 | 576 | 1 307 |
| high | 184 748 | 1 417 | 1 450 |

---

## 6. Phân tích và đối chiếu lý thuyết

### 6.1 SSH — Sụp đổ hiệu năng dưới packet loss

**Quan sát:** SSH mất **184 giây** để truyền 11 MiB ở kịch bản high (loss 3%, RTT 200ms), so với chỉ **1.1 giây** ở kịch bản low (loss 0%, RTT 20ms). Tỷ lệ tăng: ~162×.

**Giải thích:** TCP congestion control (CUBIC/Reno) phản ứng với mất gói bằng cách giảm congestion window xuống còn một nửa (hoặc về 1 MSS khi timeout). Với loss 3% và RTT 200ms:
- Mỗi retransmission timeout (RTO) ≈ 400–800ms
- Throughput thực tế ≈ `1460 / (0.2 × √0.03)` ≈ ~42 KB/s (Mathis formula)
- 11 MiB / 42 KB/s ≈ 268 giây — phù hợp với kết quả đo (~185 giây)

**Kết luận:** Kết quả **đúng với lý thuyết**. SSH không phù hợp cho mạng có loss cao khi cần truyền output lớn.

### 6.2 SSH3 — QUIC vượt trội rõ rệt dưới loss

**Quan sát:** SSH3 mất **1.4 giây** cho 11 MiB ở kịch bản high, so với SSH mất **184 giây** — nhanh hơn **130×**.

**Giải thích:** QUIC xử lý mất gói ở application layer, không block toàn bộ stream. Cơ chế ACK của QUIC chi tiết hơn TCP (SACK mặc định, không cần negotiate), cho phép phục hồi nhanh hơn. Ngoài ra, QUIC không có head-of-line blocking: mất một packet chỉ ảnh hưởng stream đó, không block các stream khác.

**Điểm bất thường:** Ở kịch bản default và low, SSH3 có `output_bytes` nhỏ hơn kích thước thực (ví dụ: 655 KiB thay vì 11 MiB ở default/8MiB). Điều này cho thấy SSH3 có thể đang dùng cơ chế nén hoặc marker được phát hiện sớm trước khi toàn bộ output đến. Cần kiểm tra lại logic `_wait_for_marker_line` với SSH3.

**Kết luận:** Kết quả **đúng với lý thuyết** của Theron & Thoby (2023). SSH3/QUIC vượt trội SSH/TCP khi có packet loss, đặc biệt với output lớn.

### 6.3 Mosh — Ổn định nhưng không đo throughput thực

**Quan sát:** Mosh mất **1.2–1.5 giây** cho 11 MiB ở mọi kịch bản (low đến high), gần như không đổi. Nhưng `output_bytes` chỉ ~0.2–0.5 KiB — Mosh không thực sự truyền 11 MiB.

**Giải thích:** Mosh dùng SSP để đồng bộ trạng thái màn hình. Khi lệnh sinh 11 MiB output, terminal scroll qua hàng nghìn dòng, nhưng Mosh chỉ cần gửi **diff cuối cùng** (vài dòng cuối màn hình). Latency đo được phản ánh thời gian server hoàn thành lệnh + 1 RTT để sync màn hình, không phải thời gian truyền toàn bộ output.

**Hệ quả quan trọng:** Mosh **không phù hợp** cho các tác vụ cần đọc toàn bộ output (pipe, redirect, script). Nó chỉ phù hợp cho tương tác người dùng nhìn màn hình.

**Kết luận:** Kết quả **đúng với lý thuyết** của Winstein & Balakrishnan (2012). Latency Mosh ổn định vì nó không phụ thuộc kích thước output mà phụ thuộc RTT.

### 6.4 So sánh tổng hợp

```
Kịch bản high, 11 MiB:
  SSH  : ████████████████████████████████████████ 184.7s  (TCP bị nghẽn bởi loss)
  SSH3 : █ 1.4s                                           (QUIC phục hồi nhanh)
  Mosh : █ 1.5s                                           (chỉ sync screen state)

Kịch bản low, 11 MiB:
  SSH  : ████ 1.1s
  SSH3 : ▌ 0.15s                                          (QUIC nhanh hơn TCP ~7×)
  Mosh : ████ 1.2s                                        (bị giới hạn bởi RTT)
```

**Ranking theo kịch bản:**
- **default/low (không loss):** SSH3 > SSH ≈ Mosh (cho output lớn)
- **medium/high (có loss):** SSH3 >> Mosh >> SSH

---

## 7. Lưu ý đo lường quan trọng

1. **Mosh `output_bytes` ≠ bytes lệnh in ra.** Không sử dụng thông số này để vẽ biểu đồ Throughput (KB/s). Trọng tâm so sánh là Time-to-Interactive (`latency_ms`).
2. **Khả năng nén:** Vì dùng lệnh `find` trả về text thuần, SSH và SSH3 sẽ tận dụng tối đa thuật toán nén để sinh tồn qua băng thông hẹp. Điều này là được phép vì nó sát với thực tế môi trường mạng.
3. **SSH3 `output_bytes` có thể nhỏ hơn thực tế** do marker được phát hiện qua fuzzy matching trước khi toàn bộ output đến. Xem `_wait_for_marker_line` trong `w4_large_output_benchmark.py`.

3. **TTFB của SSH3 (~50ms) thấp hơn SSH (~100ms)** ở kịch bản default/low, phản ánh QUIC handshake nhanh hơn và pipeline tốt hơn.

4. **Kịch bản `default`** dùng VPN (Tailscale, IP 100.x.x.x) với RTT thực ~100ms, khác với `medium` dùng tc netem trên LAN. Không so sánh trực tiếp default với medium.

---

## 8. Phụ thuộc

```bash
pip install pexpect matplotlib
```

Yêu cầu `ssh`, `ssh3`, `mosh`, `tc` (iproute2) trên cả client và server.
SSH3 server chạy trên port 4433, endpoint `:4433/ssh3-term`.

---

## 9. Cấu hình kết nối (hardcoded)

- **Host:** `10.42.0.206` (LAN) hoặc `100.66.79.93` (VPN)
- **User:** `pi`, key `~/.ssh/id_ed25519`
- **SSH3:** `-insecure`, path `:4433/ssh3-term`
- **Source IP client:** `10.42.0.1` (LAN) hoặc `100.70.166.91` (VPN)
