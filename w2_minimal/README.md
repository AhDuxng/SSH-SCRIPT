# W2 minimal — End-to-end command completion

W2 chạy ba workload:

```text
find /usr
docker logs w2-log-source
cat /tmp/w2_large_file.txt
```

## Định nghĩa một mẫu

1. Pi1 xóa output cũ đang chờ trong PTY.
2. Pi1 ghi `start = time.perf_counter_ns()` ngay trước `sendline(command)`.
3. Pi2 chạy trọn lệnh và in marker duy nhất sau khi lệnh kết thúc.
4. Pi1 đọc output liên tục cho đến marker rồi ghi `end`.
5. `completion_ms = (end - start) / 1e6`.

Vì cả hai timestamp đều lấy trên Pi1 nên không cần đồng bộ clock. `output_bytes`
đếm dữ liệu Pi1 quan sát trước marker; throughput được tính bằng
`output_bytes / completion_time`.

Với SSH và SSH3, byte stream có thứ tự nên nhận marker đồng nghĩa toàn bộ output
đứng trước marker đã tới client. Với Mosh, marker chỉ xác nhận trạng thái terminal
cuối đã được hiển thị; Mosh có thể bỏ qua trạng thái màn hình trung gian, vì vậy
không được diễn giải `output_bytes` của Mosh là toàn bộ byte do lệnh sinh ra.
W2 giữ một màn hình VT100 ảo xuyên suốt session Mosh và tìm marker trong trạng
thái đã dựng lại, vì raw output của Mosh chỉ chứa các cell thay đổi chứ không
nhất thiết chứa nguyên chuỗi marker. Mosh dùng timeout tổng riêng và không áp
idle timeout cho các khoảng không có redraw.

## Một trial

Mỗi trial mở một connection mới và đo session setup đến prompt đầu tiên. Trên
connection đó, chương trình chạy `WARMUP_SAMPLES` lần không ghi kết quả, sau đó
chạy trọn lệnh `SAMPLES_PER_TRIAL` lần và lưu từng lần vào `samples.csv`.

## Kết quả

```text
artifacts/results/experiment_order.csv
artifacts/results/samples.csv
artifacts/results/setup_samples.csv
artifacts/results/trials.csv
artifacts/results/summary.csv
artifacts/results/setup_summary.csv
artifacts/results/load_summary.csv
artifacts/results/metadata.json
artifacts/figures/*
```
