# Phân tích log W2 hiện có

Các số dưới đây được đọc từ `pi_runs/{low,medium,high}/results` trước khi sửa
workload. Không dùng chúng làm kết quả cuối vì payload cũ có 800 dòng × 128 byte
nhưng viewport Mosh chỉ có 128 dòng.

## Mosh

Raw bytes của Mosh là byte cập nhật terminal, không phải byte stream của `cat`.
Vì redraw có thể lặp dữ liệu, log cũ có sample vượt 100% raw bytes nhưng vẫn
thiếu dòng; vì screen-state có thể bỏ qua lịch sử đã cuộn, raw bytes cũng có thể
thấp hơn 100%. Do đó so sánh `received_bytes == 102400` hoặc băm trực tiếp raw
capture đều không xác thực được payload Mosh.

| Network | Scenario | Attempted | Skipped | Marker / planned | Coverage / attempted |
|---|---:|---:|---:|---:|---:|
| low | W2-S1 | 980 | 20 | 97,900% | 78,572% |
| low | W2-S2 | 1.132 | 868 | 56,100% | 80,838% |
| low | W2-S4 | 1.442 | 2.558 | 35,300% | 82,323% |
| medium | W2-S1 | 46 | 954 | 3,600% | 66,845% |
| medium | W2-S2 | 49 | 1.951 | 1,450% | 50,212% |
| medium | W2-S4 | 91 | 3.909 | 1,275% | 39,827% |
| high | W2-S1 | 20 | 980 | 1,000% | 40,362% |
| high | W2-S2 | 36 | 1.964 | 0,800% | 28,524% |
| high | W2-S4 | 65 | 3.935 | 0,625% | 25,227% |

Bản sửa tạo 25 dòng × 4.096 byte. W2-S4 vì vậy dùng 100 dòng payload cộng tám
marker, nằm trọn trong viewport `4096 × 128`. Mỗi sample xóa viewport, đồng bộ
tất cả vai trò rồi dựng lại payload canonical từ các dòng exact/unique để kiểm
tra đủ 102.400 byte và SHA-256. Raw capture vẫn được giữ riêng để chẩn đoán.

## SSH3 so với SSH

| Network | Scenario | SSH3 / SSH median latency | SSH3 / SSH mean throughput |
|---|---:|---:|---:|
| low | W2-S1 | 1,045× | 0,966× |
| low | W2-S2 | 1,015× | 0,993× |
| low | W2-S4 | 0,987× | 1,016× |
| medium | W2-S1 | 2,871× | 0,324× |
| medium | W2-S2 | 2,948× | 0,320× |
| medium | W2-S4 | 2,994× | 0,301× |
| high | W2-S1 | 1,244× | 0,772× |
| high | W2-S2 | 1,359× | 0,714× |
| high | W2-S4 | 1,331× | 0,742× |

Chênh lệch không chỉ đến từ sample đầu: median gần như không đổi khi bỏ
`sample_index=1`. Khoảng từ byte cuối đến completion marker có median xấp xỉ
0 ms cho cả SSH và SSH3, nên marker parser không giải thích được độ chậm. Phần
chênh lệch nằm trong khoảng first-byte → last-byte dưới loss/jitter của lần chạy
cũ. Low gần ngang nhau, medium chậm gần 3× và high chậm khoảng 1,24–1,36×; dạng
không đơn điệu này cần được giữ như kết quả đo và kiểm tra lại bằng một run mới,
không được hiệu chỉnh số liệu.

`tools/analyze_w2.py` nay sinh `ssh3_vs_ssh.csv` và cảnh báo khi median SSH3 chậm
hơn SSH quá 5%. Run mới vẫn kiểm tra một UDP socket, một conversation và QUIC
StreamID riêng cho từng role bằng `tools/verify_ssh3_mux.py`.
