Tạo giúp tôi một Google Sheet (dùng Google Apps Script hoặc hướng dẫn tạo thủ công) tổng hợp kết quả đo **W4 - Output Delivery Latency** của 3 giao thức mạng: **SSH, SSH3, Mosh** khi thực thi 3 lệnh sinh output lớn trong 4 kịch bản mạng.

### Thông tin benchmark:
- **3 giao thức**: SSH (OpenSSH), SSH3 (QUIC-based), Mosh (Mobile Shell)
- **3 lệnh sinh output lớn**: `find /`, `git status`, `docker logs`
- **4 kịch bản mạng**: Default (không suy giảm), Low (suy giảm thấp), Medium (suy giảm trung bình), High (suy giảm cao)

### Dữ liệu đo được:

#### Sheet 1: Session Setup Time (ms) - Trung bình

| Kịch bản | SSH | SSH3 | Mosh |
|-----------|------|------|------|
| Default | 253.6 | 63.1 | 372.3 |
| Low | 1939.8 | 1376.1 | 2860.5 |
| Medium | 2943.2 | 1632.8 | 3774.3 |
| High | 4232.7 | 1837.6 | 5836.1 |

#### Sheet 2: Output Delivery Latency (ms) - Mean & P95

**Lệnh `find /`:**

| Kịch bản | SSH (mean) | SSH (p95) | SSH3 (mean) | SSH3 (p95) | Mosh (mean) | Mosh (p95) |
|-----------|-----------|-----------|-------------|------------|-------------|------------|
| Default | 20.4 | 26.0 | 12.3 | 16.2 | 20.8 | 21.5 |
| Low | 267.1 | 377.5 | 327.9 | 503.2 | 316.8 | 425.6 |
| Medium | 447.2 | 675.3 | 647.0 | 862.7 | 444.7 | 614.8 |
| High | 969.2 | 1683.7 | 1501.4 | 2022.9 | 575.4 | 687.3 |

**Lệnh `git status`:**

| Kịch bản | SSH (mean) | SSH (p95) | SSH3 (mean) | SSH3 (p95) | Mosh (mean) | Mosh (p95) |
|-----------|-----------|-----------|-------------|------------|-------------|------------|
| Default | 20.0 | 22.0 | 5.2 | 9.0 | 20.5 | 21.1 |
| Low | 154.4 | 244.3 | 158.0 | 235.9 | 170.1 | 249.7 |
| Medium | 240.4 | 330.1 | 239.4 | 338.9 | 288.1 | 376.2 |
| High | 376.4 | 693.2 | 363.8 | 586.4 | 455.2 | 772.1 |

**Lệnh `docker logs`:**

| Kịch bản | SSH (mean) | SSH (p95) | SSH3 (mean) | SSH3 (p95) | Mosh (mean) | Mosh (p95) |
|-----------|-----------|-----------|-------------|------------|-------------|------------|
| Default | 39.6 | 44.4 | 25.0 | 31.8 | 40.6 | 41.9 |
| Low | 910.2 | 1130.7 | 929.4 | 1132.3 | 932.6 | 1128.6 |
| Medium | 1016.5 | 1239.7 | 1014.6 | 1263.3 | 1045.4 | 1286.1 |
| High | 1152.7 | 1519.4 | 1147.0 | 1433.7 | 1244.1 | 1624.2 |

### Yêu cầu format Google Sheet:

1. **Sheet 1**: "Session Setup" - bảng session setup time
2. **Sheet 2**: "Output Delivery Latency" - tổng hợp cả 3 lệnh, có cả mean và p95
3. **Highlight (conditional formatting)**:
   - Ô có giá trị **thấp nhất** (tốt nhất) trong mỗi hàng → **tô xanh lá đậm (bold)**
   - Ô có giá trị **cao nhất** (tệ nhất) trong mỗi hàng → **tô đỏ nhạt**
4. **Header** rõ ràng, freeze row đầu tiên
5. Thêm 1 sheet "Summary" tóm tắt giao thức nào thắng nhiều nhất ở mỗi kịch bản

### Ghi chú quan trọng:
- Mosh sử dụng cơ chế screen-based nên chỉ gửi frame cuối cùng, không gửi toàn bộ output → output_bytes thấp hơn SSH/SSH3 đáng kể (đặc biệt với `find /`). Số sample hợp lệ của Mosh cũng ít hơn ở kịch bản medium/high.
- SSH3 rất nhanh ở kịch bản Default nhưng suy giảm mạnh ở High (đặc biệt với `find /`).
- Đơn vị: milliseconds (ms). Giá trị thấp hơn = tốt hơn.

Hãy tạo Google Apps Script hoặc hướng dẫn chi tiết từng bước để tạo sheet này.
