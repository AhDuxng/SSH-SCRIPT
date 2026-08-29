"""Ghi kết quả thí nghiệm ra CSV theo schema cố định.

Kết quả thô và kết quả đã tổng hợp được ghi bằng hai hàm khác nhau: schema của
kết quả thô do workload khai báo và không được suy ra từ dữ liệu, để một trial
thiếu cột không lặng lẽ làm đổi định dạng tệp.
"""

from __future__ import annotations

import csv
from pathlib import Path


# Ghi kết quả thô theo schema đã khai báo trước.
def write_rows(path: Path, fieldnames, rows) -> None:
    fieldnames = list(fieldnames)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        # Schema là hợp đồng do workload khai báo. Lịch chạy dùng chung mang
        # theo nhiều trường hơn mức một workload cần, nên cột thừa được bỏ qua
        # thay vì làm hỏng việc ghi tệp.
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


# Ghi bảng tổng hợp; schema lấy từ hàng đầu vì nó do analyzer sinh ra.
def write_summary(path: Path, rows) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError(f"không có dòng tổng hợp cho {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


# Đọc CSV và kiểm tra các cột bắt buộc có mặt.
def read_rows(path: Path, required=()) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} thiếu cột: {sorted(missing)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"không có dữ liệu trong {path}")
    return rows
