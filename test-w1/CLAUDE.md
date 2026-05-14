# W1 — Command Loop Benchmark

## Mục tiêu

So sánh **độ trễ thực thi lệnh** trên ba protocol shell từ xa: **SSH (OpenSSH)**, **SSH3 (QUIC)**, **Mosh (UDP/SSP)** — trong điều kiện đầu vào giống hệt nhau.

Mỗi lần chạy phải trả lời được hai câu hỏi, và giữ chúng **tách biệt**:

1. **Command loop latency** — khi session đã sẵn sàng, gửi một lệnh rồi đợi đến lúc output kết thúc mất bao lâu. Đây là metric *chính*.
2. **Session setup latency** — từ lúc spawn tiến trình client đến lúc thấy shell prompt đầu tiên mất bao lâu. Metric *phụ*, phải export riêng, không được lẫn vào (1).

Không đo: throughput, băng thông, thời gian reconnect, hành vi roaming của Mosh.

## Những bất biến cần giữ khi cập nhật

Các điểm dưới đây đã được cân nhắc có chủ đích. Đừng đổi nếu không có lý do rõ ràng — nếu đổi, cập nhật file này luôn.

- **Cùng một `commands`, cùng `identity-file`, cùng `source-ip`, cùng `timeout`, cùng `seed` cho cả ba protocol.** Bất kỳ thay đổi nào chỉ áp cho một protocol đều phá tính so sánh được.
- **Đo hoàn thành lệnh bằng marker `echo __W1_DONE_...__` sau `{ cmd; };`**, không bằng cách đợi prompt quay lại. Một số shell/protocol vẽ lại prompt theo cách khác nhau — marker cho ranh giới chính xác, độc lập với prompt.
- **Đuôi marker (`marker_tail`) phải đổi mọi ký tự so với lần trước** (xem `_next_marker_tail`). Mosh gửi *screen delta*; nếu vị trí ký tự không đổi, byte đó có thể không xuất hiện trong pty stream và regex sẽ không match. Không "đơn giản hoá" bằng random độc lập từng lần.
- **`prompt_re` và `_build_token_re` phải cho phép ANSI escape / CR / LF xen vào giữa các ký tự** (`_ECHO_GAP`). Mosh và một số terminal redraw hay chèn các sequence này — nếu regex khớp chuỗi liên tục, test sẽ flaky trên Mosh.
- **PS1 được export *sau* khi đã đo session setup**, và session setup dừng ở prompt đầu tiên *trước* lúc export PS1. Không gộp hai giai đoạn.
- **`trials` = số session độc lập. `iterations` = số mẫu trong một session.** Không trộn hai khái niệm — phân tích trend theo `round_id` (trial) phụ thuộc vào điều này.
- **Lệnh SSH gốc cho cả ba protocol phải dùng chung bộ option** (xem `_session_command`): `-tt`, `-b <source-ip>`, `StrictHostKeyChecking`, `-i`, `BatchMode`. Mosh bootstrap qua cùng lệnh SSH đó (`--ssh=...`), SSH3 tự có key/insecure flag tương ứng.
- **Failure không im lặng.** Mỗi lần timeout/EOF/ValueError phải ghi vào `FailureRecord` và xuất ra CSV với `status=fail`. `success_rate_pct` trong summary tính theo `n_ok / (n_ok + n_fail)`.

## Output mong đợi

Sau một lần chạy thành công, trong `--output-dir` phải có đủ:

- `w1_line_log.csv` — một dòng / sample, gồm cả ok và fail. Là nguồn dữ liệu duy nhất cho plot.
- `w1_session_setup.csv` — session setup riêng, một dòng / trial / protocol / command.
- Bảng summary in ra stdout (min / mean / median / stdev / p95 / p99 / max / CI95) theo (protocol, workload, command).
- Biểu đồ trend theo trial (`plot_trend.py`): cả `mean` và `p95`, mỗi command một cặp chart, cả ba protocol trên cùng trục.

Nếu thêm metric mới, thêm cột vào `w1_line_log.csv` rồi mở rộng `plot_trend.py` — đừng tạo file CSV thứ ba cho cùng một loại dữ liệu.

## Nguyên tắc khi cập nhật script

- **Thay đổi ảnh hưởng phép đo → phải cập nhật cả ba protocol cùng lúc.** Ví dụ đổi cách detect "lệnh xong" thì phải verify trên SSH, SSH3 và Mosh.
- **Trước khi đổi regex / marker / prompt**, cân nhắc tác động lên Mosh trước (khó nhất), rồi SSH3, cuối cùng SSH.
- **Không thêm sleep cố định để "ổn định" kết quả.** Nếu flaky, sửa điều kiện đồng bộ (regex, drain, marker) chứ không che bằng sleep.
- **Không bỏ qua sample fail để cho số đẹp.** Fail là tín hiệu protocol có vấn đề — phải giữ trong output.
- **Trước khi merge thay đổi đo lường**, chạy ít nhất 1 trial × 1 iteration mỗi protocol để xác nhận marker, prompt, session setup đều hoạt động.
