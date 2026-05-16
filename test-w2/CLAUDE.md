# CLAUDE.md — W2: Đo Screen Update Latency (SSH / SSH3 / Mosh)

File này cung cấp hướng dẫn cho Claude Code (hoặc các AI model khác) khi làm việc với code trong thư mục `test-w2/`.

---

## 1. Mục tiêu và Bản chất Đo lường

Benchmark **W2** không đo tốc độ mạng thô, mà đo **Screen Update Latency** — thời gian từ khi server muốn cập nhật màn hình cho đến khi ứng dụng Terminal tại Client thực sự nhận và render xong khung hình đó.

**Đặc biệt lưu ý về Mosh:** 
Mosh là giao thức dồng bộ trạng thái (State Synchronization Protocol). Sức mạnh của nó CHỈ phát huy khi tương tác với các ứng dụng giao diện Full-screen (ANSI Curses) như `top` thực, `htop`, `vim`. 
Nếu chạy các lệnh sinh stream log liên tục theo chiều dọc (ví dụ `top -bn1` hay `tail -f`), Mosh sẽ phải cuộn màn hình y hệt SSH, làm mất đi ưu thế chênh lệch.

## 2. Vấn đề của cấu trúc cũ (Cần khắc phục)

1. **Sai bản chất tương tác:** Script hiện tại dùng vòng lặp `while true; do top -bn1...` sinh ra một luồng văn bản cuộn dọc. Điều này biến W2 thành một dạng bài đo "Stream Delivery" giống hệt W4, không phản ánh đúng thao tác cập nhật khung hình ANSI.
2. **Nhiễu thời gian CPU x Server (Execution Bias):** Gọi `top -bn1` liên tục tốn rất nhiều CPU trên Raspberry Pi (lên tới hàng chục/trăm ms). Việc ghép timestamp ngay trước khi gọi `top` khiến độ trễ đo được bị đội lên rât cao, che lấp đi độ trễ mạng thực sự.

## 3. Yêu cầu thiết kế mới cho `_measure_top` (Workload CUI)

Khi cấu trúc lại phương pháp cho workload `top` (hoặc CUI workload), kịch bản phải thỏa mãn:

* **Sử dụng ANSI Escape Codes:** Script (bằng Bash hoặc Python) được bơm lên Server phải in ra các mã ANSI xóa màn hình (`\033[2J`), đưa con trỏ về góc (`\033[H`), sau đó mới in một khối văn bản tĩnh + timestamp. Đều đặn 1 giây/lần.
* **Tiết kiệm CPU tuyệt đối:** Dừng việc gọi các lệnh hệ thống nặng (`top`, `ps`). Chỉ đơn thuần là một vòng lặp in chuỗi (cực kỳ nhẹ tốn < 1ms) xen kẽ với lệnh `sleep 1`.
* **Timestamp chính xác:** Timestamp (Epoch) phải được gài thẳng vào khung hình ngay sau khi dùng lệnh điều hướng con trỏ. Máy client (Pexpect) sẽ bắt regex mã ANSI này cùng timestamp để tính `Screen Update Latency`.

## 4. Workload `tail` và `ping`
Hai workload này chủ yếu sinh stream log hoặc dòng update đơn lẻ. Tuy không tận dụng tối đa sức mạnh full-screen của mosh như workload trên, nhưng vẫn nên giữ lại để làm đối chứng xem với stream dữ liệu tiết lưu nhỏ giọt (1 dòng / giây hoặc 0.1 giây), độ ngốn buffer của QUIC và TCP khác biệt thế nào ở môi trường mạng kém. 

Yêu cầu duy trì cơ chế đo: Server ngầm ghi file/ping -> In timestamp -> Client nhận.

## 5. Metric cần đo
- `latency_ms` = thời gian Pexpect nhận được regex chứa timestamp - thời gian Server nhúng timestamp. 
*(Lưu ý phải áp dụng thuật toán `_estimate_clock_offset_ns` để đồng bộ sai số đồng hồ giữa local máy Client và Server).*