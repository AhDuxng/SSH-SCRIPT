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
    """Giá trị của một giao thức theo từng kịch bản; None = không có trong ma trận."""

    protocol: str
    values: list[float | None]


# Vẽ nhóm cột, chỉ cấp vị trí cho cấu hình thực sự có số liệu và căn giữa
# nhóm quanh tick. Trả về vị trí tick trên trục x.
def grouped_bars(
    axis, scenarios, series, *, annotate=True, annotation_format="{:.1f}",
):
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


# Vẽ mỗi kịch bản một panel, mỗi stream vật lý một cột, nhóm theo giao thức.
#
# Khác với grouped_bars (gộp mọi stream thành một cột cho mỗi giao thức), hàm
# này giữ nguyên chi tiết từng stream mà stream_summary.csv đã thống kê. Giao
# thức nào không hỗ trợ đa stream chỉ xuất hiện ở kịch bản một workload — cột
# của nó không bị bịa ra ở các kịch bản còn lại.
def per_stream_panels(
    scenarios, lookup, column, protocol_order, *,
    ylabel, title="", scenario_titles=None, role_label=None, note="",
    annotation_format="{:.1f}",
):
    scenario_titles = scenario_titles or {}
    role_label = role_label or (lambda protocol, role: role)

    # Thứ tự cột: theo giao thức trước, rồi tới stream trong giao thức đó.
    panels = []
    for scenario in scenarios:
        bars = []
        for protocol in protocol_order:
            roles = sorted(
                key[2] for key in lookup
                if key[0] == protocol and key[1] == scenario
            )
            for role in roles:
                value = value_or_none(lookup, (protocol, scenario, role), column)
                if value is not None:
                    bars.append((protocol, role, value))
        panels.append((scenario, bars))

    # Panel có nhiều stream phải rộng hơn, nếu không cột và nhãn chồng lên nhau.
    widths = [max(len(bars), 1) for _, bars in panels]
    total = sum(widths)
    figure, axes = plt.subplots(
        1, len(panels), sharey=True,
        figsize=(max(7.0, 0.62 * total + 1.4), 4.2),
        gridspec_kw={"width_ratios": widths},
    )
    if len(panels) == 1:
        axes = [axes]

    observed = [value for _, bars in panels for _, _, value in bars]
    ceiling = max(observed, default=1.0) or 1.0
    for axis, (scenario, bars) in zip(axes, panels):
        labels = []
        for index, (protocol, role, value) in enumerate(bars):
            style = PROTOCOL_STYLE.get(protocol, {})
            axis.bar(
                index, value, 0.72, edgecolor="black", linewidth=0.5,
                color=style.get("color"), hatch=style.get("hatch"),
            )
            labels.append(
                f"{protocol_label(protocol)}\n{role_label(protocol, role)}"
            )
        if bars:
            axis.set_xticks(range(len(bars)), labels, fontsize=5.5)
            axis.set_xlim(-0.65, len(bars) - 0.35)
            for container in axis.containers:
                axis.bar_label(
                    container, fmt=annotation_format, fontsize=5.5,
                    padding=1.5, rotation=90,
                )
        axis.set_ylim(0, ceiling * 1.30)
        axis.set_title(scenario_titles.get(scenario, scenario), fontsize=8)
        axis.tick_params(axis="x", length=0)

    axes[0].set_ylabel(ylabel)
    if title:
        figure.suptitle(title, y=1.0)
    if note:
        figure.text(0.5, -0.05, note, ha="center", fontsize=6.5)
    return figure

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
