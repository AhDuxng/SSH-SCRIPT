"""Kiểu vẽ dùng chung cho hình đưa vào bài báo.

Nguyên tắc quan trọng nhất ở đây là **trung thực về ma trận thí nghiệm**: một
giao thức chỉ xuất hiện ở kịch bản mà nó thực sự được đo. Mosh không có stream
logic nên chỉ có mặt ở kịch bản một workload; hình không được tạo cột rỗng, cột
0, hay lặp giá trị S1 sang S2/S4 để nhóm trông cân đối.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Cache của matplotlib phải nằm ngoài thư mục kết quả để không lẫn vào artifact.
_CACHE = str(Path(tempfile.gettempdir()) / "mux_tt_matplotlib_cache")
os.environ.setdefault("MPLCONFIGDIR", _CACHE)
os.environ.setdefault("XDG_CACHE_HOME", _CACHE)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from stream_mux.capability import label as protocol_label  # noqa: E402

# Bảng màu an toàn với người mù màu (Okabe-Ito). Mỗi giao thức giữ nguyên màu và
# hatch trên mọi hình để người đọc nhận ra ngay mà không phải tra chú giải.
PROTOCOL_STYLE = {
    "ssh": {"color": "#0072B2", "hatch": "///"},
    "ssh3": {"color": "#E69F00", "hatch": "---"},
    "mosh": {"color": "#009E73", "hatch": "\\\\\\"},
}

# Kích thước theo chuẩn bài báo hai cột.
SINGLE_COLUMN = (3.4, 2.4)
DOUBLE_COLUMN = (7.0, 3.0)
WIDE = (7.0, 4.0)

RC_PARAMS = {
    "figure.dpi": 120,
    "savefig.dpi": 300,
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 8,
    "legend.fontsize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.5,
    "axes.axisbelow": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.frameon": False,
    # Chữ trong PDF giữ dạng vector và nhúng font TrueType để nhà xuất bản
    # không phải rasterise lại.
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
}


# Áp kiểu vẽ chung; gọi một lần ở đầu mỗi script vẽ hình.
def use_paper_style() -> None:
    matplotlib.rcParams.update(RC_PARAMS)


@dataclass(frozen=True)
class Series:
    """Một giao thức và các giá trị của nó theo từng kịch bản.

    `values[i]` là None khi cấu hình đó không nằm trong ma trận thí nghiệm.
    Giá trị None được **bỏ qua** khi vẽ, không phải vẽ thành 0.
    """

    protocol: str
    values: list[float | None]


# Vẽ nhóm cột, chỉ đặt cột ở nơi thực sự có phép đo.
def grouped_bars(
    axis, scenarios, series, *, annotate=True, annotation_format="{:.1f}",
):
    """Trả về vị trí tick trên trục x.

    Với mỗi kịch bản, chỉ những giao thức có số liệu mới được cấp một vị trí
    cột, và nhóm được căn giữa quanh tick. Nhờ vậy một kịch bản chỉ có SSH và
    SSH3 sẽ hiện đúng hai cột sát nhau thay vì hai cột lệch sang bên cạnh một
    khoảng trống gợi ý phép đo bị thiếu.
    """
    ticks = np.arange(len(scenarios), dtype=float)
    width = 0.26
    observed = [
        value for item in series for value in item.values if value is not None
    ]
    # Một nhóm toàn giá trị 0 (ví dụ tỷ lệ timeout bằng không) vẫn cần trục có
    # chiều cao dương, nếu không matplotlib cảnh báo và trục bị suy biến.
    ceiling = max(observed, default=1.0) or 1.0
    for index, scenario_tick in enumerate(ticks):
        present = [item for item in series if item.values[index] is not None]
        if not present:
            continue
        offsets = (np.arange(len(present)) - (len(present) - 1) / 2) * width
        for item, offset in zip(present, offsets):
            style = PROTOCOL_STYLE[item.protocol]
            value = item.values[index]
            axis.bar(
                scenario_tick + offset, value, width,
                color=style["color"], hatch=style["hatch"],
                edgecolor="black", linewidth=0.5,
                label=protocol_label(item.protocol),
            )
            if annotate:
                axis.text(
                    scenario_tick + offset, value + ceiling * 0.02,
                    annotation_format.format(value),
                    ha="center", va="bottom", fontsize=6,
                )
    axis.set_xticks(ticks, scenarios)
    axis.set_ylim(0, ceiling * 1.18)
    return ticks


# Chú giải không lặp nhãn khi mỗi cột được vẽ riêng.
def deduplicated_legend(axis, **kwargs):
    handles, labels = axis.get_legend_handles_labels()
    unique: dict[str, object] = {}
    for handle, name in zip(handles, labels):
        unique.setdefault(name, handle)
    if unique:
        axis.legend(unique.values(), unique.keys(), **kwargs)


# Ghi chú các cấu hình không nằm trong ma trận, để hình tự giải thích.
def annotate_excluded(figure, matrix_note: str) -> None:
    if matrix_note:
        figure.text(0.5, 0.005, matrix_note, ha="center", fontsize=6.5)


# Lưu hình ở cả hai định dạng; PDF giữ nguyên dạng vector.
def save_figure(figure, output_dir: Path, stem: str) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "pdf"):
        figure.savefig(output_dir / f"{stem}.{suffix}", bbox_inches="tight")
    plt.close(figure)


# Xoá hình của lần chạy trước để thư mục chỉ chứa kết quả hiện tại.
def clear_figures(output_dir: Path) -> None:
    output_dir = Path(output_dir)
    if not output_dir.exists():
        return
    for pattern in ("figure_*.png", "figure_*.pdf"):
        for path in output_dir.glob(pattern):
            path.unlink()


# Đọc một ô số từ bảng tổng hợp, trả None khi cấu hình không được đo.
def value_or_none(lookup, key, column):
    row = lookup.get(key)
    if row is None:
        return None
    raw = row.get(column, "")
    if raw in (None, ""):
        return None
    return float(raw)
