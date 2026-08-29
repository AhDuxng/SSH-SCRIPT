# Khung chạy thí nghiệm

`harness` chứa mọi thứ *quanh* phép đo: quyết định chạy tổ hợp nào, đọc và kiểm
tra cấu hình, ghi kết quả, tổng hợp thống kê, vẽ hình. Nó phụ thuộc vào
`stream_mux` để mở connection; `stream_mux` không bao giờ phụ thuộc ngược lại.

```text
config.env
   │  load_settings / build_plan          settings.py
   ▼
ExperimentPlan ── build_matrix ─────────► experiment.py
   │                  │  loại tổ hợp mà giao thức không phục vụ được
   │                  └─ dùng stream_mux/capability.py
   ▼
build_schedule ──────────────────────────► lịch randomized complete blocks
   │
   ▼
workload chạy trial  (wN-mux-tt/src/)     ◄── stream_mux mở connection/stream
   │
   ▼
write_rows ──────────────────────────────► results.py   (kết quả thô)
   │
   ▼
analyze_wN.py ── summarize_latency ──────► statistics.py (kết quả tổng hợp)
   │
   ▼
plot_wN.py ── grouped_bars ──────────────► plotting.py   (PDF vector + PNG)
```

## Quyết định phương pháp nằm ở đâu

**Giao thức nào chạy kịch bản nào** — `stream_mux/capability.py`. Không nơi nào
khác được kiểm tra tên giao thức để suy ra điều này.

> Mosh chỉ được đánh giá với một terminal session (S1), vì nó không cung cấp
> stream logic tương đương SSH channel hay QUIC stream. Do đó S2 và S4 chỉ được
> đánh giá cho SSH và SSH3.

Ngoại lệ có chủ đích: `Scenario(measures_multiplexing=False)`. W4 dùng nó vì
kịch bản của W4 mô tả *loại tải nền* chứ không đánh giá multiplexing — nhiều
vai trò ở đó là tình huống cần đo. Mọi giao thức đều tham gia, và giao thức
không multiplex được ghi `stream_count = 1`.

**Số trial** — `DEFAULT_TRIALS_PER_CONFIGURATION` trong `experiment.py`, hiện là
**5**. Không tệp `config.env` nào khai báo lại; đặt `TRIALS_PER_COMBINATION`
trong config chỉ để ghi đè có chủ đích cho một lần chạy riêng.

## Hai đại lượng dễ lẫn

| Trường | Ý nghĩa |
|---|---|
| `logical_workload_count` | số vai trò mà kịch bản định nghĩa; không đổi theo giao thức |
| `stream_count` | số stream transport thực sự mở; bằng 1 với giao thức không multiplex |

Với W4-MIX: `logical_workload_count = 3` cho mọi giao thức, nhưng
`stream_count` là 3 với SSH/SSH3 và 1 với Mosh.

## Quy ước trong bảng kết quả

Ô **trống** và **0** mang ý nghĩa khác nhau và không được dùng lẫn:

- ô trống — phép đo không tồn tại hoặc không áp dụng cho giao thức đó;
- `0.000` — có phép đo, và kết quả bằng không.

`percentile([])` và `rate_pct(x, 0)` trả về chuỗi rỗng chứ không phải 0. Hình vẽ
bỏ qua ô trống thay vì vẽ cột 0.

## Thống kê

`statistics.py` giữ nguyên công thức của các bản cài đặt rời rạc trước đây —
percentile nội suy tuyến tính, mean/median của thư viện chuẩn. Đã kiểm chứng
trùng khớp trên 600 phép thử ngẫu nhiên, và bốn analyzer tái tạo đúng từng ô
của kết quả cũ.

Độ trễ chỉ được tính trên mẫu đã hoàn thành. Vì vậy mọi bảng tổng hợp phải kèm
completion rate và timeout rate: nếu không, phân bố độ trễ bị thiên lệch do chỉ
còn lại mẫu thành công.

## Vẽ hình

`plotting.py` giữ một bảng màu an toàn với người mù màu và gán cố định màu +
hatch cho từng giao thức, để nhận dạng trực quan không đổi giữa các hình trong
cùng bài báo. PDF được xuất dạng vector với font nhúng (`pdf.fonttype = 42`).

`grouped_bars` chỉ cấp vị trí cột cho cấu hình thực sự có số liệu và căn giữa
nhóm quanh tick. Một kịch bản chỉ có SSH và SSH3 hiện đúng hai cột sát nhau,
không phải hai cột lệch bên cạnh khoảng trống gợi ý phép đo bị mất.
