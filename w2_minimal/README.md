# W2 minimal — Large-output responsiveness

W2 sử dụng ba workload:

```text
find /usr
docker logs w2-log-source
cat /tmp/w2_large_file.txt
```

Phương pháp lấy mẫu tham khảo `test-w2`, nhưng không thay workload. Một writer
duy nhất phía server lặp workload ở tốc độ cấu hình, xen marker `sequence +
timestamp` theo chu kỳ rồi ghi tuần tự ra cùng PTY. Client đo thời gian marker
được nhìn thấy dưới tải output đang chạy.

## Một trial

1. Mở connection mới và đo session setup đến prompt đầu tiên.
2. Ước lượng chênh lệch clock Pi1–Pi2 bằng nhiều round-trip probe.
3. Chạy workload một lần với output bỏ đi để xác nhận exit code bằng 0.
4. Lặp workload ở tốc độ cố định và xen marker mỗi 100 ms bằng cùng một writer.
5. Bỏ `WARMUP_SAMPLES` marker đầu rồi ghi đúng `SAMPLES_PER_TRIAL` mẫu.
6. Tính `latency = client_receive - server_event - clock_offset`.

Marker cho phép ANSI/redraw xen giữa từng ký tự và sequence phải tăng. Điều này
giúp đo Mosh theo dữ liệu thực sự xuất hiện trên màn hình, thay vì giả định Mosh
là một byte stream giống SSH/SSH3.

Đây là **event-display latency dưới tải output**, không phải throughput và cũng
không phải thời gian hoàn thành một lần chạy lệnh. Nếu trial timeout, các vị trí
mẫu còn thiếu vẫn được ghi vào CSV để success rate có mẫu số đúng.

`trials.csv` lưu số byte, thời gian thu và tốc độ terminal thực nhận của từng
connection. `load_summary.csv` tổng hợp tải theo giao thức và workload. Chỉ nên
so sánh latency khi SSH và SSH3 có tốc độ thực nhận tương đương.

## Chuẩn bị target

Target cần Python 3, `find`, `docker`, GNU `date`, `sleep`, `yes`, `head`, `wc`
và một container tên `w2-log-source`. `run_w2.sh` tự tạo file text 64 MiB và
chép writer đo lên target.

## Kết quả

```text
artifacts/results/experiment_order.csv
artifacts/results/samples.csv
artifacts/results/setup_samples.csv
artifacts/results/clock_offsets.csv
artifacts/results/trials.csv
artifacts/results/summary.csv
artifacts/results/setup_summary.csv
artifacts/results/load_summary.csv
artifacts/results/metadata.json
artifacts/figures/*.png
artifacts/figures/*.pdf
```

Mỗi hình latency có đủ SSH, SSH3, Mosh và ghi success rate trên từng cột.
