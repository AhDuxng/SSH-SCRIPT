"""Thống kê dùng chung cho mọi workload.

Công thức được giữ nguyên như các bản cài đặt rời rạc trước đây; module này chỉ
gom chúng về một chỗ để bốn workload không còn bốn phiên bản có thể trôi khác
nhau. Không thêm phép thống kê mới nếu phương pháp đo chưa yêu cầu.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

# Số chữ số thập phân của mọi giá trị mili giây và phần trăm trong kết quả.
DECIMALS = 3


# Định dạng một số thực, giữ ô trống khi không có dữ liệu.
def fmt(value) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{DECIMALS}f}"


# Percentile theo nội suy tuyến tính giữa hai phần tử liền kề.
def percentile(values, probability: float):
    """Trả về "" khi không có mẫu nào, để bảng kết quả giữ ô trống thay vì 0."""
    ordered = sorted(values)
    if not ordered:
        return ""
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


@dataclass(frozen=True)
class LatencySummary:
    """Tóm tắt phân bố độ trễ của một nhóm mẫu đã hoàn thành."""

    count: int
    mean_ms: str
    median_ms: str
    p95_ms: str
    p99_ms: str

    # Trải phẳng thành các cột CSV với tiền tố cho trước.
    def as_columns(self, prefix: str = "") -> dict[str, str]:
        return {
            f"{prefix}n": self.count,
            f"{prefix}mean_ms": self.mean_ms,
            f"{prefix}median_ms": self.median_ms,
            f"{prefix}p95_ms": self.p95_ms,
            f"{prefix}p99_ms": self.p99_ms,
        }


# Tính Mean, Median, P95 và P99 của một tập độ trễ.
def summarize_latency(values) -> LatencySummary:
    values = [float(item) for item in values]
    return LatencySummary(
        count=len(values),
        mean_ms=fmt(statistics.mean(values)) if values else "",
        median_ms=fmt(statistics.median(values)) if values else "",
        p95_ms=fmt(percentile(values, 0.95)),
        p99_ms=fmt(percentile(values, 0.99)),
    )


# Bốn cột độ trễ quen thuộc dưới dạng dict, đơn vị nằm trong tên cột.
def latency_stats(values) -> dict[str, str]:
    summary = summarize_latency(values)
    return {
        "mean_ms": summary.mean_ms,
        "median_ms": summary.median_ms,
        "p95_ms": summary.p95_ms,
        "p99_ms": summary.p99_ms,
    }


# Tỷ lệ phần trăm, giữ ô trống khi mẫu số bằng không.
def rate_pct(numerator, denominator) -> str:
    if not denominator:
        return ""
    return fmt(100.0 * numerator / denominator)


# Trung bình cộng của một cột phần trăm đã lưu dưới dạng chuỗi.
def mean_pct(values) -> str:
    numbers = [float(item) for item in values if item not in (None, "")]
    return fmt(statistics.mean(numbers)) if numbers else ""


# Đọc một cột số từ các hàng CSV, bỏ qua ô trống.
def column(rows, name: str, predicate=None) -> list[float]:
    """Chỉ bỏ qua ô **trống**; mẫu thất bại vẫn phải được đếm ở nơi khác.

    Hàm này dành cho việc lấy độ trễ của các mẫu đã hoàn thành. Người gọi phải
    tự báo cáo completion rate và timeout rate, nếu không phân bố độ trễ sẽ bị
    thiên lệch do chỉ còn lại mẫu thành công.
    """
    output = []
    for row in rows:
        if predicate is not None and not predicate(row):
            continue
        value = row.get(name, "")
        if value in (None, ""):
            continue
        output.append(float(value))
    return output
