"""Khung chạy thí nghiệm: cấu hình, ma trận, kết quả và thống kê.

Package này chứa mọi thứ *quanh* phép đo — quyết định chạy tổ hợp nào, đọc và
kiểm tra cấu hình, ghi kết quả, tổng hợp thống kê. Nó phụ thuộc vào
`stream_mux` để mở connection, nhưng `stream_mux` không bao giờ phụ thuộc
ngược lại: lõi transport chỉ biết mở, kiểm chứng và đóng stream.
"""

from .experiment import (
    DEFAULT_TRIALS_PER_CONFIGURATION,
    Configuration,
    ExperimentMatrix,
    Scenario,
    build_matrix,
    build_schedule,
    render_matrix,
)
from .results import read_rows, write_rows, write_summary
from .settings import (
    ConfigurationError,
    ExperimentPlan,
    build_plan,
    cfg_bool,
    load_settings,
)
from .statistics import (
    LatencySummary,
    column,
    fmt,
    latency_stats,
    mean_pct,
    percentile,
    rate_pct,
    summarize_latency,
)

__all__ = [
    "DEFAULT_TRIALS_PER_CONFIGURATION",
    "Configuration",
    "ConfigurationError",
    "ExperimentMatrix",
    "ExperimentPlan",
    "LatencySummary",
    "Scenario",
    "build_matrix",
    "build_plan",
    "build_schedule",
    "cfg_bool",
    "column",
    "fmt",
    "latency_stats",
    "load_settings",
    "mean_pct",
    "percentile",
    "rate_pct",
    "read_rows",
    "render_matrix",
    "summarize_latency",
    "write_rows",
    "write_summary",
]
