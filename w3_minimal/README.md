# W3 minimal

Benchmark độ trễ gõ phím của SSH, SSH3 và Mosh trong Vim và Nano.

## Thiết kế hiện tại

- Mỗi tổ hợp `protocol × target × profile` chạy số connection độc lập do `TRIALS_PER_COMBINATION` quy định.
- Chỉ đo hai target tương tác: **Vim và Nano**; không đo Shell.
- Mỗi connection gõ đúng một lượt toàn bộ [`payloads/probe_text.c`](payloads/probe_text.c): **80 ký tự**, kể cả 6 ký tự xuống dòng. CSV đánh số `char_index=1/80 ... 80/80`.
- Mỗi block chứa đủ mọi tổ hợp rồi được xáo trộn bằng `RANDOM_SEED`; thứ tự thật được lưu tại `experiment_order.csv`.
- Chỉ bắt đầu đo sau khi interactive channel và mọi tải nền đã READY, sau đó warm-up đồng nhất 5 giây.
- `c0_only`: không tải nền.
- `c0_bg4`: `log + ping + sysmon + output` với output 100 KiB/s.
- `c0_bg4_heavy`: cùng bốn tải nhưng output 1 MiB/s.

SSH và SSH3 đều đọc rồi loại output nền trong bộ nhớ và ghi tổng byte cho từng channel. Mosh không có nhiều channel: tải output chạy ngay trong terminal Mosh để giữ đúng bản chất giao thức.

Mosh luôn dùng `--predict=always`. Khi thống kê, SSH, SSH3 và Mosh được đặt cạnh nhau trên cùng một hình; tỷ lệ hoàn thành được ghi ngay trên cột để tránh hiểu nhầm các metric chỉ tính từ mẫu thành công.

Startup Mosh dùng handshake ready-file có retry: interactive shell phải READY trước khi runner gửi lệnh mở tải nền. Mỗi connection có `mosh_<trial_tag>_interactive_debug.log`; lỗi READY nêu chính xác role còn thiếu.

## Cách xác nhận connection và stream

- SSH: trước khi trial hợp lệ, runner yêu cầu `ssh -O check` thành công, thấy đúng một TCP socket `ESTABLISHED` của ControlMaster, và không thấy TCP socket riêng ở các launcher channel.
- SSH3: một tiến trình Go gọi `Dial` đúng một lần, sau đó mở interactive và từng tải nền thành các session channel trên cùng client. Mỗi channel ánh xạ sang một QUIC stream riêng.
- Verifier yêu cầu: đúng một UDP socket, một conversation ID, stream ID khác nhau cho mọi role, READY đủ role và byte nền dương trên từng stream.

Patch Go chỉ dùng để build [`bin/ssh3-mux`](bin/ssh3-mux) cục bộ; nó **không sửa hoặc ghi đè SSH3 đã cài trên máy**. Bộ đếm byte được ghi vào file audit riêng, không đi qua PTY đang được đo.

## Phương pháp đo

`TerminalTracker` phân tích trạng thái VT100/xterm. Mỗi lần đo sẽ:

1. xóa output cũ và chụp vị trí con trỏ;
2. gửi một ký tự;
3. dừng đồng hồ chỉ khi parser thấy ký tự được vẽ đúng tại vị trí con trỏ đó; Enter phải tạo chuyển động con trỏ bắt đầu tại đúng vị trí đã chụp.

Vì vậy một ký tự giống probe xuất hiện ở nơi khác trong raw output nền không thể làm mẫu thành công giả.

Thống kê dùng cách cũ: gộp mọi ký tự thành công của các trial trong cùng tổ hợp để tính mean, median, P90 và P95. Timeout được báo cáo qua số lượng và tỷ lệ hoàn thành nhưng không được đưa vào giá trị độ trễ.

## Cấu trúc

```text
w3_minimal/
├── run_w3.sh                 # entry point
├── config.example.env        # cấu hình mẫu được lưu trong Git
├── config.env                # cấu hình cục bộ, không được Git theo dõi
├── src/                      # scheduler, trial, protocol và terminal parser
├── tools/                    # analyze, plot và verifier SSH3
├── scripts/                  # build SSH3 mux và tải nền remote
├── patches/                  # patch Go được ghim
├── payloads/                 # nội dung được gõ
├── tests/                    # kiểm thử parser/phép đo
├── .venv/                    # môi trường Python cần giữ để chạy
└── artifacts/                # dữ liệu, log và hình sinh ra
```

Các lệnh sử dụng nằm trong [`run.md`](run.md).
